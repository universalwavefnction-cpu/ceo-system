from ceo_agents import *  # The cleaner: notes snapshotted in git, judged in a copy by routines/dream.md, applied or held.
PASS, INDEX = "Lessons pass", (200, 25_000)  # the title of a page's pass on main; an index's cap, in lines and bytes

def suggested(repo, base):
    """(when the last pass began to read the runs, its `Lessons-read:` trailer, "" before a first; each once, the lessons
    of the project's finished runs that ended at or after it; the last pass's commit, "" before a first): the last pass is
    the newest commit on base titled PASS."""
    log = git("log", f"--grep=^{PASS}", "--format=%s%x00%H%x00%(trailers:key=Lessons-read,valueonly)", base, cwd=repo).stdout
    last, since = next((rest for title, *rest in (one.split("\0") for one in log.splitlines()) if title.startswith(PASS)),
                       ("", ""))
    runs = [read_json(path) for path in sorted((repo / ".ceo" / "runs").glob("*/result.json"))]
    found = list(dict.fromkeys(one for run in runs if run.get("ended") and str(run["ended"]) >= since
                               and run.get("state") != "retry" for one in worker_lessons(repo / str(run.get("log") or "-"))))
    return since, found, last

def dream(path, model=None, timeout=1800, due=False):
    """One pass of the cleaner over path, a folder of notes or a page its project tracks: (0 applied or 3 held, its line).
    due, a page's pass runs only on work since the last one, else (0, ""): a lesson suggested since, or an order merged
    on main since (a merge commit on it, as the door makes) while the page holds more than the paragraph init seeds."""
    where = Path(path).resolve()
    top = git("-C", str(where.parent), "rev-parse", "--show-toplevel", cwd=None).stdout.strip()
    if not (folder := where.is_dir()) and not top:
        raise Refused(f"{path} is no folder of notes, nor a page in a git repository")
    repo = where if folder else Path(top).resolve()
    page = None if folder else where.relative_to(repo).as_posix()  # named so, never by a path of this machine
    work = home() / "dream" / hashlib.sha256(str(where).encode()).hexdigest()[:12] if folder else repo / ".ceo" / "dream"
    if (lock := claim(work / "dream.log")) is None:  # the log of its passes, kept beside the copy, is its lock
        raise Refused(f"a pass of {page or where} is at work")
    log, copy, read, index = os.fdopen(lock, "ab", buffering=0), work / "copy", now(), page or "MEMORY.md"
    notes = lambda: {one.relative_to(copy).as_posix(): one.read_bytes().decode(errors="surrogateescape") for one in (
        [copy / page] if page else sorted(copy.rglob("*"))) if one.is_file() and ".git" not in one.relative_to(copy).parts}
    try:
        if folder and top and git("ls-files", "--", where.name, cwd=where.parent).stdout.strip():
            raise Refused(f"{where} is tracked by the repository above it, so its notes are that one's pages")
        for step in ([["init", "-q", "-b", "main"], ["add", "-Af"], ["commit", "-q", "-m", "dream: snapshot"]] if folder
                     else [["fetch", "-q", "origin", "main"]]):  # a folder's own repository, made when it has none,
            git(*step, cwd=repo, timeout=NET)  # holds all that changed since the last pass; a page is read on origin's main
        if not (base := rev(repo, "HEAD") if folder else rev(repo, "refs/remotes/origin/main") or rev(repo, "refs/heads/main")):
            raise Refused(f"{where} holds no notes" if folder else "there is no main to read the page on")
        if folder and (links := [one.partition("\t")[2] for one in git("ls-tree", "-r", base, cwd=repo).stdout.splitlines()
                                 if one.startswith("120000 ")]):  # the copy keeps each link, a file's, a folder's or dangling
            raise Refused(f"{where} holds symlinks ({', '.join(links[:3])}), which the model would write through, so no pass "
                          "was made: put the notes themselves in their place")
        _, found, last = suggested(repo, base) if page else ("", [], "")  # read after `read`: a run ending meanwhile is the next's
        if page and due and not found and (blob(repo, base, page).strip() in (
                "", (KERNEL.parent / "routines" / "lessons.md").read_text().strip()) or not git(
                "rev-list", "-1", "--first-parent", "--merges", f"{last}..{base}" if last else base, cwd=repo).stdout):
            return 0, ""  # no work since the last pass, so no pass
        routine = (page and blob(repo, base, "routines/dream.md")) or (KERNEL.parent / "routines" / "dream.md").read_text()
        if (starts := clean(repo, model or default_worker(repo)))[0] is None:
            raise Refused(NO_WORKER)
        if trouble := clone(repo, base, copy, lock):
            raise Refused(f"no clean copy of {page or where} could be made: {trouble}")
        brief = (f"{routine}\n\n## This pass\n\nThe notes: {', '.join(before := notes()) or 'none yet'}. The index: {index}, "
                 f"at most {INDEX[0]} lines and {INDEX[1]:,} bytes.\n" + "".join(f"Lesson: {one}\n" for one in found))
        mark(log, f"dreamer starts: {shlex.join(starts[0])}")
        ran = "it could not start"
        with contextlib.suppress(OSError):
            ran = run_bounded(starts[0], copy, log, timeout, [], stdin=brief.encode("utf-8", "replace"), env=starts[1])
        if ran != 0:  # it failed, or ran past its clock: nothing changes
            raise Refused(f"the model ran past its {timeout} s: {LIMIT}" if ran is None else
                          f"the model failed ({ran}), so nothing changed: {'.ceo/dream' if page else work}/dream.log")
        after = notes()
        lines = [sum(len(one.splitlines()) for one in side.values()) for side in (before, after)]
        size = (len((text := after.get(index, "")).splitlines()), len(text.encode(errors="surrogateescape")))
        why = ("it removes over 30 % of the lines" if 10 * lines[1] < 7 * lines[0] else "its index is over its cap"
               if size[0] > INDEX[0] or size[1] > INDEX[1] else "")
        said = (f"{page or where}: {lines[0]} lines, then {lines[1]} ({max(0, round(100 - 100 * lines[1] / (lines[0] or 1)))} % "
                f"removed); its index {index}: {size[0]} of {INDEX[0]} lines, {size[1]:,} of {INDEX[1]:,} bytes; lessons "
                f"suggested: {len(found)}")
        files = {name: after.get(name) for name in {*before, *after} if before.get(name) != after.get(name)}
        message = f"{PASS}: {said}\n\nLessons-read: {read}\n" if page else f"dream: {said}\n"
        commit = commit_files(repo, base, files, message, "the pass")  # every pass is a commit
        if applied := not why and (record_on_main(repo, "the lessons pass", lambda new: (files, message, "on main") if blob(
                repo, new, page) == blob(repo, base, page) else None) if page else traces(where, "--ignored", "-uall") == (base, "")
                and not git("merge", "-q", "--ff-only", commit, cwd=where).returncode and f"in {where}"):  # only notes still as judged
            return 0, f"{said}; applied {applied}"
        branch = f"dream/{slugify(Path(page).stem if page else where.name)}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S-%f}"
        git("update-ref", f"refs/heads/{branch}", commit, cwd=repo)
        return 3, f"{said}; held for the owner's word as the branch {branch}, for {why or 'the notes moved meanwhile'}"
    finally:
        shutil.rmtree(copy, ignore_errors=True)
        log.close()
