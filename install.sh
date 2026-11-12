#!/bin/sh
# Makes this machine ready for the kernel and says plainly what is missing: python3, git, GitHub and an agent, one
# line each, then the `ceo` link in ~/.local/bin and one question about Claude Code's transcripts. It installs no
# package, edits no shell file, starts nothing and reads no credential: `gh api user` is its only `gh` call.
# Run it again and it says the same and changes nothing. Exit 0 with nothing missing, else 1.
clone=$(cd "$(dirname "$0")" && pwd -P) || exit 1
missing=0 pyok=
if [ "$(uname -s)" = Darwin ]; then get='brew install'; else get='sudo apt-get install'; fi

tick() { printf '✓ %s: %s\n' "$1" "$2"; }
cross() { printf '✗ %s: %s. Fix: %s\n' "$1" "$2" "$3"; missing=$((missing + 1)); }

if ! command -v python3 >/dev/null 2>&1; then
    cross python3 'python3 is not installed' "$get python3"
elif py=$(python3 -c 'import sys; v = sys.version_info; print("%d.%d.%d" % v[:3]); sys.exit(v < (3, 10))' 2>/dev/null); then
    tick python3 "$py"
    pyok=1
elif [ -n "$py" ]; then
    cross python3 "Python $py is older than 3.10" "$get python3"
else
    cross python3 'python3 does not run' "$get python3"
fi

if ! gitv=$(git --version 2>/dev/null); then
    if [ "$(uname -s)" = Darwin ]; then fix='xcode-select --install'; else fix="$get git"; fi
    cross git 'git is not installed' "$fix"
elif ! git var GIT_COMMITTER_IDENT >/dev/null 2>&1; then
    cross git 'git has no name and email to sign a commit with' \
        'git config --global user.name "Your Name" && git config --global user.email you@example.com'
else
    tick git "${gitv#git version }"
fi

if ! command -v gh >/dev/null 2>&1; then
    cross GitHub 'gh is not installed' "$get gh"
elif login=$(gh api user --jq .login 2>/dev/null </dev/null) && [ -n "$login" ]; then
    tick GitHub "logged in as $login"
else
    cross GitHub 'gh is not logged in to GitHub' 'gh auth login'
fi

agents=
for name in claude codex; do
    if command -v "$name" >/dev/null 2>&1; then agents="${agents:+$agents, }$name"; fi
done
if [ -n "$agents" ]; then
    tick agent "$agents"
else
    cross agent 'neither claude nor codex is on PATH' 'npm install -g @anthropic-ai/claude-code'
fi

# The link: one `ceo` in ~/.local/bin to this clone's launcher, left as it is when it already resolves there (the
# same file, by any path); a file that is no link is never touched.
bin=$HOME/.local/bin link=$HOME/.local/bin/ceo target=$clone/tools/launch.py
if [ -e "$link" ] && [ ! -L "$link" ]; then
    cross ceo "$link exists and is not a link" "mv $link $link.old"
elif ! mkdir -p "$bin"; then
    cross ceo "could not make $bin" "mkdir -p $bin"
elif [ "$(readlink "$link" 2>/dev/null)" = "$target" ] || [ "$link" -ef "$target" ] ||
    { rm -f "$link" && ln -s "$target" "$link"; }; then
    tick ceo "~/.local/bin/ceo -> $target"
else
    cross ceo "could not link $link" "ln -sf $target $link"
fi

# The one setting, asked only when Claude Code is here and has not set its own transcript keeping: a y adds the
# key to its settings.json, whole, beside the old file and renamed over it. Not readable JSON: left alone, unasked.
settings=${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json
keep='
import json, os, sys, tempfile
path, write = os.path.realpath(sys.argv[1]), sys.argv[2] == "write"
try:
    with open(path, encoding="utf-8") as file:
        data = json.load(file)
except FileNotFoundError:
    data = {}
except (OSError, ValueError):
    sys.exit(1)
if not isinstance(data, dict) or "cleanupPeriodDays" in data:
    sys.exit(1)
if write:
    data["cleanupPeriodDays"] = 90
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=folder, prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
            file.write("\n")
        os.chmod(temp, os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o644)
        os.replace(temp, path)
    except BaseException:
        os.unlink(temp)
        raise
'
if [ -n "$pyok" ] && command -v claude >/dev/null 2>&1 && python3 -I -B -c "$keep" "$settings" ask; then
    printf 'Claude Code keeps its transcripts 30 days, and the CEO remembers a project from them. A y adds "cleanupPeriodDays": 90 to %s [y/N] ' "$settings"
    if IFS= read -r answer; then [ -t 0 ] || echo; else answer=; echo; fi
    case $answer in
        y | yes)
            if python3 -I -B -c "$keep" "$settings" write; then echo "Added \"cleanupPeriodDays\": 90 to $settings."
            else echo "Could not write $settings: nothing changed."; fi ;;
    esac
fi

if [ "$missing" -gt 0 ]; then
    echo "$missing missing: run each fix above, then $clone/install.sh again."
    exit 1
fi
case ":$PATH:" in
    *":$bin:"*) echo 'Ready. Next: ceo try' ;;
    *)
        echo 'Ready.'
        echo "Add this line to your shell's startup file (~/.zshrc for zsh, ~/.bashrc for bash):"
        echo '  export PATH="$HOME/.local/bin:$PATH"'
        echo 'Until a new terminal reads it, this works:'
        echo '  ~/.local/bin/ceo try' ;;
esac
