import json
import os
import shlex
import sys
import traceback
from datetime import datetime, timezone
from fnmatch import fnmatch
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALL_DIR = os.path.expanduser("~/.claude-guardrails")

DEFAULTS = {
    "protected_branches": ["main", "master"],
    "audit_dir": "~/.claude/audit",
    "allowlist_path": "~/.claude-guardrails/mcp-write-allowlist.txt",
    "destructive_commands": [
        {"pattern": r"migrate:fresh|db:wipe|migrate:reset", "context": r"production|--env=prod", "reason": "Refusing destructive DB command against production."},
    ],
    "mcp": {
        "gated_server_prefixes": [],
        "write_verb_regex": r"(^|[_-])(send|set|record|create|update|delete|archive|deliver|enqueue|publish|add|import|mark|push|save|tag|move|schedule|reorder|score|start|run|pull|write|assign|attach|copy|remove|queue|cancel|enrich|deploy|upload|perform|watch|manage|convert)([_-]|$)",
        "audit_all_servers": True,
    },
    "formatters": {},
    "format_exclude_globs": ["*/vendor/*", "*/node_modules/*", "*/.venv/*"],
    "push_evidence": {
        "max_tests": 40,
        "test_timeout_seconds": 540,
        "formatter_timeout_seconds": 120,
        "docker_timeout_seconds": 15,
        "docker_bin": "docker",
    },
    "repos": [],
}

_STATE = {"config_error": None}


def config_path():
    env = os.environ.get("GUARDRAILS_CONFIG")
    if env:
        return os.path.expanduser(env)
    for candidate in (os.path.join(INSTALL_DIR, "guardrails.config.json"), os.path.join(HERE, "..", "guardrails.config.json")):
        if os.path.exists(candidate):
            return os.path.normpath(candidate)
    return None


@lru_cache(maxsize=None)
def load():
    cfg = json.loads(json.dumps(DEFAULTS))
    path = config_path()
    if not path:
        return cfg
    try:
        with open(path) as f:
            user = json.load(f)
        if not isinstance(user, dict):
            raise ValueError("config root must be an object")
    except Exception as e:
        _STATE["config_error"] = f"{path}: {type(e).__name__}: {e}"
        _append(os.path.join(_audit_dir(cfg), "hook-errors.log"), f"config: {_STATE['config_error']}")
        return cfg
    for key, value in user.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key] = {**cfg[key], **value}
        else:
            cfg[key] = value
    return cfg


def config_error():
    load()
    return _STATE["config_error"]


def _audit_dir(cfg):
    return os.path.expanduser(os.environ.get("GUARDRAILS_AUDIT_DIR") or cfg["audit_dir"])


def audit_dir():
    return _audit_dir(load())


def error_log():
    return os.path.join(audit_dir(), "hook-errors.log")


def allowlist_path():
    return os.path.expanduser(os.environ.get("GUARDRAILS_ALLOWLIST") or load()["allowlist_path"])


def real(path):
    return os.path.realpath(os.path.expanduser(path))


def repo_for(repo_root):
    if not repo_root:
        return None
    root = real(repo_root)
    for repo in load()["repos"]:
        if real(repo["path"]) == root:
            return repo
    return None


def glob_match(path, pattern):
    return fnmatch(path, pattern) or ("**/" in pattern and fnmatch(path, pattern.replace("**/", "")))


def expand_template(template, **values):
    argv = []
    for token in shlex.split(template):
        if token == "{files}":
            argv += list(values.get("files", []))
            continue
        for key, value in values.items():
            if not isinstance(value, (list, tuple)):
                token = token.replace("{" + key + "}", str(value))
        argv.append(token)
    return argv


def decide(event, decision, reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event,
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def add_context(event, text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))


def _append(path, message):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}\n")
    except Exception:
        pass


def log_hook_error(hook, message=None):
    if message is None:
        lines = traceback.format_exc().strip().splitlines()
        message = lines[-1] if lines else "unknown error"
    _append(error_log(), f"{hook}: {message}")
