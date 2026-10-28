#!/usr/bin/env python3
import json
import os
import subprocess
import sys

from guardrails_config import expand_template, glob_match, load, log_hook_error


def find_root(file_path, marker):
    directory = os.path.dirname(os.path.abspath(file_path))
    if not marker:
        return directory
    home = os.path.expanduser("~")
    while True:
        if os.path.exists(os.path.join(directory, marker)):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory or directory == home:
            return None
        directory = parent


def main():
    file_path = json.load(sys.stdin).get("tool_input", {}).get("file_path", "")
    if not file_path:
        return
    cfg = load()
    if any(glob_match(file_path, g) for g in cfg["format_exclude_globs"]):
        return
    formatter = cfg["formatters"].get(os.path.splitext(file_path)[1].lstrip("."))
    if not formatter:
        return
    root = find_root(file_path, formatter.get("root_marker"))
    if root is None:
        return
    argv = expand_template(formatter["command"], file=file_path, root=root)
    result = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=formatter.get("timeout_seconds", 60))
    if result.returncode != 0:
        tail = (result.stdout + result.stderr).strip().splitlines()
        log_hook_error("format-on-write", f"{file_path}: {tail[-1] if tail else 'exit ' + str(result.returncode)}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_hook_error("format-on-write")
