#!/bin/sh
# The kernel's full tests and its budget: at most 5,450 lines of its own code (the owner's cap, CEO.md;
# raised from 1500 on 8 October 2026, from 4,000 and then 4,200 on 13 October, and from 5,000 on 19 October),
# then every top-level test file, each alone in its own python3, CEO_GATE_JOBS of them at once (8 by default; 1 runs
# them one after another). The door runs this from the root of the joined commit on every order about the kernel.
set -e
cd "$(dirname "$0")"
lines=$(ls *.py | grep -v '^test_' | xargs cat | wc -l)
# Raised to 5,600 on 10 November 2026 and returned to 5,450 on 11 November.
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
# The tests named, as `python3 -m unittest` runs them, after the file their verdict goes in once unittest has its
# result: 0 if all passed, 4 if none failed but some skipped, 3 if all that failed is in test_door.RealGitHub, else 1.
run='import sys, unittest
verdict = sys.argv.pop(1)
result = unittest.main(module=None, argv=sys.argv, exit=False).result
failed = [test for test, _ in result.errors + result.failures] + result.unexpectedSuccesses
if failed and all(test.id().startswith("test_door.RealGitHub.") for test in failed):
    code = 3
else:
    code = 1 if failed else 4 if result.skipped else 0
with open(verdict, "w") as file:
    file.write(f"{code}\n")
sys.exit(code)'
jobs=${CEO_GATE_JOBS:-8}
case $jobs in *[!0-9]*) jobs_ok=0 ;; *) jobs_ok=1 ;; esac
if [ "$jobs_ok" = 0 ] || ! [ "$jobs" -ge 1 ] 2>/dev/null; then
    echo "gate: CEO_GATE_JOBS is $jobs, not a whole number of at least 1"
    exit 1
fi
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# Each file alone, its output and its verdict in files of its own. Its sh runs python3 and then exits 0, never
# exec'ing it, so that xargs, which starts no further file once one dies by a signal, starts every file.
names=
for file in test_*.py; do
    if [ -f "$file" ]; then names="$names ${file%.py}"; fi  # no test file leaves the pattern as written
done
printf '%s\n' $names | xargs -r -n 1 -P "$jobs" sh -c \
    'python3 -B -c "$2" "$1/$3.verdict" "$3" > "$1/$3.out" 2>&1 || :; exit 0' sh "$out" "$run" || :
# Every passing file's output whole, then every failing one's, so that a traceback ends what the door keeps.
passed= failed= bad=0 github=0 files=0
for name in $names; do
    files=$((files + 1))
    verdict=$(cat "$out/$name.verdict" 2>/dev/null || :)  # none: killed, ended before a result, or not imported
    case $verdict in
        0 | 4) passed="$passed $name" ;;
        3) failed="$failed $name" github=1 ;;
        *) failed="$failed $name" bad=1 ;;
    esac
done
for name in $passed $failed; do
    cat "$out/$name.out"
done
echo "gate: $files test files ran, at most $jobs at once${failed:+; these failed:$failed}"
[ "$bad" = 0 ] || exit 1
[ "$github" = 1 ] || green
# That class is the one test on GitHub itself, and GitHub's own faults fail it too (8 October 2026: a TLS
# timeout, then `gh api user` exiting 1). So it runs once more, alone, and the gate passes only if it passes
# there: a skip, gh logged out meanwhile, proves nothing, nor does an exit 0 before unittest wrote its verdict of 0.
echo "Only test_door.RealGitHub failed: it runs once more in 30 seconds, and must pass, not skip."
sleep 30
python3 -B -c "$run" "$out/retry.verdict" test_door.RealGitHub || exit 1
[ "$(cat "$out/retry.verdict" 2>/dev/null || :)" = 0 ] || exit 1
echo "RealGitHub passed on the second run"
green
