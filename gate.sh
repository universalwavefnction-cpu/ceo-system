#!/bin/sh
# The kernel's full tests and its budget: at most 5,450 lines of its own code (the owner's cap, CEO.md;
# raised from 1500 on 8 October 2026, from 4,000 and then 4,200 on 13 October, and from 5,000 on 19 October),
# then every test file. The door runs this from the root of the joined commit on every order about the kernel.
set -e
cd "$(dirname "$0")"
lines=$(ls *.py | grep -v '^test_' | xargs cat | wc -l)
if [ "$lines" -gt 5450 ]; then
    echo "gate: the kernel's code is $((lines)) lines, over its cap of 5,450 (CEO.md): to add, remove"
    exit 1
fi
# Every .py file at the top, in ears/ or tools/, at most 8,000 tokens (32,000 bytes, 4 a token), what an agent
# reads in one call. Only top-level code counts toward the 5,450 lines. A test file oversize.txt lists stays
# at most at the bytes written there: it shrinks, never grows.
over=0
for file in *.py ears/*.py tools/*.py; do
    [ -f "$file" ] || continue  # a folder without a Python file leaves its pattern as written
    size=$(($(wc -c < "$file")))
    cap=$(awk -v file="$file" '$1 == file && file ~ /^test_/ { print $2 }' oversize.txt 2>/dev/null || true)
    if [ "$size" -gt "${cap:-32000}" ]; then
        echo "gate: $file is $size bytes (~$((size / 4)) tokens), over its cap of ${cap:-32000} bytes: split it by" \
            "concern into top-level files of at most 8,000 tokens each"
        over=1
    fi
done
[ "$over" = 0 ]
# The door alone names, in CEO_GATE_RECORD, its record of the clean trees it saw pass in full: such a tree, under the
# same gate.sh and python3, passes at once. A dirty tree runs in full, never recorded; the tests never see it.
record=${CEO_GATE_RECORD:-} key=
unset CEO_GATE_RECORD
if [ -n "$record" ] && changed=$(git status --porcelain 2>/dev/null) && [ -z "$changed" ]; then
    key="$(git rev-parse 'HEAD^{tree}') $(git hash-object gate.sh) $(python3 --version)"
    if grep -qxF "$key" "$record" 2>/dev/null; then
        echo "gate: this tree passed in full at the door already ($key): not run again"
        exit 0
    fi
fi
green() {  # a passing exit, which records the key of a clean tree the door named its record for
    [ -z "$key" ] || echo "$key" >> "$record" || echo "gate: $record could not be written: nothing is recorded"
    exit 0
}
# The tests named, as `python3 -m unittest` runs them; exit 0 if all passed, 4 if none failed but some
# skipped, 3 if all that failed is in test_door.RealGitHub, else 1.
tests() {
    python3 -B -c 'import sys, unittest
result = unittest.main(module=None, argv=sys.argv, exit=False).result
failed = [test for test, _ in result.errors + result.failures] + result.unexpectedSuccesses
if failed and all(test.id().startswith("test_door.RealGitHub.") for test in failed):
    sys.exit(3)
sys.exit(1 if failed else 4 if result.skipped else 0)' "$@"
}
code=0
tests discover -p 'test_*.py' || code=$?
case $code in 0 | 4) green ;; 3) ;; *) exit "$code" ;; esac
# That class is the one test on GitHub itself, and GitHub's own faults fail it too (8 October 2026: a TLS
# timeout, then `gh api user` exiting 1). So it runs once more, alone, and the gate passes only if it passes
# there: a skip, gh logged out meanwhile, proves nothing.
echo "Only test_door.RealGitHub failed: it runs once more in 30 seconds, and must pass, not skip."
sleep 30
tests test_door.RealGitHub || exit 1
echo "RealGitHub passed on the second run"
green
