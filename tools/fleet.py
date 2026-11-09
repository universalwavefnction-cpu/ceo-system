#!/usr/bin/env python3
"""The board pull: every CEO's status file, from the owner's own list, into $CEO_HOME/board, and the page made from
them (standard library only; it imports no kernel module, so a fault elsewhere in the kernel cannot stop the board).

    tools/fleet.py pull
    tools/fleet.py install <folder>

pull reads $CEO_HOME/fleet.json (CEO_HOME defaults to ~/.ceo), outside every repository; tools/fleet.example.json
shows one. Its `ceos` come in the page's order, each with a `name`, the `host` the page shows and the `folder` of its
project. A local entry runs this kernel's ceo.py there, with its optional `python` (the python running this tool by
default). An entry with `ssh`, a target as ssh takes it, so an alias of ~/.ssh/config works, runs
`cd <folder> && <python> <kernel> board ...` on that host: `kernel`, the path of ceo.py there, is required, `python`
is python3 by default, and all three pass as written, so a leading ~ expands there. `timeout` is the seconds any one
command may take, 120 by default; each of `copies`, {"ssh": ..., "folder": ...}, gets the status files by scp, for a
board served on another host. A CEO's file is replaced, whole, only by output that is a status file of that CEO;
else its last good file stays and one line says why. A file there that fails the same test, as an older pull could
leave one, is left off the page, which would refuse it whole. The pull exits 0 once the page is written, whatever CEO
or copy failed; it fails when fleet.json cannot be pulled from (then nothing is pulled or made), when no CEO has a
status file to render, and when the page cannot be made.

install <folder> writes <folder>/ceo-kernel.fleet.plist, the LaunchAgent that runs pull every 300 s with this
CEO_HOME and logs to $CEO_HOME/board/fleet.log, and prints the launchctl line that loads it (when a job of that
label is loaded already, unload it first: launchctl bootout gui/$(id -u)/ceo-kernel.fleet); it never runs launchctl.
On Linux, cron runs pull instead: `crontab -e`, then one line, with this kernel's path and a python3 of cron's PATH:

    */5 * * * * CEO_HOME=$HOME/.ceo python3 $HOME/ceo-kernel/tools/fleet.py pull >> $HOME/.ceo/board/fleet.log 2>&1

The shell opens that log before pull runs, so make the board folder first: mkdir -p ~/.ceo/board.
"""
import argparse
import contextlib
import json
import os
import plistlib
import shlex
import signal
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
KERNEL = HERE.parent / "ceo.py"  # this kernel's own: every local entry and the page run it
EXAMPLE = HERE / "fleet.example.json"
LABEL = "ceo-kernel.fleet"
SSH = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=10")  # never a prompt; a host that does not answer fails in 10 s
TIMEOUT = 120  # seconds any one command may take, unless fleet.json says otherwise
GRACE = 5  # seconds a stopped command's process group gets to exit before it is killed
PULLS = 8  # CEOs pulled at once, so a hung host holds up only its own


class Wrong(Exception):
    """fleet.json cannot be pulled from; nothing has been pulled or made."""


def home():
    """$CEO_HOME, ~/.ceo by default, as ceo_base.home() has it, made absolute: the page and the copies run from inside
    the board folder, where a relative one would name another."""
    return Path(os.path.abspath(Path(os.environ.get("CEO_HOME") or "~/.ceo").expanduser()))


def text(one, key, where):
    """one[key], a string that is not blank and holds no NUL, which no command can take; else what where lacks."""
    if not (isinstance(one.get(key), str) and one[key].strip() and "\0" not in one[key]):
        raise Wrong(f"{where} has no {key}, a string")
    return one[key]


def config(path):
    """The CEOs, the copies and the time limit of the fleet file at path, checked whole before anything runs."""
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except OSError as why:
        raise Wrong(f"it cannot be read: {why.strerror}") from None
    except ValueError as why:
        raise Wrong(f"it is not JSON: {why}") from None
    if not (isinstance(found, dict) and isinstance(found.get("ceos"), list) and found["ceos"]):
        raise Wrong("it lists no CEO: it is an object whose `ceos` is a list of entries")
    limit = found.get("timeout", TIMEOUT)
    if isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit <= 0:
        raise Wrong("its timeout is no positive number of seconds")
    ceos = []
    for at, one in enumerate(found["ceos"], 1):
        if not isinstance(one, dict):
            raise Wrong(f"its CEO {at} is no object")
        name, host, folder = (text(one, key, f"its CEO {at}") for key in ("name", "host", "folder"))
        if Path(name).name != name or name.startswith("."):
            raise Wrong(f"its CEO {at} is named {name!r}, which cannot name a file in the board folder")
        if name in (other["name"] for other in ceos):
            raise Wrong(f"it lists {name} twice, and a CEO has one file")
        entry = dict(name=name, host=host, folder=folder)
        if "ssh" in one:
            entry.update(ssh=text(one, "ssh", name), kernel=text(one, "kernel", name))
            if entry["ssh"].startswith("-"):
                raise Wrong(f"{name}'s ssh target is an option, {entry['ssh']}")
        default = "python3" if "ssh" in one else sys.executable
        entry["python"] = text(one, "python", name) if "python" in one else default
        ceos.append(entry)
    copies = found.get("copies", [])
    if not isinstance(copies, list):
        raise Wrong("its copies are no list")
    for at, one in enumerate(copies, 1):
        if not isinstance(one, dict):
            raise Wrong(f"its copy {at} is no object")
        for key in ("ssh", "folder"):
            text(one, key, f"its copy {at}")
        if one["ssh"].startswith("-"):
            raise Wrong(f"its copy {at}'s ssh target is an option, {one['ssh']}")
    return ceos, copies, limit


def attempt(args, cwd, limit):
    """What args prints, and None when it exited 0 within limit seconds, else why it failed. Past the limit its whole
    process group is asked to stop, then killed; a process that left the group on purpose may keep running."""
    try:
        with subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, start_new_session=True) as proc:
            try:
                out, err = proc.communicate(timeout=limit)
            except subprocess.TimeoutExpired:
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, sig)
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        proc.communicate(timeout=GRACE)
                        break
                return b"", f"timed out after {limit:g} s"
    except OSError as why:
        return b"", f"could not start: {why.strerror}" + (f": {why.filename}" if why.filename else "")
    if proc.returncode:
        said = err.decode("utf-8", "replace").strip().splitlines()
        return out, (f"exited {proc.returncode}" if proc.returncode > 0 else f"ended by signal {-proc.returncode}") + (
            f": {' '.join(said[-1].split())[:300]}" if said else "")
    return out, None


def fault(data, name):
    """Why data is not a status file of the CEO name, by the test board.read makes; None when it is one."""
    try:
        found = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):  # not UTF-8, cut off, or nested past what json reads
        return "not JSON"
    if not (isinstance(found, dict) and isinstance(found.get("ceo"), str) and found["ceo"]
            and isinstance(found.get("status"), dict) and isinstance(found.get("wave"), dict)):
        return f"not a status file of {name}"
    if found["ceo"] != name:  # board --page refuses a page with two files of one CEO
        return f"not a status file of {name}: it is {found['ceo']}'s"
    return None


def write_whole(path, data):
    """All of data or none of it, kept through a crash, as ceo_base.write_whole writes a file."""
    handle, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with open(handle, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def pull_one(entry, board, limit):
    """None once entry's status file is replaced by the one its command printed, else why the last file stays."""
    name, host = entry["name"], entry["host"]
    if "ssh" in entry:
        there = (f"cd {entry['folder']} && {entry['python']} {entry['kernel']} board --name {shlex.quote(name)} "
                 f"--host {shlex.quote(host)}")
        out, why = attempt(["ssh", *SSH, entry["ssh"], there], None, limit)
    else:
        out, why = attempt([os.path.expanduser(entry["python"]), str(KERNEL), "board", "--name", name, "--host", host],
                           os.path.expanduser(entry["folder"]), limit)
    why = why or fault(out, name)
    if not why:
        try:
            write_whole(board / f"{name}.json", out)
        except OSError as err:
            why = f"could not be written: {err.strerror}"
    return why


def pull():
    path = home() / "fleet.json"
    try:
        ceos, copies, limit = config(path)
    except Wrong as why:
        sys.exit(f"fleet: {path}: {why}; write it as {EXAMPLE} shows")
    board = home() / "board"
    board.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=min(PULLS, len(ceos))) as pool:
        whys = list(pool.map(lambda entry: pull_one(entry, board, limit), ceos))
    files = []
    for entry, why in zip(ceos, whys):
        file = board / f"{entry['name']}.json"
        if why:
            print(f"{entry['name']}: " + ("kept the last file" if file.exists() else "has no file yet") + f": {why}")
        if file.exists():
            try:
                bad = fault(file.read_bytes(), entry["name"])
            except OSError as err:
                bad = f"unreadable: {err.strerror}"
            if bad:  # a file written before this pull, by hand or by an older one; it would refuse the whole page
                print(f"{entry['name']}: its file is left off the page: {bad}")
            else:
                files.append(str(file))
    if not files:
        sys.exit(f"fleet: no CEO of {path} has a status file in {board} to render")
    _, failed = attempt([sys.executable, str(KERNEL), "board", "--page", *files, "--out", str(board / "index.html")],
                        board, limit)
    for copy in copies:  # the files, whatever the page did: a board served elsewhere renders its own
        target = f"{copy['ssh']}:{copy['folder']}"
        for args in (["ssh", *SSH, copy["ssh"], f"mkdir -p {copy['folder']}"],
                     ["scp", "-q", *SSH, *files, f"{target}/"]):
            if why := attempt(args, board, limit)[1]:
                print(f"copy to {target} failed: {why}")
                break
    if failed:
        sys.exit(f"fleet: the page was not made: {failed}")
    print(f"fleet: {datetime.now().astimezone().isoformat(timespec='seconds')}: {whys.count(None)} of {len(ceos)} "
          f"CEOs pulled, {len(files)} on the page")
    return 0


def install(folder):
    folder, at = Path(os.path.abspath(Path(folder).expanduser())), home()
    folder.mkdir(parents=True, exist_ok=True)
    (at / "board").mkdir(parents=True, exist_ok=True)  # launchd opens the log there before pull runs
    log = str(at / "board" / "fleet.log")
    job = dict(Label=LABEL, ProgramArguments=[os.path.abspath(sys.executable), str(Path(__file__).resolve()), "pull"],
               StartInterval=300, EnvironmentVariables={"CEO_HOME": str(at)}, StandardOutPath=log,
               StandardErrorPath=log)
    path = folder / f"{LABEL}.plist"
    write_whole(path, plistlib.dumps(job))
    print(f"fleet: wrote {path}, which pulls every 300 s once loaded; this loads it:")
    print(f"launchctl bootstrap gui/$(id -u) {shlex.quote(str(path))}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tools/fleet.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pull", help="every CEO's status file of $CEO_HOME/fleet.json, then the page")
    sub.add_parser("install", help="write the LaunchAgent that pulls every 300 s").add_argument(
        "folder", help="where it goes; ~/Library/LaunchAgents is where launchd finds it at login")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # each line reaches the job's log as it is said
    return pull() if args.command == "pull" else install(args.folder)


if __name__ == "__main__":
    sys.exit(main())
