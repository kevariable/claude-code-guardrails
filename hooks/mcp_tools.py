import os
import re

from guardrails_config import allowlist_path, load, log_hook_error

__all__ = ["short_name", "is_write", "is_gated_server", "reviewed_writes", "log_hook_error"]


def short_name(tool):
    return tool.split("__")[-1]


def is_write(tool):
    return bool(re.search(load()["mcp"]["write_verb_regex"], short_name(tool)))


def is_gated_server(tool):
    return tool.startswith(tuple(load()["mcp"]["gated_server_prefixes"]))


def reviewed_writes():
    path = allowlist_path()
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return {line.split("#")[0].strip() for line in f if line.split("#")[0].strip()}
