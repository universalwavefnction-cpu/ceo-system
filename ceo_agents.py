from ceo_door import *  # The agents at work: who runs and what each does, how one starts, the login probe.
PROBE, FRESH = b"Reply with the single word ok.\n", 600  # the login probe, and its seconds of trust
# An agent's phase, named by the first words of the kernel's last mark in its log; before one, setup.
PHASES = (("worker starts", "model"), ("reviewer starts", "model"), ("done-when", "done-when"), ("replay", "replay"),
          ("recovery", "recovery"), ("door: its done-when", "done-when"), ("door: ./gate.sh", "gate"),
          ("door: push", "push"), ("night: tests", "tests"), ("researcher starts", "model"))
TAIL = 2 * 2**20  # bytes of a log, from its end, searched for its newest line of text

def heal(repo):
    """Retry unreadable reviews; API blocks wait a day, or for login recovery after limit/auth errors."""
    for path in sorted((repo / ".ceo" / "runs").glob("*/review.json")):
        found = read_json(path)
        if (found.get("verdict") == "blocked" and found.get("reason") == UNREADABLE_REVIEW
                and order_active(repo, path.parent.name)):
            write_json(path, {**found, "verdict": "retry"})
            continue
        blocked = api_blocked(repo, found)
        if not blocked:
            continue
        next_day = blocked[0] < now()[:10]
        recoverable = re.search(r"\b(?:session|weekly|usage) limit\b|usage_limit_reached|"
                                r"\b(?:auth(?:entication|orization)?|unauthorized|refresh(?:ed|ing)?|401)\b", blocked[1], re.I)
        if next_day or recoverable and login_recovered(
                repo, found.get("reviewer"), found.get("ended") or found["retries"][-1]):
            when = "next day's first pass" if next_day else "login now answers and the next pass"
            write_json(path, {**found, "verdict": "retry", "reason": (
                f"the reviewer met an api error after three retries on {blocked[0]}, so the "
                f"{when} makes it again: {blocked[1]}")})

def probe(repo, model):
    """Why one turn of model, started clean as a worker in an empty folder, does not answer: "" when it
    answers, None when it is no agent CLI, for only an agent CLI has a login (a script is never probed)."""
    try:
        if isolation(repo, model) is None:
            return None
        cmd, env = clean(repo, model)
    except Refused as why:  # a ceo.toml that cannot be read starts nothing, and the health line says so
        return str(why)
    with tempfile.TemporaryDirectory() as away, tempfile.TemporaryFile() as log:
        try:
            began, code = time.monotonic(), run_bounded(cmd, away, log, 120, [], stdin=PROBE, env=env)
        except OSError as err:
            return f"it could not start: {err}"
        said = os.fstat(log.fileno()).st_size > 0  # any word at all is an answer to the probe
        why = "it gave no answer in 120 s" if code is None else api_error(log, 0, began, code, said)
        return why or (f"it exited {code}" if code else "")

def login_of(model):
    """The program whose login model uses: the commands of one program share it, whatever their flags."""
    try:
        return Path(shlex.split(model)[0]).name
    except (ValueError, IndexError):
        return model

def healthy(repo, need=None):
    """False while a login probe of need's program failed in the last ten minutes, or while a hand-written
    '*' entry holds every start; without need, false for any fresh failure. need is probed unless it, or a
    failure of its program, is fresh; '*' (the door) checks only the hold. .ceo/login.json keeps each
    probe's time and why it failed, "" when it answered; a program with no probe is said once in the tick's
    log, and .ceo/unprobed, which never expires, keeps that it was."""
    path, unprobed = repo / ".ceo" / "login.json", repo / ".ceo" / "unprobed"
    seen = {model: last for model, last in read_json(path).items() if time.time() - last[0] < FRESH}
    failed = {login_of(model) for model, (_, why) in seen.items() if why}
    if need and need != "*" and need not in seen and login_of(need) not in failed:
        why = probe(repo, need)
        if why is not None:
            seen[need] = [time.time(), why]
            write_json(path, seen)
        elif (program := login_of(need)) not in (unprobed.read_text(encoding="utf-8").splitlines()
                                                  if unprobed.is_file() else ()):
            with open(repo / ".ceo" / "tick.log", "ab") as log:  # said, then kept: a crash between says it twice
                mark(log, f"{program} has no login probe, so a failed login shows only as its runs failing")
            with open(unprobed, "a", encoding="utf-8") as kept:
                kept.write(program + "\n")
        if why:  # said once a probe: in the tick's log, and to every surface
            failed.add(login_of(need))
            said = f"LOGIN FAILED, so nothing new starts with {need}: it did not answer: {why}."
            print(f"ceo: {said}", flush=True)
            event(repo, "login", "", said)
    return not failed & {login_of(need), "*"} if need else not failed

def login_recovered(repo, model, since):
    """A successful probe of this model after its failure; a cached success before it proves nothing."""
    if model and healthy(repo, model):
        when, why = read_json(repo / ".ceo" / "login.json").get(model, (0, ""))
        with contextlib.suppress(ValueError, TypeError):
            return not why and when > datetime.fromisoformat(since).timestamp()
    return False

def start(repo, kind, order, args, children, busy, slug, source=None, planned=False, cache=None):
    """Start a run, review, door or night in a session of its own, to outlive this tick; held by a
    failed login of its program or '*', while the worker an order names would share its reviewer's vendor, or while
    a run before it left its worktree or branch. Return why not, "" once started, or None: not its round, or capped."""
    model = {"run": args.worker, "review": args.reviewer or args.worker, "night": args.worker}.get(kind)
    shares = 1  # GB of the machine's budget it draws: one for each spin a run builds at once
    if kind in ("run", "review"):  # the worker an order names, or its reviewer, is the one probed
        if not order_active(repo, slug, source, cache=cache):
            return
        try:
            text = order_bytes(repo, order, source, cache).decode("utf-8")
            if kind == "run" and (why := round_reason(repo, text)):
                line = f"- `{order.relative_to(repo)}` not started: {why}."
                status = repo / "STATUS.md"
                if not status.exists() or line not in status.read_text(encoding="utf-8").splitlines():
                    with open(repo / ".ceo" / "tick.log", "ab") as log:
                        mark(log, f"not started: {order.relative_to(repo)}: {why}.")
                    stamp(repo, [line])
                return
            worker, reviewer = crew(repo, text, model, model)
            shares = (spins := len(parse_order(text)[2]) if kind == "run" and not parse_order(text)[5] else 0) or 1
            model = reviewer if kind == "review" else worker
        except (OSError, UnicodeDecodeError, Refused) as why:
            print(f"ceo: not started: the {kind} of {order.relative_to(repo)}: {why}.", flush=True)
            return str(why)
    if not healthy(repo, need := "*" if kind == "door" else (model or default_worker(repo))):
        return f"the login of {login_of(need)} failed in the last ten minutes, or a '*' entry holds every start"
    # The night's clock is its window's end, not a phase's.
    cmd = [sys.executable, str(KERNEL), kind, *([str(order)] if order else []),
           *(["--timeout", str(args.timeout)] if kind != "night" else [])]
    if source:
        cmd += ["--source", source]
    if model is not None:
        cmd += ["--model" if kind == "night" else f"--{ROLE.get(kind, 'worker')}", model]
    if kind == "run":  # a floor's refusal, or what a run before it left, is said here, as the reason it did not start
        try:
            admit(repo, model or default_worker(repo))
            if why := ran_before(repo, slug, spins):
                raise Refused(why)
        except Refused as why:
            print(f"ceo: not started: {order.relative_to(repo)}: {why}.", flush=True)
            return str(why)
    try:  # the machine's memory, decided here and handed down: a refused start spawns no child
        share = None if kind == "door" else draw(repo, kind, slug, shares)
    except Refused as why:
        print(f"ceo: not started: the {kind}" + (f" of {order.relative_to(repo)}" if order else "") + f": {why}.",
              flush=True)
        return str(why)
    # The inherited lock reserves capacity before run() claims its own lock. Only a held lock
    # counts: a child refused before creating a run must be eligible again after it exits.
    launch = claim(repo / ".ceo" / "plan-starts" / slug) if planned else None
    try:
        if planned and launch is None:
            return ""  # a start of it, by a pass before, still holds its plan's slot
        with open(repo / ".ceo" / "tick.log", "ab") as log:
            children[(kind, slug)] = subprocess.Popen(
                cmd, cwd=repo, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                pass_fds=[fd for fd in (launch, share) if fd is not None])
    finally:
        for fd in {launch, share} - {None}:
            os.close(fd)
    if kind != "door":
        busy.add((kind, slug))
    print(f"ceo: started the {kind}" + (f" of {order.relative_to(repo)}" if order else ""), flush=True)
    return ""

def logs_of(repo, kind, stem, since):
    """(role, slug, logs) of an agent at work: a run's worker log, or one for each of its spins; a review's
    own; for a lock of no order, the door's, the newest log of its name written since it began, whose run
    is the order it works on."""
    runs = repo / ".ceo" / "runs"
    if kind == "run":
        return "worker", stem, sorted((runs / stem).glob("spin-*.log")) or [runs / stem / "worker.log"]
    if kind in ROLE:
        return ROLE[kind], stem, [runs / stem / f"{kind}.log"]
    # The night's log is in the folder of its date, of no order.
    where = repo / ".ceo" / "night" if stem == "night" else runs
    newest = sorted((path.stat().st_mtime, path) for path in where.glob(f"*/{stem}.log"))[-1:]
    logs = [path for when, path in newest if when >= since]
    return stem, logs[0].parent.name if logs and where == runs else None, logs

def agents(repo):
    """One row for each live agent of repo, the longest at work first: its role, order (and spin), when
    it began and for how long, its phase by the kernel's last mark in its log, the newest line of text
    there, its process id, and all of it as the sentence STATUS.md shows. It lives while its lock is held
    and the process the lock names, or a process group of its run, is alive; the door's is a lock of no
    order, named after its role. The kernel reads files and never asks the model."""
    ceo, rows = repo / ".ceo", []
    live = [*at_work(repo), *(("lock", lock.stem, lock) for lock in sorted((ceo / "locks").glob("*.lock"))
                              if held(lock))]
    table = process_table() if live else []
    pids, groups = {pid for pid, _, _ in table}, {group for _, _, group in table}
    for kind, stem, lock in live:
        try:
            since, signed = lock.stat().st_mtime, lock.read_bytes().strip()  # sign() wrote both
            role, slug, logs = logs_of(repo, kind, stem, since)
        except OSError:
            continue  # it ended, and was swept, while it was read
        meta = read_json(lock.with_suffix(".json")) if kind == "run" else {}
        # Its own process, or, once that is gone, the newest group of the run that still lives.
        pid = int(signed) if signed.isdigit() and int(signed) in pids else next(
            (group for group in reversed(meta.get("groups", [])) if group in groups), None)
        if pid is None:
            continue  # its process is gone, as when the tick holds a cut-off run's lock to look at it
        order = slug and (read_json(ceo / "runs" / slug / "result.json").get("order")
                          or read_json(ceo / "locks" / f"{slug}.json").get("order"))
        # As the owner says it: the worker on order 10 round 2, of the order 010-round-2.
        who = role + (" on order " + re.sub(r"^0+(?=\d)", "", slug).replace("-", " ") if slug else "")
        started = datetime.fromtimestamp(since, timezone.utc).isoformat(timespec="seconds")
        elapsed = max(0, int(time.time() - since))
        for log in logs or [None]:
            mark, line = told(log, since) if log else ("", "")
            phase = next((phase for words, phase in PHASES if mark.startswith(words)), "setup")
            spin = int(log.stem[5:]) if log and log.stem.startswith("spin-") else None
            head = who + (f" spin {spin}" if spin else "") + f", {lasted(elapsed)}, {phase}"
            rows.append({"role": role, "order": order, "slug": slug, "spin": spin, "started": started,
                         "elapsed": elapsed, "phase": phase, "line": line, "pid": pid,
                         "sentence": head + (f": {line}" if line else "")})
    return sorted(rows, key=lambda row: -row["elapsed"])

def told(log, since):
    """(the kernel's last mark in log, the newest line of text after it), read from its end: of the model's
    stream-json only what it wrote as text, never a tool call, its result or other JSON. A log last written
    before since, when its agent began, holds only attempts before it, for an agent writes a mark first."""
    try:
        with open(log, "rb") as file:
            if os.fstat(file.fileno()).st_mtime < since:
                return "", ""
            with mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as data:
                at = data.rfind(b"\n== ceo ")
                found = re.match(rb"\n== ceo \S+: ([^\n]*)", data[at:at + 4096]) if at >= 0 else None
                mark = found[1].decode("utf-8", "replace") if found else ""
                # Only what came after the mark: the done-when's output is never the model's line.
                end, floor = len(data), max(0, len(data) - TAIL, at)
                while end > floor:
                    start = data.rfind(b"\n", floor, end) + 1
                    if not start and floor:
                        break  # a line the floor cuts: older than any shown
                    if line := said(data[start:end]):
                        return mark, line
                    end = start - 1
                return mark, ""
    except (OSError, ValueError):  # no log yet, or an empty one, which cannot be mapped
        return "", ""

def said(raw):
    """What one line of a log says to the owner, or "": a plain line, or the text of an assistant message
    of the model's stream-json; never a mark of the kernel's, a tool call or other JSON."""
    if raw.startswith(b"{"):
        with contextlib.suppress(ValueError, TypeError, AttributeError, KeyError, RecursionError):
            event = json.loads(raw)
            if event.get("type") == "assistant":
                return words("\n".join(block["text"] for block in event["message"]["content"]
                                       if block.get("type") == "text"))
        return ""
    return "" if raw.startswith(b"== ceo ") else words(raw.decode("utf-8", "replace"))

def words(text):
    """The last line of prose in text, as one clean line of at most 200 characters, or "": it has a word in
    it and is no JSON and no code fence, so text that ends in JSON printed pretty shows the line before."""
    for line in reversed(text.splitlines()):
        line = " ".join(re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|[\x00-\x1f\x7f-\x9f]", " ", line).split())
        if re.search(r"\w", line) and not line.startswith("```") and not json_text(line):
            return line if len(line) <= 200 else line[:199] + "…"
    return ""

def json_text(line):
    """True when line is JSON, an object cut short, or a line of JSON printed pretty: a member of an
    object ("key": value) or a value of an array, with its comma."""
    if line.startswith("{") or re.match(r'"(?:[^"\\]|\\.)*"\s*:', line):
        return True
    try:
        json.loads(line.removesuffix(","))
    except RecursionError:  # an array nested deeper than Python reads is JSON all the same
        return True
    except ValueError:
        return False
    return True

def lasted(seconds):
    """A duration as the owner reads it: 40 s, 58 min, 2 h 5 min."""
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min" if minutes else f"{seconds} s"
