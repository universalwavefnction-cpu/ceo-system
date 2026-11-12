from ceo_orders import *  # Where and whether a model runs: the floors, a clean environment, worktrees, clones, checks.
# An agent CLI's flags that keep it to its folder; claude-auto is claude, whichever account it picks.
ISOLATION = {"claude": "--setting-sources project", "claude-auto": "--setting-sources project"}
ROUTING = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")  # the owner's own
ROUTING += ("OPENAI_BASE_URL", "GOOGLE_GEMINI_BASE_URL")  # Codex's and Gemini's; their API keys are their login
VENDORS = {"codex": "openai", "gemini": "google"}  # a program's vendor where vendor.<program> names none

def wait_recheck(repo):
    """Wait for the one-minute load to drop below the CPU count, bounded by recheck_wait seconds."""
    deadline = time.monotonic() + float(settings(repo).get("recheck_wait", 300))
    while (left := deadline - time.monotonic()) > 0 and os.getloadavg()[0] >= (os.cpu_count() or 1):
        time.sleep(min(1, left))

def completion_checks(repo, script, checks, timeout, survivors, **bounded):
    """Judge each (worktree, log, record), retrying only failures once and keeping each output tail."""
    for tree, _, record in checks:
        # Check committable work before tests add their own files; ignored output needs no commit.
        left = git(*CHANGES, "--", ":(top,exclude).ceo",
                   ":(glob,exclude)**/__pycache__/**", ":(glob,exclude)**/*.pyc", cwd=tree)
        record["uncommitted"] = bool(left.returncode or left.stdout.strip())
        record["done_when_runs"] = []
    for attempt in range(2):
        offsets = []
        for _, log, _ in checks:
            mark(log, f"done-when: attempt {attempt + 1}")
            offsets.append(log.tell())
        codes = run_all([(["bash", "-c", script], tree, log, b"") for tree, log, _ in checks],
                        timeout, survivors, **bounded)
        for (_, log, record), since, code in zip(checks, offsets, codes):
            log.seek(since)
            output = "\n".join(log.read().decode("utf-8", errors="replace").splitlines()[-40:])
            record["done_when_runs"].append({"exit_code": code, "output": output})
            record["done_when_exit"] = code
            mark(log, f"done-when exited {code}")
        checks = [check for check in checks if check[2]["done_when_exit"] != 0]
        if not checks or attempt:
            break
        for _, log, _ in checks:
            mark(log, "done-when: waiting for load to fall before its one re-run")
        wait_recheck(repo)

def free_ram():
    """Bytes of RAM free or reclaimable at once: Linux's MemAvailable, else (macOS) vm_stat's free,
    inactive and speculative pages."""
    with contextlib.suppress(OSError):
        meminfo = Path("/proc/meminfo").read_text()
        return int(re.search(r"^MemAvailable:\s*(\d+) kB", meminfo, re.M)[1]) * 1024
    out = subprocess.run(["/usr/bin/vm_stat"], capture_output=True, text=True).stdout
    pages = re.findall(r"Pages (?:free|inactive|speculative):\s*(\d+)", out)
    return int(re.search(r"page size of (\d+)", out)[1]) * sum(map(int, pages))

def quota(command, repo, timeout=60):
    """What a `quota_cmd` of ceo.toml prints: {"left": share left, 0 to 100}, or {"trouble": why not}."""
    with contextlib.suppress(OSError, subprocess.SubprocessError, ValueError):
        left = float(subprocess.run(["bash", "-c", str(command)], cwd=repo, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=timeout, check=True).stdout)
        if 0 <= left <= 100:
            return {"left": left}
    return {"trouble": f"`{command}` printed no number from 0 to 100"}

def vendor_of(conf, model, role="the model"):
    """The vendor of model's program, as `vendor.<program>` in ceo.toml (conf) names it, else VENDORS says
    by its name: codex is openai's, gemini google's, every other program claude's."""
    try:
        program = Path(shlex.split(model)[0]).name
        return conf.get(f"vendor.{program}", VENDORS.get(program, "claude"))
    except (ValueError, IndexError):  # a command from an order or ceo.toml, which no flag check saw
        raise Refused(f"{role} `{model}` is no command") from None

# The capacity refusals admit() and draw() raise, each from its start: such a start waits for room, never for a CEO.
FLOOR = re.compile(r"below the (?:disk|RAM) floor: |the \S+ quota is at \S+ %, under its floor of "
                   r"|the machine is swapping: |the machine's memory budget is spent: ")
def low_disk(repo):
    """Whether free disk is below ceo.toml's disk floor, as admit() reads it; False where admit() refuses the
    floor or ceo.toml itself, a pass of its own to stop there."""
    with contextlib.suppress(Refused):
        floor = settings(repo).get("disk_floor_gb", 20)
        return isinstance(floor, (int, float)) and shutil.disk_usage(repo).free / GB < floor
    return False

def admit(repo, model):
    """Refuse before anything starts while free disk or RAM is below its floor in ceo.toml, or the quota
    of the model's vendor (vendor_of's), as `quota_cmd.<vendor>` prints it, is
    under its floor. A run already alive is never stopped by a floor."""
    conf = settings(repo)
    floors = [conf.get(key, default) for key, default in (("disk_floor_gb", 20), ("ram_floor_gb", 4),
                                                           ("quota_floor_pct", 5))]
    if not all(isinstance(floor, (int, float)) for floor in floors):
        raise Refused("a floor in ceo.toml is not a number")
    disk, ram = shutil.disk_usage(repo).free / GB, free_ram() / GB
    if disk < floors[0] or ram < floors[1]:
        raise Refused(f"below the {'disk' if disk < floors[0] else 'RAM'} floor: disk {disk:.1f} GB free "
                      f"(floor {floors[0]:g} GB), RAM {ram:.1f} GB free (floor {floors[1]:g} GB)")
    vendor = vendor_of(conf, model) if model else ""
    command = conf.get(f"quota_cmd.{vendor}")
    if not command:
        return
    read = quota(command, repo)
    if "trouble" in read:
        raise Refused(f"the {vendor} quota cannot be read: {read['trouble']}")
    if read["left"] < floors[2]:
        raise Refused(f"the {vendor} quota is at {read['left']:g} %, under its floor of {floors[2]:g} %")

def pressure():
    """Why the machine swaps, or "": macOS's own memory pressure level, else Linux's stall reading. Swap in
    use is no sign alone: macOS keeps gigabytes of it long after the pressure that wrote it has passed."""
    if sys.platform == "darwin":  # 1 normal, 2 warn, 4 critical
        level = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                               capture_output=True, text=True).stdout.strip()
        return {"2": "macOS memory pressure is warn", "4": "macOS memory pressure is critical"}.get(level, "")
    with contextlib.suppress(OSError, TypeError, ValueError):  # a Linux without the reading never refuses
        some = float(re.search(r"^some avg10=([\d.]+)", Path("/proc/pressure/memory").read_text(), re.M)[1])
        return f"some task waited on memory {some:g} % of the last 10 s" if some >= 10 else ""
    return ""

def draw(repo, kind, slug, shares=1):
    """Draw shares GB for an agent from the machine's one memory budget, memory_budget_gb in $CEO_HOME/
    machine.toml (its RAM by default), one CEO at a time: the share's descriptor, held while this process
    lives, or None when the tick that started it drew it. Refused while the machine swaps, unless the
    project's RAM floor is 0, or while the agents at work hold all of the budget."""
    mine = home() / "memory" / f"{hashlib.sha256(str(repo).encode()).hexdigest()[:12]}.{kind}.{slug}"
    if held(mine):  # handed down: tick and child decide once, so a refused start spawns nothing
        return None
    mine.parent.mkdir(parents=True, exist_ok=True)
    with open(home() / "memory.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # every CEO of this machine reads and draws the budget in turn
        budget = settings(home(), "machine.toml").get(
            "memory_budget_gb", os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / GB)
        drawn = sum(int(share.read_text() or 0) for share in mine.parent.iterdir() if held(share))
        if settings(repo).get("ram_floor_gb", 4) and (why := pressure()):
            raise Refused(f"the machine is swapping: {why}")
        if not isinstance(budget, (int, float)):
            raise Refused("memory_budget_gb in $CEO_HOME/machine.toml is not a number")
        if drawn + shares > budget:
            raise Refused(f"the machine's memory budget is spent: its agents hold {drawn} of {budget:g} GB "
                          f"(memory_budget_gb in $CEO_HOME/machine.toml), and this {kind} needs {shares}")
        share = claim(mine)
        mine.write_text(str(shares))
        return share

def isolation(repo, model):
    """The flags, last on its command line, that keep model's CLI to its folder: claude's, or
    isolation.<program> in ceo.toml; None for a program that is no agent CLI and has no login."""
    program = Path(shlex.split(model)[0]).name
    flags = settings(repo).get(f"isolation.{program}", ISOLATION.get(program))
    return None if flags is None else shlex.split(str(flags))

def clean(repo, model):
    """The command line (None without a model) and environment model starts with, to see only this
    repository: its CLI's isolation, no Anthropic routing unless ceo.toml's keep_env names it."""
    keep = str(settings(repo).get("keep_env", "")).split()
    env = {name: value for name, value in os.environ.items() if name in keep or name not in ROUTING}
    return model and shlex.split(model) + (isolation(repo, model) or []), env

def dry_environment(repo):
    """A base proof borrows only the kept tick's named environment, never its Python installation."""
    named = ("PATH", "HOME", "CEO_HOME", *str(settings(repo).get("keep_env", "")).split())
    env = {key: os.environ[key] for key in named
           if key in os.environ and key not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    if virtual := os.environ.get("VIRTUAL_ENV"):
        virtual = Path(os.path.abspath(virtual))
        root = virtual.resolve()
        env["PATH"] = os.pathsep.join(part for part in env.get("PATH", "").split(os.pathsep)
                                     if not (Path(os.path.abspath(part)).is_relative_to(virtual)
                                             or Path(part).resolve().is_relative_to(root)))
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="CEO kernel", GIT_COMMITTER_NAME="CEO kernel",
               GIT_AUTHOR_EMAIL="ceo@localhost", GIT_COMMITTER_EMAIL="ceo@localhost")
    return env

def dry_run(repo, slug, base, script, log, timeout, survivors, lock, **bounded):
    """Run the first before-worker proof once in clone()'s clean copy, keeping its exit and tail."""
    copy = repo / ".ceo" / f"{slug}.dry-run"
    record = {"base": base, "exit_code": None, "output": ""}
    mark(log, f"done-when, on its base {base} in a clean clone: it must fail")
    try:
        if trouble := clone(repo, base, copy, lock):
            record["output"] = f"no copy of commit {base} could be made: {trouble}"
        else:
            since = log.tell()
            record["exit_code"] = run_bounded(["bash", "-c", script], copy, log, timeout, survivors,
                                                env=dry_environment(repo), **bounded)
            log.seek(since)
            record["output"] = "\n".join(log.read().decode("utf-8", errors="replace").splitlines()[-40:])
    except OSError as err:
        record["output"] = f"the clean copy's done-when could not run: {err}"
    finally:
        shutil.rmtree(copy, ignore_errors=True)
    return record

def crew(repo, text, worker=None, reviewer=None):
    """Resolve the named worker and its cross-vendor reviewer."""
    named = parse_order(text)[2]
    if not named:
        return worker, reviewer
    conf = settings(repo)
    worker = str(conf.get(f"workers.{named}", named))
    vendor = vendor_of(conf, worker, "the order's worker")
    reviewer = str(conf.get(f"reviewers.{named}", conf.get(f"reviewers.{vendor}", worker)))
    if vendor_of(conf, reviewer, "its reviewer") == vendor and conf.get("same_vendor_review") is not True:
        raise Refused(f"the order's worker and its reviewer are both of the vendor {vendor}: map {vendor} "
                      "to another vendor's reviewer under [reviewers] in ceo.toml, or set "
                      "same_vendor_review = true")
    return worker, reviewer

def excluded(repo):
    """The sparse-checkout patterns that keep the folders ceo.toml's [checkout] exclude lists, evidence
    no test reads, out of a checkout; git still carries them. [] keeps a checkout whole."""
    folders = settings(repo).get("checkout.exclude", [])
    folders = folders if isinstance(folders, list) else [str(folders)]  # one may be named without brackets
    # Escaped, git reads *, ?, [ and \ in a name as themselves: orders/proof[1] keeps orders/proof1.
    literal = [re.sub(r"([*?[\\])", r"\\\1", folder.strip("/")) for folder in folders]
    patterns = [f"!/{folder}/" for folder in literal if folder]
    return ["/*", *patterns] if patterns else []

def checkout(path, patterns, *how):
    """The git steps that check out path, made with --no-checkout, as how says, without what patterns
    keep out: sparse is a matter of the checkout, so no commit or merge made in it changes."""
    sparse = [["-C", str(path), "sparse-checkout", "set", "--no-cone", *patterns]] if patterns else []
    return [*sparse, ["-C", str(path), "checkout", "-q", *how]]

def worktree(repo, path, ref, *options, lock):
    """git worktree add, with its options, of path at ref, checked out as excluded() says; the git that
    failed, else the last that ran."""
    patterns = excluded(repo)
    add = ["worktree", "add", "-q", *options, *(["--no-checkout"] if patterns else []), str(path), ref]
    for step in (add, *(checkout(path, patterns) if patterns else ())):
        done = git(*step, cwd=repo, pass_fds=(lock,))
        if done.returncode:
            break
    return done

def clone(repo, commit, path, lock, url=None, door=False):
    """Make path a clean copy of commit, from url (origin) or the repository; return what went wrong,
    or "". A clone shares no branches with the repository; only the door's keeps origin, to push."""
    shutil.rmtree(path, ignore_errors=True)
    common = str((repo / git("rev-parse", "--git-common-dir", cwd=repo).stdout.strip()).resolve())
    main = "" if door else rev(repo, "refs/remotes/origin/main")
    for step in (["clone", "-q", "--no-checkout", *(["--reference", common, url] if url
                                                     else ["--shared", common]), str(path)],
                 *checkout(path, excluded(repo), "--detach", commit),
                 *([] if door else [["-C", str(path), "remote", "remove", "origin"]]),
                 # Removing origin deletes its tracking refs; restore the fetched comparison bases.
                 *([["-C", str(path), "update-ref", ref, main] for ref in
                    ("refs/heads/main", "refs/remotes/origin/main")] if main else [])):
        # git holds the caller's lock too: a git that outlives a killed caller keeps the next out.
        done = git(*step, cwd=repo, pass_fds=(lock,), timeout=NET)
        if done.returncode:
            return done.stderr.strip() or f"git {step[-2]} failed"
    return ""
