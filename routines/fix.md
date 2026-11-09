# <title>: round <round>, its review's findings fixed

ORDER: <number>
ROUND: <round>
REPLACES: <order>

## Goal
The owner wants the outcome of `<order>`. Its run ended done at head <head>, and a fresh reviewer found these
problems in it. Each is a claim with a shell command that exits non-zero while the claim is true, and each command
failed when the kernel replayed it on that head:
<findings>

Do this, in order:
1. Run `git merge --no-edit <head>`. It is that run's branch head in this repository.
2. Fix every finding above, so that its command exits 0 from the root of a clean checkout, and keep the outcome of
   `<order>` whole: skip, remove or weaken no test, and change nothing only to make a command pass.
3. Commit with explicit paths, and end with `git status --porcelain` printing nothing.

The work itself is `<order>`'s, which that run followed: read it for what the work is. Reviewer: check the work as
`<order>` asks, on this round's head, and that each finding above is fixed.
<worker>
## Done when
```bash
git merge-base --is-ancestor <head> HEAD || { echo "the run's head <head> is not merged into this head"; exit 1; }
<checks>
<block>
```

## Not asked
Anything beyond the findings above and the outcome of `<order>`. Restarting any tick, LaunchAgent or systemd unit. Going live.
