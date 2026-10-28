#!/usr/bin/env python3
import os

from guardrails_config import add_context, error_log


def main():
    log = error_log()
    if not os.path.exists(log):
        return
    marker = log + ".seen"
    with open(log) as f:
        lines = f.read().splitlines()
    seen = 0
    if os.path.exists(marker):
        with open(marker) as f:
            seen = int(f.read().strip() or 0)
    fresh = lines[seen:] if seen <= len(lines) else lines
    with open(marker, "w") as f:
        f.write(str(len(lines)))
    if not fresh:
        return
    shown = "\n".join(fresh[-10:])
    add_context("SessionStart", f"GUARDRAILS WARNING: {len(fresh)} hook failure(s) since the last session, from {log}. Report this to the user before starting work.\n{shown}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
