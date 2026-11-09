#!/bin/sh
# The owner's word from any agent that runs a shell tool (Hermes, OpenClaw), mapped as the Telegram ear
# maps it: `hermes.sh idea <project> <words>...` keeps an idea and prints its number; `hermes.sh answer
# <project> <slug> <choice> [comment...]` answers a waiting row.
# <project> is a repository's folder name, or its path, as $CEO_HOME/projects.txt lists it: the first
# listed that exists.
kernel="$(cd "$(dirname "$0")/.." && pwd)/ceo.py"
list="${CEO_HOME:-$HOME/.ceo}/projects.txt"
{ [ "$1" = idea ] && [ $# -ge 3 ]; } || { [ "$1" = answer ] && [ $# -ge 4 ]; } || {
  echo "usage: hermes.sh idea <project> <words>... | answer <project> <slug> <choice> [comment...]" >&2
  exit 2
}
what="$1" name="$2"
shift 2
# Each path once and trimmed, as ceo.py open reads them; awk keeps a last line that has no newline, and
# reads any byte in the C locale.
repo=$(name="$name" LC_ALL=C awk '{ gsub(/^[[:space:]]+|[[:space:]]+$/, "") } $0 != "" && !seen[$0]++ {
  last = $0; sub(/.*\//, "", last); if ($0 == ENVIRON["name"] || last == ENVIRON["name"]) print }' "$list" \
  2>/dev/null | while IFS= read -r one; do [ -d "$one" ] && { printf '%s\n' "$one"; break; }; done)
[ -n "$repo" ] || { echo "hermes.sh: no project named $name in $list" >&2; exit 2; }
cd "$repo" || exit 2
if [ "$what" = idea ]; then
  exec python3 "$kernel" idea -- "$*"
fi
slug="$1" choice="$2"
shift 2
exec python3 "$kernel" answer "$slug" "$choice" "--comment=$*"
