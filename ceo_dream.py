from ceo_agents import *  # The cleaner: notes snapshotted in git, judged in a copy by routines/dream.md, applied or held.
PASS, INDEX, SHELF = "Lessons pass", (200, 25_000), 5_000  # a page's pass's title on main; an index's cap; the shelf's
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
def claude_dreams():
    """True while Claude Code's own dreaming is on, read from its files, never written: its flag tengu_onyx_plover, cached
    in .claude.json, is enabled or available, and its autoDreamEnabled in settings.json (unset: the flag's enabled) is true."""
    with contextlib.suppress(AttributeError, KeyError, OSError, TypeError):
        flag = read_json((claude_home() if os.environ.get("CLAUDE_CONFIG_DIR") else Path.home()) / ".claude.json")[
            "cachedGrowthBookFeatures"]["tengu_onyx_plover"]
        return (flag.get("enabled") is True or flag.get("available") is True) and (read_json(path, None) if (
            path := claude_home() / "settings.json").exists() else {}).get("autoDreamEnabled", flag.get("enabled")) is True
    return False  # an unreadable settings.json is None, which has no .get: the AttributeError counts it as off
def dream(path, model=None, timeout=1800, due=False):
    """One pass of the cleaner over path, a folder of notes or a page its project tracks: (0 applied or 3 held, its line).
    due, a page's pass runs only on work since the last one, else (0, ""): a lesson suggested since, or an order merged
    on main since (a merge commit on it, as the door makes) while the page holds more than the paragraph init seeds."""
    where = Path(path).resolve()
    top = git("-C", str(where.parent), "rev-parse", "--show-toplevel", cwd=None).stdout.strip()
    if not (folder := where.is_dir()) and not top:
        raise Refused(f"{path} is no folder of notes, nor a page in a git repository")
    if folder and claude_dreams():  # Claude Code consolidates its own notebooks, so one cleaner at a time, before any write
        return 0, "Claude Code's own dreaming is on, so this pass steps aside"
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
        shared, shelf_read, others = home() / "SHELF.md", "", copy / ".shelf"  # data beside a page, never committed
        same = lambda one: git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=one).stdout  # its repository
        if page:  # the shared shelf as this pass read it, empty when there is none, CRLF kept, and each other listed
            others.mkdir(exist_ok=True)  # project's page: never one of this project's own folders or worktrees
            (others / "SHELF.md").write_text(shelf_read := text_of(shared), encoding="utf-8", newline="")
            for n, line in enumerate(map(str.strip, text_of(home() / "projects.txt").splitlines()), 1):
                if line and (other := Path(line)).is_dir() and same(other) != same(repo) and (text := (main := rev(
                        other, "refs/remotes/origin/main") or rev(other, "refs/heads/main")) and blob(other, main, "LESSONS.md")).strip():
                    (others / f"{n}-{other.name}.md").write_bytes(text.encode(errors="surrogateescape"))
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
        lines = [sum(len(text.splitlines()) for name, text in side.items() if page or not name.startswith("sessions/"))
                 for side in (before, after)]  # a folder's session logs are no notes; a page asked for is counted wherever it is
        size, shelf_left = (len((text := after.get(index, "")).splitlines()), len(text.encode(errors="surrogateescape"))), (
            text_of(others / "SHELF.md") if page else "")  # the shelf the model left, checked whether or not it changed
        old, new = (side.get(index, "").splitlines() for side in (before, after))  # a link to a shelf with text keeps what it took
        lines[1] += sum(max(0, i2 - i1 - j2 + j1) for _, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new).get_opcodes()
                        if shelf_left.strip() and any(one.startswith("- On the shared shelf:") for one in new[j1:j2]))
        why = ("it removes over 30 % of the lines" if 10 * lines[1] < 7 * lines[0] else "its index is over its cap"
               if size[0] > INDEX[0] or size[1] > INDEX[1] else "the shared shelf loses over 30 % of its lines" if 10 * len(
                   shelf_left.splitlines()) < 7 * len(shelf_read.splitlines()) else f"the shared shelf is over {SHELF:,} bytes"
               if len(shelf_left.encode()) > SHELF else "")
        said = (f"{page or where}: {lines[0]} lines, then {lines[1]} ({max(0, round(100 - 100 * lines[1] / (lines[0] or 1)))} % "
                f"removed); its index {index}: {size[0]} of {INDEX[0]} lines, {size[1]:,} of {INDEX[1]:,} bytes; lessons "
                f"suggested: {len(found)}")
        files = {name: after.get(name) for name in {*before, *after} if before.get(name) != after.get(name)}
        message = f"{PASS}: {said}\n\nLessons-read: {read}\n" if page else f"dream: {said}\n"
        commit = commit_files(repo, base, files, message, "the pass")  # every pass is a commit
        with recording(home(), "SHELF.lock") if page else contextlib.nullcontext(), contextlib.ExitStack() as undo:
            why = why or ("the shared shelf moved meanwhile" if page and text_of(shared) != shelf_read else "")  # one page's
            if applied := not why and (record_on_main(repo, "the lessons pass", lambda new: (files, message, "on main") if blob(
                    repo, new, page) == blob(repo, base, page) and (shelf_left == text_of(shared) or not undo.callback(*((
                    write_whole, shared, shelf_read) if shelf_read else (shared.unlink,))) or not write_whole(shared, shelf_left))
                    else None) if page else traces(where, "--ignored", "-uall") == (base, "")
                    and not git("merge", "-q", "--ff-only", commit, cwd=where).returncode and f"in {where}"):
                undo.pop_all()  # pass at a time looks at the shelf and writes it, just before its page, still as judged:
                return 0, f"{said}; applied {applied}"  # never a lone link, and one whose page does not land undoes it
        branch = f"dream/{slugify(Path(page).stem if page else where.name)}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S-%f}"
        git("update-ref", f"refs/heads/{branch}", commit, cwd=repo)
        return 3, f"{said}; held for the owner's word as the branch {branch}, for {why or 'the notes moved meanwhile'}"
    finally:
        shutil.rmtree(copy, ignore_errors=True)
        log.close()
