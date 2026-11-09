# CEO kernel
`ceo.py` (stdlib Python): filing, worktree build/order test, fresh PR review, merge to `main`. Models read `$CEO_HOME/OWNER.md`
first (default `~/.ceo/OWNER.md`, created if missing). `./gate.sh`: all `test_*.py`, real git/processes/files, stub models/GitHub;
logged-in `gh`: `ceo-kernel-door-test`; cap 5,450 lines. Order: `# Title`, `## Goal`, `## Done when` (one fenced `bash` block, red
before/green after), `## Not asked`. Filed in `orders/`, it runs; a sibling `.cut` stops it; writers: owner (bug reproduces; night
findings), or the CEO for rounds/revisions under `OWNER.md`. Families share a number or `REPLACES:`; that chain outranks `ROUND:`;
a round filed after a cut starts too. `ceo.py idea "<words>"` appends verbatim to `IDEAS.md`, commits `Idea <n>` on main, pushes
with an origin, and prints `<n>`. `ceo.py session` opens the owner's session (`ceo.toml`'s `session`, default `claude`) in the repo
root with `SESSION.md` as `--append-system-prompt` for ideas, to-dos, pushback and wider thinking. `ceo.py bug "<words>" [--attach
<file>]...` commits `bugs/<n>.md` and files `orders/<N>-bug-<n>-reproduce.md`, quoting words and attachments as data; matching open
bugs receive them instead (first error line, else words). When `bugs/<n>/repro.sh` merges, failing on main for the reported reason,
the tick files a fix order, done when it passes untouched; otherwise a `waiting` row asks for drop or detail (`routines/bug-*.md`).
`ceo.py answer <slug> <choice> [--comment "<text>"]` answers waiting rows. Ears in `ears/`: `hermes.sh`, `telegram-board.py` (see
headers). Plans (`plans/<name>.md`): `# Title`, optional `at once <n>` (default 1), and `- orders/<NNN>-<slug>.md` lines with
optional `after <NNN>[, <NNN>]` (earlier) and ` — <sentence>`. An order or plan not yet filed is a draft in `drafts/orders/` or
`drafts/plans/`, which the kernel never reads; filing moves it into `orders/` or `plans/` in a commit, which alone starts work; a
plan is read as it stands each pass, an edited line counts from the next pass, prose never. Plans start within their limit, after
predecessors/replacement rounds merge; rounds keep slots/predecessors; picked mocks unblock successors. Newest-round cuts block
descendants; `STATUS.md` shows `## Plans` with sentences/states (a line naming no file in `orders/`, or an invalid order, in Needs
attention); rows keep each file's state. For `herdr ceo <project>`, set `<kernel>` to this kernel's folder in your shell:
`herdr() { if [ "$1" = ceo ]; then shift; python3 "<kernel>/ceo.py" open "$@"; else command herdr "$@"; fi; }`.
## The tick (always on; macOS: holds awake, preventing only idle sleep on battery)
    python3 ceo.py tick [--every <s>] [--worker|--reviewer "<cmd>"] [--timeout <s>]    (--checker "<cmd>" is accepted and ignored)
Each pass (once or every `--every` seconds): daily sweep, recovery, answers, reviews, door, pull, bugs, then each eligible order
(filed, valid, its family's latest, not cut, finished or run, `after` merged, under `round_cap` (3, `ceo.toml`), no waiting row):
plans' starts, then the rest, oldest filed first; each starts or keeps why in `.ceo/ready/<slug>.json` (`since`, its first eligible
pass, clocks stalls), two unstarted passes make a Needs attention line, a bell. Orders use fetched origin/main despite local edits,
else the folder; `STATUS.md` names the source. Work uses separate sessions and `.ceo/tick.log`; floors gate each start; a second
tick refuses. `STATUS.md` records each pass: `Last tick: <time>`, `Health:` (login, retries today), `Free:` (disk, RAM), and `##
Running now` (agents). `ceo.py keep` writes an unloaded tick agent with the last start's flags:
`~/Library/LaunchAgents/ceo.<repo>.plist` or `systemd --user`'s `ceo-<repo>.service`; it prints the load command; `--remove`
deletes it. One-shot ticks finish picked reviews and doors. After `stall_minutes` (45) idle, a latest round stalls unless at work,
waiting/finished: `.ceo/stalls/<slug>.json`, a `stalled` event and Needs attention line; `moving` clears it on progress (healed
floors/API retries count). Merged, hand-landed or picked families finish; a latest cut finishes unless an earlier uncut review can
merge. One CEO decides at a time; stdout becomes `DECISION.json` and `decisions.md` on main. Models start clean: claude uses
`--setting-sources project` (CLIs: `isolation.<program>` in `ceo.toml`), without `ANTHROPIC_BASE_URL`, `_AUTH_TOKEN` or `_API_KEY`
unless `keep_env` names them; agents inherit `PATH`, `CEO_HOME` and `keep`'s environment. API errors mean `retry` next pass, thrice
daily, then `blocked` (none for spins): the CLI's last result says `terminal_reason` `api_error`, or any exit in the first minute
without an answer: a worker's commit or change (uncommitted work is `failed`, a stated limit), a reviewer's `REVIEW.json`. Login
probes take one turn, trusted ten minutes: worker each pass, others before starting; failure holds only its program's starts.
Before `run`/`review`, `ceo.toml` floors require `disk_floor_gb` (20 GB, repo volume), `ram_floor_gb` (4 GB), and `quota_floor_pct`
(5% vendor quota, printed by `quota_cmd.<vendor>`; `claude` unless `vendor.<program>` says otherwise). Refusal creates nothing,
prints floor and numbers, exits 2; next pass retries. `ceo.py sweep [--dry-run]` (lists on dry run) removes all unchanged for two
days: the worktrees, branches (`order/<slug>`, `-cut-<n>`, `-spin-<n>`) and `.ceo/` `runs`, `review`, `waiting` and locks of an
order cut, closed on the owner's word, or merged, pulled and shown in `STATUS.md`, leaving `.ceo/swept/<slug>` so it never runs
again; `.ceo/interrupted/*` and `.ceo/night/*`; the simple globs of `sweep.txt` (`~` too; NUL bytes and `**` loops unguarded; no
pattern of the repository's or home's path), never the repository, a folder holding it, home, `.git` or `.ceo`; at once, every
other `__pycache__`. Nothing alive goes; a dirty worktree stays, named in `Sweep: <time>, freed <n> GB` atop `STATUS.md`. During
`ceo.toml`'s `night` (`"02:00-06:00"` local, `""` disables), the tick starts `ceo.py night [--model "<cmd>"]
[--timeout|--max-findings <n>]`: `test_cmd` (default `./gate.sh`) in a fresh clone, then a read-only worker given
`routines/night.md`, the project and what was declined; the three most harmful findings failing in a clean clone, none declined or
still open, become drafts, each a `waiting` row: `agree` files it, `decline [comment]` moves it to `routines/night-declined.md`,
`comment` tells the next night. Ideas go to `IDEAS.md`, all to `routines/night-<date>.md`, linked atop `STATUS.md`; recorded here
first, then pushed, or by the next tick. On another machine, `tools/bootstrap-host.sh <ssh-host>`, run from the owner's, readies
its tick to merge one scratch order or names the login to make; `tools/collect-host.py <ssh-host>` proves it.
## What the owner sees, said once: `ceo.py status [--json]`
`.ceo/events.jsonl`: work, login, stall, recovery, plus `cut` on `.cut`; `status`: rows/unreadable count; `--json`: rows
(state/sentence), live agents without model names. `run`/`tick` register in
`$CEO_HOME/projects.txt`; trailing CR in projects joins the next repo, and shell ears miss both.
## Another project
`ceo.py init [<path>]` (default here; refuses existing `VISION.md`) commits `VISION.md`, `orders/README.md`, `ceo.toml` and `.ceo/`
in `.gitignore`, registers the repo, prints next steps and warns of another agent system in `CLAUDE.md` or `AGENTS.md`.
## One run, and the pick of a spin: `ceo.py run orders/<n>-<name>.md [--worker "<command>"] [--timeout <seconds>]`
Malformed orders create nothing (one sentence, exit 2); valid ones get `.ceo/wt/<slug>`, branch `order/<slug>`, from last-seen
`origin/main` (else `HEAD`); done-when must fail before the worker (`claude -p`, or `--worker`) reads the order on stdin, logging
to `.ceo/runs/<slug>/worker.log`; 1800 s/phase or SIGTERM stops group/descendants (detached ones may survive). Failed checks
(spins/recovery too) rerun once below CPU-count one-minute load or after `recheck_wait` (300 s); `done_when_runs` keeps exits and
last 40 output lines, per spin too, in the result or interrupted record; `result.json` says `done` (0; branch pushed, PR opened as
`pr`), `failed` (1; both checks failed), or `blocked`/`retry` (2); interrupted run/review leave no result (exit 2, or 143 at once)
for recovery: passing work becomes `done` (`recovered: true`), else all changes, untracked too, become `wip: cut off <time>` on
`order/<slug>-cut-<n>` (two if HEAD and branch diverge), before removal; the record goes to `.ceo/interrupted/<slug>-<time>/`, and
the next run tells its worker, recording `resumed_from` as the newest kept branch. Mocks (`## Mocks`, 1–6 lines; `## Preview` per
`orders/README.md`) use one worker and branch for numbered previews; a passing test and all previews lead to an owner pick kept
with the branch, without review, PR or merge, for a new build order. Limit: a cut during recovery's worktree removal can resume
without a file kept on an older cut branch. Spins (`## Spins`, `## Preview`: `orders/README.md`) run workers concurrently in
`.ceo/wt/<slug>-spin-<i>` on `order/<slug>-spin-<i>`, each told `Your spin: <its line>`. A spin is finished when its done-when
block passes and its preview exists, even if the clock stopped its worker. Two or more finished: `waiting` (exit 3), branches,
heads and previews in `.ceo/waiting/<slug>.json` and `STATUS.md`'s `## Waiting for your word`, unreviewed until picked; otherwise
`failed`. `ceo.py pick <slug> <i>` or `{"pick": i}` in `.ceo/answers/<slug>.json` (read once per tick) makes the chosen branch
`order/<slug>`, worktree `.ceo/wt/<slug>`, run `done` (`"picked": i`), and opens its pull request, telling the reviewer the spin.
Other worktrees go, branches stay; unfinished picks are refused. Cut-off spins are cleared whole. `## Worker` (`orders/README.md`)
overrides `--worker`, under its vendor's floors. In `ceo.toml`, `reviewers.<worker name>` overrides `--reviewer`, falling back to
`reviewers.<vendor>`, then the worker. Same-vendor pairs are refused before starting (exit 2) unless `same_vendor_review = true`.
`result.json` and `started` name the `worker`. `ceo.toml`'s `worker` replaces `claude -p`; Codex, Gemini, vendors: HARNESSES.md.
## Review and door (usage-limit blocks retry after login recovery; other API blocks retry next day)
    python3 ceo.py review <order.md> [--reviewer "<cmd>"] [--timeout <s>]    python3 ceo.py door [--timeout <s>]
Fresh reviewer (default worker): order/finished commit in its own clone; `REVIEW.json`: summary, findings (claim, shell command
nonzero while true), counted only on clean-clone failure within Goal/Done when; other faults are summary ideas. `review.json`:
`survives` if none counts, else `fails`; `blocked` if no readable answer or replay times out. `posted`: PR verdict: approve,
request changes or comment. One door/repo merges oldest `survives` first, serially; `fails` and `blocked` never merge. Server PR:
merged is cleared; closed needs attention. A clean clone joins main into the reviewed commit and runs done-when and `./gate.sh`. An
atomic push of that merge to main and the PR branch leases both tips; main moved by `RECORDS` only: 3 re-pushes; else a retest. The
server must allow these atomic updates. Order history is preserved; GitHub recognizes the PR head on main as merged. Anything on
the branch but the reviewed commit or a join a door recorded needs attention. `result.json` records `merged`; worktree and branches
go. Surviving reviews retry failed tests once in a later door, with no worker/review alive; `door_failures` records failures. A
second makes `review.json` the door's own `fails` (`door`: both failures, then `door.log`'s last 40 lines indented), whose fix
round below `round_cap` is `routines/door-fix.md`'s, filed. A CEO merge's failure or a conflict (`conflicts`) gets `attention`
under `## Needs attention`; PR/worktree stay. After a merge tick pulls fetched `origin/main` `--ff-only` into the owner's folder
only on `main`, with no changes except `STATUS.md` and `.ceo/`; otherwise `STATUS.md` says `main is N behind origin`;
Ctrl-C/SIGTERM waits for the pull. Worktrees/copies omit `checkout.exclude`; git keeps them.
