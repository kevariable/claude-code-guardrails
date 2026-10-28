import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def config_path():
    env = os.environ.get("HARNESS_VCS_CONFIG")
    if env:
        return os.path.expanduser(env)
    for candidate in (os.path.expanduser("~/.claude-guardrails/harness-vcs.json"), os.path.join(HERE, "harness-vcs.json")):
        if os.path.exists(candidate):
            return candidate
    raise FileNotFoundError("no harness-vcs.json found; copy harness-vcs.example.json to ~/.claude-guardrails/harness-vcs.json")


def load():
    with open(config_path()) as f:
        cfg = json.load(f)
    for repo in cfg["repos"]:
        repo["git_dir"] = os.path.realpath(os.path.expanduser(repo["git_dir"]))
        repo["work_tree"] = os.path.realpath(os.path.expanduser(repo["work_tree"]))
    cfg.setdefault("skip", [])
    return cfg
