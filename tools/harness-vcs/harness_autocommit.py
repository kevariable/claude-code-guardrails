#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from harness_vcs import load


def git(repo, *args):
    if not os.path.isdir(repo["git_dir"]):
        return None
    return subprocess.run(
        ["git", f"--git-dir={repo['git_dir']}", f"--work-tree={repo['work_tree']}", *args],
        capture_output=True, text=True, timeout=20, cwd=repo["work_tree"],
    )


def locate(cfg, path):
    path = os.path.realpath(path) if os.path.exists(path) else os.path.abspath(path)
    if any(s in path for s in cfg["skip"]):
        return None
    for repo in sorted(cfg["repos"], key=lambda r: len(r["work_tree"]), reverse=True):
        if path.startswith(repo["work_tree"] + os.sep):
            rel = os.path.relpath(path, repo["work_tree"])
            if any(rel == a or (a.endswith("/") and rel.startswith(a)) for a in repo["areas"]):
                return repo, rel
            return None
    return None


def commit(repo, message):
    staged = git(repo, "diff", "--cached", "--quiet")
    if staged is not None and staged.returncode == 1:
        git(repo, "commit", "-q", "-m", message)


def main():
    payload = json.load(sys.stdin)
    cfg = load()
    session = (payload.get("session_id") or "")[:8]

    if payload.get("hook_event_name") == "PostToolUse":
        path = payload.get("tool_input", {}).get("file_path", "")
        hit = locate(cfg, path) if path else None
        if hit:
            repo, rel = hit
            git(repo, "add", "-f", "--", rel)
            commit(repo, f"auto: {payload.get('tool_name', 'edit')} {rel} [session {session}]")
        return

    for repo in cfg["repos"]:
        git(repo, "add", "-u")
        commit(repo, f"auto: end of turn, files changed outside Edit/Write [session {session}]")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            log = os.path.join(os.path.expanduser(os.environ.get("GUARDRAILS_AUDIT_DIR") or "~/.claude/audit"), "hook-errors.log")
            os.makedirs(os.path.dirname(log), exist_ok=True)
            with open(log, "a") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} harness_autocommit: {type(e).__name__}: {e}\n")
        except Exception:
            pass
