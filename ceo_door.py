from ceo_run import *  # The merge door: the joined work tested, main pushed, the PR merged, each try recorded.
RECORDS = ("orders/", "plans/", "bugs/", "results/", "decisions.md", "IDEAS.md", "LESSONS.md", "STATUS.md")
REPUSHES = 3  # tries after a refused merge push, only while main moved by records
FIX_SINCE = datetime(2026, 10, 25, tzinfo=timezone.utc)  # older failed reviews keep the CEO's path: no first-pass burst
# Check unconditional exact pins without requiring pip or a dependency resolver in the fallback.
PYTHON_PINS = r"""import importlib.metadata as metadata, pathlib, re, sys
bad = False
for path in pathlib.Path(".").glob("requirements*.txt"):
    for name, pin in re.findall(r"(?m)^\s*([\w.-]+)(?:\[[^\]\n]+\])?==([^\s;#*]+)[ \t]*(?:#.*)?$", path.read_text()):
        try:
            got = metadata.version(name)
        except metadata.PackageNotFoundError:
            got = "not installed"
        if got != pin:
            print(f"door: fallback python3 ({sys.executable}): {name} {got}; {path} pins {pin}", flush=True)
            bad = True
sys.exit(1 if bad else 0)
"""

def door_runs(repo, busy=()):
    """Runs with a surviving review or a CEO merge, including unfinished cleanup, oldest first."""
    working = any(kind in RECORD for kind, _ in busy) or any(at_work(repo))
    runs = sorted((path.stat().st_mtime, path.parent.name, read_json(path), read_json(path.with_name("result.json"))) for path in (repo / ".ceo" / "runs").glob("*/review.json"))
    return [(slug, result) for _, slug, verdict, result in runs if verdict.get("commit") == result.get("head") and (ceo_merge_pending(result)
                or verdict.get("verdict") == "survives" and not result.get("attention") or result.get("merged") and result.get("ceo_merge"))
            and result.get("pr") and not result.get("cleared") and (result.get("merged") or not result.get("door_failures") or not working)
            and (result.get("merged") or order_active(repo, slug, for_merge=True))]

def door(args):
    """Settle each eligible run's merged pull request; merge the oldest open one."""
    repo = toplevel()
    lock = claim(repo / ".ceo" / "locks" / "door.lock")  # also held by each git and gh it starts
    if lock is None: raise Refused("the door of this repository is at work already")
    sign(lock)
    if origin(repo) is None: raise Refused("the repository has no origin, where pull requests merge")
    signal.signal(signal.SIGTERM, on_sigterm)
    for slug, result in door_runs(repo):
        pr = landed(repo, result, lock) or server(repo, result["pr"], lock)
        if pr.get("state") == "OPEN": return merge(repo, slug, result, lock, args.timeout)
        if pr.get("state") == "MERGED": clear(repo, slug, result, pr, lock)
        elif pr.get("state"): attention(repo, slug, result, f"its pull request #{result['pr']} was closed unmerged")
    return 0

def landed(repo, result, lock):
    commit, main, here = result.get("merge_commit"), "refs/remotes/origin/main", {"cwd": repo, "pass_fds": (lock,)}
    reached = commit and git("fetch", "-q", "origin", f"+refs/heads/main:{main}", timeout=NET, **here).returncode == 0 and git(
            "merge-base", "--is-ancestor", commit, main, **here).returncode == 0
    return {"state": "MERGED", "mergeCommit": {"oid": commit}} if reached else {}

def server(repo, number, lock):
    """The pull request's state and merge commit as the server has them now, or {}."""
    view = gh(origin(repo)[1], "pr", "view", str(number), "--json", "state,mergeCommit", pass_fds=(lock,))
    with contextlib.suppress(ValueError): return json.loads(view.stdout) if view.returncode == 0 else {}
    return {}

def door_failure(failure):
    ran = "ran past the wall clock" if failure["exit"] is None else f"exited {failure['exit']}"
    return f"{failure['what']} {ran} on the joined commit {failure['joined']}"

def merge(repo, slug, result, lock, timeout):
    """Test the reviewed commit joined with main, then publish its merge only while both remote
    tips still match. GitHub recognizes the pull request's head reaching main as a merge."""
    if not order_active(repo, slug, for_merge=True): return
    (url, _), reviewed, branch = origin(repo), result["head"], f"order/{slug}"
    copy, in_copy = repo / ".ceo" / "door", {"cwd": repo / ".ceo" / "door", "pass_fds": (lock,)}
    done_when = read_json(repo / ".ceo" / "locks" / f"{slug}.json").get("done_when")
    if not done_when: return attention(repo, slug, result, "its run kept no done-when block to test it with")
    path = repo / ".ceo" / "runs" / slug / "review.json"
    review, age = read_json(path), path.stat()
    if review.get("posted") is None:  # Nothing merges without the review on its pull request.
        refused = post(repo, result, review, override=ceo_merge_pending(result), pass_fds=(lock,))
        if review["posted"] is None: return later(result, f"its review is not on pull request #{result['pr']} yet: {refused}")
        write_json(path, review)
        os.utime(path, ns=(age.st_atime_ns, age.st_mtime_ns))  # its age: the oldest review goes first
    try:
        with open(repo / ".ceo" / "runs" / slug / "door.log", "ab", buffering=0) as log:
            # Written first: the door's newest log names the order it works on, for `agents`.
            mark(log, f"door: a clean clone of origin, to join main into {reviewed}")
            trouble = clone(repo, reviewed, copy, lock, url, door=True)
            if trouble: return later(result, f"no clone of origin could be made: {trouble}")
            base = rev(copy, "origin/main")
            if result.get("merge_commit") and git("merge-base", "--is-ancestor", result["merge_commit"], base, **in_copy).returncode == 0:
                return clear(repo, slug, result, {"mergeCommit": {"oid": result["merge_commit"]}}, lock)
            head = rev(copy, f"refs/remotes/origin/{branch}")
            # It replaces only the reviewed commit or a join of it a door before this one recorded.
            if head not in (reviewed, *result.get("joins", ())): return attention(repo, slug, result, "its branch on origin moved after its review")
            joined = git("merge", "-q", "--no-edit", "-m", f"Join main into {branch}", "origin/main", **in_copy)
            if joined.returncode:
                conflicts = git("diff", "--name-only", "--diff-filter=U", **in_copy).stdout.split()
                return attention(repo, slug, result, f"it conflicts with main in {', '.join(conflicts)}" if conflicts else f"git cannot join it with main: {joined.stderr}", conflicts)
            joined = rev(copy, "HEAD")
            if any(copy.glob("requirements*.txt")) and not os.access(copy / ".venv/bin/python3", os.X_OK):
                mark(log, "door: check fallback python3 against pinned requirements")
                code = run_bounded(["bash", "-c", "python3 -c " + shlex.quote(PYTHON_PINS)], copy, log, timeout, [])
                if code != 0: return later(result, "its fallback Python dependencies could not be verified against " "the pinned requirements (see door.log)")
            gate = [("./gate.sh", "./gate.sh")] if (copy / "gate.sh").is_file() else []
            seen = {**os.environ, "CEO_GATE_RECORD": str(repo / ".ceo" / "gate-green")}  # trees gate.sh saw pass
            failures = result.get("door_failures", [])
            retry = f", a retry after {door_failure(failures[0])}" if failures else ""
            for what, script in [("its done-when block", done_when), *gate]:
                mark(log, f"door: {what} on the joined commit {joined}{retry}")
                code = run_bounded(["bash", "-c", script], copy, log, timeout, [], env=seen)
                if code != 0:
                    why = door_failure(failure := {"what": what, "exit": code, "joined": joined, "time": now()})
                    if ceo_merge_pending(result):  # an explicit CEO merge returns its failure to the CEO
                        return attention(repo, slug, result, why)
                    note(repo, slug, door_failures=[*failures, failure])
                    if failures:  # the door's own failed review: its fix round below round_cap, else the CEO's path
                        why = f"{door_failure(failures[0])}; on its retry, {why}"
                        end = path.with_name("door.log").read_text("utf-8", "replace").splitlines()[-40:]
                        write_json(path, {**read_json(path), "verdict": "fails", "ended": now(), "door": f"{why}.\n\n" + "".join(f"    {line}\n" for line in end)})
                        event(repo, "failed", slug, f"{result.get('title') or result['order']}: at the door, {why}.")
                        return 1
                    event(repo, "retry", slug, f"{result.get('title') or result['order']}: a retry of a failed door: {why}.")
                    return later(result, why)
            mark(log, f"door: push and merge the joined commit {joined}")
            if joined != head:
                note(repo, slug, joins=[*result.get("joins", ()), joined])  # before it can land
                pushed = git("push", "-q", f"--force-with-lease=refs/heads/{branch}:{head}", "origin", f"HEAD:refs/heads/{branch}", timeout=NET, **in_copy)
                if pushed.returncode: return later(result, f"the joined commit was not pushed: {pushed.stderr.strip()}")
            verdict = read_json(repo / ".ceo" / "runs" / slug / "review.json")
            if verdict.get("commit") != reviewed or (verdict.get("verdict") != "survives" and not ceo_merge_pending(result)): return later(result, "its review changed while the door worked")
            # The atomic push pins both tips, preserving the tested tree and PR ancestry.
            if trouble := push_merge(repo, slug, result, base, joined, log, **in_copy): return later(result, trouble)
            for _ in range(10):  # Let GitHub recognize the merge before deleting the PR branch.
                if server(repo, result["pr"], lock).get("state") == "MERGED": break
                time.sleep(2)
            return clear(repo, slug, result, {"mergeCommit": {"oid": result["merge_commit"]}}, lock)
    finally: shutil.rmtree(copy, ignore_errors=True)

def later(result, why):
    print(f"ceo: the door takes {result['order']} again later: {why}", flush=True)
    return 2

def is_record(path): return any(path.startswith(record) if record.endswith("/") else path == record for record in RECORDS)

def push_merge(repo, slug, result, base, joined, log, **in_copy):
    """Publish the tested tree, or merge records that arrived on main without changing tested code.
    Return the reason to take the door later, or an empty string once the atomic push lands."""
    branch, onto, tree = f"order/{slug}", base, f"{joined}^{{tree}}"
    family = f"orders/{slug.split('-', 1)[0]}-"
    refused = ""
    for attempt in range(REPUSHES + 1):
        commit = git("commit-tree", tree, "-p", onto, "-p", joined, "-m", f"{result['title']} (#{result['pr']})", **in_copy)
        if commit.returncode: return refused + f"the merge commit could not be made: {commit.stderr.strip()}"
        commit = result["merge_commit"] = commit.stdout.strip()
        note(repo, slug, merge_commit=commit)  # recover a push whose caller dies before recording it
        if attempt: mark(log, f"door: push again onto main {onto}: {', '.join(map(ascii, paths)) or '(no changed paths)'}")
        pushed = git("push", "-q", "--atomic", f"--force-with-lease=refs/heads/main:{onto}", f"--force-with-lease=refs/heads/{branch}:{joined}", "origin",
                     f"{commit}:refs/heads/main", f"{commit}:refs/heads/{branch}", timeout=NET, **in_copy)
        if pushed.returncode == 0: return ""
        refused = f"the tested merge was not pushed: {pushed.stderr.strip()}; "
        if attempt == REPUSHES: return refused + f"{REPUSHES} pushes again were refused"
        fetched = git("fetch", "-q", "origin", "main", branch, timeout=NET, **in_copy)
        if fetched.returncode: return refused + f"could not fetch both tips: {fetched.stderr.strip()}"
        main = git("rev-parse", "--verify", "refs/remotes/origin/main", **in_copy)
        head = git("rev-parse", "--verify", f"refs/remotes/origin/{branch}", **in_copy)
        if main.returncode or head.returncode: return refused + "could not read both fetched tips"
        main = main.stdout.strip()
        if main == onto: return refused + "main did not move"
        if head.stdout.strip() != joined: return refused + "the branch on origin moved from the tested join"
        if git("merge-base", "--is-ancestor", base, main, **in_copy).returncode: return refused + "main no longer holds the tested base"
        # Read path bytes: text mode aliases CR and LF, and not every Git path is UTF-8.
        changed = subprocess.run(["git", "diff", "--name-only", "--no-renames", "-z", base, main], capture_output=True, **in_copy)
        if changed.returncode: return refused + "could not compare main to the tested base"
        paths = [os.fsdecode(path) for path in changed.stdout.split(b"\0")[:-1]]
        if any(not is_record(path) for path in paths): return refused + "main moved outside the record paths"
        if any(path.startswith(family) for path in paths): return refused + "main changed a record of this order's number"
        merged = git("merge-tree", "--write-tree", main, joined, errors="surrogateescape", **in_copy)
        if merged.returncode: return refused + "the records conflict with the tested join or could not be merged"
        tree = merged.stdout.splitlines()[0]
        changed = subprocess.run(["git", "diff-tree", "-r", "--name-only", "--no-renames", "-z", joined, tree], capture_output=True, **in_copy)
        if changed.returncode or not set(map(os.fsdecode, changed.stdout.split(b"\0")[:-1])).issubset(paths):
            return refused + "the merged tree is not the tested tree outside main's moved records"
        onto = main

def attention(repo, slug, result, why, conflicts=()):
    """Record why the run needs attention: no door takes it again; its pull request stays open."""
    current = read_json(repo / ".ceo" / "runs" / slug / "result.json")
    tried = {"ceo_merge": {**current["ceo_merge"], "tried": now(), "outcome": why}} if ceo_merge_pending(current) else {}
    note(repo, slug, attention=why, **tried, **({"conflicts": conflicts} if conflicts else {}))
    print(f"ceo: needs attention: {result['order']}: {why}", flush=True)
    event(repo, "blocked", slug, f"{result.get('title') or result['order']}: the door cannot merge it: {why}.")
    return 1

def clear(repo, slug, result, pr, lock):
    """Record the merge commit, delete the branch on origin, the worktree and the branch here; a door
    cut off here is finished by the next, for each step can run again."""
    merged = (pr.get("mergeCommit") or {}).get("oid")
    if not merged: return later(result, "the server names no merge commit yet")
    note(repo, slug, merged=merged)
    if not result.get("merged"):  # told once, though a door cut off below clears it again
        event(repo, "merged", slug, f"{result.get('title') or result['order']}: merged into main.")
    ref, wt, here = f"refs/heads/order/{slug}", repo / ".ceo" / "wt" / slug, {"cwd": repo, "pass_fds": (lock,)}
    gone = git("push", "-q", "origin", "--delete", ref, timeout=NET, **here)
    if gone.returncode and "remote ref does not exist" not in gone.stderr: return later(result, f"its branch on origin is not deleted yet: {gone.stderr.strip()}")
    left = git(*CHANGES, cwd=wt, pass_fds=(lock,)) if wt.exists() else None  # work saved there since the review stays, for the owner
    if left and (left.returncode or left.stdout.strip() or git("worktree", "remove", str(wt), **here).returncode): return later(result, "its worktree holds later work, or git will not remove it")
    git("worktree", "prune", **here)
    git("update-ref", "-d", ref, result["head"], **here)  # only while it names the reviewed commit
    note(repo, slug, cleared=now())
    print(f"ceo: merged {result['order']} as {merged}", flush=True)
    return 0

def join_moves(repo, cache=None):
    """Each conflict the door met in a run of an active filed family, filed once on main as its join round."""
    source = rev(repo, "refs/remotes/origin/main") or rev(repo, "refs/heads/main")
    for path in sorted((repo / ".ceo" / "runs").glob("*/result.json")):
        slug, found = path.parent.name, read_json(path)
        orders = found.get("conflicts") and order_snapshot(repo, source, cache)
        family = orders and order_families(orders).get(found["order"])
        if family and order_active(repo, slug, source, cache=cache):
            with contextlib.suppress(Refused):  # the next pass tries again; a stall, after stall_minutes
                record_on_main(repo, f"the join of {slug}", joining(repo, found, orders[found["order"]]))

def joining(repo, found, row):
    """A change for record_on_main(): the join round of found's run (routines/join.md), at its ROUND, filed."""
    def change(base):
        order, number = found["order"], Path(found["order"]).name.split("-", 1)[0]
        new = f"orders/{number}-join-{found['head'][:7]}.md"  # one join of each head that met a conflict
        if blob(repo, base, new): return None  # filed before
        title, block, worker = parse_order(row["text"])
        fills = {"title": title, "number": number, "round": str(row["round"]), "order": order, "head": found["head"],
                 "files": ", ".join(found["conflicts"]), "worker": f"\n## Worker\n{worker}\n" if worker else "", "block": block.rstrip("\n")}
        made = re.sub(r"<([a-z]+)>", lambda m: fills.get(m[1], m[0]), blob(repo, base, "routines/join.md") or (KERNEL.parent / "routines" / "join.md").read_text(encoding="utf-8"))
        return {new: made}, f"{order}: its join round {new}", parse_order(made) and new
    return change

def fix_moves(repo, cache=None):
    """Each fresh failed review of a done run of an active filed family, below round_cap: its fix round filed
    once on main. At the cap, with the owner's word waiting, after a stall or for an older review, the CEO's path."""
    source, ceo = rev(repo, "refs/remotes/origin/main") or rev(repo, "refs/heads/main"), repo / ".ceo"
    for path in sorted((ceo / "runs").glob("*/review.json")):
        slug, review, found, fresh = path.parent.name, read_json(path), read_json(path.with_name("result.json")), False
        with contextlib.suppress(TypeError, ValueError):  # no time, or none with its zone, is older
            fresh = datetime.fromisoformat(review.get("ended")) >= FIX_SINCE
        if not (fresh and review.get("verdict") == "fails" and review.get("commit") == found.get("head")
                and found.get("state") == "done" and not any(found.get(key) for key in ("merged", "ceo_merge"))
                and not (ceo / "waiting" / f"{slug}.json").exists() and not (ceo / "stalls" / f"{slug}.json").exists()): continue
        orders = order_snapshot(repo, source, cache)
        family, cap = order_families(orders).get(found["order"]), settings(repo).get("round_cap", 3)
        if family and type(cap) is int and orders[found["order"]]["round"] < cap and order_active(repo, slug, source, cache=cache):
            with contextlib.suppress(Refused):  # the next pass tries again
                record_on_main(repo, f"the fix round of {slug}", fixing(repo, found, review, orders[found["order"]]))

def fixing(repo, found, review, row):
    """A change for record_on_main(): the next round of found's run, its done-when each counting finding of its review
    (routines/fix.md) or none for the door's own failure (routines/door-fix.md), then the order's own block, filed."""
    def change(base):
        order, number, next_ = found["order"], Path(found["order"]).name.split("-", 1)[0], row["round"] + 1
        new = f"orders/{number}-round-{next_}.md"
        if blob(repo, base, new): return None  # filed before, by this or by the CEO
        title, block, worker = parse_order(row["text"])
        counting = [one for one in review.get("findings", ()) if one.get("counts")]
        fills = {"title": re.sub(r": round [0-9]+, its review's findings fixed$", "", title), "number": number,
                 "round": str(next_), "order": order, "head": found["head"], "door": review.get("door", ""),
                 "findings": "\n".join(f"- {' '.join(one['claim'].split())}: `{one['command']}`" for one in counting),
                 "worker": f"\n## Worker\n{worker}\n" if worker else "", "block": block.rstrip("\n"),
                 "checks": "\n".join(f"bash -c {shlex.quote(one['command'])} || {{ echo \"finding {i} of the review still "
                                     "holds\"; exit 1; }" for i, one in enumerate(counting, 1))}  # as replayed: set -e keeps its force
        routine = "routines/door-fix.md" if review.get("door") else "routines/fix.md"
        made = re.sub(r"<([a-z]+)>", lambda m: fills.get(m[1], m[0]), blob(repo, base, routine)
                      or (KERNEL.parent / routine).read_text(encoding="utf-8"))
        return {new: made}, f"{order}: its fix round {new}", parse_order(made) and new
    return change
