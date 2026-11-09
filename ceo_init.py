from ceo_base import *  # A project's setup: init, open, and the CEO's session.
# What `init` gives a project: the vision the owner writes, and the words of another agent system's
# CLAUDE.md or AGENTS.md, which would bind every worker of this one.
VISION_SKELETON = ("# Vision\n\n## What this project is for\n\n## What done looks like\n\n"
                   "## What it must never do\n")
OTHER_SYSTEM = re.compile(r"\b(herdr|chief|crew|desk-post)s?\b", re.I)

def open_project(project):
    """Find one listed repository and open or focus its visible CEO pane."""
    path = home() / "projects.txt"
    try:
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError as err:
        raise Refused(f"cannot read {path}: {err}; add a project with ceo.py init <path>") from None
    listed = list(dict.fromkeys(line.strip() for line in text.splitlines() if line.strip()))
    matches = [Path(entry) for entry in listed
               if (project == entry or project == Path(entry).name) and Path(entry).is_dir()]
    if len(matches) != 1:
        reason = "ambiguous" if matches else "unknown"
        names = "\n".join(listed) or "(none); add a project with ceo.py init <path>"
        raise Refused(f"{reason} project {project!r}; projects in {path}:\n{names}")
    repo = matches[0]
    name = f"ceo-{repo.name}"
    try:
        found = subprocess.run(["herdr", "agent", "list"], capture_output=True, text=True)
        if found.returncode:
            raise Refused(f"herdr agent list exited {found.returncode}: {found.stderr.strip()}")
        panes = json.loads(found.stdout)["result"]["agents"]
        if not isinstance(panes, list) or any(not isinstance(row, dict) or
                                            (row.get("name") is not None and
                                             not isinstance(row["name"], str)) for row in panes):
            raise ValueError("expected a list of herdr agents with string or null names")
        command = (["herdr", "agent", "focus", name] if any(row.get("name") == name for row in panes) else
                   ["herdr", "agent", "start", name, "--cwd", str(repo), "--", sys.executable, str(KERNEL), "session"])
        return subprocess.run(command, cwd=repo).returncode
    except (OSError, ValueError, KeyError, TypeError) as err:
        raise Refused(f"cannot open the CEO pane: {err}") from None

def session(again=False):
    """Open one live CEO per project with SESSION.md; direct Claude continues only with this folder's history,
    Codex resumes its last session here, and Gemini, with no flag to take SESSION.md, opens without it."""
    repo, lock = toplevel(), None
    try:
        command = shlex.split(settings(repo).get("session", "claude"))
        if not command: raise Refused("session is empty")
        lock = claim(repo / ".ceo" / "session.lock")
        if lock is None: raise Refused(f"a CEO session is already running in {repo}")
        if again and Path(command[0]).name == "claude":
            config = claude_home() / "projects"
            again = any(path.is_file() for path in (config / re.sub(r"[^a-zA-Z0-9]", "-", str(repo))).glob("*.jsonl"))
        source = repo / "SESSION.md" if (repo / "SESSION.md").exists() else KERNEL.parent / "SESSION.md"
        prompt, program = source.read_text(encoding="utf-8"), Path(command[0]).name
        # Codex has no system-prompt flag; -c reads its value as TOML, and a JSON string is a TOML string.
        given = "developer_instructions=" + json.dumps(prompt, ensure_ascii=False)
        flags = {"codex": [*["resume", "--last"] * again, "-c", given], "gemini": []}.get(
            program, [*["--continue"] * again, "--append-system-prompt", prompt])
        if program == "gemini":
            print("ceo: gemini has no flag to take SESSION.md, so this session opens new and without it",
                  file=sys.stderr, flush=True)
        return subprocess.run([*command, *flags], cwd=repo, pass_fds=(lock,)).returncode  # held even if the wrapper dies
    except (OSError, UnicodeDecodeError) as err: raise Refused(f"the session cannot start: {err}") from None
    except (AttributeError, TypeError, ValueError) as err: raise Refused(f"session is not a valid command: {err}") from None
    finally:
        if lock is not None: os.close(lock)

def commit_alone(repo, paths, line, message):
    """Commit the files at paths as they are and, given line, .gitignore as HEAD has it with line added,
    from an index of its own: nothing else the owner has changed or staged goes in, nor their own new
    lines of .gitignore. The owner's index then holds these paths as committed. Return why git made no
    commit, else ""."""
    as_given = {"cwd": repo, "encoding": "utf-8", "errors": "surrogateescape"}  # the files' own bytes
    with tempfile.TemporaryDirectory() as scratch:
        alone = {**as_given, "env": {**os.environ, "GIT_INDEX_FILE": os.path.join(scratch, "index")}}
        steps = [["read-tree", "HEAD"]] * bool(rev(repo, "HEAD")) + [["add", "--", *paths]]
        if line:
            base = git("cat-file", "blob", "HEAD:.gitignore", **as_given).stdout  # "" without one
            text = base + "\n" * bool(base and not base.endswith("\n")) + line
            blob = git("hash-object", "-w", "--stdin", input=text, **as_given).stdout.strip()
            steps.append(["update-index", "--add", "--cacheinfo", f"100644,{blob},.gitignore"])
        for step in [*steps, ["commit", "-q", "-m", message]]:  # the owner's hooks run as on any commit
            done = git(*step, **alone)
            if done.returncode:
                said = " ".join((done.stderr or done.stdout).split())
                return said or f"git {step[0]} exited {done.returncode}"
    files = [*paths, *[".gitignore"] * bool(line)]
    reset = git("reset", "-q", "--", *files, **as_given)
    if reset.returncode:  # the commit stands; the owner's index, without its files, would delete them
        said = (reset.stderr.strip().splitlines() or [f"git reset exited {reset.returncode}"])[0]
        print(f"ceo: warning: committed, but git left the index as it was ({said}): `git reset -q -- "
              f"{shlex.join(files)}` here mends it, or the next commit deletes them.", file=sys.stderr)
    return ""

def init(path):
    """Give the git repository at path, else the one here, its CEO, the kernel staying one tree run by
    path: VISION.md, the kernel's orders/README.md, CEO.md and lessons page, and ceo.toml where missing,
    and .ceo/ in .gitignore, in one commit on its branch; list it and print the next steps. Refused, with
    nothing kept, at a path not a repository's top, on a detached HEAD, while git is midway through
    something, where VISION.md exists, or when a file cannot be written or git makes no commit."""
    folder = Path(path or ".").resolve()
    top = git("rev-parse", "--show-toplevel", cwd=folder) if folder.is_dir() else None
    repo = Path(top.stdout.strip()).resolve() if top and top.returncode == 0 else None
    # Without a path, the repository here, as for every other command; a path given must be its top: a
    # folder inside a repository, the owner's home say, is no project of its own. As git names it, so
    # that run and tick list the same line.
    if repo is None or path and not repo.samefile(folder):
        raise Refused(f"{folder} is not {'the top folder of' if path else 'in'} a git repository")
    if git("symbolic-ref", "-q", "HEAD", cwd=repo).returncode:
        raise Refused(f"{repo} has no branch checked out to commit on")
    # Midway through a merge, a pick, a revert or a rebase, git folds its state into any commit (a second
    # parent, the pick's author); any commit deletes the message git prepared for the owner's own, all a
    # squash merge or a `cherry-pick -n` leaves; an index another git process holds would never learn of it.
    gitdir = Path(git("rev-parse", "--absolute-git-dir", cwd=repo).stdout.strip())
    for busy in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply",
                 "SQUASH_MSG", "MERGE_MSG", "index.lock"):
        if (gitdir / busy).exists():
            raise Refused(f"git is midway through something in {repo} ({gitdir / busy} exists): "
                          "finish it first")
    # As JSON, with the line breaks it leaves raw escaped too: settings splits ceo.toml by str.splitlines.
    quoted = json.dumps(repo.name, ensure_ascii=False).translate({0x85: r"\u0085", 0x2028: r"\u2028",
                                                                  0x2029: r"\u2029"})
    files = {"orders/README.md": (KERNEL.parent / "orders" / "README.md").read_text(encoding="utf-8"),
             "CEO.md": (KERNEL.parent / "CEO.md").read_text(encoding="utf-8"),
             "LESSONS.md": (KERNEL.parent / "routines" / "lessons.md").read_text(encoding="utf-8"),
             "ceo.toml": f"name = {quoted}\n"}
    ignore, had_orders = repo / ".gitignore", (repo / "orders").is_dir()
    old = ignore.read_bytes() if ignore.is_file() else None
    try:  # the check and the write in one, so nothing is written over the owner's own vision
        with open(repo / "VISION.md", "x", encoding="utf-8") as vision:
            vision.write(VISION_SKELETON)
    except FileExistsError:
        raise Refused(f"{repo} has a VISION.md already") from None
    made, line = ["VISION.md"], ""
    try:
        for name, text in files.items():  # a file the owner has already stays as it is
            (repo / name).parent.mkdir(exist_ok=True)
            with contextlib.suppress(FileExistsError), open(repo / name, "x", encoding="utf-8") as out:
                out.write(text)
                made.append(name)
        # As git reads it: a line with a space before it is another pattern, so .ceo/ is added after it.
        line = "" if {b".ceo", b".ceo/", b"/.ceo", b"/.ceo/"} & set((old or b"").splitlines()) else ".ceo/\n"
        if line:
            with open(ignore, "ab") as out:  # the owner's own lines stay as they are
                out.write(b"\n" * bool(old and not old.endswith(b"\n")) + line.encode())
    except OSError as err:  # a file named orders, a folder it may not write in
        why = f"nothing is kept: {err.strerror}: {err.filename}"
    else:
        written = made + [".gitignore"] * bool(line)
        why = commit_alone(repo, made, line, f"ceo.py init: {', '.join(written)}")
        why = why and f"git made no commit, so nothing is kept: {why}"
    if why:  # undone, so that init runs again once the owner has dealt with what was said
        for name in made:
            (repo / name).unlink(missing_ok=True)
        if not had_orders:
            with contextlib.suppress(OSError):
                (repo / "orders").rmdir()
        if line and old is None:
            ignore.unlink(missing_ok=True)
        elif line:
            ignore.write_bytes(old)
        raise Refused(why)
    register(repo)
    kernel, here = f"python3 {shlex.quote(str(KERNEL))}", shlex.quote(str(repo))
    print(f"ceo: {repo} has its CEO: {', '.join(written)} committed. Next:\n1. Write VISION.md: "
          "what this project is for, what done looks like, what it must never do; commit it and, with an "
          "origin, push it: every worker starts from origin's main.\n2. Write the first order, as "
          "orders/README.md says, a draft in drafts/orders/001-<name>.md, and file it: move it into orders/ "
          f"and commit.\n3. Start the tick: cd {here} && {kernel} tick --every 60; then {kernel} keep, "
          "there, keeps it alive.\nceo: Claude Code keeps its transcripts 30 days; to keep them 90, for this project's "
          f'memory, add "cleanupPeriodDays": 90 to {claude_home() / "settings.json"}: the kernel never sets it.', flush=True)
    # A file every worker reads, written for another agent system: said, never in the way, for rewriting
    # it is the owner's.
    found = [(name, word.lower()) for name in ("CLAUDE.md", ".claude/CLAUDE.md", "AGENTS.md")
             if (repo / name).is_file()
             for word in OTHER_SYSTEM.findall((repo / name).read_text(encoding="utf-8", errors="replace"))]
    if found:
        names = " and ".join(dict.fromkeys(name for name, _ in found))
        words = ", ".join(dict.fromkeys(word for _, word in found))
        print(f"ceo: warning: every worker here is bound by {names}, written for another agent system "
              f"({words}); rewrite before the first order.", file=sys.stderr)
    return 0
