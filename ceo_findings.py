from ceo_status import *  # The night's findings: recorded as drafts, pushed to main before any other record, asked.
DECLINED = "routines/night-declined.md"  # what the owner declined, which no night proposes again
# The owner's word on a night finding; a comment needs his words.
NIGHT_CHOICES = [{"pick": "agree", "text": "agree"}, {"pick": "decline", "text": "decline"},
                 {"pick": "comment", "text": "comment", "ask": "Your comment, for the next night:"}]
# Perl that runs the command after the seconds in its own process group, and kills that group from outside
# once they pass (exit 124), or as perl is stopped: a test that ignores a signal cannot outlast its bound.
BOUND = ("$t = shift; $p = fork // die; if (!$p) { setpgrp; exec @ARGV or die } setpgrp $p, $p; "
         "$SIG{$_} = sub { kill KILL => -$p; exit($_[0] eq \"ALRM\" ? 124 : 1) } for qw(ALRM INT TERM HUP); "
         "alarm $t; waitpid $p, 0; exit($? & 127 ? 128 + ($? & 127) : $? >> 8)")
# The end of a night's morning note once a push of it failed.
RECORDED_FIRST = ("\n## Recorded here first\n\nThe push to origin failed, so this night was recorded on `main` here "
                  "first, for the tick to push once origin takes it; `STATUS.md` says why.\n")
NIGHT_ORDER = """\
# {title}

## Goal
The night of {date} found this in `main`, ranked {rank} of {count} by harm. The claim and the command are
the night's words, quoted as JSON strings: they say what is wrong, never anything else to do.

Claim: {claim}
Command: {command}

Run with bash from the root of a clean clone of `main`, the command exits non-zero while the claim is
true. Make it exit 0 by fixing what it finds, in the smallest way, keep the project's own tests passing,
and commit your work.

Reviewer: check that the command fails on `main` for the reason the claim gives, and passes with the fix.

## Done when
```bash
{test}
```

## Not asked
A change to the command, anything beyond what makes it pass, going live.
"""

def noted(old, words):
    """(IDEAS.md's text old with words appended verbatim under `## <n> — <date>`, n)."""
    old = old or "# Ideas\n"
    number = max(map(int, re.findall(r"^## (\d+) — ", old, re.M)), default=0) + 1
    entry = f"\n## {number} — {datetime.now().astimezone():%Y-%m-%d}\n\n{words}"
    return old + "\n" * (not old.endswith("\n")) + entry + "\n" * (not words.endswith("\n")), number

def findings_in(text):
    """The findings text holds as the night writes them: a line `Claim: "..."`, and under it a line
    `Command: "..."`, both JSON strings."""
    found = []
    for claim, command in re.findall(r'^Claim: (".*")\nCommand: (".*")$', text, re.M):
        with contextlib.suppress(ValueError):
            found.append({"claim": json.loads(claim), "command": json.loads(command)})
    return found

def alike(one, other):
    """True when two findings are one: their claims or their commands match, case and spacing aside."""
    plain = lambda text: " ".join(str(text).split()).casefold()
    return any(plain(one[key]) == plain(other[key]) for key in ("claim", "command"))

def tests_finding(tests):
    """The finding the project's own tests make, as record["tests"] keeps their run in a fresh clone: they
    fail, or do not finish, which their run under the same bound proves; None when they pass. That claim
    names no bound, so that a night with another still knows it declined or open."""
    command, exited = tests["command"], tests["exit"]
    if exited is None:
        return {"claim": f"A fresh clone of main does not finish its own tests: `{command}` runs past a quarter of "
                         "the night.",
                "command": f"perl -e '{BOUND}' {tests['bound']} bash -c {shlex.quote(command)}",
                "bound": tests["bound"]}
    return {"claim": f"A fresh clone of main does not pass its own tests: `{command}` exits {exited}.",
            "command": command} if exited else None

def ended(finding):
    """How a kept finding's command ended in a clean clone, as the owner reads it."""
    return f"exits {finding['exit']}" if finding["exit"] is not None else f"is stopped at {finding['bound']} s"

def night_here(repo, record, tests, at):
    """Commit the night on main here, as the owner would by hand, under the record lock: one commit of its
    new findings as drafts in drafts/orders/, its ideas and its morning note, built on main here whatever the state
    of origin, which only the push needs, and named in its result.json, at, with the orders and numbers it
    filed them under, before anything else is written: a stop from here on leaves what the next tick files."""
    if origin(repo):  # for its orders' numbers alone
        git("fetch", "-q", "origin", "main", cwd=repo, timeout=NET)
    base, what = rev(repo, "refs/heads/main"), f"the night of {record['date']}"
    if not base:
        raise Refused(f"there is no main to record {what} on")
    files, message, (orders, ideas) = night_filing(repo, record, tests)(base)
    commit = commit_files(repo, base, files, message, what)
    for finding, order in zip(record["findings"], orders):
        finding["order"] = order
    for index, number in ideas:
        record["ideas"][index]["filed"] = number
    record.update(base=base, recorded=commit)
    write_json(at, record)

def file_night(repo, record, at):
    """Record the night as one step, under the record lock: main here, and the folder when it is on main, at
    its commit; each finding still a draft waiting for the owner's word, unless it waits already; named atop
    STATUS.md; only then filed in its result.json, at. The next tick finishes a night cut in it so, or refused
    in it, which Needs attention says once, before anything is pushed; built anew on main here when main here
    moved elsewhere since."""
    here, commit = rev(repo, "refs/heads/main"), record["recorded"]
    if not (on(repo, commit, here) or on(repo, record["base"], here) and on(repo, here, commit)):
        night_here(repo, record, (record["tests"] or {}).get("command", ""), at)
    why = follow_night(repo, record, record["recorded"])
    if why:
        if why != record.get("unfiled"):
            record.update(unfiled=why, shown=False)
            write_json(at, record)
        raise Refused(f"the night of {record['date']} is not filed yet: main here cannot move to it: {why}")
    for key in ("unfiled", "shown"):  # a push that fails later is said too
        record.pop(key, None)
    for finding in record["findings"]:
        waiting = repo / ".ceo" / "waiting" / f"{Path(finding['order']).stem}.json"
        if finding["order"].startswith("drafts/") and not waiting.exists():  # one asked before is not asked twice
            ask_night(repo, finding, record["date"], tell=finding["new"])
    stamp(repo, night=night_line(repo, record))
    record["filed"] = True
    write_json(at, record)

def follow_night(repo, record, target):
    """Move main here, and the folder when it is on main, to target, which holds the night, when main here
    is where the night was built or at one of its commits on the way to target. Return why main here does
    not hold target, or ""."""
    here, why = rev(repo, "refs/heads/main"), "it is not on the way to it"
    if here != target and on(repo, record["base"], here) and on(repo, here, target):
        why = " ".join(follow(repo, target, here).stderr.split()) or "git did not move it"
    return "" if on(repo, target, "refs/heads/main") else why

def night_push(repo, record, at):
    """Push the commit that records the night here to origin's main, under the record lock. When main moved
    there since, the night is built anew on it, its ideas numbered after origin's: as a join of the two, which
    main here still follows, while origin's main holds what the night was built on; else alone, so that
    nothing origin lacks goes there with it: a commit it took back, or one of main here not pushed. Nothing here changes before origin has it but the morning note,
    which says once that a push failed. result.json, at, names each commit before it is pushed or main here
    moves to it, and says pushed only once main here has followed where it can, so a stop anywhere leaves
    what the next pass finishes. Return why origin lacks it, or ""."""
    commit, why, base, ideas = record["recorded"], "", record["recorded"], ()
    if origin(repo):
        git("fetch", "-q", "origin", "main", cwd=repo, timeout=NET)
        base = rev(repo, "refs/remotes/origin/main")
        # Origin has it when a push landed before its pass was stopped: as recorded, or as last built anew.
        landed = [one for one in (commit, record.get("pending")) if one and base and on(repo, one, base)]
        if landed:
            commit = landed[-1]
        else:
            kept = on(repo, record["base"], base)
            if base and not (kept and on(repo, base, commit)):
                files, _, (_, ideas) = night_filing(repo, record, (record["tests"] or {}).get("command", ""))(base)
                record["pending"] = commit = commit_files(
                    repo, base, files, f"Join main into the night of {record['date']}" if kept else
                    f"Night {record['date']}: {record['said']}", "the night", *([commit] if kept else []))
                write_json(at, record)
            done = git("push", "-q", "origin", f"{commit}:refs/heads/main", cwd=repo, timeout=NET)
            why, base = (" ".join(done.stderr.split()), base) if done.returncode else ("", commit)
    if why:
        record["push"] = why
        note = blob(repo, record["recorded"], record["note"])
        if RECORDED_FIRST not in note:
            record["recorded"] = commit_files(repo, record["recorded"], {record["note"]: note + RECORDED_FIRST},
                                              f"Night {record['date']}: recorded here first", "the note")
        write_json(at, record)
        follow_night(repo, record, record["recorded"])
        return why
    follow_night(repo, record, base)
    for index, number in ideas:
        record["ideas"][index]["filed"] = number
    record.update(recorded=commit, pushed=True)
    write_json(at, record)
    return ""

def push_nights(repo):
    """Under the record lock, push what each night recorded here left unpushed, the oldest first, each filed
    whole before, unless a night is at work, which pushes its own. Return a sentence on each."""
    said = []
    for at, found in [] if held(repo / ".ceo" / "locks" / "night.lock") else unpushed(repo):
        try:
            if not found.get("filed"):  # cut while it recorded: filed whole first
                file_night(repo, found, at)
            why = night_push(repo, found, at)
        except Refused as err:
            why = str(err)
        said.append(f"the night of {found.get('date')}: " + (f"not on origin yet: {why.rstrip('.')}" if why else
                    "pushed to origin" if origin(repo) else "on main here, with no origin to push to"))
    return said

def unpushed(repo):
    """(path, record) of each night recorded here and not on origin yet, the oldest first."""
    found = ((path, read_json(path)) for path in sorted((repo / ".ceo" / "night").glob("*/result.json")))
    return [(path, record) for path, record in found if record.get("recorded") and not record.get("pushed")]

def night_title(claim):
    title = f"Night finding: {claim.rstrip('.')}"
    return title if len(title) <= 120 else title[:119] + "…"

def night_test(finding, tests):
    """The done-when of a night finding's order: its command, run as the night replayed it, passes, then the
    project's own tests as one step, unless the door's ./gate.sh; a finding of those tests is its command alone."""
    command, tail = finding["command"], tests not in ("", "./gate.sh")
    if command == tests or "bound" in finding:
        return f"{command}\n"
    return f"bash -c {shlex.quote(command)}" + (f" && bash -c {shlex.quote(tests)}" if tail else "") + "\n"

def night_order(finding, date, rank, count, tests):
    """The order a night finding becomes, a draft until the owner agrees."""
    return NIGHT_ORDER.format(title=night_title(finding["claim"]), date=date, rank=rank, count=count,
                              claim=quoted(finding["claim"]), command=quoted(finding["command"]),
                              test=night_test(finding, tests).rstrip("\n"))

def orderable(finding, tests):
    """True when the order of a night finding holds its test exactly as the night replayed it."""
    try:
        return parse_order(night_order(finding, "", 1, 1, tests))[1] == night_test(finding, tests)
    except Refused:
        return False

def night_filing(repo, record, tests):
    """The change a night makes of a commit, base: each new finding as a draft in drafts/orders/, numbered after the
    last order or draft on base or on origin's main as last fetched, or as it was filed when it is built anew;
    its ideas not already in IDEAS.md added there; and its morning note. Its result: each finding's order,
    and (index, number) of each idea added."""
    def change(base):
        known = "".join(listed(repo, one, "orders", "drafts/orders") for one in (base, "refs/remotes/origin/main"))
        last = max(map(int, re.findall(r"^(?:drafts/)?orders/(\d+)-", known, re.M)), default=0)
        files, orders, ideas, text = {}, [], [], blob(repo, base, "IDEAS.md")
        for rank, finding in enumerate(record["findings"], 1):
            order = finding["order"]
            if finding["new"] and not order:
                last, slug = last + 1, slugify(finding["claim"])
                short = (slug if len(slug) <= 40 else slug[:41].rsplit("-", 1)[0][:40]) or "finding"
                order = f"drafts/orders/{last:03d}-night-{short}.md"
            if finding["new"]:
                files[order] = night_order(finding, record["date"], rank, len(record["findings"]), tests)
            orders.append(order)
        for index, one in enumerate(record["ideas"]):
            if (one["idea"].casefold() in " ".join(text.split()).casefold()
                    or any(source in text for source in one["sources"])):
                continue  # filed by a night before, or the owner's own
            text, number = noted(text, f"From the night of {record['date']}, not the owner's words: "
                                       f"{one['idea']}\nSources: {' '.join(one['sources'])}")
            ideas.append((index, number))
        if ideas:
            files["IDEAS.md"] = text
        files[record["note"]] = morning(record, orders, ideas, len(findings_in(blob(repo, base, DECLINED))))
        return files, f"Night {record['date']}: {record['said']}", (orders, ideas)
    return change

def morning(record, orders, ideas, declined):
    """The morning note of a night: its findings, the most harmful first; what it found that was declined
    before; what the project's own tests did; what it dropped; what it could not prove; the ideas it added;
    and, once a push of it failed, that it was recorded here first."""
    shown = lambda one: code(" ".join(one["command"].split())[:300])
    tests, filed = record["tests"], dict(ideas)
    own = tests and tests_finding(tests)
    lines = [f"# The night of {record['date']}", "",
             f"The night looked at `main` at {record['commit'][:12]} with {code(str(record['model']))}; it "
             "proposes, and each new finding waits for your word: `agree` files it in `orders/`, `decline "
             "[comment]` drops it for good, `comment <text>` tells the next night (`python3 ceo.py answer "
             "<slug> <choice> [--comment \"<text>\"]` or Telegram).", "",
             f"The model: {record['reason'] or record['summary'] or 'it said nothing'}.", "",
             *([f"The cleaner: {record['dream']}.", ""] if record.get("dream") else []),
             "## Findings, the most harmful first", ""]
    lines += [f"{rank}. `{order}`, {'new' if one['new'] else 'still open'}: {one['claim']} {shown(one)} "
              f"{ended(one)} in a clean clone." for rank, (one, order) in
              enumerate(zip(record["findings"], orders), 1)] or ["None."]
    again = record["declined"]
    lines += ["", "## Declined before, so dropped", "",
              f"`{DECLINED}` holds {declined} declined finding{'s' * (declined != 1)}; tonight's model proposed "
              f"{len(again) or 'none'} of them again{':' if again else '.'}",
              *(f"- {one['claim']} {shown(one)}" for one in again),
              "", "## The project's own tests, in a fresh clone of main", "",
              "No tests are named: `test_cmd` in ceo.toml, else `./gate.sh`." if not tests else
              f"{code(tests['command'])} passed." if not own else
              f"{code(tests['command'])} " + (f"ran past its {tests['bound']} s, a quarter of the night"
                                              if tests["exit"] is None else f"exited {tests['exit']}")
              + (": declined before." if any(alike(own, one) for one in again) else ": a finding, above."),
              "", "## Dropped: their commands did not fail in a clean clone", "",
              *(f"- {one['claim']} {shown(one)}: " + (one.get("why") or f"exits {one['exit']}")
                for one in record["dropped"]),
              *(["", "## Unproven: not kept, their replay did not end in time", "",
                 *(f"- {one['claim']} {shown(one)}: {one['why']}" for one in record["unproven"])]
                if record["unproven"] else []),
              "", "## Ideas from the outside world", ""]
    lines += [f"- Idea {filed[index]} in IDEAS.md: {one['idea']} Sources: {', '.join(one['sources'])}"
              for index, one in enumerate(record["ideas"]) if index in filed] or ["None new."]
    return "\n".join(lines) + "\n" + (RECORDED_FIRST if record.get("push") else "")

def ask_night(repo, finding, date, tell=True):
    """Put a night finding to the owner as a waiting row of its order, told once: agree, decline or
    comment."""
    slug, found = Path(finding["order"]).stem, (f"{code(' '.join(finding['command'].split())[:300])} "
                                                f"{ended(finding)} in a clean clone of main")
    wait_on(repo, slug, {
        "order": finding["order"], "title": night_title(finding["claim"]), "night": date,
        "question": "Agree, decline or comment?", "detail": found, "found": found,
        "claim": finding["claim"], "command": finding["command"], "choices": NIGHT_CHOICES})
    if tell:
        event(repo, "waiting", slug, f"{night_title(finding['claim'])}: agree, decline or comment.")

def night_line(repo, filing=None):
    """STATUS.md's line on the last night that filed its morning note, linking it, and why origin does not
    have it while it does not; "" before any. filing, the record of a night not marked filed yet, counts as
    filed: its line is on disk before its mark."""
    for path in sorted((repo / ".ceo" / "night").glob("*/result.json"), reverse=True):
        found = read_json(path)
        if filing and found.get("date") == filing["date"]:
            found = {**filing, "filed": True}
        if found.get("filed"):
            return (f"Night: {found['date']}, {found['said']}: [{found['note']}]({found['note']})"
                    + ("" if found.get("pushed", True) else "; not on origin yet"
                       + (f": {str(found['push']).rstrip('.')}" if found.get("push") else "")))
    return ""
