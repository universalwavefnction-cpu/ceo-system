# <title>: joined with main

ORDER: <number>
ROUND: <round>
REPLACES: <order>

## Goal
The owner wants the outcome of `<order>` on today's main. Its run, head <head>, passed its done-when and its review;
the merge door found that it conflicts with main in <files>, because main moved after the run was built.

Do this, in order:
1. Run `git merge --no-edit <head>`. It is that run's branch head in this repository.
2. Resolve every conflict so that both main's outcomes and the outcome of `<order>` stay true: keep both sides'
   behaviour; skip, remove or weaken no test. Name each hunk, and how you resolved it, in the merge commit's
   message.
3. Commit the merge with explicit paths. Change nothing else unless this order's done-when fails; if it fails, fix
   only what it names.

The work itself is `<order>`'s, which that run followed: read it for what the work is. Reviewer: check the work as
`<order>` asks, on this round's head, and that the join keeps main's behaviour in <files>.
<worker>
## Done when
```bash
git merge-base --is-ancestor <head> HEAD || { echo "the run's head <head> is not merged into this head"; exit 1; }
<block>
```

## Not asked
Anything beyond the join and the outcome of `<order>`. Restarting any tick, LaunchAgent or systemd unit. Going live.
