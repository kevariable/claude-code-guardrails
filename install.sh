#!/usr/bin/env bash
set -euo pipefail

src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest="$HOME/.claude-guardrails"
project="$PWD"
link=0
settings=1
remove=0
dry=0

usage() {
    cat <<EOF
Usage: ./install.sh [options]

  --dest DIR       where hooks and config live (default: ~/.claude-guardrails)
  --project DIR    project whose .claude/settings.json gets the hooks (default: current dir)
  --user           write to ~/.claude/settings.json instead, so every project gets the hooks
  --link           symlink hooks/ instead of copying, so a git pull updates them
  --no-settings    install files only, do not touch any settings.json
  --uninstall      remove the hook entries from settings.json (files and ask rules stay)
  --dry-run        print what would change, write nothing
EOF
}

target=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dest) dest="$2"; shift 2 ;;
        --project) project="$2"; shift 2 ;;
        --user) target="$HOME/.claude/settings.json"; shift ;;
        --link) link=1; shift ;;
        --no-settings) settings=0; shift ;;
        --uninstall) remove=1; shift ;;
        --dry-run) dry=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

for bin in python3 git; do
    command -v "$bin" >/dev/null || { echo "$bin is required" >&2; exit 1; }
done

dest="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$dest")"
target="${target:-$project/.claude/settings.json}"
merge=(python3 "$src/tools/merge_settings.py" "$target" --snippet "$src/settings/settings.snippet.json" --dest "$dest")
[[ $dry -eq 1 ]] && merge+=(--dry-run)

if [[ $remove -eq 1 ]]; then
    "${merge[@]}" --uninstall
    echo "permissions.ask rules and $dest were left in place. To delete the files: rm -rf \"$dest\""
    exit 0
fi

if [[ $dry -eq 1 ]]; then
    echo "would install hooks from $src/hooks into $dest/hooks ($([[ $link -eq 1 ]] && echo symlink || echo copy))"
else
    mkdir -p "$dest" "$dest/tools"
    if [[ $link -eq 1 ]]; then
        rm -rf "$dest/hooks"
        ln -s "$src/hooks" "$dest/hooks"
    else
        rm -rf "$dest/hooks"
        cp -R "$src/hooks" "$dest/hooks"
        rm -rf "$dest/hooks/__pycache__"
    fi
    cp -R "$src/tools/harness-vcs" "$dest/tools/"
    for pair in "guardrails.config.example.json:guardrails.config.json" "hooks/mcp-write-allowlist.example.txt:mcp-write-allowlist.txt"; do
        from="${pair%%:*}"
        to="${pair##*:}"
        if [[ -e "$dest/$to" ]]; then
            echo "$dest/$to exists, left untouched"
        else
            cp "$src/$from" "$dest/$to"
            echo "created $dest/$to"
        fi
    done
fi

[[ $settings -eq 1 ]] && "${merge[@]}"

cat <<EOF

Next steps:
  1. Edit $dest/guardrails.config.json: set protected_branches, add one entry under "repos" per project, list your MCP servers under mcp.gated_server_prefixes.
  2. Review $dest/mcp-write-allowlist.txt and the permissions.ask list in $target.
  3. Run the tests: python3 "$src/tests/run_tests.py"
  4. Start a new Claude Code session in the project so the hooks load.
EOF
