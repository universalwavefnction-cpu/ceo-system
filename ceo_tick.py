from ceo_release import *  # The tick: one pass over orders, answers, stalls and the night; the loop; the daily sweep.
import ceo_decisions  # loaded with the tick's own image: its dispatcher stays if its source changes later
# The macOS hold: no idle sleep (-i) or sleep on mains power (-s), until this pid ends (-w).
HOLD = ("caffeinate", "-i", "-s", "-w")
WAITING = "## Waiting for your word"
RUNNING = "## Running now"
NO_CEO = "- `ceo.toml` names no `ceo` command: no stalled order is decided until it names one"

def footprint(path, fresh=float("inf")):
    """(newest change, bytes on disk) of path and all in it, links not followed, until one at fresh."""
    newest, size, todo = 0, 0, [path]
    while todo and newest < fresh:
        here = todo.pop()
        with contextlib.suppress(OSError):
            info = here.lstat()
            newest, size = max(newest, info.st_mtime), size + info.st_blocks * 512
            todo += here.iterdir() if stat.S_ISDIR(info.st_mode) else []
    return newest, size

def sweep(repo, dry=False):
    """Print and remove unused kernel scratch and sweep.txt paths, idle for two days; return the STATUS.md line."""
    ceo, old = repo / ".ceo", time.time() - 2 * DAY
    locks, doomed, branches, swept, kept = ceo / "locks", [], [], [], []
    unshown = {path.parent for path, _ in attention_lines(repo)}
    for record in sorted((ceo / "runs").glob("*/result.json")):
        slug, result = record.parent.name, read_json(record)
        wt, cut = ceo / "wt" / slug, (repo / result.get("order", "-")).with_suffix(".cut")
        merged = result.get("merged")
        retired = not (merged and result.get("cleared") or cut.is_file() or result.get("closed"))
        if retired and (order_active(repo, slug) or not (wt / ".git").is_file()):
            continue
        # Keep a merge pull() still needs and a line STATUS.md has not shown yet.
        if (merged and git("merge-base", "--is-ancestor", merged, "refs/heads/main", cwd=repo).returncode or record.parent in unshown):
            continue
        # A run of spins has a worktree for each spin until the pick, and its choices in .ceo/waiting.
        trees = [wt, *(ceo / "wt" / f"{slug}-spin-{n}" for n in range(1, len(result.get("spins", ())) + 1))]
        mine = [*trees, record.parent, ceo / "review" / slug, ceo / "review" / f"{slug}.replay", ceo / "waiting" / f"{slug}.json"]
        mine.append(ceo / "ready" / f"{slug}.json")  # its readiness record too
        if (any(footprint(path, old)[0] >= old for path in [*mine, cut]) or any(held(locks / f"{slug}.{kind}") for kind in ("run", "review"))
                or groups_alive(read_json(locks / f"{slug}.json")) or held(locks / "door.lock") and not result.get("cleared")):
            continue
        dirty = [str(tree.relative_to(repo)) for tree in trees if (tree / ".git").is_file() and (
            (status := git(*CHANGES, cwd=tree)).returncode or status.stdout.strip())]
        if dirty:  # nothing of the run goes while one of its worktrees holds a change
            kept += dirty
            continue
        if retired:  # Keep its records and branches: a later round may still need them.
            saved = git("for-each-ref", f"--contains={rev(wt, 'HEAD')}", "refs/remotes/origin/", cwd=repo)
            doomed += [wt] if not saved.returncode and saved.stdout.strip() else []
            continue
        doomed += [path for path in mine if os.path.lexists(path)]
        branches += git("for-each-ref", "--format=%(refname:short)", *(f"refs/heads/order/{slug}{tail}"
                        for tail in ("", "-cut-*", "-spin-*")), cwd=repo).stdout.split()
        swept.append(slug)
    doomed += [path for path in sorted((ceo / "interrupted").glob("*")) if footprint(path, old)[0] < old]
    pushing = {path.parent for path, _ in unpushed(repo)}  # a record the tick still pushes from
    doomed += [path for path in sorted((ceo / "night").glob("*")) if path not in pushing and footprint(path, old)[0] < old]
    for root, dirs, _ in os.walk(repo):  # links are not followed
        doomed += [Path(root, name) for name in dirs if name == "__pycache__"]
        dirs[:] = [name for name in dirs if name != "__pycache__" and not (root == str(repo) and name in (".git", ".ceo"))]
    listed = repo / "sweep.txt"  # one path or glob a line, ~ allowed, relative to the repository
    lines = listed.read_text(encoding="utf-8", errors="replace").splitlines() if listed.is_file() else []
    shut = {repo, Path.home().resolve(), *repo.parents}  # nor .git or .ceo, as links resolve
    for number, entry in enumerate(map(str.strip, lines), 1):
        if entry[:1] in ("", "#"):
            continue
        # The repository's own path and home's are places, never patterns: in p[1], build is not p1/build.
        pattern = os.path.join(glob.escape(str(repo)), re.sub(r"^~[^/]*", lambda home: glob.escape(os.path.expanduser(home[0])), entry))
        try:  # a line that fails is one line, and the sweep goes on with the rest
            found = [Path(os.path.abspath(hit)) for hit in sorted(glob.glob(pattern, recursive=True))]
            doomed += [path for path in found if (real := path.resolve()) not in shut and not {
                repo / ".git", repo / ".ceo"} & {real, *real.parents} and footprint(path, old)[0] < old]
        except (OSError, RuntimeError, ValueError) as err:  # RuntimeError: a link loop, before 3.13
            print(f"ceo: not swept: line {number} of sweep.txt, {entry}: {err}", flush=True)
    for slug in [] if dry else swept:  # first the tombstone: the tick never starts a swept order again
        (ceo / "swept").mkdir(exist_ok=True)
        write_json(ceo / "swept" / slug, {"merged": read_json(ceo / "runs" / slug / "result.json").get("merged")})
        for kind in ("run", "json", "review"):
            (locks / f"{slug}.{kind}").unlink(missing_ok=True)
    freed, verb = 0, "would remove" if dry else "removed"
    for path in dict.fromkeys(doomed):
        shown, size = path.relative_to(repo) if repo in path.parents else path, footprint(path)[1]
        try:
            if not dry:
                if ceo / "night" in path.parents:
                    unlock(path)  # its read-only copy, which a night killed at once left
                shutil.rmtree(path, ignore_errors=True)  # all that can go, never through a link
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)  # what refused, again, to say why
                elif os.path.lexists(path):
                    path.unlink()  # a file, or a link as itself
            print(f"ceo: {verb} {shown}, {size / GB:.2f} GB", flush=True)
        except OSError as err:  # one line, and the sweep goes on with the rest
            print(f"ceo: not removed: {shown}: {err}", flush=True)
        freed += size - (0 if dry else footprint(path)[1])
    git("worktree", "prune", *(["--dry-run"] if dry else []), cwd=repo)  # git keeps a branch it holds
    for name in branches:
        if dry or git("branch", "-D", name, cwd=repo).returncode == 0:
            print(f"ceo: {verb} the branch {name}", flush=True)
    line = (("the sweep would free" if dry else f"Sweep: {now()}, freed") + f" {freed / GB:.2f} GB"
            + (f"; kept, with uncommitted changes: {', '.join(kept)}" if kept else ""))
    if not dry:
        stamp(repo, sweep=line)
    return line

def tick_pass(repo, args, children, picked_now):
    """Sweep daily, recover, take cuts and picks, push, review, merge, pull, start eligible orders and write status."""
    ceo, remote = repo / ".ceo", origin(repo)
    cache = {}
    heal(repo)
    if time.time() - footprint(ceo / "last-sweep")[0] >= DAY:
        (ceo / "last-sweep").touch()  # first: a sweep that fails is tried again a day later
        sweep(repo)
    for slug, outcome in recover(repo, args.timeout):
        print(f"ceo: recovered {slug}: {outcome}", flush=True)
        if (found := read_json(ceo / "runs" / slug / "result.json")).get("recovered") and not found.get("mocks"):
            event(repo, "done", slug, f"{found.get('title')}: built, and its test passes.")
    for cut in sorted((repo / "orders").glob("*.cut")):  # an order cut on the owner's word, told once
        slug = slugify(cut.stem)
        if not any((seen.get("event"), seen.get("slug")) == ("cut", slug) for seen in events(repo)[0]):
            found = read_json(ceo / "runs" / slug / "result.json") or read_json(ceo / "locks" / f"{slug}.json")
            event(repo, "cut", slug, f"{found.get('title') or cut.stem}: cut; no more work goes into it.")
        # Reclaim cut checkouts now, except parked conflicts; records and branches wait for the sweep.
        wt, locks = ceo / "wt" / slug, ceo / "locks"
        if (not (wt / ".git").is_file() or read_json(ceo / "runs" / slug / "result.json").get("conflicts")
                or held(locks / "door.lock") or any(held(locks / f"{slug}.{kind}") for kind in ("run", "review"))
                or groups_alive(read_json(locks / f"{slug}.json"))):
            continue
        if (changes := git(*CHANGES, cwd=wt)).returncode or changes.stdout.strip():
            continue
        if (head := rev(wt, "HEAD")) and on(repo, head, "refs/heads/main", f"refs/remotes/origin/order/{slug}"):
            git("worktree", "remove", "--force", str(wt), cwd=repo)
    if unpushed(repo) and not held(ceo / "locks" / "night.lock"):  # a night at work pushes its own
        with recording(repo):  # what a night recorded here and could not push, before any word on it
            for said in push_nights(repo):
                print(f"ceo: {said}.", flush=True)
    for answer in sorted((ceo / "answers").glob("*.json")):  # the owner's word, as ceo.py answer writes it
        try:  # a pick of a spin, or the word on a bug that cannot be reproduced or on a night finding
            asked = read_json(ceo / "waiting" / answer.name)
            said = (bug_answer(repo, answer.stem, read_json(answer)) if asked.get("bug")
                    else night_answer(repo, answer.stem, read_json(answer)) if asked.get("night")
                    else decision_answer(repo, answer.stem, read_json(answer)) if asked.get("decision")
                    else pick(repo, answer.stem, read_json(answer).get("pick")))
            if not any(asked.get(key) for key in ("bug", "night", "decision")):
                picked_now.add(answer.stem)
            print(f"ceo: {said}.", flush=True)
        except Refused as why:
            print(f"ceo: refused: {why}.", flush=True)
            if asked.get("bug") or asked.get("night"):
                continue  # keep the owner's word until recording succeeds
        answer.unlink()  # recorded, or a spin pick taken or refused
    ended = None
    for waiting in sorted((ceo / "waiting").glob("*.json")):
        found = read_json(waiting)
        if found.get("decision"):  # a question about an earlier stop cannot hold up the new state
            if ended is None:
                ended = {}
                for seen in events(repo)[0]:
                    if seen.get("event") in ("done", "failed", "cut", "merged"):
                        with contextlib.suppress(ValueError, TypeError, KeyError):
                            slug, at = seen["slug"], datetime.fromisoformat(seen["time"]).timestamp()
                            ended[slug] = max(ended.get(slug, 0), at)
            with contextlib.suppress(ValueError, TypeError, KeyError):
                stop = read_json(ceo / "decisions" / found["decision"] / "stall.json")
                # The run may finish while the CEO is answering, before the waiting row is written.
                progress = stop.get("last_progress")
                if progress is None:
                    progress = datetime.fromisoformat(found["time"]).timestamp()
                if ended.get(waiting.stem, 0) > progress:
                    waiting.unlink(missing_ok=True)
        draft, there = str(found.get("order") or "-"), "refs/remotes/origin/main"
        # A draft gone from origin and local main needs no choice; keep an unpushed draft while local main lags.
        # While its name is taken in orders/, its agree is refused and its row and word stay.
        if found.get("night") and (not draft.startswith("drafts/") or not listed(repo, there, draft) and (
                not listed(repo, "refs/heads/main", draft) or (shared := git(
                    "merge-base", "refs/heads/main", there, cwd=repo).stdout.strip()) and listed(repo, shared, draft))):
            waiting.unlink(missing_ok=True)
    for waiting in sorted((ceo / "waiting").glob("*.json")):  # a pick cut off after it recorded done
        picked = read_json(ceo / "runs" / waiting.stem / "result.json").get("picked")
        if picked and not read_json(waiting).get("pr_handoff"):
            with contextlib.suppress(Refused):  # one still at work finishes by itself
                print(f"ceo: {pick(repo, waiting.stem, picked)}.", flush=True)
                picked_now.add(waiting.stem)
    for key in [key for key, proc in children.items() if proc.poll() is not None]:
        del children[key]  # collected, so no finished child stays behind as a zombie
    busy = {key for key in children if key[0] != "door"} | {(kind, slug) for kind, slug, _ in at_work(repo)}
    busy |= {("night", "")} if held(ceo / "locks" / "night.lock") else set()  # a night another tick started
    results = sorted((ceo / "runs").glob("*/result.json"), key=lambda path: path.stat().st_mtime)
    healthy(repo, args.worker or default_worker(repo))  # on every pass, idle or not, before anything starts
    for result in results:  # reviews, the oldest result first
        slug, found = result.parent.name, read_json(result)
        if (found.get("state") == "done" and not found.get("mocks") and found.get("order")
                and not read_json(ceo / "waiting" / f"{slug}.json").get("pr_handoff")
                and ("review", slug) not in busy and to_make(result.parent / RECORD["review"])):
            start(repo, "review", repo / found["order"], args, children, busy, slug, source=found.get("source"), cache=cache)
    if (remote and ("door", "") not in children and not held(ceo / "locks" / "door.lock") and door_runs(repo, busy)):
        start(repo, "door", None, args, children, busy, "")
    fetched = remote and git("fetch", "-q", "origin", "+refs/heads/main:refs/remotes/origin/main", cwd=repo, timeout=NET).returncode == 0
    behind = pull(repo) if remote else 0
    bug_moves(repo)  # after the pull, so an order it files is in the folder, and before filed orders start
    chute(repo)  # and each finished run's lessons on the kernel, to the owner's inbox and the bug door
    join_moves(repo, cache)  # and the join round of each conflict the door met
    fix_moves(repo, cache)  # and the fix round of each fresh failed review below round_cap
    source = rev(repo, "refs/remotes/origin/main") if fetched else None
    if source:
        local = git("status", "--porcelain", "--", ":!STATUS.md", ":!.ceo", cwd=repo).stdout.strip()
        local = local or git("symbolic-ref", "-q", "HEAD", cwd=repo).stdout.strip() != "refs/heads/main"
        source_line = ("your folder has local changes; orders run from origin" if local else "Orders: orders run from origin")
    else:
        source_line = "Orders: orders run from your folder; " + ("origin fetch failed" if remote else "no origin")
    snapshot = plan_snapshot(repo, source, cache)
    decision, tried = decide_plans(snapshot), {}  # each eligible order: why it did not start, "" once it did
    for path in [*decision["start"], *decision["alone"], *decision["capped"]]:
        slug = slugify(Path(path).stem)
        why = "" if ("run", slug) in busy else start(repo, "run", repo / path, args, children, busy, slug, source=source,
                                                     planned=path in decision["start"], cache=cache)
        if why is not None:
            tried[path] = why
    night_due(repo, args, children, busy)  # last: the owner's orders go first
    shown = list(attention_lines(repo))
    retries = sum(when[:10] == now()[:10] for name in RECORD.values() for path in (ceo / "runs").glob(f"*/{name}")
                  for when in read_json(path).get("retries", ()))
    login = "login ok" if healthy(repo) else "LOGIN FAILED"
    asks = [line for path in waiting_rows(repo) for line in choices(path)]
    running = [f"- {row['sentence']}" for row in agents(repo)]
    snapshot = plan_snapshot(repo, source, cache)
    plans = list(plan_lines(snapshot, decision := decide_plans(snapshot), tried))
    live = {slugify(Path(path).stem) for path in [*decision["start"], *decision["alone"]]} | {  # a capped round is not eligible
        slugify(Path(one["latest"]).stem) for one in order_families(snapshot["orders"]).values() if not one["finished"]
        and any(snapshot["orders"][one["latest"]][key] for key in ("started", "phase"))}  # eligible, it ran, or a child is at work
    unfiled = [f"- `{name}` lists `{one['path']}`, which is not in orders/: file it there, or mend the line"
               for name, plan in snapshot["plans"].items() for one in plan["orders"] if one["path"] not in snapshot["orders"]]
    unfiled += [f"- `{path}` is filed and cannot start: {snapshot['orders'][path]['invalid'].rstrip('.')}: mend it"
                for path in decision["invalid"]]
    unfiled += shared_numbers(snapshot["orders"])
    readiness(repo, {slugify(Path(path).stem): why for path, why in tried.items()}, live)
    stamp(repo, [line for _, line in shown] + unfiled, {"## Plans": plans, RUNNING: running, WAITING: asks}, anew=ANEW,
          tick=f"Last tick: {now()}",
          health=f"Health: {now()}, {login}, {retries} retries today",
          free=f"Free: disk {shutil.disk_usage(repo).free / GB:.1f} GB, RAM {free_ram() / GB:.1f} GB",
          behind=f"main is {behind} behind origin" if behind else "", night=night_line(repo), orders=source_line)
    write_return(repo, snapshot, running)  # from what this pass wrote: the owner's screen on his return
    for path, line in shown:  # after the line is written: a pass cut off between writes it once more
        write_json(path, {**read_json(path), "shown": line})

def hold_awake():
    """On macOS, start caffeinate for this process's lifetime; report its absence and keep working."""
    if sys.platform != "darwin":
        return
    try:
        # Never waited for, as the tick's lock is never closed; should it end first, subprocess collects it.
        subprocess.Popen([*HOLD, str(os.getpid())], stdin=subprocess.DEVNULL)
    except OSError as err:  # not found, or not runnable
        print(f"ceo: caffeinate cannot start ({err.strerror}), so this tick does not hold the machine awake", file=sys.stderr, flush=True)

def awake(repo):
    """Whether repo's macOS tick holds the machine awake with a live hold_awake() child."""
    lock = repo / ".ceo" / "tick.lock"
    if sys.platform != "darwin" or not held(lock):
        return False
    tick = lock.read_text().strip()
    out = subprocess.run(["ps", "-A", "-o", "ppid=,stat=,command="], capture_output=True, errors="replace").stdout
    # Its parent, then its command; a zombie has already exited.
    return any(row[:1] + row[2:] == [tick, *HOLD, tick] and not row[1].startswith("Z") for row in map(str.split, out.splitlines()))

def tick(args):
    repo = toplevel()
    started = ({name: hashlib.sha256(data).digest() for name, data in kernel_code().items()} if args.every is not None and repo == KERNEL.parent else None)
    rejected = set()
    lock = claim(repo / ".ceo" / "tick.lock")  # held until this process exits
    if lock is None:
        raise Refused("another tick is running on this repository")
    sign(lock)  # its pid, by which awake() finds its hold
    if not awake(repo):
        hold_awake()
    # Saved by every start, so that the tick ceo.py keep writes for the owner to load is this one.
    write_json(repo / ".ceo" / "tick.json", {flag: getattr(args, flag) for flag in TICK_FLAGS})
    register(repo)
    children = {}  # (kind, slug) -> what this tick started that may still be running
    try:
        while True:
            picked_now = set()
            try:
                tick_pass(repo, args, children, picked_now)
            except (OSError, Refused) as err:  # a ceo.toml that cannot be read stops one pass
                print(f"ceo: this pass stopped early: {err}", file=sys.stderr, flush=True)
            finally:
                # A one-shot tick finishes consumed picks' reviews despite bookkeeping failures, and any door they started.
                for (kind, slug), proc in children.items():
                    if args.every is None and picked_now and (kind == "door" or kind in ROLE and slug in picked_now):
                        proc.wait()
            try:
                ring(repo, watch_stalls(repo))
                pending = (any((repo / ".ceo/stalls").glob("*.json")) or any(not path.with_name("applied.json").exists()
                           for path in (repo / ".ceo/decisions").glob("*/stall.json")))
                # An empty ceo command decides nothing: said once, in the log and STATUS.md, until ceo.toml names one.
                status, empty = repo / "STATUS.md", ceo_decisions.unnamed(repo)
                said = status.is_file() and NO_CEO in status.read_text(encoding="utf-8").splitlines()
                if empty and not said:
                    with open(repo / ".ceo" / "tick.log", "ab") as log:
                        mark(log, "ceo.toml names no ceo command: no stalled order is decided until it names one")
                    stamp(repo, [NO_CEO])
                elif said and not empty:
                    stamp(repo, remove=[NO_CEO])
                if pending and not empty:
                    ceo_decisions.dispatch(repo, args.timeout, children)
            except (OSError, Refused) as err:
                print(f"ceo: stalls could not be read: {err}", file=sys.stderr, flush=True)
            if args.every is None:
                return 0
            if started is not None:
                restart_kernel(repo, children, started, rejected)
            time.sleep(args.every)
    except KeyboardInterrupt:
        return 130
