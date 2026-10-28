#!/usr/bin/env python3
import os
import subprocess

from guardrails_config import add_context, load


def main():
    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    out = subprocess.run(["git", "-C", project, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=5)
    branch = out.stdout.strip()
    if branch in set(load()["protected_branches"]):
        add_context("SessionStart", f"WARNING: You are on protected branch '{branch}'. Never commit to it directly. Create a feature branch (feat/... or fix/...) before making any changes.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
