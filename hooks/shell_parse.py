import os
import re
import shlex

HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\s*(?=\n|$)", re.S)
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
MAX_DEPTH = 3


def strip_heredocs(cmd):
    return HEREDOC.sub("", cmd)


def segments(cmd):
    cmd = strip_heredocs(cmd)
    out, buf, quote, i = [], [], None, 0
    while i < len(cmd):
        c = cmd[i]
        if quote:
            buf.append(c)
            if c == "\\" and quote == '"' and i + 1 < len(cmd):
                buf.append(cmd[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"":
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < len(cmd):
            buf += [c, cmd[i + 1]]
            i += 2
            continue
        if cmd[i:i + 2] in ("&&", "||"):
            out.append("".join(buf))
            buf = []
            i += 2
            continue
        if c in ";|\n" or (c == "&" and (not buf or buf[-1] not in "<>")):
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


def tokens_of(segment):
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def resolve(base, target):
    return os.path.normpath(os.path.join(base, os.path.expanduser(target)))


def inner_command(tokens):
    if not tokens:
        return None
    if tokens[0] == "eval":
        return " ".join(tokens[1:]) or None
    if os.path.basename(tokens[0]) in SHELLS:
        for i, token in enumerate(tokens[1:], 1):
            if token.startswith("-") and not token.startswith("--") and "c" in token[1:]:
                return tokens[i + 1] if i + 1 < len(tokens) else None
    return None


def git_call(tokens, cwd):
    if "git" not in tokens:
        return None
    i, path = tokens.index("git") + 1, cwd
    while i < len(tokens) and tokens[i].startswith("-"):
        if tokens[i] == "-C" and i + 1 < len(tokens):
            path = resolve(path, tokens[i + 1])
            i += 2
        elif tokens[i] == "-c":
            i += 2
        else:
            i += 1
    if i >= len(tokens):
        return None
    return path, tokens[i], tokens[i + 1:]


def walk(cmd, cwd, depth=0):
    for segment in segments(cmd):
        tokens = tokens_of(segment)
        if not tokens:
            continue
        if tokens[0] == "cd" and len(tokens) > 1:
            cwd = resolve(cwd, tokens[1])
            continue
        inner = inner_command(tokens)
        if inner is not None and depth < MAX_DEPTH:
            yield from walk(inner, cwd, depth + 1)
            continue
        call = git_call(tokens, cwd)
        if call:
            yield call
