#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone


def load(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        text = f.read().strip()
    return json.loads(text) if text else {}


def snippet(path, dest):
    with open(path) as f:
        return json.loads(f.read().replace("{{GUARDRAILS_DIR}}", dest))


def commands(group):
    return [h.get("command", "") for h in group.get("hooks", [])]


def install(settings, snip):
    hooks = settings.setdefault("hooks", {})
    for event, groups in snip.get("hooks", {}).items():
        existing = hooks.setdefault(event, [])
        present = {c for g in existing for c in commands(g)}
        for group in groups:
            fresh = [h for h in group["hooks"] if h["command"] not in present]
            if fresh:
                existing.append({**group, "hooks": fresh})
    ask = settings.setdefault("permissions", {}).setdefault("ask", [])
    for rule in snip.get("permissions", {}).get("ask", []):
        if rule not in ask:
            ask.append(rule)
    return settings


def uninstall(settings, dest):
    marker = os.path.join(dest, "hooks") + "/"
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        groups = []
        for group in hooks[event]:
            kept = [h for h in group.get("hooks", []) if marker not in h.get("command", "")]
            if kept:
                groups.append({**group, "hooks": kept})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if "hooks" in settings and not settings["hooks"]:
        del settings["hooks"]
    return settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("settings")
    parser.add_argument("--snippet", required=True)
    parser.add_argument("--dest", required=True)
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dest = os.path.abspath(os.path.expanduser(args.dest))
    current = load(args.settings)
    before = json.dumps(current, sort_keys=True)
    result = uninstall(current, dest) if args.uninstall else install(current, snippet(args.snippet, dest))
    text = json.dumps(result, indent=2) + "\n"

    if json.dumps(result, sort_keys=True) == before:
        print(f"{args.settings}: already up to date")
        return
    if args.dry_run:
        sys.stdout.write(text)
        return
    os.makedirs(os.path.dirname(os.path.abspath(args.settings)), exist_ok=True)
    if os.path.exists(args.settings):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(args.settings, f"{args.settings}.bak.{stamp}")
        print(f"backed up {args.settings} to {args.settings}.bak.{stamp}")
    with open(args.settings, "w") as f:
        f.write(text)
    print(f"{'removed guardrails from' if args.uninstall else 'wrote'} {args.settings}")


if __name__ == "__main__":
    main()
