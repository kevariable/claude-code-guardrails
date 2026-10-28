#!/usr/bin/env python3
import json
import os
import re
import sys
from datetime import datetime, timezone

from guardrails_config import audit_dir, load
from mcp_tools import is_gated_server, is_write, log_hook_error


def clip(value, limit=200):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"...[{len(value)} chars]"
    if isinstance(value, list):
        return [clip(v, limit) for v in value[:25]] + ([f"...[{len(value)} items]"] if len(value) > 25 else [])
    if isinstance(value, dict):
        return {k: clip(v, limit) for k, v in value.items()}
    return value


def main():
    payload = json.load(sys.stdin)
    tool = payload.get("tool_name", "")
    if not is_write(tool):
        return
    if not load()["mcp"]["audit_all_servers"] and not is_gated_server(tool):
        return

    response = payload.get("tool_response")
    text = json.dumps(response, default=str) if response is not None else ""
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session": payload.get("session_id"),
        "cwd": payload.get("cwd"),
        "tool": tool,
        "input": clip(payload.get("tool_input", {})),
        "error": bool(re.search(r'"is_?error"\s*:\s*true|"error"\s*:', text)),
        "response": clip(text, 300),
    }

    os.makedirs(audit_dir(), exist_ok=True)
    path = os.path.join(audit_dir(), datetime.now().strftime("%Y-%m") + ".jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_hook_error("audit-mcp-actions")
