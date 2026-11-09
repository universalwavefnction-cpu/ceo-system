#!/bin/sh
# Make <ssh-host> carry one order by itself: gh and claude there, both logged in; this kernel at the
# commit this script runs from, in ~/ceo-kernel; a private scratch project, <owner>/ceo-trial-<host>,
# listed in $CEO_HOME/projects.txt, with one order, a draft; the agent `ceo.py keep` writes, loaded; then
# the order filed. Run once per host from a plain terminal on the owner's Mac:
#     tools/bootstrap-host.sh <ssh-host>
# A login it cannot do stops it with one sentence saying what to do by hand; run it again after.
set -eu
host=${1:?usage: tools/bootstrap-host.sh <ssh-host>}
here=$(cd "$(dirname "$0")/.." && pwd)
url=$(git -C "$here" remote get-url origin)
commit=$(git -C "$here" rev-parse HEAD)
# The script reaches the host on standard input and is read whole before it starts, for everything it
# runs reads /dev/null: no command there can swallow the lines after it.
ssh "$host" "sh -s -- '$host' '${host#*@}' '$url' '$commit'" <<'THERE'
set -eu
bootstrap() {
    host=$1 name=$2 url=$3 commit=$4
    stop() { echo "bootstrap-host: $name: $*" >&2; exit 1; }
    again="then run this again."
    export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

    if [ "$(uname)" = Darwin ]; then
        command -v brew >/dev/null || stop "install Homebrew there by hand (https://brew.sh), $again"
        command -v gh >/dev/null || brew install -q gh
        command -v claude >/dev/null || brew install -q --cask claude-code
    else
        command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash
        if ! command -v gh >/dev/null; then  # gh has no install script: its release archive needs no root
            releases=https://github.com/cli/cli/releases
            version=$(curl -fsSLI -o /dev/null -w '%{url_effective}' $releases/latest | sed 's#.*/v##')
            build=gh_${version}_linux_$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/')
            unpacked=$(mktemp -d)
            curl -fsSL "$releases/download/v$version/$build.tar.gz" | tar -xz -C "$unpacked"
            mkdir -p "$HOME/.local/bin"
            mv "$unpacked/$build/bin/gh" "$HOME/.local/bin/gh"
            rm -rf "$unpacked"
        fi
    fi
    for tool in gh claude; do
        command -v $tool >/dev/null || stop "$tool could not be installed there."
    done
    gh auth status >/dev/null 2>&1 ||
        stop "log in to GitHub there by hand, ssh -t $host $(command -v gh) auth login, $again"
    claude auth status >/dev/null 2>&1 ||
        stop "log in to Claude there by hand, ssh -t $host $(command -v claude) auth login, $again"

    gh auth setup-git  # git clones and pushes with gh's login

    kernel=$HOME/ceo-kernel
    [ -d "$kernel/.git" ] ||
        gh repo clone "$(echo "$url" | sed -E 's#.*github\.com[:/]+##; s#(\.git)?/*$##')" "$kernel" -- -q
    git -C "$kernel" fetch -q origin
    git -C "$kernel" checkout -q --detach "$commit" ||
        stop "$kernel cannot check out $commit, which this script runs from:" \
            "push it, or undo the changes there, $again"

    owner=$(gh api user --jq .login)
    repo=$owner/ceo-trial-$name project=$HOME/ceo-trial-$name
    # Created only when GitHub answers that it is missing, else reused: GitHub refuses repositories
    # created in quick succession.
    if ! found=$(gh api "repos/$repo" --silent 2>&1); then
        case $found in
        *"(HTTP 404)"*) found=$(gh repo create "$repo" --private 2>&1) ||
            stop "GitHub did not create $repo ($(printf %s "$found" | tr -s '\n' ' ')), $again" ;;
        *) stop "GitHub did not say whether $repo exists ($(printf %s "$found" | tr -s '\n' ' ')), $again" ;;
        esac
    fi
    [ -d "$project/.git" ] || gh repo clone "$repo" "$project" -- -q
    cd "$project"
    # The scratch worker and its kept tick need an identity too, without changing the host's global one.
    git config --local user.name "CEO kernel"
    git config --local user.email ceo@localhost
    git rev-parse -q --verify HEAD >/dev/null || git symbolic-ref HEAD refs/heads/main
    if [ ! -f orders/001-hello.md ] && [ ! -f drafts/orders/001-hello.md ]; then
        mkdir -p drafts/orders
        machine=$(hostname)
        printf '# Vision\n\nA scratch project: the CEO kernel carries one order on %s by itself.\n' \
            "$machine" >VISION.md
        printf '# Below these floors the kernel starts nothing new.\ndisk_floor_gb = 5\nram_floor_gb = 1\n' \
            >ceo.toml
        cat >drafts/orders/001-hello.md <<ORDER
# Hello from $machine

## Goal
Write HELLO.md at the root of this repository, one line, "Hello from $machine.", naming the host this runs
on, and commit it.

## Done when
\`\`\`bash
git show HEAD:HELLO.md | grep -qF '$machine'
\`\`\`

## Not asked
Any other file.
ORDER
        git add VISION.md ceo.toml drafts/orders/001-hello.md
        git commit -qm "A scratch project with one order, 001-hello"
    fi
    git push -q -u origin main

    listed=${CEO_HOME:-$HOME/.ceo}/projects.txt
    mkdir -p "$(dirname "$listed")"
    [ -z "$(tail -c 1 "$listed" 2>/dev/null)" ] || echo >>"$listed"  # never glued onto a last line
    grep -qxF "$(pwd -P)" "$listed" 2>/dev/null || pwd -P >>"$listed"

    # keep writes the agent with the flags of the last tick started with --every: one such tick here
    # makes its first pass, probing the worker's login, and is stopped, as the owner would by hand. Any
    # agent kept before is stopped first, so this tick is the only one.
    unload=$(python3 "$kernel/ceo.py" keep --remove | sed -n 's/.*stop it with: //p')
    sh -c "$unload" >/dev/null 2>&1 || true
    mkdir -p .ceo
    rm -f .ceo/login.json  # the probes the kernel trusts ten minutes: a login just made is probed afresh
    log=$project/.ceo/tick.log before=$(grep '^Health: ' STATUS.md 2>/dev/null || true)
    python3 "$kernel/ceo.py" tick --every 60 >>"$log" 2>&1 &
    tick=$! waited=0
    # Its Health line, timed, is written last in a pass: STATUS.md changes earlier, when it sweeps.
    until health=$(grep '^Health: ' STATUS.md 2>/dev/null) && [ "$health" != "$before" ]; do
        kill -0 $tick 2>/dev/null || stop "its first tick ended before its first pass: see $log."
        [ $waited -lt 300 ] || { kill $tick; stop "its first tick made no pass in 300 s: see $log."; }
        sleep 2
        waited=$((waited + 2))
    done
    kill $tick 2>/dev/null || true
    wait $tick 2>/dev/null || true
    if [ "${health%, login ok, *}" = "$health" ]; then  # a login can pass its status and still not answer
        said=$(sed -n 's/.* did not answer: //p' "$log" | tail -n 1)
        stop "log in to Claude there by hand, ssh -t $host $(command -v claude) auth login" \
            "(its worker said: ${said:-nothing}), $again"
    fi
    load=$(python3 "$kernel/ceo.py" keep | sed -n 's/.*; load it with: //p')
    [ -n "$load" ] || stop "ceo.py keep printed no command to load its agent."
    sh -c "$load"

    if [ -f drafts/orders/001-hello.md ]; then  # filed last: in orders/, the tick starts it
        mkdir -p orders
        git mv drafts/orders/001-hello.md orders/001-hello.md
        git commit -qm "File 001-hello"
    fi
    git push -q origin main
    echo "bootstrap-host: $name: its own tick runs; order 001 of $repo is filed: collect its proof" \
        "within two days of its merge, before the host's sweep removes it."
}
bootstrap "$@" </dev/null
THERE
