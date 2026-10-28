#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys

from guardrails_config import decide, load, log_hook_error
from shell_parse import strip_heredocs, walk

SWITCHED = {}


def deny(reason):
    decide("PreToolUse", "deny", reason)


def git_out(path, *args):
    try:
        return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def branch_of(path):
    return git_out(path, "rev-parse", "--abbrev-ref", "HEAD")


def toplevel(path):
    return git_out(path, "rev-parse", "--show-toplevel") or path


def is_branch(path, name):
    out = subprocess.run(
        ["git", "-C", path, "show-ref", "--verify", "--quiet", f"refs/heads/{name}"],
        capture_output=True, timeout=5,
    )
    return out.returncode == 0


def switched_to(sub, args, path):
    if sub not in {"checkout", "switch"} or "--" in args:
        return None
    for flag in ("-b", "-B", "-c", "-C"):
        if flag in args:
            i = args.index(flag)
            return args[i + 1] if i + 1 < len(args) else None
    positional = [a for a in args if not a.startswith("-")]
    if len(positional) == 1 and is_branch(path, positional[0]):
        return positional[0]
    return None


def push_target(refspec, branch):
    dst = refspec.split(":")[-1].lstrip("+").removeprefix("refs/heads/")
    return branch if dst in {"HEAD", "@", ""} else dst


def check_git(path, sub, args, protected):
    if sub in {"commit", "push", "merge", "rebase"} and "--no-verify" in args:
        deny("Refusing --no-verify. Fix the underlying hook failure instead.")

    top = toplevel(path)
    target = switched_to(sub, args, path)
    if target:
        SWITCHED[top] = target
        return

    branch = SWITCHED.get(top) or branch_of(path)

    if sub == "commit" and branch in protected:
        deny(f"Refusing to commit directly to '{branch}'. Create a feature branch first.")

    if sub == "push":
        refspecs = [a for a in args if not a.startswith("-")][1:]
        targets = {push_target(r, branch) for r in refspecs} if refspecs else {branch}
        if targets & protected:
            deny("Refusing to push to a protected branch. Push a feature branch and open a PR.")


def main():
    payload = json.load(sys.stdin)
    cmd = payload.get("tool_input", {}).get("command", "")
    cwd = payload.get("cwd") or os.getcwd()
    if not cmd:
        return

    cfg = load()
    text = strip_heredocs(cmd)
    for rule in cfg["destructive_commands"]:
        context = rule.get("context") or ""
        if re.search(rule["pattern"], text, re.I) and (not context or re.search(context, text, re.I)):
            deny(rule.get("reason") or "Refusing destructive command.")

    protected = set(cfg["protected_branches"])
    for path, sub, args in walk(cmd, cwd):
        check_git(path, sub, args, protected)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log_hook_error("block-dangerous-git")
        decide("PreToolUse", "ask", "The git guard hook failed, so this command could not be checked. Approve manually?")
