from ceo_review import *  # A worker's run: started and watched; its spins, mocks and the owner's pick; settled after a cut.
# The owner's words, told to a worker after the order text when its run starts from kept work.
RESUMED = "Earlier work on this order was cut off; your branch starts from it. Read git log and git status first."

def run(args, told):
    order, raw, title, done_when, repo, slug, wt, run_dir = prepare(args.order, args.source)
    if not FILED.fullmatch(order.relative_to(repo).as_posix()):  # where a file lives is its only authority
        raise Refused(f"{order.relative_to(repo)} is not filed: only an order in orders/ runs, never a draft")
    if not order_active(repo, slug, args.source):
        raise Refused("this order family is finished or has a later round")
    if why := round_reason(repo, raw.decode("utf-8")):
        raise Refused(why)
    register(repo)
    worker = args.worker if args.worker is not None else default_worker(repo)
    worker = crew(repo, raw.decode("utf-8"), worker)[0]
    cmd, env = clean(repo, worker)  # a ceo.toml that cannot be read refuses before anything exists
    admit(repo, worker)  # below a floor the run is not created, and the tick tries again next pass
    started = now()
    _, _, variants, preview, _, mocks = parse_order(raw.decode("utf-8"))
    spins = [] if mocks else variants
    draw(repo, "run", slug, len(spins) or 1)  # its share, held by this process until it exits
    mock_entries = [{"spin": n, "line": line, "branch": f"order/{slug}", "preview": str((wt / Path(preview).with_stem(f"{Path(preview).stem}-{n}")).relative_to(repo))}
                    for n, line in enumerate(variants, 1)] if mocks else []
    # Each spin builds on a branch and in a worktree of its own, until the pick makes one the run's.
    names = [f"{slug}-spin-{n}" for n in range(1, len(spins) + 1)] or [slug]
    locks = repo / ".ceo" / "locks"
    # Held from before anything exists until the last process of the run is gone, even when ceo.py
    # itself is killed: while it is held, recovery leaves the run alone.
    lock = claim(locks / f"{slug}.run")
    if lock is None:
        raise Refused("a process of this order's run is still alive")
    sign(lock)
    # A run whose worker met an api error runs again in its worktree; any other has run before.
    again = (last := read_json(run_dir / "result.json")).get("state") == "retry"
    if why := ran_before(repo, slug, len(spins)):
        raise Refused(why)
    # From the newest branch that keeps the work of a run of this order cut off, else from origin's
    # main as last seen, so that its pull request holds no unpushed commit of the owner's.
    found = cuts(repo, slug)
    resumed = f"order/{slug}-cut-{found[-1]}" if found else None
    base = (rev(repo, f"refs/heads/{resumed}") if resumed else rev(repo, args.source) if args.source else rev(repo, "refs/remotes/origin/main") or rev(repo, "HEAD"))
    first = not (again or resumed or spins or mocks or re.search(r"^REPLACES:", raw.decode("utf-8").split("\n## ", 1)[0], re.M))
    previous = read_json(locks / f"{slug}.json")
    # What recovery needs if this process dies: the test as it ran, where to look for the living,
    # and where the run began, to tell whether its worker left any work.
    meta = {"order": str(order.relative_to(repo)), "title": title, "started": started, "boot": boot(), "done_when": done_when, "groups": [], "base": base,
            "resumed_from": resumed, "spins": len(spins), "source": args.source, "mocks": mock_entries}
    if previous.get("dry_run") and (again or first and previous.get("cleared") and previous.get("base") == base
                                    and previous["dry_run"].get("base") == base and previous.get("done_when") == done_when):
        meta["dry_run"] = previous["dry_run"]
    write_json(locks / f"{slug}.json", meta)
    def started_group(group):
        meta["groups"].append(group)
        write_json(locks / f"{slug}.json", meta)
    if not again:
        # --no-track: no upstream, whose write to .git/config two runs starting at once race for.
        for name in names:
            made = worktree(repo, wt.with_name(name), base, "--no-track", "-b", f"order/{name}", lock=lock)
            if made.returncode:
                raise Refused(f"git could not create the worktree: {made.stderr.strip()}")
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "worker.log"
    # Paths are repo-relative so a recorded run compares across machines.
    result = {"order": str(order.relative_to(repo)), "title": title, "state": "blocked", "reason": "",
              "worker": worker, "worker_exit": None, "survivors": [], "done_when_exit": None, "done_when_runs": [],
              "head": None, "worktree": None if spins else str(wt.relative_to(repo)), "started": started, "ended": None, "log": str(log_path.relative_to(repo)),
              "retries": last.get("retries", []), "resumed_from": resumed, "source": args.source}
    if "dry_run" in meta:
        result["dry_run"] = meta["dry_run"]
    if mocks:
        result["mocks"] = mock_entries
    if spins:  # the run has no head and no worktree of its own until the pick
        result["spins"] = [{"spin": n, "line": line, "branch": f"order/{name}", "worktree": str(wt.with_name(name).relative_to(repo)),
                            "preview": str((wt.with_name(name) / preview).relative_to(repo)), "worker_exit": None, "done_when_exit": None, "head": None,
                            "finished": False} for n, (name, line) in enumerate(zip(names, spins), 1)]
    bounded, why = {"keep": (lock,), "started": started_group}, ""
    signal.signal(signal.SIGTERM, on_sigterm)
    try:
        with open(log_path, "a+b", buffering=0) as log:
            if first:
                if "dry_run" not in meta:
                    meta["dry_run"] = dry_run(repo, slug, base, done_when, log, args.timeout, result["survivors"], lock, **bounded)
                    write_json(locks / f"{slug}.json", meta)
                else:
                    mark(log, f"done-when: reusing its recorded dry run on {base}")
                result["dry_run"] = meta["dry_run"]
                before = result["dry_run"]["exit_code"]
            else:
                mark(log, "done-when, before the worker: it must fail")
                before = run_bounded(["bash", "-c", done_when], wt.with_name(names[0]), log, args.timeout, result["survivors"], **bounded)
            if before == 0 and not again:  # once it failed, work it holds is work done, not nothing
                result["done_when_exit"] = 0
                if first:
                    result["reason"] = result["attention"] = f"its done-when already passes on {base}: it proves nothing"
                else:
                    result["reason"] = "the done-when block already passes, so the order has nothing to prove"
                return result
            if worker is None:
                result["reason"] = f"the worker could not start: {NO_WORKER}"
            else:
                mark(log, f"worker starts: {worker}")
                # Recovery judges the worktree only once this is recorded: before it, no work was done.
                meta["worker"] = worker
                write_json(locks / f"{slug}.json", meta)
                refused(order, "")  # its worker begins: the reason its order did not start goes
                told += lessons_page(repo, base)  # the page as its branch starts from it, after the owner's, before the order
                brief = raw + f"\n{RESUMED}\n".encode() if resumed else raw
                if mocks:
                    brief += ("\nDraw every mock in this worktree; each preview file:\n" + "\n".join(
                        f"{entry['spin']}. {entry['line']}: {Path(entry['preview']).relative_to(wt.relative_to(repo))}" for entry in mock_entries) + "\n").encode()
                if spins:  # a run of spins tells its own events, waiting and picked
                    return spin(repo, slug, result, told + brief, done_when, worker, (cmd, env), args.timeout, bounded)
                if not mocks:
                    brief += ENDING
                event(repo, "started", slug, f"{title}: a worker is building it: {worker}.")
                since, began, before = log.tell(), time.monotonic(), traces(wt)
                try:
                    result["worker_exit"] = run_bounded(cmd, wt, log, args.timeout, result["survivors"], stdin=told + brief, env=env, **bounded)
                    if result["worker_exit"] is None:
                        result["reason"] = f"the worker ran past the {args.timeout}s wall clock: {LIMIT}"
                    why = api_error(log, since, began, result["worker_exit"], traces(wt) != before)
                except OSError as err:
                    result["reason"] = f"the worker could not start: {err}"
            completion_checks(repo, done_when, [(wt, log, result)], args.timeout, result["survivors"], **bounded)
        if not result["reason"]:
            result["state"] = ("done" if result["done_when_exit"] == 0 and not result["uncommitted"] else "failed")
            if result["uncommitted"]:
                result["reason"] = "the worker left uncommitted changes or its git status could not be read"
            if result["done_when_exit"] != 0 and why:  # failing after an api error is no verdict
                result["reason"] = retry(result, "state", "worker", why)
        return result
    except Stopped:
        # An interruption, never a result: the next tick settles the run as it settles a kill.
        result = None
        raise
    except BaseException as err:
        result["reason"] = f"ceo.py itself stopped: {err!r}"
        raise
    finally:
        if result is not None:
            if not spins:
                head = git("rev-parse", "HEAD", cwd=wt)
                result["head"] = head.stdout.strip() if head.returncode == 0 else None
            if mocks and result["done_when_runs"]:
                finish_mocks(repo, slug, result)
            if result["state"] == "done":
                ensure_pr(repo, slug, result)
            result["ended"] = now()
            write_json(run_dir / "result.json", result)
            # An api error is no result, as a review's is no verdict; a run of spins tells its own.
            if result["state"] != "retry" and not spins and result["state"] != "waiting":
                event(repo, result["state"], slug, f"{title}: " + {"done": "built, and its test passes.",
                    "failed": result["reason"] or "its test does not pass."}.get(result["state"], f"blocked: {result['reason'].rstrip('.')}."))

def spin(repo, slug, result, raw, done_when, worker, starts, timeout, bounded):
    """Start every spin's worker at once, as clean() gives its command line and environment (starts),
    each told its spin after the order, then, once all have ended or the clock has stopped them, test
    every spin in its worktree: a spin is finished when its done-when block passes there and its preview
    exists. With two finished or more the run waits for the pick."""
    (cmd, env), spins = starts, result["spins"]
    trees = [repo / entry["worktree"] for entry in spins]
    with contextlib.ExitStack() as files:
        logs = [files.enter_context(open((repo / result["log"]).with_name(f"spin-{entry['spin']}.log"), "a+b", buffering=0)) for entry in spins]
        for log, entry in zip(logs, spins):
            mark(log, f"worker starts: {worker}; Your spin: {entry['line']}")
        try:
            exits = run_all([(cmd, tree, log, raw + f"\nYour spin: {entry['line']}\n".encode()) for tree, log, entry in zip(trees, logs, spins)],
                            timeout, result["survivors"], env=env, **bounded)
        except OSError as err:
            result["reason"] = f"the worker could not start: {err}"
            return result
        completion_checks(repo, done_when, list(zip(trees, logs, spins)), timeout, result["survivors"], **bounded)
    result["done_when_runs"] = [{"spin": entry["spin"], **run} for entry in spins for run in entry["done_when_runs"]]
    for entry, tree, worker_exit in zip(spins, trees, exits):
        entry.update(worker_exit=worker_exit, head=git("rev-parse", "HEAD", cwd=tree).stdout.strip() or None,
                     finished=entry["done_when_exit"] == 0 and not entry["uncommitted"] and (repo / entry["preview"]).is_file())
    finished = sum(entry["finished"] for entry in spins)
    if finished < 2:
        result.update(state="failed", reason=f"{finished} of {len(spins)} spins finished: a pick needs two")
    else:
        ask(repo, slug, result)
        result["state"] = "waiting"
    return result

def finish_mocks(repo, slug, result):
    """A mock run ends at the owner's pick, only when every preview and the test pass."""
    for entry in result["mocks"]:
        entry.update(head=result["head"], finished=result["done_when_exit"] == 0 and not result.get("uncommitted") and (repo / entry["preview"]).is_file())
    ready = all(entry["finished"] for entry in result["mocks"])
    result.update(state="waiting" if ready else "failed", branch=f"order/{slug}", reason="" if ready else "the mock test failed or a preview is missing")
    if ready:
        ask(repo, slug, result)

def pick(repo, slug, number):
    """Record a mock pick, or promote a spin to review; finish interrupted picks idempotently."""
    ceo, wt = repo / ".ceo", repo / ".ceo" / "wt" / slug
    path, waiting = ceo / "runs" / slug / "result.json", ceo / "waiting" / f"{slug}.json"
    if slugify(slug) != slug or not path.exists():
        raise Refused(f"no run of {slug} is waiting for a pick")
    lock = claim(ceo / "locks" / f"{slug}.run")
    if lock is None:
        raise Refused("a process of this order's run is still alive")
    try:
        result = read_json(path)
        # The waiting file goes last, so while it is there a pick recorded done is not finished.
        again = result.get("state") == "done" and waiting.exists() and number == result.get("picked")
        if result.get("state") != "waiting" and not again:
            raise Refused(f"the run of {slug} is not waiting for a pick")
        finished = {entry["spin"]: entry for entry in result.get("mocks") or result["spins"] if entry["finished"]}
        chosen = finished.get(number) if type(number) is int else None  # a bool is no spin's number
        if chosen is None:
            raise Refused(f"spin {number} is not a finished spin of {slug}, whose finished spins are " + ", ".join(map(str, finished)))
        if result.get("mocks"):
            result.update(state="done", picked=number, branch=chosen["branch"])
            write_json(path, result)
            event(repo, "picked", slug, f"{result['title']}: mock {number} picked; branch kept.")
            waiting.unlink(missing_ok=True)
            return f"picked mock {number} of {slug}: {chosen['branch']} kept; building needs a new order"
        if not again:
            if rev(repo, f"refs/heads/{chosen['branch']}"):
                done = git("branch", "-m", chosen["branch"], f"order/{slug}", cwd=repo, pass_fds=(lock,))
                if done.returncode:
                    raise Refused(f"git could not rename its branch: {' '.join(done.stderr.split())}")
            if not wt.exists():
                done = git("worktree", "move", chosen["worktree"], str(wt), cwd=repo, pass_fds=(lock,))
                if done.returncode:
                    raise Refused(f"git could not move its worktree: {' '.join(done.stderr.split())}")
            result.update(state="done", picked=number, head=chosen["head"], worktree=str(wt.relative_to(repo)))
            ensure_pr(repo, slug, result)
            write_json(path, result)
        for entry in result["spins"]:
            if entry is not chosen:
                git("worktree", "remove", "--force", "--force", entry["worktree"], cwd=repo)
                shutil.rmtree(repo / entry["worktree"], ignore_errors=True)
        event(repo, "picked", slug, f"{result['title']}: spin {number} is picked and goes to review.")
        waiting.unlink(missing_ok=True)
        return f"picked spin {number} of {slug}: order/{slug} goes to review"
    finally:
        os.close(lock)

def recover(repo, timeout):
    """Settle every run that was cut off: no result.json and no process of it alive. Yield
    (slug, what was done). A run started without a lock, by an older kernel, is never touched."""
    ceo = repo / ".ceo"
    git("worktree", "prune", cwd=repo)
    for lock_path in sorted((ceo / "locks").glob("*.run")):
        slug = lock_path.stem
        if (ceo / "runs" / slug / "result.json").exists():
            continue
        lock = claim(lock_path)
        if lock is None:
            continue  # a process of the run holds it
        try:
            meta = read_json(lock_path.with_suffix(".json"))
            outcome = None if groups_alive(meta) else settle(repo, slug, meta, lock, timeout)
            if outcome:
                yield slug, outcome
        except OSError as err:
            yield slug, f"could not be settled: {err}"
        finally:
            os.close(lock)

def settle(repo, slug, meta, lock, timeout):
    """Keep a cut-off run's finished work as done; else keep all its worker left on branches of their
    own, then a record of it, and clear it to run again. A run of spins is never judged: it is cleared
    with its spins. Passing but uncommitted work stays failed in its worktree."""
    ceo = repo / ".ceo"
    wt, run_dir, branch = ceo / "wt" / slug, ceo / "runs" / slug, f"order/{slug}"
    spun = [f"{slug}-spin-{n}" for n in range(1, meta.get("spins", 0) + 1)]
    has_branch = not git("rev-parse", "-q", "--verify", f"refs/heads/{branch}", cwd=repo).returncode
    if not meta or not (wt.exists() or run_dir.exists() or has_branch or any(
            (ceo / "wt" / name).exists() or rev(repo, f"refs/heads/order/{name}") for name in spun)):
        return None  # nothing was made yet, or it is cleared already; without meta, nothing is ours
    sign(lock)  # the run's agent is now this tick, which settles it
    if has_branch and not wt.exists():  # a clear cut short: bring the worktree back to judge it
        made = worktree(repo, wt, branch, lock=lock)
        if made.returncode:
            return f"left alone: its branch {branch} cannot be checked out: {made.stderr.strip()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    passed, head, kept = None, None, []
    checked = {"done_when_runs": []}
    with open(run_dir / "worker.log", "a+b", buffering=0) as log:
        # Before its worker started a run did no work. Without its .git file the directory is no
        # worktree, and git in the block would read the repository around it.
        if not meta.get("worker"):
            mark(log, "recovery: the run was cut off before its worker started; nothing to judge")
        elif (wt / ".git").exists() and meta.get("done_when"):
            mark(log, "recovery: the run was cut off; done-when in its worktree")
            completion_checks(repo, meta["done_when"], [(wt, log, checked)], timeout, [], keep=(lock,))
            passed = checked["done_when_exit"]
            if passed != 0 and checked["uncommitted"]:
                trouble = wip(wt, lock)
                if trouble:  # nothing is cleared while a change of the worker's is not committed
                    mark(log, f"recovery: {trouble}")
                    return f"left alone: {trouble}"
            head = git("rev-parse", "HEAD", cwd=wt).stdout.strip() or None
    if passed == 0 or meta.get("mocks") and meta.get("worker"):
        result = {"order": meta.get("order"), "title": meta.get("title"), "state": "done", "reason": "",
            "worker": meta.get("worker"), "worker_exit": None, "survivors": [], "done_when_exit": passed, **checked,
            "head": head, "worktree": str(wt.relative_to(repo)), "started": meta.get("started"),
            "ended": now(), "log": str((run_dir / "worker.log").relative_to(repo)), "recovered": True,
            "resumed_from": meta.get("resumed_from"), "source": meta.get("source")}
        if "dry_run" in meta:
            result["dry_run"] = meta["dry_run"]
        if checked.get("uncommitted"):
            result.update(state="failed", reason="the worker left uncommitted changes or its git status could not be read")
        if meta.get("mocks"):
            result["mocks"] = meta["mocks"]
            finish_mocks(repo, slug, result)
        write_json(run_dir / "result.json", result)
        return f"{result['state']}: its worktree is kept"
    # Each line of work its worker left gets a branch of its own before anything is removed: its
    # worktree's HEAD, which holds every change by now, and its branch, which a detached HEAD leaves
    # behind. A line on main or where the run began is none of the worker's.
    tip, count = rev(repo, f"refs/heads/{branch}"), ceo / "locks" / f"{slug}.cuts"
    bases = [ref for ref in (meta.get("base"), "refs/heads/main", "refs/remotes/origin/main") if ref]
    lines = [line for line in dict.fromkeys((head, tip)) if line and not on(repo, line, *bases)]
    for line in lines:
        if any(on(repo, line, other) for other in lines if other != line):
            continue  # inside the other line, which keeps it
        made = cuts(repo, slug, "--points-at", line)  # by a recovery of this run cut off before its end
        cut = made[-1] if made else max([read_json(count).get("last", 0), *cuts(repo, slug)]) + 1
        kept, head = [*kept, f"order/{slug}-cut-{cut}"], line
        if not made:
            write_json(count, {"last": cut})  # the last n given, so that a deleted branch frees none
            if git("branch", kept[-1], line, cwd=repo).returncode:
                return f"left alone: {kept[-1]} could not be made, so nothing is removed"
    # The record is complete before anything is cleared, and the slug is free only at the end.
    record = ceo / "interrupted" / f"{slug}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}"
    record.parent.mkdir(parents=True, exist_ok=True)
    os.rename(run_dir, record)
    write_json(record / "record.json", {"order": meta.get("order"), "started": meta.get("started"), "recovered": now(),
        "done_when_exit": passed, "head": head, "branch": branch, "done_when_runs": checked["done_when_runs"],
        "kept": kept[-1] if kept else None, "log": "worker.log"})
    for path in (wt, *(ceo / "wt" / name for name in spun)):
        git("worktree", "remove", "--force", "--force", str(path), cwd=repo)
        if path.exists():
            shutil.rmtree(path)  # a checkout cut off before git made it a worktree
    git("worktree", "prune", cwd=repo)
    for name in spun:
        git("branch", "-D", f"order/{name}", cwd=repo)
    (ceo / "waiting" / f"{slug}.json").unlink(missing_ok=True)  # its spins are no choice any more
    if tip:  # its branch goes only once its tip is on a branch that keeps work, on main or its base
        if not on(repo, tip, *bases, *(f"refs/heads/order/{slug}-cut-{n}" for n in cuts(repo, slug))):
            return (f"its branch {branch} stays, as no branch that keeps work holds its tip: " f"its record is {record.relative_to(repo)}")
        git("update-ref", "-d", f"refs/heads/{branch}", tip, cwd=repo)
    shutil.rmtree(ceo / f"{slug}.dry-run", ignore_errors=True)
    if not (wt.exists() or run_dir.exists() or rev(repo, f"refs/heads/{branch}")):
        meta["cleared"] = now()
        write_json(ceo / "locks" / f"{slug}.json", meta)
    told = (f"its work is kept on {kept[-1]}, which its next run starts from" + "".join(f", and on {name}" for name in kept[:-1])
            if kept else "cleared, so it can run again")
    return f"{told}: its record is {record.relative_to(repo)}"

def wip(wt, lock):
    """Commit every change in the worktree, untracked files included, as one commit; return why that
    failed, or "" once nothing is left uncommitted. Hooks are skipped: they judge finished work."""
    # In a sparse worktree (excluded()) git adds a file written into a folder kept out only with --sparse.
    sparse = git("config", "--get", "core.sparseCheckout", cwd=wt).stdout.strip() == "true"
    added = git("add", "-A", *(["--sparse"] if sparse else []), cwd=wt, pass_fds=(lock,))
    made = git("commit", "-q", "--no-verify", "-m", f"wip: cut off {now()}", cwd=wt, pass_fds=(lock,))
    left = git(*CHANGES, cwd=wt)
    if left.returncode or left.stdout.strip():
        why = added.stderr or made.stderr or left.stderr
        return f"its changes could not be committed: {' '.join(why.split())}"
    return ""
