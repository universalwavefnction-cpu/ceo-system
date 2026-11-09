from ceo_dream import *  # The night: its window, the researcher in a read-only copy, its briefing, the word on a finding.
NIGHT = "02:00-06:00"  # the night's window, in local time, when ceo.toml names none ("" there names none)
PART = 30_000  # characters, the last ones, of each part of the project a night's brief carries
NIGHT_BRIEF = """\

Your working directory is a read-only clean copy of commit {commit}, the project's main: read it, run
what only reads, and copy it elsewhere for anything that writes. You change nothing; the owner decides.

Write your answer as one JSON object to the file ../NIGHT.json, in the folder above your copy:
{{"summary": "what you looked at and what you found",
 "findings": [{{"claim": "one sentence", "command": "one shell command"}}],
 "ideas": [{{"idea": "one sentence", "sources": ["https://..."]}}]}}
Each finding is a claim plus one shell command that exits non-zero while the claim is true, the most
harmful first, at most {most}. The kernel replays each command with bash from the root of a fresh clean
copy of commit {commit} in another directory, where nothing you made exists: each must stand on its own,
and no command may hold an absolute path of this machine. A finding counts only when its command fails
there, and then becomes an order whose test is that command passing. An idea is what the outside world
suggests, with the sources it comes from, at most {most}; it is never a finding. With nothing found,
write "findings": [].

"""

def window(repo):
    """(start, end), in local time, of the night window of ceo.toml (`night`, NIGHT when it names none, ""
    for none) that holds this moment, or None outside it. A window may run past midnight."""
    text = str(settings(repo).get("night", NIGHT)).strip()
    found = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)", text)
    if text and (not found or found.group(1, 2) == found.group(3, 4)):
        raise Refused(f"ceo.toml's night must be two different local times, as \"{NIGHT}\", or \"\" for none")
    if not text:
        return None
    at = datetime.now()
    start, end = (at.replace(hour=int(found[h]), minute=int(found[h + 1]), second=0, microsecond=0)
                  for h in (1, 3))
    if end <= start:  # past midnight: tonight's window, or last night's while it lasts
        start, end = (start, end + timedelta(days=1)) if at >= start else (start - timedelta(days=1), end)
    return (start, end) if start <= at < end else None

def keepable(value):
    """value when it is text the kernel can write and run: a string with no NUL that UTF-8 can encode; else None."""
    if isinstance(value, str) and "\x00" not in value:
        with contextlib.suppress(UnicodeEncodeError):
            value.encode("utf-8")
            return value
    return None

def night_known(repo, commit):
    """What the night knows on commit, (declined, open). Declined: the findings of routines/night-declined.md
    and of each night order cut, which never come back. Open: those of each night order neither cut nor
    merged, and of each draft in drafts/orders/, each with its path and the owner's comments on it."""
    orders = listed(repo, commit, "orders", "drafts/orders").splitlines()
    declined, open_ = findings_in(blob(repo, commit, DECLINED)), []
    for path in filter(re.compile(r"(?:drafts/)?orders/\d+-night-[a-z0-9-]+\.md").fullmatch, orders):
        stem, slug = path[:-3], Path(path).stem
        found = findings_in(blob(repo, commit, path))[:1]
        if f"{stem}.cut" in orders:
            declined += found
        elif not (read_json(repo / ".ceo" / "runs" / slug / "result.json").get("merged")
                  or (repo / ".ceo" / "swept" / slug).exists()):  # a merge swept away is merged too
            open_ += [{**one, "order": path, "comments": blob(repo, commit, f"{stem}.comment")} for one in found]
    return declined, open_

def replayed(repo, commit, command, path, log, timeout, record, lock, what="replay"):
    """The exit of command, run with bash from the root of a fresh clean copy of commit at path, removed
    after, or None when the clock stopped it."""
    trouble = clone(repo, commit, path, lock)
    if trouble:
        raise Refused(f"no clean copy of main could be made: {trouble}")
    mark(log, f"{what}: {' '.join(command.split())}")
    try:
        return run_bounded(["bash", "-c", command], path, log, timeout, record["survivors"])
    finally:
        unlock(path)

def briefing(repo, commit, record, open_):
    """What the night's model is told of the project after its brief, each part under its heading, of each
    the last PART characters: the files of commit, what is still open and what the tests did."""
    bugs = "".join(blob(repo, commit, path) for path in listed(repo, commit, "bugs").splitlines()
                   if re.fullmatch(r"bugs/\d+\.md", path))
    still = "".join(f"{one['order']}{'' if one['order'].startswith('drafts/') else ', filed'}:\nClaim: {quoted(one['claim'])}\n"
                    f"Command: {quoted(one['command'])}\n{one['comments']}\n" for one in open_)
    tests = record["tests"]
    ran = "No tests are named." if not tests else f"`{tests['command']}` " + (
        f"ran past its {tests['bound']} s, a quarter of the night." if tests["exit"] is None
        else f"exited {tests['exit']}.")
    parts = [("VISION.md", blob(repo, commit, "VISION.md")), ("IDEAS.md", blob(repo, commit, "IDEAS.md")),
             ("bugs/", bugs),
             (DECLINED, blob(repo, commit, DECLINED)),
             ("Findings still open, with the owner's comments on them", still),
             ("The project's own tests, in a fresh clone of main", ran)]
    return "".join(f"## {title}\n\n{(text or '')[-PART:].strip() or '(none)'}\n\n" for title, text in parts)

def night(args, told):
    """The night's research on this repository, once a night inside its window, with the clock at the
    window's end: the project's own tests in a fresh clone, then the model in a read-only one; each command
    it gives replayed in a fresh clone, and at most --max-findings that fail recorded on main as drafts in
    drafts/orders/, each waiting for the owner's word, with the morning note. Return what it found, in one line."""
    repo = toplevel()
    register(repo)
    span = window(repo)
    if span is None:
        raise Refused(f"it is outside the night's window, \"{settings(repo).get('night', NIGHT)}\" in "
                      "ceo.toml, in local time")
    date, ceo = f"{span[1]:%Y-%m-%d}", repo / ".ceo"
    folder, lock = ceo / "night" / date, claim(ceo / "locks" / "night.lock")
    if lock is None:
        raise Refused("a night of this repository is at work")
    sign(lock)
    if folder.exists():
        raise Refused(f"the night of {date} has run: .ceo/night/{date} exists")
    if pending := unpushed(repo):  # filed on a main without it, the next night's orders would clash with its
        raise Refused(f"the night of {pending[0][1]['date']} is not on origin yet; the tick pushes it first")
    model = args.model if args.model is not None else default_worker(repo)
    starts = clean(repo, model)
    admit(repo, model)  # below a floor nothing is made, and the tick tries again next pass
    draw(repo, "night", "")  # its share, held by this process until it exits
    for left in (ceo / "night").glob("*/repo"):  # a read-only copy a killed night left behind
        unlock(left)
    folder.mkdir(parents=True)
    end = time.monotonic() + min(time.mktime(span[1].timetuple()) - time.time(), args.timeout or DAY)
    record = {"date": date, "started": now(), "ended": None, "model": model, "commit": None, "tests": None,
              "summary": "", "findings": [], "declined": [], "dropped": [], "unproven": [], "ideas": [],
              "said": "", "dream": "", "note": f"routines/night-{date}.md", "filed": False, "base": None, "recorded": None,
              "pushed": False, "push": "", "reason": "", "survivors": []}
    signal.signal(signal.SIGTERM, on_sigterm)
    try:
        try:  # first the cleaner's pass of LESSONS.md, on a quarter of the night, when it has work since the last pass
            record["dream"] = dream(repo / "LESSONS.md", model, max(1, int((end - time.monotonic()) / 4)), due=True)[1]
        except Refused as why:  # it says why, and the research goes on
            record["dream"] = f"no pass of LESSONS.md: {why}"
        with open(folder / "night.log", "a+b", buffering=0) as log:
            research(repo, args, told, record, folder, log, lock, starts, end)
        return (f"the night of {date}: {record['said']}; the morning note is {record['note']}"
                + ("" if record["pushed"] else f"; it is not on origin yet: {record['push'].rstrip('.')}"))
    except Stopped as why:
        record["reason"] = f"stopped by {why}"
        raise Refused(f"the night of {date} was stopped by {why}: .ceo/night/{date}/result.json") from None
    except Refused as why:
        record["reason"] = str(why)
        raise
    except BaseException as err:
        record["reason"] = f"ceo.py itself stopped: {err!r}"
        raise
    finally:
        for name in ("repo", "tests", "replay"):
            unlock(folder / name)
        record["ended"] = now()
        write_json(folder / "result.json", record)  # not night.json: NIGHT.json, on a disk blind to case

def research(repo, args, told, record, folder, log, lock, starts, end):
    """The night's steps, kept in record: main as origin has it; its own tests; the model; the replay of
    each finding not declined; then those kept recorded on main here, put to the owner, and pushed."""
    (cmd, env), most, left = starts, args.max_findings, lambda: end - time.monotonic()
    if origin(repo):
        git("fetch", "-q", "origin", "main", cwd=repo, timeout=NET)
    commit = record["commit"] = rev(repo, "refs/remotes/origin/main") or rev(repo, "refs/heads/main")
    if not commit:
        raise Refused("there is no main to look at")
    declined, open_ = night_known(repo, commit)
    tests = str(settings(repo).get("test_cmd") or (
        "./gate.sh" if "gate.sh" in listed(repo, commit).splitlines() else ""))
    kept = []
    if tests:  # first, with no model: does a fresh clone pass its own tests, in a quarter of the night?
        bound = max(1, int(left() / 4))
        exited = replayed(repo, commit, tests, folder / "tests", log, bound, record, lock, "night: tests")
        record["tests"] = {"command": tests, "exit": exited, **({"bound": bound} if exited is None else {})}
        found = tests_finding(record["tests"])
        if found:  # they fail, or do not finish: a finding already
            (record["declined"] if any(alike(found, other) for other in declined) else kept).append(
                {**found, "exit": exited})
    copy, path = folder / "repo", folder / "NIGHT.json"
    trouble = clone(repo, commit, copy, lock)
    if trouble:
        raise Refused(f"no clean copy of main could be made: {trouble}")
    subprocess.run(["chmod", "-R", "a-w", str(copy)], capture_output=True)  # it proposes, never fixes
    routine = blob(repo, commit, "routines/night.md") or (KERNEL.parent / "routines" / "night.md").read_text(
        encoding="utf-8")
    brief = told + (routine + NIGHT_BRIEF.format(commit=commit, most=most)
                    + briefing(repo, commit, record, open_)).encode("utf-8", "replace")
    if cmd is None:
        record["reason"] = f"the model could not start: {NO_WORKER}"
    else:
        mark(log, f"researcher starts: {record['model']}")
        since, began = log.tell(), time.monotonic()
        try:  # it stops a quarter of the night before its end, which the replays use
            ran = run_bounded(cmd, copy, log, max(1, left() * 3 / 4), record["survivors"], stdin=brief, env=env)
            why = api_error(log, since, began, ran, path.exists())
            record["reason"] = (f"the model ran past its clock: {LIMIT}" if ran is None
                                else f"the model ended with an api error: {why}" if why else "")
        except OSError as err:
            record["reason"] = f"the model could not start: {err}"
    unlock(copy)
    answer = read_json(path)
    proposed = answer.get("findings") if isinstance(answer.get("findings"), list) else []
    record["summary"] = " ".join((keepable(answer.get("summary")) or "").split())
    if cmd is not None and not record["reason"] and not isinstance(answer.get("findings"), list):
        record["reason"] = "the model's answer in NIGHT.json cannot be read as findings"
    for index, one in enumerate(proposed):  # as the model ranked them, the most harmful first
        claim, command = (keepable(one.get(key)) if isinstance(one, dict) else None for key in ("claim", "command"))
        if not (claim and claim.strip() and command and command.strip()):
            continue
        finding = {"claim": " ".join(claim.split()), "command": command.strip()}
        if any(alike(finding, other) for other in declined):
            record["declined"].append(finding)
        elif (any(alike(finding, other) for other in (*kept, *record["dropped"], *record["unproven"]))
              or len(kept) >= most):
            continue  # said twice, or found by the tests already; or no place is left
        elif not orderable(finding, tests):  # a line of it would end its order's test, or change it
            record["dropped"].append({**finding, "exit": None, "why": "it cannot be an order's test"})
        elif left() <= 0:
            record["unproven"].append({**finding, "exit": None, "why": "no time was left to replay it"})
        else:  # each its share of the time left, of the places left, so that one that never ends leaves the rest
            share = max(1, int(left() / min(len(proposed) - index, most - len(kept))))
            exited = replayed(repo, commit, finding["command"], folder / "replay", log, share, record, lock)
            if exited is None:
                record["unproven"].append({**finding, "exit": None, "why": f"its replay ran past its {share} s"})
            else:
                (kept if exited else record["dropped"]).append({**finding, "exit": exited})
    for finding in kept:
        known = next((one for one in open_ if alike(finding, one)), {})
        finding.update(order=known.get("order"), new=not known)
    # Two findings of one open order are one: the first, the more harmful, stands for it.
    kept = [one for n, one in enumerate(kept) if one["new"] or all(one["order"] != old["order"] for old in kept[:n])]
    record["ideas"] = [{"idea": " ".join(one["idea"].split()), "sources": one["sources"]} for one in (
        answer.get("ideas") if isinstance(answer.get("ideas"), list) else [])
        if isinstance(one, dict) and (keepable(one.get("idea")) or "").strip()
        and isinstance(one.get("sources"), list) and one["sources"]
        and all(re.fullmatch(r"https?://\S+", keepable(source) or "") for source in one["sources"])
    ][:most]
    new = sum(finding["new"] for finding in kept)
    record["findings"], record["said"] = kept, (f"{len(kept)} finding{'s' * (len(kept) != 1)}"
                                                + (f", {new} new for your word" if new else "")
                                                + (f", {len(kept) - new} still open" if len(kept) > new else ""))
    with recording(repo):  # to the end of its push: no other record of this machine's reaches origin first
        night_here(repo, record, tests, folder / "result.json")
        file_night(repo, record, folder / "result.json")
        night_push(repo, record, folder / "result.json")

def night_answer(repo, slug, answer):
    """The owner's word on a night finding, read once by the tick: agree moves its draft into orders/, filed;
    decline adds it and his comment to routines/night-declined.md and removes its draft; comment keeps his words
    beside its order, for the next night, and the finding waits on."""
    path = repo / ".ceo" / "waiting" / f"{slug}.json"
    waiting, choice = read_json(path), answer.get("pick")
    order, said = str(waiting.get("order") or ""), " ".join(str(answer.get("comment") or "").split())
    if not (re.fullmatch(r"drafts/orders/\d+-night-[a-z0-9-]+\.md", order) and "claim" in waiting):
        raise Refused(f"the waiting row of {slug} is no night finding's")
    stem, when, filed = order[:-3], f"{datetime.now().astimezone():%Y-%m-%d %H:%M}", order[7:]
    def change(base):
        if choice == "agree":
            if listed(repo, base, filed) or not listed(repo, base, order):  # a name taken, even by an empty file
                raise Refused(f"{filed} is taken in orders/, or {order} is not on main: nothing is filed")
            return ({order: None, filed: blob(repo, base, order)}, f"Night finding {filed}: filed on the owner's word",
                    f"filed as {filed}: it starts as a filed order")
        if choice == "decline":
            old = blob(repo, base, DECLINED) or (
                "# Night findings declined\n\nThe owner declined these: a night drops any finding whose claim or "
                "command matches one of them before it is replayed, so none comes back.\n")
            return ({DECLINED: f"{old}\n## {when}, {order}\n\nClaim: {quoted(waiting['claim'])}\nCommand: "
                               f"{quoted(waiting.get('command', ''))}\nComment: {quoted(said)}\n",
                     order: None, f"{stem}.comment": None},
                    f"Night finding {order}: declined, and its draft removed", "declined: no night proposes it again")
        if choice == "comment" and said:
            return ({f"{stem}.comment": blob(repo, base, f"{stem}.comment") + f"{when}: {said}\n"},
                    f"Night finding {order}: the owner's comment", "your comment is kept for the next night")
        raise Refused(f"{choice} is no word on {slug}: agree, decline [comment] or comment <words>")
    told = record_on_main(repo, f"the word on {order}", change)
    if choice == "comment":  # no decision: it waits on, showing what he said
        write_json(path, {**waiting, "detail": f"{waiting.get('found')}; your comment: {said}"})
    else:
        path.unlink(missing_ok=True)
    return f"{order}: {told}"

def night_due(repo, args, children, busy):
    """Start this repository's night once a night, inside its window, when origin has every night before it."""
    try:
        span = window(repo)
    except Refused as why:
        print(f"ceo: no night: {why}.", flush=True)
        return
    if (span and not (repo / ".ceo" / "night" / f"{span[1]:%Y-%m-%d}").exists() and not unpushed(repo)
            and ("night", "") not in busy):
        start(repo, "night", None, args, children, busy, "")
