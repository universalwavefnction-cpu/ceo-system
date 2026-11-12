from ceo_workspace import *  # What the owner reads and answers: STATUS.md, waiting rows and choices, attention, stalls.
from ceo_activity import run_activity
STAMPS = {"tick": re.compile("Last tick: "), "health": re.compile("Health: "),
          "free": re.compile("Free: disk "), "behind": re.compile(r"main is \d+ behind origin$"),
          "sweep": re.compile("Sweep: "), "night": re.compile("Night: "),
          "orders": re.compile(r"Orders: |your folder has local changes; orders run from origin$")}
NEEDS = "## Needs attention"
ANEW = re.compile(r"- `[^`]+` (?:is filed and cannot start: |lists `[^`]+`, which is not in orders/)")  # each pass's own
UNSTARTED = re.compile(r"- `[^`]+` is eligible and not started: ")  # each stall watch's own: the alarm, on the stall clock
BELL_SINCE = datetime(2026, 10, 25, tzinfo=timezone.utc).timestamp()  # older stops ring no bell: no first-pass burst
DECIDE_CHOICES = [{"pick": "answer", "text": "answer", "ask": "Your answer, for the CEO:"},
                  {"pick": "cut", "text": "cut"}]
def wait_on(repo, slug, row, where=""):
    """Write the owner's waiting row, its question and detail clipped, timestamped for oldest-first ordering."""
    (repo / ".ceo" / "waiting").mkdir(exist_ok=True)
    write_json(repo / ".ceo" / "waiting" / f"{slug}.json", {**row, "time": now("microseconds"), **{
        key: clipped(row[key], where) for key in ("question", "detail") if key in row}})
def clipped(text, where=""):
    """text past 40 lines or 4,000 characters cut to 4,000 or fewer: its first 40 lines, what is left out and where."""
    if not isinstance(text, str) or len(lines := text.splitlines()) <= 40 and len(text) <= 4000:
        return text
    shown = (whole := "\n".join(lines[:40]))[:min(3800, 3900 - len(where))]  # leaves room for a long where
    left = len(lines) - (min(40, len(lines)) if shown == whole else len(shown.splitlines()))  # one in part is shown
    return f"{shown} [{left} more lines left out" + f"; the whole text: `{where}`" * bool(where) + "]"
def waiting_rows(repo):
    """The paths of repo's waiting rows that ask the owner, with choices, the oldest instant first, whatever its offset;
    first by name, those without. A row of mocks or spins, from before they left the kernel, asks him nothing."""
    def instant(path):
        with contextlib.suppress(ValueError, TypeError):
            return datetime.fromisoformat(read_json(path).get("time")).timestamp()
        return float("-inf")
    return sorted((path for path in (repo / ".ceo" / "waiting").glob("*.json") if asked_choices(read_json(path))),
                  key=lambda path: (instant(path), path.name))
def asked_choices(waiting):
    """Return explicit choices, or the standard choices for a CEO decision predating them."""
    return waiting.get("choices") or (DECIDE_CHOICES if waiting.get("decision") else [])
def ceo_merge_pending(result):
    override = result.get("ceo_merge") or {}
    return bool(override and override.get("head") == result.get("head")
                and not override.get("tried") and not result.get("merged"))

def attention_lines(repo):
    """(record, line) for unseen run/review attention and nights not recorded on main or pushed."""
    for path in sorted(path for name in RECORD.values() for path in (repo / ".ceo" / "runs").glob(f"*/{name}")):
        found = read_json(path)
        kind = {"result.json": found.get("attention") and "attention",
                "review.json": found.get("verdict")}.get(path.name)
        what = {"attention": f"needs attention: {found.get('attention')}",
                "blocked": f"the review is blocked: {found.get('reason')}",
                "retry": f"the review waits for a retry: {found.get('reason')}"}.get(kind)
        if blocked := path.name == "review.json" and api_blocked(repo, found):
            what = (f"the review is blocked by an api error after three retries on {blocked[0]}; usage limits retry "
                    "after login recovery; otherwise the next day, "
                    f"{datetime.fromisoformat(blocked[0]) + timedelta(days=1):%Y-%m-%d}: {blocked[1]}")
        line = f"- `{found.get('order')}` {what}"
        # shown is the line shown: a blocked record shows its notice even if a kernel before showed it
        if what and (found.get("shown") != line if blocked else not found.get("shown")):
            yield path, line
    for path in sorted((repo / ".ceo" / "night").glob("*/result.json")):
        found = read_json(path)
        if found.get("unfiled") and not found.get("shown"):
            yield path, (f"- `{found.get('note')}` needs attention: the night could not record it on main here: "
                         f"{str(found['unfiled']).rstrip('.')}; the tick records it once main here can take it, then "
                         "pushes it, before another night runs")
        elif found.get("push") and not found.get("shown"):
            yield path, (f"- `{found.get('note')}` needs attention: the night could not push it to origin: "
                         f"{str(found['push']).rstrip('.')}; it was recorded here first, and the tick pushes it "
                         "once origin takes it, before another night runs")
def choices(path):
    """STATUS.md's waiting run and numbered choices, of a waiting row that asks, a bug's or a CEO stop's."""
    waiting = read_json(path)
    word = ("<n>", "detail") if waiting.get("bug") else ("<choice>", "text")
    question = " ".join(str(clipped(waiting.get("question"), f".ceo/waiting/{path.name}")).split())
    return [f"- `{waiting.get('order')}` {waiting.get('title')}: {question} "
            f"`python3 ceo.py answer {path.stem} {word[0]} [--comment \"<{word[1]}>\"]`",  # a word that is the choice
            *(f"  - {one['pick']}. {one['text']}" if str(one["pick"]) != one["text"] else f"  - {one['text']}"
              for one in asked_choices(waiting))]  # itself is said once
def readiness(repo, tried, live=()):
    """Keep each eligible order's first since and latest refusal; clear reasons on start, remove obsolete unstarted rows."""
    ceo = repo / ".ceo"
    (folder := ceo / "ready").mkdir(parents=True, exist_ok=True)
    with open(folder.with_suffix(".lock"), "a") as lock:  # as refused() holds it: no refusal lands between read and write
        fcntl.flock(lock, fcntl.LOCK_EX)
        for slug in sorted({*tried, *live, *(path.stem for path in folder.glob("*.json"))}):
            path, old = folder / f"{slug}.json", read_json(folder / f"{slug}.json")
            if slug in tried and (tried[slug] or not old):  # a start whose run has not begun changes nothing
                write_json(path, {"since": old.get("since") or now(), "reason": " ".join(tried[slug].split())})
            elif run_recorded(repo, slug):
                if slug in live and not old or old.get("reason"):  # a run of it began: why it did not start goes
                    write_json(path, {"since": old.get("since") or now(), "reason": ""})
            elif not (slug in tried or slug in live or held(ceo / "plan-starts" / slug) or held(ceo / "locks" / f"{slug}.run")
                      or (ceo / "runs" / slug / "result.json").exists()):  # a retry: its worker began, and it runs again
                path.unlink(missing_ok=True)
def refused(order, why):
    """Record a filed child's latest refusal, or clear it with "" once its worker begins."""
    with contextlib.suppress(Refused, OSError, ValueError, TypeError):  # no record where none can be kept
        repo, order, slug = toplevel(), Path(order).resolve(), slugify(Path(order).resolve().stem)
        if FILED.fullmatch(order.relative_to(repo).as_posix()) and not (why and run_recorded(repo, slug)):
            (folder := repo / ".ceo" / "ready").mkdir(exist_ok=True)  # before the tick's first pass makes it
            with open(folder.with_suffix(".lock"), "a") as held:  # the tick's readiness() holds it from read to write
                fcntl.flock(held, fcntl.LOCK_EX)
                if (old := read_json(path := folder / f"{slug}.json")) or why:
                    write_json(path, {"since": old.get("since") or now(), "reason": " ".join(str(why).split())})
def ready_times(repo):
    """{slug: since, in Unix seconds} of each readiness record: the first pass that found the order eligible."""
    ready = {}
    for path in (repo / ".ceo" / "ready").glob("*.json"):
        with contextlib.suppress(ValueError, TypeError, KeyError):
            ready[path.stem] = datetime.fromisoformat(read_json(path)["since"]).timestamp()
    return ready
def unstarted(repo):
    """(slug, readiness record, stall record) of each eligible order not started, stalled: the alarm rides the stall clock."""
    for path in sorted((repo / ".ceo" / "ready").glob("*.json")):
        if (found := read_json(path)).get("reason") and (stall := read_json(repo / ".ceo" / "stalls" / path.name)):
            yield path.stem, found, stall

def stall_snapshot(repo, ready=None):
    """Read file/event progress and observe CPU deltas; ready maps slugs to readiness timestamps."""
    ceo, rows, source = repo / ".ceo", {}, rev(repo, "refs/remotes/origin/main")
    # The orders here, and those only origin's main holds, which a folder with local changes runs from there.
    upstream = order_snapshot(repo, source) if source else {}
    local, theirs = order_snapshot(repo), order_families(upstream)
    orders, families = {**upstream, **local}, {**theirs, **order_families(local)}
    activity, working = run_activity(repo), {slug for _, slug, _ in at_work(repo)}  # a live run or review: no stall
    for path in orders:
        order = repo / path
        slug = slugify(order.stem)
        began = (ready or {}).get(slug)
        family = families[path]
        result = read_json(ceo / "runs" / slug / "result.json")
        rows[slug] = {"ready": began, "last_progress": max(began or 0, activity.get(slug, 0)), "state": "ready" if began is not None else "filed",
                      "merged": bool(result.get("merged")), "cut": orders[path]["cut"],
                      "waiting": ceo_merge_pending(result) or (ceo / "waiting" / f"{slug}.json").exists(), "last_log": "",
                      "inactive": family["finished"] or family["latest"] != path, "alive": slug in working}
        rows[slug]["waiting"] |= not run_recorded(repo, slug) and any(
            slugify(Path(p).stem) in activity for p in family["members"] if p != path)
        row, states = rows[slug], []
        roots = [ceo / "runs" / slug, ceo / "review" / slug, ceo / "review" / f"{slug}.replay"]
        # os.walk passes over a folder removed while it walks, as a reviewer's own tests remove their temporary ones.
        for path in (Path(top, name) for root in roots for top, _, names in os.walk(root) for name in names):
            with contextlib.suppress(OSError):  # a concurrent cleanup may remove a file
                if not path.is_file():
                    continue
                changed = path.stat().st_mtime
                row["last_progress"] = max(row["last_progress"], changed)
                if path.name in RECORD.values():
                    found = read_json(path)
                    if state_ := found.get("state") or found.get("verdict"):
                        states.append((changed, state_))
        row["state_time"], row["state"] = max(states, default=(began or 0, row["state"]))
    for seen in events(repo)[0]:
        # These notices observe progress; counting them would let the monitor heal its own stall.
        if seen.get("event") in ("stalled", "moving") or seen.get("slug") not in rows:
            continue
        row = rows[seen["slug"]]
        with contextlib.suppress(ValueError, TypeError, KeyError):
            changed = datetime.fromisoformat(seen["time"]).timestamp()
            row["last_progress"] = max(row["last_progress"], changed)
            if changed >= row["state_time"]:
                row["state_time"], row["state"] = changed, seen["event"]
            row["merged"] |= seen.get("event") == "merged"
    for path, family in families.items():
        if any(rows[slugify(Path(p).stem)]["merged"] for p in family["members"]):
            rows[slugify(Path(path).stem)]["inactive"] = True
    # A folder with local changes runs orders from origin and lags it: a round merged there ends the family, a join or
    # the kernel's fix round (titled as routines/fix.md titles it) filed there ends the runs before it, and a cut there
    # ends a run the bug door noted closed here; another cut or the CEO's round there waits for its decision's receipt.
    for path, family in theirs.items():  # none with no origin
        latest, slug = family["latest"], slugify(Path(path).stem)
        ended = latest != path and (re.search(r"-join-[0-9a-f]{7}\.md$", latest) or re.match(
            r"# .*: round [0-9]+, its review's findings fixed$", upstream[latest]["text"], re.M))
        closed = upstream[path]["cut"] and read_json(ceo / "runs" / slug / "result.json").get("closed")
        if path in orders and (family["complete"] or ended or closed):
            rows[slug]["inactive"] = True
    log = ceo / "tick.log"
    if log.is_file():
        names = re.compile(r"(?<![\w-])(" + "|".join(map(re.escape, sorted(rows, key=len, reverse=True)))
                           + r")(?![\w-])") if rows else None
        with log.open(encoding="utf-8", errors="replace") as lines:
            for line in lines:
                for match in names.finditer(line) if names else ():
                    rows[match[1]]["last_log"] = line.rstrip("\r\n")
    return rows
def stalled(snapshot, at, stall_minutes=45):
    """The stall rule, from one order's file snapshot and the clock alone."""
    return (snapshot["ready"] is not None and not any(snapshot.get(key) for key in ("inactive", "alive"))
            and not any(snapshot[key] for key in ("merged", "cut", "waiting"))
            and at - snapshot["last_progress"] > stall_minutes * 60)
def stall_line(record):
    progress = datetime.fromtimestamp(record["last_progress"], timezone.utc).isoformat()
    return (f"- `{record['slug']}` stalled since {record['since']}; "
            f"last progress {progress} ({record['state']}).")
def record_stalls(repo, snapshot, at, stall_minutes=45):
    """Record each stop once; a new progress timestamp releases it, even with a zero-minute clock."""
    folder, removed = repo / ".ceo" / "stalls", []
    folder.mkdir(parents=True, exist_ok=True)
    for slug in sorted(set(snapshot) | {path.stem for path in folder.glob("*.json")}):
        path, row = folder / f"{slug}.json", snapshot.get(slug)
        old = read_json(path)
        stopped = row is not None and stalled(row, at, stall_minutes)
        if old:
            if not stopped or row["last_progress"] > old["last_progress"]:
                path.unlink()
                removed.append(stall_line(old))
                event(repo, "moving", slug, f"{slug}: moving again.")
        elif stopped:
            record = {"slug": slug, "since": datetime.fromtimestamp(at, timezone.utc).isoformat(),
                      "last_progress": row["last_progress"],
                      "state": row["state"], "last_log": row["last_log"]}
            write_json(path, record)
            event(repo, "stalled", slug, f"{slug}: stalled; last progress {record['last_progress']}.")
    stuck = [f"- `{slug}` is eligible and not started: {str(found['reason']).rstrip('.')}." for slug, found, _ in unstarted(repo)]
    stamp(repo, [stall_line(read_json(path)) for path in sorted(folder.glob("*.json"))] + stuck, remove=removed, anew=UNSTARTED)
def watch_stalls(repo):
    minutes = settings(repo).get("stall_minutes", 45)
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not 0 <= minutes < float("inf"):
        raise Refused("stall_minutes in ceo.toml must be a nonnegative number")
    snapshot = stall_snapshot(repo, ready_times(repo))
    record_stalls(repo, snapshot, time.time(), minutes)
    return snapshot

def identity(stall):
    return hashlib.sha256(json.dumps(stall, sort_keys=True).encode()).hexdigest()
def idle_spell(repo, snapshot, at):
    """An open intent with nothing alive, stalled, asked within a day, run or filed for 30 minutes: the spell, else None."""
    main = "refs/remotes/origin/main" if rev(repo, "refs/remotes/origin/main") else "refs/heads/main"
    tree = set(git("ls-tree", "-rz", "--name-only", main, "--", "intents", cwd=repo).stdout.split("\0"))  # its files
    intents = sorted(name for name in tree if re.fullmatch(r"intents/[^/]+\.md", name) and name[:-3] + ".closed" not in tree)
    if not intents or any(row["alive"] or stalled(row, float("inf")) for row in snapshot.values()) or any(
            held(path) for path in (repo / ".ceo" / "locks").glob("*.lock")):
        return None
    for path in (repo / ".ceo" / "waiting").glob("*.json"):  # one without a readable time is old
        with contextlib.suppress(ValueError, TypeError):
            if at - datetime.fromisoformat(read_json(path).get("time")).timestamp() < 86400:
                return None
    last = [int(git("log", "-1", "--no-renames", "--diff-filter=A", "--format=%ct", main, "--", ":(glob)orders/*.md",
                    ":(glob)intents/*.md", cwd=repo).stdout.strip() or 0)]
    for seen in events(repo)[0]:  # a cut, a wait or a question moves nothing here
        with contextlib.suppress(ValueError, TypeError, KeyError):
            last += [datetime.fromisoformat(seen["time"]).timestamp()] * (seen["event"] in ("started", "done", "failed", "merged"))
    if at - (last := max(last)) <= 30 * 60:
        return None
    since, shown = datetime.fromtimestamp(last, timezone.utc).isoformat(timespec="minutes"), ", ".join(f"`{one}`" for one in intents)
    return {"slug": "idle", "state": "idle", "since": since, "last_progress": last, "intents": intents,
            "said": f"an intent is open ({shown}) and nothing has run since {since}"}
def ring(repo, snapshot, at=None):
    """Record each attention (>30m), idle intent, stalled-unstarted or the week's day-old questions bell once; send nothing."""
    ceo, at, rung = repo / ".ceo", time.time() if at is None else at, []
    for slug, row in sorted(snapshot.items()):
        if (row["ready"] is not None and not any(row[key] for key in ("merged", "cut", "waiting", "inactive"))
                and row["state"] in ("failed", "fails", "blocked", "diverges")
                and BELL_SINCE <= row["state_time"] and at - row["state_time"] > 30 * 60):
            since = datetime.fromtimestamp(row["state_time"], timezone.utc).isoformat(timespec="minutes")
            rung.append((f"attention {slug} {int(row['state_time'])}",
                         f"`{slug}` needs attention since {since}: {row['state']}"))
    if spell := idle_spell(repo, snapshot, at):  # once, 30 minutes after its CEO was woken, or after it began
        woken = ceo / "decisions" / identity(spell) / "stall.json"
        if at - (woken.stat().st_mtime if woken.exists() else spell["last_progress"] + 30 * 60) > 30 * 60:
            answer = read_json(woken.with_name("applied.json")).get("reason")
            rung.append((f"idle {spell['last_progress']}", spell["said"] + f"; the CEO answered: {answer}" * bool(answer)))
    for slug, found, stall in unstarted(repo):
        rung.append((f"unstarted {slug} {stall.get('since')}", f"`{slug}` is eligible and not started: {found.get('reason')}"))
    old, week = [], "asked %d-W%02d" % datetime.fromtimestamp(at, timezone.utc).isocalendar()[:2]
    for path in waiting_rows(repo):  # the questions a day old ring once a week, together; their rows stay as they are
        with contextlib.suppress(ValueError, TypeError):
            if at - (asked := datetime.fromisoformat((row := read_json(path)).get("time"))).timestamp() >= 86400:
                old.append((path, row, asked))
    for path, row, asked in old[:1]:  # the oldest, its question on one short line
        rung.append((week, f"questions waiting a day or more for your word: {len(old)}; the oldest, `{path.stem}`, since "
                     f"{asked.isoformat()[:16]}: {' '.join(str(row.get('question') or row.get('title')).split())[:100]}"))
    (ceo / "bells").mkdir(parents=True, exist_ok=True)
    for bell in (ceo / "bells").glob("asked-*.json"):  # what is true now: this week's bell, while a question is old
        if not old or bell.stem != slugify(week):
            bell.unlink(missing_ok=True)
    for key, line in rung:
        if not (bell := ceo / "bells" / f"{slugify(key)}.json").exists():
            write_json(bell, {"key": key, "kind": key.split()[0], "line": line, "time": now()})

def stamp(repo, attention_=(), sections=None, remove=(), anew=None, **top):
    """Replace kernel top lines and named sections (empty removes them), then append unseen attention lines."""
    path = repo / "STATUS.md"
    lines = [old for old in (path.read_text(encoding="utf-8").splitlines() if path.exists() else [])
             if old not in remove and not any(STAMPS[kind].match(old) for kind in top)
             and not (anew and anew.match(old) and old not in attention_)]  # anew: those of its kind are only these
    for heading in sections or ():
        if heading in lines:  # the last rows: heading, rows and the empty line before
            at = end = lines.index(heading)
            while end + 1 < len(lines) and lines[end + 1].startswith(("- ", "  ")):
                end += 1
            del lines[at - 1 if at and not lines[at - 1] else at:end + 1]
    at = 1 if lines[:1] and lines[0].startswith("# ") else 0
    lines[at:at] = [line for line in top.values() if line]
    while at < len(lines) and any(kind.match(lines[at]) for kind in STAMPS.values()):
        at += 1
    rows = [line for heading, found in (sections or {}).items() if found for line in ("", heading, *found)]
    # An empty line closes the rows, so the owner's line below them is never taken for one of them.
    lines[at:at] = rows + ([""] if rows and at < len(lines) and lines[at] else [])
    for new in attention_:
        if new not in lines:
            if NEEDS not in lines:
                lines += ["", NEEDS]
            lines.insert(lines.index(NEEDS) + 1, new)
    write_whole(path, "\n".join(lines) + "\n")

def write_return(repo, snapshot, running):
    """Rewrite the 40-line return screen each pass; exclude it and its temp file in this checkout only."""
    exclude = repo / git("rev-parse", "--git-path", "info/exclude", cwd=repo).stdout.strip()
    if b"/RETURN.md" not in (exclude.read_bytes().splitlines() if exclude.is_file() else []):
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with open(exclude, "ab") as out:  # the owner's lines kept byte for byte; write_whole's own name, too
            out.write(b"\n/RETURN.md\n/.RETURN.md.tmp\n")
    families, plans = order_families(snapshot["orders"]), []
    for name, plan in snapshot["plans"].items():
        found = [families.get(one["path"], {}) for one in plan["orders"]]
        if not all(family.get("finished") for family in found):
            plans.append(f"- `{name}` {plan['title']}: {sum(bool(family.get('complete')) for family in found)} "
                         f"of {len(found)} orders merged")
    asks = [lines[0] + (" (" + "; ".join(line.strip()[2:] for line in lines[1:]) + ")") * bool(lines[1:])
            for lines in map(choices, waiting_rows(repo))]
    stuck = [f"- `{slug}` is eligible and not started since {str(found.get('since'))[:16]}: {found.get('reason')}"
             for slug, found, _ in unstarted(repo)]
    bugs = [f"- `{path.relative_to(repo)}`" for path in sorted((repo / "bugs").glob("*.md"))
            if not re.search(r"^## Closed$", path.read_text(encoding="utf-8", errors="replace"), re.M)]
    happened = [f"- {str(one.get('time'))[:16]} {one.get('event')} {one.get('slug') or '-'}: {one.get('sentence')}"
                for one in events(repo)[0][::-1][:7]]
    board, dev = home() / "board" / "index.html", settings(repo).get("dev_url")
    links = [f"- the board: `{board}`"] * board.is_file() + [f"- dev: {dev}"] * bool(dev)
    word = ("needs you" if asks else "working" if running else "needs attention" if stuck else
            "done" if snapshot["plans"] and not plans else "idle")
    text = [f"# {repo.name}: {word}", f"Written {now()}; STATUS.md holds every line."]
    for heading, found, most in (("## Plans", plans, 6), ("## Waiting for your word", asks, 8), (NEEDS, stuck, 2),
                                 ("## Open bugs", bugs, 3), ("## What happened, newest first", happened, 7),
                                 ("## Links", links, 2)):
        text += [heading, *found[:most]] + [f"- and {len(found) - most} more"] * (len(found) > most) if found else []
    write_whole(repo / "RETURN.md", "\n".join(text) + "\n")
