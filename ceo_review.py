from ceo_records import *  # The review: its brief, the reviewer's copy, the verdict, the PR.
ANSWER = "REVIEW.json"  # where the reviewer answers, in its copy
SPLIT, SPLIT_IT = 300, " lines: split it"  # lines of code a first run may add, and the refusal
UNREADABLE_REVIEW = f"the reviewer's answer in {ANSWER} cannot be read as a summary and findings"
BRIEF = """\
You are a fresh reviewer. You did not build this work and owe its builder nothing.
Your working directory is a copy of your own of commit {commit}: the finished work on the order below.
Change nothing outside this directory. It is removed when you are done.

Judge the work only against what the order states in its Goal and Done when; a fault outside them
goes into the summary as an idea, never into findings.
Run the tests the work touches and its done-when; the door runs the whole suite once, on the joined commit.
Check the work against the order, then write your answer as one JSON object to the file {answer} here:
{{"summary": "what you checked and what you found",
 "findings": [{{"claim": "one sentence", "command": "one shell command"}}]}}
Each finding is a claim plus one shell command that exits non-zero while the claim is true.
The kernel replays every command with bash from the root of a fresh clean checkout of commit {commit}
in another directory, where nothing you made here exists: each command must stand on its own, and no
command or summary may hold an absolute path of this machine. A finding counts only when its command
fails there. When you find nothing, write "findings": [].

The order:

"""
ENDING = (b"\nBefore you end, commit your work and leave git status empty, and run the tests your change "
          b"touches and this done-when; the door runs the whole suite once, on the joined commit. Never change "
          b"LESSONS.md: only the kernel writes it, and work that changes it is refused before review; what this "
          b"work taught the next worker here goes in your final answer, one line `Lesson: <one line>` each.\n")
def inspect(args, told):
    """Review the run's finished commit in an isolated copy; record the answer beside the result."""
    kind, role = args.command, ROLE[args.command]
    order, raw, title, _, repo, slug, _, run_dir = prepare(args.order, args.source)
    if not order_active(repo, slug, args.source):
        raise Refused("this order family is finished or has a later round")
    model = getattr(args, role) if getattr(args, role) is not None else default_worker(repo)
    model = crew(repo, raw.decode("utf-8"), reviewer=model)[1]  # the reviewer of the named worker's vendor
    admit(repo, model)
    starts = clean(repo, model)  # a ceo.toml that cannot be read refuses before anything is recorded
    draw(repo, kind, slug)  # its share, held by this process until it exits
    lock = claim(repo / ".ceo" / "locks" / f"{slug}.{kind}")
    if lock is None:
        raise Refused(f"a {kind} of this order is running")
    sign(lock)
    result, last = read_json(run_dir / "result.json"), read_json(run_dir / RECORD[kind])
    commit = result.get("state") == "done" and result.get("head")
    if not commit or not to_make(run_dir / RECORD[kind]):
        raise Refused(f"a {kind} needs a run that ended done, with no {RECORD[kind]} yet")
    # The review goes on the run's pull request: without one, it waits for the next tick.
    if origin(repo) and not result.get("pr"):
        if not ensure_pr(repo, slug, result) and "OWNER/REPO" not in result["reason"]:  # else gh cannot name origin: no PR
            raise Refused(result["reason"])
        note(repo, slug, pr=result["pr"])
    copy, log_path = repo / ".ceo" / kind / slug, run_dir / f"{kind}.log"
    record = {"order": result.get("order"), "commit": commit, "verdict": "blocked", "reason": "",
              "summary": "", "findings": [], "posted": None, role: model, f"{role}_exit": None, "survivors": [],
              "started": now(), "ended": None, "log": str(log_path.relative_to(repo)),
              "retries": last.get("retries", [])}
    signal.signal(signal.SIGTERM, on_sigterm)
    try:
        with open(log_path, "a+b", buffering=0) as log:
            # A first run too big to review well, or any run that wrote LESSONS.md, is refused unread: no copy.
            # Each reason is named; size last, so a run refused for its size still ends so and is not rounded.
            record["reason"] = "; ".join(filter(None, (wrote_lessons(record, repo), oversized(record, raw, repo))))
            if not record["reason"]:
                record["reason"] = look(record, kind, told, raw, repo, copy, starts, log, lock, args.timeout)
        post(repo, result, record)
        return record
    except Stopped:
        record = None  # an interruption, never a verdict: the next tick starts it again
        raise
    except BaseException as err:
        record["reason"] = f"ceo.py itself stopped: {err!r}"
        raise
    finally:
        for path in (copy, copy.with_name(f"{slug}.replay")):
            shutil.rmtree(path, ignore_errors=True)
        if record is not None:
            record["ended"] = now()
            write_json(run_dir / RECORD[kind], record)
            verdict, found = record["verdict"], sum(one["counts"] for one in record.get("findings", ()))
            if verdict == "survives" and not result.get("pr"): note(repo, slug, attention="it survived its review, but " + no_github(origin(repo)))
            if verdict in ("fails", "blocked"):  # a verdict that holds the order up
                event(repo, {"fails": "failed"}.get(verdict, verdict), slug, f"{title}: " + (
                    f"a fresh reviewer found {found} problem{'s' * (found != 1)} in it." if verdict == "fails"
                    else f"its {kind} is blocked: {record['reason'].rstrip('.')}."))
def review_brief(commit):
    return BRIEF.format(commit=commit, answer=ANSWER).encode()
def oversized(record, raw, repo):
    """Refuse oversized first-run code additions; tests, a Cargo.lock, records, Markdown, rounds and joins do not count."""
    if re.search(r"^REPLACES:", raw.decode("utf-8").split("\n## ", 1)[0], re.M):
        return ""
    main = "refs/remotes/origin/main" if rev(repo, "refs/remotes/origin/main") else "refs/heads/main"
    numstat = git("diff", "--numstat", f"{main}...{record['commit']}", "--", ".", ":(exclude,glob)**/test_*.py",
                  ":(exclude,glob)**/*.test.*", ":(exclude,glob)**/*.spec.*", ":(exclude,glob)**/Cargo.lock",
                  ":(exclude)orders/", ":(exclude)plans/", ":(exclude)results/", ":(exclude,glob)**/*.md", cwd=repo)
    # A binary file's row says -; a diff that cannot run counts nothing, so the run is reviewed as before.
    added = sum(int(row.split("\t")[0]) for row in numstat.stdout.splitlines() if row[:1].isdigit())
    if numstat.returncode or added <= SPLIT:
        return ""
    return f"adds {added}{SPLIT_IT}"
def wrote_lessons(record, repo):
    """Refuse a run, a round too, whose commits since its branch left main change LESSONS.md: the kernel's alone."""
    main = "refs/remotes/origin/main" if rev(repo, "refs/remotes/origin/main") else "refs/heads/main"
    diff = git("diff", "--name-only", f"{main}...{record['commit']}", "--", "LESSONS.md", cwd=repo).stdout
    return "it changes LESSONS.md, which only the kernel writes; a lesson goes in the final answer" * bool(diff.strip())
def look(record, kind, told, raw, repo, copy, starts, log, lock, timeout):
    """Run the reviewer with clean()'s command and environment and its brief; return why no verdict."""
    commit, role, (cmd, env) = record["commit"], ROLE[kind], starts
    trouble = clone(repo, commit, copy, lock)  # the commit a run ended with, in the repository
    if trouble:
        return f"no copy of commit {commit} could be made: {trouble}"
    (copy / ANSWER).unlink(missing_ok=True)  # only this reviewer's answer can give a verdict
    brief = review_brief(commit) + raw
    if cmd is None:
        return f"the {role} could not start: {NO_WORKER}"
    mark(log, f"{role} starts: {record[role]}")
    since, began = log.tell(), time.monotonic()
    try:
        record[f"{role}_exit"] = run_bounded(cmd, copy, log, timeout, record["survivors"],
                                             stdin=told + brief, keep=(lock,), env=env)
    except OSError as err:
        return f"the {role} could not start: {err}"
    if record[f"{role}_exit"] is None:
        return f"the {role} ran past the {timeout}s wall clock: {LIMIT}"
    why = api_error(log, since, began, record[f"{role}_exit"], (copy / ANSWER).exists())
    if why:
        return retry(record, "verdict", role, why)
    return judge(record, repo, copy, log, lock, timeout)
def judge(review, repo, copy, log, lock, timeout):
    """Fill in review from the reviewer's answer and the replay of its commands, or say why not."""
    answer = read_json(copy / ANSWER)
    shutil.rmtree(copy)
    findings = answer.get("findings") if isinstance(answer.get("findings"), list) else [None]
    texts = [answer.get("summary")] + [text for finding in findings for text in (
        (finding.get("claim"), finding.get("command")) if isinstance(finding, dict) else [0])]
    if not all(isinstance(text, str) and text.strip() for text in texts):
        return UNREADABLE_REVIEW
    review["summary"] = answer["summary"]
    replay = copy.with_name(f"{copy.name}.replay")
    for finding in answer["findings"]:
        # A fresh copy for each command: one command must not change what the next one sees.
        trouble = clone(repo, review["commit"], replay, lock)
        if trouble:
            return f"no clean copy of the finished commit could be made: {trouble}"
        mark(log, f"replay: {finding['command']}")
        code = run_bounded(["bash", "-c", finding["command"]], replay, log, timeout,
                           review["survivors"], keep=(lock,))
        shutil.rmtree(replay)
        review["findings"].append({"claim": finding["claim"], "command": finding["command"],
                                   "exit": code, "counts": code not in (0, None)})
    counts = any(finding["counts"] for finding in review["findings"])
    if not counts and any(finding["exit"] is None for finding in review["findings"]):
        return f"a replayed command ran past the {timeout}s wall clock and no finding counts"
    review["verdict"] = "fails" if counts else "survives"
    return ""
def ensure_pr(repo, slug, result):
    """Publish the run's head and PR number in result, or its refusal; branch conflicts wait for a decision."""
    remote = origin(repo)
    if (remote is None or result.get("pr") or read_json(repo / ".ceo" / "waiting" / f"{slug}.json").get("pr_handoff")
            or not order_active(repo, slug, result.get("source"))):
        return result.get("pr")
    branch, record = f"order/{slug}", json.dumps(result, indent=2)
    body = f"Order: `{result['order']}`\n\nResult:\n```json\n{record}\n```\n"
    step = git("push", "--porcelain", "-q", "origin", f"{result['head']}:refs/heads/{branch}", cwd=repo, timeout=NET)
    if step.returncode == 0:
        step = gh(remote[1], "pr", "create", "--head", branch, "--base", "main",
                  "--title", result.get("title") or result["order"], "--body", body)
    found = re.search(r"/pull/(\d+)", step.stdout + step.stderr)
    result["pr"] = int(found.group(1)) if found else None
    if not found:
        result["reason"] = f"no pull request could be opened: {' '.join((step.stdout + step.stderr).split())}"
        # The run records its done event after this call; ask on the review retry so that event cannot expire the row.
        if result.get("ended") and re.search(r"\[rejected\] \((?:non-fast-forward|fetch first)\)", step.stdout):
            live = git("ls-remote", "--heads", "origin", f"refs/heads/{branch}", cwd=repo, timeout=NET)
            if live.returncode == 0 and (heads := live.stdout.split()):
                question = (f"Remote {branch} is at {heads[0]}; the finished run is {result['head']}. "
                            "Reconcile these heads and answer to retry the PR, or cut this order?")
                wait_on(repo, slug, {"order": result["order"], "title": result.get("title") or slug,
                                    "question": question, "detail": result["reason"], "pr_handoff": True,
                                    "decision": f"pr-{result['head']}", "choices": DECIDE_CHOICES})
                event(repo, "waiting", slug, question)
    return result["pr"]
def post(repo, result, review, override=False, **kw):
    """Post the verdict on the PR, or as a comment if GitHub refuses the author's own review; return its last refusal."""
    if not result.get("pr") or review["verdict"] in ("blocked", "retry") and not override:
        return ""
    words = "\n".join([f"The CEO kernel's review: {review['verdict']}.", "", review["summary"] or review.get("reason", "")] + [
        f"- {finding['claim']} `{finding['command']}` exits {finding['exit']}."
        for finding in review["findings"] if finding["counts"]])
    for how in ("--approve" if review["verdict"] == "survives" else "--request-changes", "--comment"):
        done = gh(origin(repo)[1], "pr", "review", str(result["pr"]), how, "--body", words, **kw)
        if done.returncode == 0:
            review["posted"] = how[2:]
            return ""
    return " ".join(done.stderr.split())
