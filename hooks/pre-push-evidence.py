#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from guardrails_config import audit_dir, decide, expand_template, glob_match, load, log_hook_error, repo_for
from shell_parse import strip_heredocs, walk


def ask(reason):
    decide("PreToolUse", "ask", reason)


def deny(reason):
    decide("PreToolUse", "deny", reason)


def log_path():
    return os.path.join(audit_dir(), "push-evidence.jsonl")


def run(args, cwd, timeout=60):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def push_dir(cmd, cwd):
    for path, sub, args in walk(cmd, cwd):
        if sub == "push" and "--delete" not in args and "-d" not in args:
            return path
    return None


def in_app(path, app):
    return app in (".", "") or path.startswith(app.rstrip("/") + "/")


def app_relative(path, app):
    return path if app in (".", "") else path.removeprefix(app.rstrip("/") + "/")


def is_source(rel, repo):
    globs = repo.get("changed_file_globs") or ["**/*"]
    excludes = repo.get("exclude_globs") or []
    return any(glob_match(rel, g) for g in globs) and not any(glob_match(rel, g) for g in excludes)


def stem(name):
    for suffix in (".test", ".spec"):
        base = os.path.splitext(name)[0]
        if base.endswith(suffix):
            return base.removesuffix(suffix)
    return os.path.splitext(name)[0]


def find_tests(app_root, changed, repo):
    pattern = repo.get("test_file_pattern") or "test_{stem}.py"
    tests_dir = repo.get("tests_dir") or "tests"
    test_glob = pattern.replace("{stem}", "*")
    index = {}
    for root, dirs, files in os.walk(os.path.join(app_root, tests_dir)):
        dirs[:] = [d for d in dirs if d not in {"node_modules", "vendor", ".venv", ".git"}]
        for f in files:
            if glob_match(f, test_glob):
                index.setdefault(f, []).append(os.path.relpath(os.path.join(root, f), app_root))
    picked = []
    for rel in changed:
        name = os.path.basename(rel)
        if glob_match(name, test_glob):
            picked.append(rel)
        else:
            picked += index.get(pattern.replace("{stem}", stem(name)), [])
    return sorted(set(picked))


def record(entry):
    path = log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def already_verified(sha):
    path = log_path()
    if not os.path.exists(path):
        return False
    with open(path) as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                if row.get("sha") == sha and row.get("result") == "pass":
                    return True
    return False


def main():
    payload = json.load(sys.stdin)
    cmd = payload.get("tool_input", {}).get("command", "")
    if "push" not in strip_heredocs(cmd):
        return
    path = push_dir(cmd, payload.get("cwd") or os.getcwd())
    if not path:
        return

    common = run(["git", "-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir"], path).stdout.strip()
    repo_root = os.path.dirname(common) if common else ""
    repo = repo_for(repo_root)
    if not repo:
        return

    settings = load()["push_evidence"]
    top = run(["git", "-C", path, "rev-parse", "--show-toplevel"], path).stdout.strip()
    app = repo.get("app_dir") or "."
    app_root = os.path.normpath(os.path.join(top, app))
    sha = run(["git", "rev-parse", "HEAD"], top).stdout.strip()
    if already_verified(sha):
        return

    diff = run(["git", "diff", "--name-only", "--diff-filter=ACMR", f"{repo.get('base_ref', 'origin/main')}...HEAD"], top)
    if diff.returncode != 0:
        ask(f"Could not diff against {repo.get('base_ref')} ({diff.stderr.strip()[:200]}), so tests were not checked. Push anyway?")
    changed = [app_relative(f, app) for f in diff.stdout.split() if in_app(f, app)]
    changed = [f for f in changed if is_source(f, repo)]
    runner = repo.get("test_runner") or ""
    uses_container = "{container}" in runner
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo": repo.get("name"),
        "sha": sha,
        "top": top,
        "changed": len(changed),
        "runner": "container" if uses_container else "local",
    }
    if not changed:
        record({**entry, "result": "pass", "note": "no matching source files changed"})
        return

    values = {"files": [os.path.join(app_root, f) for f in changed], "app_root": app_root, "repo_root": repo_root, "container": repo.get("container", "")}
    formatter = repo.get("formatter_check")
    if formatter:
        fmt = run(expand_template(formatter, **values), app_root, settings["formatter_timeout_seconds"])
        if fmt.returncode != 0:
            record({**entry, "result": "fail", "stage": "formatter"})
            deny("Formatter check failed on changed files. Format, commit, then push again.\n" + (fmt.stdout + fmt.stderr)[-1500:])

    if not runner:
        record({**entry, "result": "unverified", "stage": "no_runner"})
        ask(f"No test_runner is configured for {repo.get('name')}, so tests did not run. Push anyway?")

    tests = find_tests(app_root, changed, repo)
    if not tests:
        record({**entry, "result": "no_tests"})
        ask(f"No tests found for the {len(changed)} changed file(s) on this branch. Every code change should ship with tests. Push anyway?")

    if uses_container:
        if os.path.realpath(top) != os.path.realpath(repo_root):
            record({**entry, "result": "unverified", "stage": "worktree", "tests": tests})
            ask("Pushing from a worktree: the container only mounts the main checkout, so these tests did not run: " + ", ".join(tests[:10]) + ". Push anyway?")
        docker = settings["docker_bin"]
        container = repo.get("container", "")
        up = run([docker, "inspect", "-f", "{{.State.Running}}", container], top, settings["docker_timeout_seconds"]).stdout.strip() if shutil.which(docker) else ""
        if up != "true":
            record({**entry, "result": "unverified", "stage": "container_down", "tests": tests})
            ask(f"Container {container} is not running, so {len(tests)} matching test file(s) did not run. Start it, or push anyway?")

    batch = tests[:settings["max_tests"]]
    result = run(expand_template(runner, **{**values, "files": batch}), app_root, settings["test_timeout_seconds"])
    ok = result.returncode == 0
    record({**entry, "result": "pass" if ok else "fail", "stage": "tests", "tests": batch, "skipped_over_cap": len(tests) - len(batch)})
    if not ok:
        deny(f"Tests failed for this branch ({len(batch)} file(s)). Fix them, then push again.\n" + (result.stdout + result.stderr)[-2500:])


if __name__ == "__main__":
    try:
        main()
    except subprocess.TimeoutExpired:
        ask("The pre-push check timed out. Push without test evidence?")
    except SystemExit:
        raise
    except Exception:
        log_hook_error("pre-push-evidence")
        ask("The pre-push evidence hook failed, so tests were not checked. Push anyway?")
