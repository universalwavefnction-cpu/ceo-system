"""The ground of every layer: constants, the refusals, the clock, git, processes, records, locks, settings."""
import argparse
import ast
import contextlib
import difflib
import fcntl
import glob
import hashlib
import io
import json
import mmap
import os
import plistlib
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tools import ledger

KERNEL = Path(__file__).resolve().with_name("ceo.py")  # what every tick and child runs
GRACE = 5  # seconds a stopped process gets to exit cleanly before it is killed
NET = 300  # seconds a call to the server gets before it counts as failed
RECORD = {"run": "result.json", "review": "review.json"}
ROLE = {"review": "reviewer"}
# What a stop can promise without containment machinery, said the same way wherever it is claimed.
LIMIT = ("the worker, its process group and every process still below it are stopped; "
         "a process that detached itself on purpose may keep running.")
HELD = {signal.SIGINT, signal.SIGTERM}
GB, DAY = 10**9, 86400  # the floors' and the sweep's gigabyte, in bytes; a day, in seconds
THREE_TODAY = "ended with an api error after three retries today: "  # a block heal() may retry
NO_WORKER = "no `worker` is set in ceo.toml, and no claude program is on PATH"  # nothing to start
CHANGES = ("status", "--porcelain", "--untracked-files=all")  # all uncommitted, whatever the config
class Refused(Exception):
    """The order cannot run; nothing has been created."""

class Stopped(Exception):
    """ceo.py itself was told to stop."""
def on_sigterm(signum, _frame):
    # One stop is enough; a later SIGTERM must not cut short what the first one set going.
    signal.signal(signum, signal.SIG_IGN)
    raise Stopped(signal.Signals(signum).name)
def now(timespec="seconds"):
    return datetime.now(timezone.utc).isoformat(timespec=timespec)
def slugify(stem):
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
def git(*args, cwd, **kw):
    # Kernel-created commits must not depend on a host's Git identity or change its configuration.
    identity = ["-c", "user.name=CEO kernel", "-c", "user.email=ceo@localhost"] if args[0] in (
        "commit", "commit-tree", "merge") else []
    try:
        return subprocess.run(["git", *identity, *args], cwd=cwd, capture_output=True, text=True, **kw)
    except subprocess.TimeoutExpired as err:
        return subprocess.CompletedProcess(err.cmd, 1, "", f"git ran past its {err.timeout}s clock")
def rev(repo, ref):
    """The commit ref names, or "" when there is none."""
    return git("rev-parse", "-q", "--verify", f"{ref}^{{commit}}", cwd=repo).stdout.strip()
def traces(wt, *more):
    """What work leaves in a worktree, a worker's answer: the commit it is at and what is uncommitted (more: status's)."""
    return rev(wt, "HEAD"), git("status", "--porcelain", *more, cwd=wt).stdout
def cuts(repo, slug, *only):
    """Sorted branch suffixes n from order/<slug>-cut-<n>; only supplies extra git for-each-ref filters."""
    names = git("for-each-ref", *only, "--format=%(refname:lstrip=2)", f"refs/heads/order/{slug}-cut-*",
                cwd=repo).stdout.split()
    return sorted(int(name.rsplit("-", 1)[1]) for name in names
                  if re.fullmatch(rf"order/{slug}-cut-\d+", name))
def on(repo, commit, *refs):
    """True when commit is on one of refs, of those that exist: no other ref is needed to keep it."""
    left = git("rev-list", "-n", "1", "--ignore-missing", commit, "--not", *refs, "--", cwd=repo)
    return left.returncode == 0 and not left.stdout.strip()
def toplevel():
    top = git("rev-parse", "--show-toplevel", cwd=Path.cwd())
    if top.returncode:
        raise Refused("the kernel must start inside a git repository")
    return Path(top.stdout.strip()).resolve()
def process_table():
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,pgid=,stat="], capture_output=True,
                         text=True, check=True).stdout
    rows = [row.split() for row in out.splitlines() if row.strip()]
    # A zombie has already exited and only waits to be collected, so it counts as gone.
    return [tuple(map(int, row[:3])) for row in rows if not row[3].startswith("Z")]
def tree(group, known):
    """Every live process in known, in group, or below one of them, including ones that left."""
    rows = process_table()
    found = {pid for pid, _, pgid in rows if pid in known or pgid == group}
    while children := {pid for pid, ppid, _ in rows if ppid in found} - found:
        found |= children
    return found
def signal_all(pids, group, sig):
    for kill, target in [(os.kill, pid) for pid in pids] + [(os.killpg, group)]:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            kill(target, sig)

def stop(proc):
    """Stop proc and everything it started. Return the ids of those still seen alive after."""
    group = proc.pid  # proc leads a session of its own, so its group id is its pid
    # Snapshot first: once a parent dies its children are re-parented and the tree is lost.
    # A reaped proc's pid is free for reuse, so it is only known while proc still runs.
    victims = tree(group, {proc.pid} if proc.poll() is None else set())
    if not victims:
        return []  # nothing to stop, and the group id may already belong to another process
    signal_all(victims, group, signal.SIGTERM)
    deadline = time.monotonic() + GRACE
    while time.monotonic() < deadline:
        # Keep looking: a process started while the tree is being stopped is stopped too.
        found = tree(group, victims)
        if not found:
            break
        victims |= found
        time.sleep(0.1)
    # Freeze before the kill, so nothing can start a process between the last look and the kill.
    frozen = set()
    while fresh := tree(group, victims) - frozen:
        signal_all(fresh, group, signal.SIGSTOP)
        frozen |= fresh
    signal_all(frozen, group, signal.SIGKILL)
    proc.wait()
    # Look once more: whatever is still seen alive now is reported, not hidden.
    deadline = time.monotonic() + GRACE
    while (left := tree(group, victims | frozen)) and time.monotonic() < deadline:
        signal_all(left, group, signal.SIGKILL)
        time.sleep(0.1)
    return sorted(left)

def run_bounded(cmd, cwd, log, timeout, survivors, stdin=None, keep=(), started=None, env=None,
                errors=subprocess.STDOUT):
    """Run cmd under its clock; return exit code or None, report its group to started and leftover PIDs to survivors."""
    # A session of its own lets the whole group be stopped without touching ceo.py itself.
    with subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                          stdout=log, stderr=errors, start_new_session=True,
                          pass_fds=keep, env=env) as proc:
        try:
            if started:
                started(proc.pid)
            proc.communicate(input=stdin, timeout=timeout)
            return proc.returncode
        except subprocess.TimeoutExpired:
            return None
        finally:
            # A signal to ceo.py must not cut a stop short; it is handled once the stop is done.
            signal.pthread_sigmask(signal.SIG_BLOCK, HELD)
            survivors.extend(stop(proc))
            signal.pthread_sigmask(signal.SIG_UNBLOCK, HELD)

def run_all(jobs, timeout, survivors, keep=(), started=None, env=None):
    """Run (cmd, cwd, log, stdin) entries concurrently under one clock; None marks each timed-out exit."""
    procs = []
    try:
        for cmd, cwd, log, stdin in jobs:
            with tempfile.TemporaryFile() as given:  # not a pipe: writing it waits for no reader
                given.write(stdin)
                given.seek(0)
                procs.append(subprocess.Popen(cmd, cwd=cwd, stdin=given, stdout=log,
                                              stderr=subprocess.STDOUT, start_new_session=True,
                                              pass_fds=keep, env=env))
            if started:
                started(procs[-1].pid)
        deadline = time.monotonic() + timeout
        while any(proc.poll() is None for proc in procs) and time.monotonic() < deadline:
            time.sleep(0.1)
        return [proc.poll() for proc in procs]
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, HELD)
        for proc in procs:
            survivors.extend(stop(proc))
        signal.pthread_sigmask(signal.SIG_UNBLOCK, HELD)
def mark(log, message):
    log.write(f"\n== ceo {now()}: {message}\n".encode())
    log.flush()

def api_error(log, since, began, code, answered):
    """Explain the last CLI result's api_error, or any exit within a minute without an answer; otherwise ""."""
    log.seek(since)
    ledger.book(log, raw := log.read())  # what it spent, read where every agent's end passes
    end = {}  # the CLI writes a result line for each turn of its session: the last says how it ended
    for line in re.findall(rb'^\{.*"type": ?"result".*$', raw, re.M):
        with contextlib.suppress(ValueError):
            found = json.loads(line)
            end = found if found.get("type") == "result" else end
    if end.get("terminal_reason") == "api_error":
        return " ".join(str(end.get("result") or "api_error").split())
    return (f"it exited {code} in its first minute without an answer"
            if time.monotonic() - began < 60 and not answered else "")
def retry(record, field, role, why):
    """Record an api-error retry, at most three daily; then block. Return the reason."""
    if sum(when[:10] == now()[:10] for when in record["retries"]) >= 3:
        record[field] = "blocked"
        return f"the {role} {THREE_TODAY}{why}"
    record[field] = "retry"
    record["retries"].append(now())
    return f"the {role} ended with an api error, so the next pass runs it again: {why}"

def api_blocked(repo, record):
    """(day, why) for a review blocked by three api errors that day; other blocks and cut orders are final."""
    said, reason = f"the reviewer {THREE_TODAY}", record.get("reason")
    retries = record.get("retries") or [""]
    day = retries[-1][:10]
    if (record.get("verdict") == "blocked" and isinstance(reason, str) and reason.startswith(said)
            and sum(when[:10] == day for when in retries) >= 3
            and not (repo / str(record.get("order"))).with_suffix(".cut").is_file()):
        return day, reason[len(said):]
    return None
def default_worker(repo=None):
    """ceo.toml's `worker`, given repo, else the command of claude-auto, which picks the Claude account that
    answers, while it is on PATH, else of plain claude, always one account, else None. A ceo.toml that cannot
    be read refuses where it is read next, as before."""
    with contextlib.suppress(Refused):
        if repo is not None and (worker := settings(repo).get("worker")):
            return str(worker)
    # stream-json makes the worker's progress visible in the log while it runs, not only at the end.
    flags = "-p --permission-mode bypassPermissions --output-format stream-json --verbose"
    program = next((name for name in ("claude-auto", "claude") if shutil.which(name)), None)
    return f"{program} {flags}" if program else None
def write_whole(path, text):
    """Write all of text or none of it, kept through a crash: a file that exists can be read."""
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as out:
        out.write(text)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, path)
def write_json(path, data):
    write_whole(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
def read_json(path, *none):
    """The object in path, or {} (none, when given) when there is none to read."""
    with contextlib.suppress(OSError, ValueError):
        if isinstance(data := json.loads(path.read_text(encoding="utf-8")), dict):
            return data
    return none[0] if none else {}
def to_make(path):
    """True while the record at path is still to be made: there is none, or it says retry."""
    found = read_json(path)
    return not path.exists() or "retry" in (found.get("state"), found.get("verdict"))
def run_recorded(repo, slug):
    """A retry reuses its worktree; swept orders never rerun; a folder holding only owner.json is no run."""
    ceo = repo / ".ceo"
    again = read_json(ceo / "runs" / slug / "result.json").get("state") == "retry"
    ran = {path.name for path in (ceo / "runs" / slug).glob("*")} != {"owner.json"}
    return any((ceo / where / slug).exists() and (where != "runs" or ran)
               for where in (("swept",) if again else ("runs", "wt", "swept")))

def event(repo, kind, slug, sentence, at=None):
    """Append one surface event to .ceo/events.jsonl, timestamped to the microsecond."""
    line = json.dumps({"time": at or datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                       "event": kind, "slug": slug, "sentence": " ".join(sentence.split())},
                      ensure_ascii=False)  # one line, whatever a reason quoted from git held
    try:
        with open(repo / ".ceo" / "events.jsonl", "a", encoding="utf-8") as events:
            events.write(line + "\n")
    except OSError as err:  # the work and its record stand without the line
        print(f"ceo: the event was not written: {line}: {err}", file=sys.stderr, flush=True)

def events(repo, name="events.jsonl"):
    """The events of .ceo/<name> in the order written, and how many of its lines cannot be read."""
    path, found = repo / ".ceo" / name, []
    lines = [line for line in path.read_bytes().splitlines() if line.strip()] if path.exists() else []
    for line in lines:
        with contextlib.suppress(ValueError, RecursionError):  # a line cut short, even inside a character, or nested too deep
            if isinstance(one := json.loads(line.decode("utf-8")), dict): found.append(one)
    return found, len(lines) - len(found)
def note(repo, slug, **fields):
    """Add fields to the run's result.json."""
    path = repo / ".ceo" / "runs" / slug / "result.json"
    write_json(path, {**read_json(path), **fields})

def claim(path):
    """Hold path's lock or return None; inherited descriptors retain it until the last holder exits, including crashes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd
def held(path):
    """True while some process holds the lock at path."""
    if not path.exists():
        return False
    fd = claim(path)
    if fd is not None:
        os.close(fd)
    return fd is None
def sign(lock):
    """Write this holder's PID for agents(); the write dates the lock to when the agent began."""
    os.ftruncate(lock, 0)
    os.pwrite(lock, f"{os.getpid()}\n".encode(), 0)
def boot():
    """Which boot of the machine this is: process ids recorded in another boot mean nothing."""
    linux = Path("/proc/sys/kernel/random/boot_id")
    return linux.read_text().strip() if linux.exists() else subprocess.run(
        ["/usr/sbin/sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout.strip()
def groups_alive(meta):
    """A live member of a group started in this boot, even after closing its lock descriptor and outliving its parents."""
    groups = set(meta.get("groups", [])) if meta.get("boot") == boot() else set()
    return bool(groups) and any(pgid in groups for _, _, pgid in process_table())
def home():
    """$CEO_HOME, ~/.ceo by default: the owner's folder, outside every repository."""
    return Path(os.environ.get("CEO_HOME") or "~/.ceo").expanduser()
def claude_home():
    """$CLAUDE_CONFIG_DIR when not empty, else ~/.claude, made absolute: Claude Code's folder, never written."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser().absolute()

def register(repo):
    """List repo, one path a line, in $CEO_HOME/projects.txt, which the ears and ceo.py open read."""
    path = home() / "projects.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+", encoding="utf-8", errors="surrogateescape") as listed:
            fcntl.flock(listed, fcntl.LOCK_EX)  # two runs starting at once list it once
            listed.seek(0)
            # Each line trimmed, as ceo.py open reads it; repo on a line of its own whatever the last byte.
            if str(repo) not in map(str.strip, (text := listed.read()).splitlines()):
                listed.write(("" if text.endswith("\n") or not text else "\n") + f"{repo}\n")
    except OSError as err:  # the work goes on unlisted: only the surfaces miss it
        print(f"ceo: {repo} is not listed in {path}: {err}", file=sys.stderr, flush=True)

def owner():
    """Read $CEO_HOME/OWNER.md (~/.ceo), creating it empty if absent; return its heading and text, or b"" if empty."""
    path = home() / "OWNER.md"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileExistsError):
            path.open("xb").close()  # made only when missing: the owner's own file is never touched
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as err:
        raise Refused(f"the owner's file {path} cannot be read: {err}") from None
    return f"# From the owner, for every project\n\n{text}\n\n".encode() if text else b""

def lessons_page(repo, commit):
    """LESSONS.md as commit holds it, what this project's work has taught, under its heading; b"" if none or empty."""
    text = blob(repo, commit, "LESSONS.md").strip()
    return f"# What this project's work has taught (LESSONS.md)\n\n{text}\n\n".encode(errors="surrogateescape") if text else b""
def shelf():
    """$CEO_HOME/SHELF.md, what several projects' work has taught, under its heading; b"" if absent or empty."""
    text = text_of(home() / "SHELF.md").strip()
    return f"# What several projects' work has taught ($CEO_HOME/SHELF.md)\n\n{text}\n\n".encode() if text else b""
def text_of(path):
    """The text of path, its line ends as written, a byte UTF-8 cannot read replaced; "" when there is no such file."""
    return path.read_bytes().decode("utf-8", "replace") if path.is_file() else ""

def settings(repo, name="ceo.toml"):
    """Parse repo/file (ceo.toml by default) as dotted keys: comments, tables, numbers, strings and booleans."""
    path, conf, table = repo / name, {}, ""
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    word = r"""(?:"(?:[^"\\]|\\.)*"|'[^']*')"""  # a string; a value may also be a list of them on one line
    for number, line in enumerate(text.splitlines(), 1):
        found = re.fullmatch(rf"""\s*(?:\[\s*([\w.-]+)\s*\]|([\w.-]+)\s*=\s*({word}"""
                             rf"""|\[\s*(?:{word}\s*,\s*)*(?:{word}\s*)?\]|[\w.+-]+))?\s*(?:#.*)?""", line)
        try:  # a line that is none of these leaves found None, and found[1] raises TypeError
            table = found[1] + "." if found[1] else table
            if found[2]:  # true and false as TOML writes them, a number or a string as Python reads it,
                # but a single-quoted one, TOML's literal string, as written: its backslash escapes nothing.
                value = re.sub(word, lambda s: repr(s[0][1:-1]) if s[0][0] == "'" else s[0], found[3])
                conf[table + found[2]] = (found[3] == "true" if found[3] in ("true", "false")
                                          else ast.literal_eval(value))
        except (TypeError, ValueError, SyntaxError):
            raise Refused(f"{name} cannot be read at line {number}: {line.strip()}") from None
    return conf
def origin(repo):
    """(url, owner/name) of origin, or None; a URL that is not GitHub's is its own name."""
    url = git("remote", "get-url", "origin", cwd=repo).stdout.strip()
    github = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/*$", url)
    return (url, github.group(1) if github else url) if url else None
def no_github(remote):
    """What origin()'s answer means for a project's orders: built and checked here, never merged."""
    return (f"origin {remote[0]} is not on GitHub" if remote else "this project has no origin") + (
        ": orders are built and checked here but can never merge")
def gh(name, *args, **kw):
    """Call gh for this repository from a fresh non-repository folder, preventing checkout changes; tests stub PATH."""
    with tempfile.TemporaryDirectory() as away:
        try:
            return subprocess.run(["gh", *args, "-R", name], cwd=away, capture_output=True,
                                  text=True, timeout=NET, **kw)
        except (OSError, subprocess.TimeoutExpired) as err:
            return subprocess.CompletedProcess(args, 1, "", f"gh did not finish: {err}")
def blob(repo, commit, path):
    """The text of path in commit, its bytes kept as they are; "" when there is none."""
    return git("cat-file", "blob", f"{commit}:{path}", cwd=repo, encoding="utf-8",
               errors="surrogateescape").stdout
def listed(repo, commit, *paths):
    """The files of commit, under paths when given, one path a line."""
    return git("ls-tree", "-r", "--name-only", commit, "--", *paths, cwd=repo).stdout

def commit_files(repo, base, files, message, what, *joined):
    """Commit {path: text or None} on base using an isolated index, joining parents; leave the folder untouched or refuse."""
    with tempfile.TemporaryDirectory() as away:
        given = {"cwd": repo, "encoding": "utf-8", "errors": "surrogateescape",  # the words' own bytes
                 "env": {**os.environ, "GIT_INDEX_FILE": os.path.join(away, "index")}}
        def must(*args, **kw):
            done = git(*args, **given, **kw)
            if done.returncode:
                raise Refused(f"{what} is not saved: git {args[0]}: {' '.join(done.stderr.split())}")
            return done.stdout.strip()
        must("read-tree", base)
        for path, text in files.items():
            if text is None:
                must("update-index", "--force-remove", "--", path)
                continue
            made = must("hash-object", "-w", "--stdin", input=text)
            must("update-index", "--add", "--cacheinfo", f"100644,{made},{path}")
        parents = [arg for one in (base, *joined) for arg in ("-p", one)]
        return must("commit-tree", must("write-tree"), *parents, "-m", message)

def follow(repo, commit, here, keep=()):
    """Move local main and its checkout from here to commit; delay stops until git finishes so no lock files remain."""
    on_main = git("symbolic-ref", "-q", "HEAD", cwd=repo).stdout.strip() == "refs/heads/main"
    args = ["git", *(["merge", "-q", "--ff-only", commit] if on_main
                     else ["update-ref", "refs/heads/main", commit, here])]
    with subprocess.Popen(args, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          pass_fds=keep) as proc:
        try:  # blocked once it started: git keeps the signals it is sent
            signal.pthread_sigmask(signal.SIG_BLOCK, HELD)
            out, err = proc.communicate()
        finally:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, HELD)
    return subprocess.CompletedProcess(args, proc.returncode, out, err)
def quoted(text):
    return json.dumps(text, ensure_ascii=False)
def code(text):
    """text as inline Markdown code, whatever backticks it holds."""
    ticks = "`" * (max(map(len, re.findall("`+", text)), default=0) + 1)
    return f"{ticks} {text} {ticks}" if "`" in text else f"`{text}`"
def unlock(path):
    """Remove a copy the night made, read-only or not; links in it are not followed."""
    if path.exists():
        subprocess.run(["chmod", "-R", "u+w", str(path)], capture_output=True)
        shutil.rmtree(path, ignore_errors=True)
