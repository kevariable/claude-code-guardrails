#!/usr/bin/env python3
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, "hooks")
TESTS = os.path.join(ROOT, "tests")
FIXTURES = os.path.join(TESTS, "fixtures")


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True)


def make_repo(path, branch="master"):
    os.makedirs(path, exist_ok=True)
    git(path, "init", "-q", "-b", "master")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "test")
    git(path, "commit", "-q", "--allow-empty", "-m", "init")
    if branch != "master":
        git(path, "checkout", "-q", "-b", branch)


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def load_module(name):
    sys.path.insert(0, HOOKS)
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HOOKS, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Sandbox(unittest.TestCase):
    config = {}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.audit = os.path.join(self.tmp, "audit")
        self.config_path = os.path.join(self.tmp, "guardrails.config.json")
        with open(os.path.join(ROOT, "guardrails.config.example.json")) as f:
            base = json.load(f)
        base.update({"repos": [], "allowlist_path": os.path.join(FIXTURES, "mcp-write-allowlist.txt")})
        base["mcp"]["gated_server_prefixes"] = ["mcp__your-crm__", "mcp__claude_ai_Slack__"]
        base.update(self.config)
        self.write_config(base)
        self.env = {**os.environ, "GUARDRAILS_CONFIG": self.config_path, "GUARDRAILS_AUDIT_DIR": self.audit}
        self.env.pop("GUARDRAILS_ALLOWLIST", None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_config(self, cfg):
        self.cfg = cfg
        with open(self.config_path, "w") as f:
            json.dump(cfg, f)

    def hook(self, name, payload, env=None):
        out = subprocess.run(
            [sys.executable, os.path.join(HOOKS, name)],
            input=json.dumps(payload), capture_output=True, text=True, env={**self.env, **(env or {})}, timeout=60,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout) if out.stdout.strip() else None

    def decision(self, result):
        return result["hookSpecificOutput"]["permissionDecision"] if result else "allow"

    def audit_lines(self, name):
        path = os.path.join(self.audit, name)
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [line for line in f.read().splitlines() if line.strip()]


class GitGuard(Sandbox):
    def test_cases(self):
        dirs = {"m": os.path.join(self.tmp, "m"), "f": os.path.join(self.tmp, "f")}
        make_repo(dirs["m"])
        make_repo(dirs["f"], "feat/x")
        failures = []
        with open(os.path.join(TESTS, "git-guard-cases.txt")) as f:
            cases = [line.rstrip("\n") for line in f if line.strip()]
        for line in cases:
            expected, where, cmd = line.split("|", 2)
            cmd = cmd.replace("{m}", dirs["m"]).replace("\\n", "\n")
            got = self.decision(self.hook("block-dangerous-git.py", {"tool_input": {"command": cmd}, "cwd": dirs[where]}))
            if (got == "deny") != (expected == "DENY"):
                failures.append(f"expected {expected} got {got} [{where}] {cmd!r}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_custom_protected_branches(self):
        repo = os.path.join(self.tmp, "r")
        make_repo(repo, "release")
        self.write_config({**self.cfg, "protected_branches": ["release"]})
        self.assertEqual(self.decision(self.hook("block-dangerous-git.py", {"tool_input": {"command": "git commit -m x"}, "cwd": repo})), "deny")


class McpGate(Sandbox):
    def test_cases(self):
        failures = []
        with open(os.path.join(TESTS, "mcp-gate-cases.txt")) as f:
            cases = [line.strip() for line in f if line.strip()]
        for line in cases:
            expected, tool = line.split("|", 1)
            got = self.decision(self.hook("gate-unreviewed-mcp-writes.py", {"tool_name": tool, "tool_input": {}}))
            if got != expected:
                failures.append(f"expected {expected} got {got}: {tool}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_missing_allowlist_asks_for_every_write(self):
        self.write_config({**self.cfg, "allowlist_path": os.path.join(self.tmp, "missing.txt")})
        self.assertEqual(self.decision(self.hook("gate-unreviewed-mcp-writes.py", {"tool_name": "mcp__your-crm__add_note"})), "ask")

    def test_broken_config_fails_closed(self):
        write(self.config_path, "{not json")
        self.assertEqual(self.decision(self.hook("gate-unreviewed-mcp-writes.py", {"tool_name": "mcp__your-crm__get_contact"})), "ask")
        self.assertTrue(any("config:" in line for line in self.audit_lines("hook-errors.log")))

    def test_allowlists_never_overlap_ask_rules(self):
        with open(os.path.join(ROOT, "settings", "settings.snippet.json")) as f:
            ask = set(json.load(f)["permissions"]["ask"])
        for path in (os.path.join(FIXTURES, "mcp-write-allowlist.txt"), os.path.join(HOOKS, "mcp-write-allowlist.example.txt")):
            with open(path) as f:
                allow = {line.split("#")[0].strip() for line in f if line.split("#")[0].strip()}
            self.assertEqual(allow & ask, set(), path)


class AuditLog(Sandbox):
    def test_logs_writes_only(self):
        self.hook("audit-mcp-actions.py", {"tool_name": "mcp__your-crm__get_contact", "tool_input": {}})
        self.hook("audit-mcp-actions.py", {"tool_name": "mcp__your-crm__add_note", "tool_input": {"body": "x" * 500}})
        files = [f for f in os.listdir(self.audit) if f.endswith(".jsonl")]
        self.assertEqual(len(files), 1)
        rows = [json.loads(line) for line in self.audit_lines(files[0])]
        self.assertEqual([r["tool"] for r in rows], ["mcp__your-crm__add_note"])
        self.assertIn("[500 chars]", rows[0]["input"]["body"])


class PushEvidence(Sandbox):
    def repo(self, runner="true {files}", formatter=None, container=None):
        path = os.path.join(self.tmp, "svc")
        make_repo(path, "feat/x")
        write(os.path.join(path, "src", "foo.py"), "x = 1\n")
        write(os.path.join(path, "tests", "test_foo.py"), "def test_foo():\n    assert True\n")
        git(path, "add", ".")
        git(path, "commit", "-q", "-m", "work")
        entry = {
            "name": "svc", "path": path, "base_ref": "master", "app_dir": ".",
            "changed_file_globs": ["src/**/*.py", "tests/**/*.py"], "tests_dir": "tests",
            "test_file_pattern": "test_{stem}.py", "test_runner": runner,
        }
        if formatter:
            entry["formatter_check"] = formatter
        if container:
            entry["container"] = container
        self.write_config({**self.cfg, "repos": [entry]})
        return path

    def push(self, path, cmd="git push -u origin feat/x"):
        return self.hook("pre-push-evidence.py", {"tool_input": {"command": cmd}, "cwd": path})

    def test_passing_tests_allow_and_record(self):
        path = self.repo()
        self.assertIsNone(self.push(path))
        rows = [json.loads(line) for line in self.audit_lines("push-evidence.jsonl")]
        self.assertEqual(rows[-1]["result"], "pass")
        self.assertEqual(rows[-1]["tests"], ["tests/test_foo.py"])

    def test_verified_sha_is_not_rerun(self):
        path = self.repo()
        self.push(path)
        self.push(path)
        self.assertEqual(len(self.audit_lines("push-evidence.jsonl")), 1)

    def test_failing_tests_deny(self):
        self.assertEqual(self.decision(self.push(self.repo(runner="false {files}"))), "deny")

    def test_formatter_failure_denies(self):
        path = self.repo(formatter="false {files}")
        self.assertEqual(self.decision(self.push(path)), "deny")
        self.assertEqual(json.loads(self.audit_lines("push-evidence.jsonl")[-1])["stage"], "formatter")

    def test_no_matching_tests_asks(self):
        path = self.repo()
        write(os.path.join(path, "src", "bar.py"), "y = 2\n")
        os.remove(os.path.join(path, "tests", "test_foo.py"))
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "more")
        self.assertEqual(self.decision(self.push(path)), "ask")

    def test_container_down_asks(self):
        path = self.repo(runner="docker exec {container} pytest {files}", container="nope-1")
        self.write_config({**self.cfg, "push_evidence": {**self.cfg["push_evidence"], "docker_bin": "/nonexistent/docker"}})
        self.assertEqual(self.decision(self.push(path)), "ask")
        self.assertEqual(json.loads(self.audit_lines("push-evidence.jsonl")[-1])["stage"], "container_down")

    def test_heredoc_text_is_not_a_push(self):
        path = self.repo(runner="false {files}")
        self.assertIsNone(self.push(path, "cat > notes.md <<'EOF'\ngit push origin main\nEOF"))
        self.assertEqual(self.audit_lines("push-evidence.jsonl"), [])

    def test_real_push_after_heredoc_is_checked(self):
        path = self.repo(runner="false {files}")
        self.assertEqual(self.decision(self.push(path, "cat > notes.md <<'EOF'\nhello\nEOF\ngit push -u origin feat/x")), "deny")

    def test_push_inside_bash_c_is_checked(self):
        path = self.repo(runner="false {files}")
        self.assertEqual(self.decision(self.push(path, "bash -c 'git push -u origin feat/x'")), "deny")

    def test_unregistered_repo_is_ignored(self):
        path = os.path.join(self.tmp, "other")
        make_repo(path, "feat/x")
        self.assertIsNone(self.push(path))

    def test_find_tests_patterns(self):
        module = load_module("pre-push-evidence")
        root = os.path.join(self.tmp, "patterns")
        for rel in ("tests/Unit/FooTest.php", "tests/test_bar.py", "src/baz.test.ts"):
            write(os.path.join(root, rel))
        self.assertEqual(module.find_tests(root, ["app/Foo.php"], {"tests_dir": "tests", "test_file_pattern": "{stem}Test.php"}), ["tests/Unit/FooTest.php"])
        self.assertEqual(module.find_tests(root, ["pkg/bar.py"], {"tests_dir": "tests", "test_file_pattern": "test_{stem}.py"}), ["tests/test_bar.py"])
        self.assertEqual(module.find_tests(root, ["src/baz.ts"], {"tests_dir": "src", "test_file_pattern": "{stem}.test.ts"}), ["src/baz.test.ts"])
        self.assertEqual(module.find_tests(root, ["src/baz.test.ts"], {"tests_dir": "src", "test_file_pattern": "{stem}.test.ts"}), ["src/baz.test.ts"])


class FormatOnWrite(Sandbox):
    config = {"formatters": {"py": {"root_marker": "pyproject.toml", "command": "false {file}"}, "php": {"root_marker": "vendor/bin/pint", "command": "{root}/vendor/bin/pint {file}"}}}

    def test_failing_formatter_logs(self):
        write(os.path.join(self.tmp, "proj", "pyproject.toml"))
        target = os.path.join(self.tmp, "proj", "a.py")
        write(target)
        self.hook("format-on-write.py", {"tool_input": {"file_path": target}})
        self.assertTrue(any("format-on-write" in line for line in self.audit_lines("hook-errors.log")))

    def test_excluded_and_unrooted_files_are_skipped(self):
        write(os.path.join(self.tmp, "proj", "pyproject.toml"))
        excluded = os.path.join(self.tmp, "proj", "node_modules", "a.py")
        unrooted = os.path.join(self.tmp, "loose", "a.php")
        for path in (excluded, unrooted):
            write(path)
            self.hook("format-on-write.py", {"tool_input": {"file_path": path}})
        self.assertEqual(self.audit_lines("hook-errors.log"), [])


class SessionStart(Sandbox):
    def test_hook_health_reports_once(self):
        write(os.path.join(self.audit, "hook-errors.log"), "2026-01-01T00:00:00+00:00 some-hook: boom\n")
        first = self.hook("hook-health.py", {})
        self.assertIn("1 hook failure", first["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(self.hook("hook-health.py", {}))

    def test_branch_guard(self):
        m, f = os.path.join(self.tmp, "m"), os.path.join(self.tmp, "f")
        make_repo(m)
        make_repo(f, "feat/x")
        self.assertIn("master", self.hook("branch-guard.py", {}, {"CLAUDE_PROJECT_DIR": m})["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(self.hook("branch-guard.py", {}, {"CLAUDE_PROJECT_DIR": f}))


class Install(unittest.TestCase):
    def test_merge_is_additive_idempotent_and_reversible(self):
        tmp = tempfile.mkdtemp()
        try:
            project, dest = os.path.join(tmp, "proj"), os.path.join(tmp, "dest")
            settings = os.path.join(project, ".claude", "settings.json")
            original = {"model": "x", "permissions": {"allow": ["Bash(ls:*)"], "ask": ["Bash(gh pr merge:*)"]}, "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
            write(settings, json.dumps(original))
            env = {**os.environ, "HOME": tmp}
            cmd = ["bash", os.path.join(ROOT, "install.sh"), "--dest", dest, "--project", project]
            for _ in range(2):
                subprocess.run(cmd, check=True, capture_output=True, env=env)
            with open(settings) as f:
                merged = json.load(f)
            self.assertEqual(merged["model"], "x")
            self.assertEqual(merged["permissions"]["allow"], ["Bash(ls:*)"])
            self.assertEqual(merged["permissions"]["ask"].count("Bash(gh pr merge:*)"), 1)
            self.assertEqual(len(merged["hooks"]["PreToolUse"]), 2)
            self.assertIn(f"{dest}/hooks/block-dangerous-git.py", json.dumps(merged))
            self.assertTrue(os.path.exists(os.path.join(dest, "guardrails.config.json")))
            self.assertTrue(any(n.startswith("settings.json.bak.") for n in os.listdir(os.path.dirname(settings))))
            subprocess.run(cmd + ["--uninstall"], check=True, capture_output=True, env=env)
            with open(settings) as f:
                removed = json.load(f)
            self.assertNotIn(dest, json.dumps(removed))
            self.assertEqual(removed["hooks"], original["hooks"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
