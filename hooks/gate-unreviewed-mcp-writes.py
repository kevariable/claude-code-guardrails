#!/usr/bin/env python3
import json
import sys

from guardrails_config import allowlist_path, config_error, decide
from mcp_tools import is_gated_server, is_write, log_hook_error, reviewed_writes


def ask(reason):
    decide("PreToolUse", "ask", reason)


def main():
    tool = json.load(sys.stdin).get("tool_name", "")
    if config_error():
        ask(f"The guardrails config could not be read ({config_error()}), so {tool} could not be checked. Approve manually?")
    if not is_gated_server(tool) or not is_write(tool):
        return
    if tool in reviewed_writes():
        return
    ask(f"{tool} changes live data and has not been reviewed. Approve this call, or add it to {allowlist_path()} with a reason.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log_hook_error("gate-unreviewed-mcp-writes")
        ask("The MCP write gate hook failed, so this call could not be checked. Approve manually?")
