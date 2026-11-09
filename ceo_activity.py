"""CPU progress between stall observations, kept apart from the files that clock a run."""
from ceo_base import *

def cpu_processes():
    """pid -> (parent, group, birth identity, CPU consumed). Zombies have finished their work."""
    rows = {}
    if Path("/proc/self/stat").exists():
        epoch = int(re.search(r"^btime (\d+)$", Path("/proc/stat").read_text(), re.M)[1])
        ticks = os.sysconf("SC_CLK_TCK")
        for path in Path("/proc").glob("[0-9]*/stat"):
            with contextlib.suppress(OSError, ValueError, IndexError):
                fields = path.read_text().rsplit(")", 1)[1].split()
                if fields[0] not in ("Z", "X"):
                    rows[int(path.parent.name)] = (int(fields[1]), int(fields[2]), epoch + int(fields[19]) / ticks,
                                                   int(fields[11]) + int(fields[12]))
    else:  # macOS ps reports CPU time to hundredths and the birth date independently of elapsed time.
        with contextlib.suppress(OSError, subprocess.SubprocessError, ValueError):
            out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,pgid=,stat=,lstart=,time="],
                                 capture_output=True, text=True, check=True).stdout
            for line in out.splitlines():
                fields = line.split()
                if len(fields) != 10 or fields[3].startswith("Z"):
                    continue
                cpu = 0
                for part in fields[9].split(":"):
                    cpu = cpu * 60 + float(part)
                born = time.mktime(time.strptime(" ".join(fields[4:9]), "%a %b %d %H:%M:%S %Y"))
                rows[int(fields[0])] = (int(fields[1]), int(fields[2]), born, cpu)
    return rows

def has_descriptor(pid, stamp):
    """An inherited descriptor can identify a lock holder even if its lock's mtime was restored."""
    for path in (Path("/dev/fd") if pid == os.getpid() else Path(f"/proc/{pid}/fd")).glob("*"):
        with contextlib.suppress(OSError):
            found = path.stat()
            if (found.st_dev, found.st_ino) == (stamp.st_dev, stamp.st_ino):
                return True
    return False

def run_activity(repo):
    """Live run slugs and their last observed CPU progress; merely holding a lock is no progress.
    The sample survives one-shot ticks. Its mtime never clocks stalls, and a new PID/birth is only a baseline."""
    ceo, live = repo / ".ceo", {}
    paths = list((ceo / "locks").glob("*.run"))
    if not paths:
        return live
    with open(ceo / "activity.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = ceo / "activity.json"
        previous, samples = read_json(path), {}
        table, machine, at = cpu_processes(), boot(), time.time()
        for run in paths:
            meta = read_json(run.with_suffix(".json"))
            result = read_json(result_path := ceo / "runs" / run.stem / "result.json")
            try:
                stamp = run.stat()
                if (meta.get("boot") != machine or meta.get("order") != f"orders/{run.stem}.md"
                        or result and (result.get("state") != "retry" or result_path.stat().st_mtime_ns >= stamp.st_mtime_ns)):
                    continue
                token = [machine, meta.get("started"), stamp.st_dev, stamp.st_ino, stamp.st_mtime_ns]
                locked = held(run)
                owner = int(run.read_text().strip() or 0)
                recorded = run.with_suffix(".json").stat().st_mtime
            except (OSError, ValueError):
                continue  # a concurrent sweep may remove the lock or result
            old = previous.get(run.stem, {})
            old = old if isinstance(old, dict) and old.get("token") == token else {}
            before = old.get("cpu", {})
            groups = set(meta.get("groups", []))
            # A recycled signed PID is not this run's parent. A recorded group must predate its record,
            # belong to that parent now, or have a process identity already observed in this run.
            owned = {owner} if locked and owner in table and (table[owner][2] <= stamp.st_mtime + 1
                                                            or has_descriptor(owner, stamp)) else set()
            while children := {pid for pid, (parent, _, _, _) in table.items() if parent in owned} - owned:
                owned |= children
            members = {pid for pid, (_, group, birth, _) in table.items()
                       if f"{pid}:{birth}" in before or group in groups and (pid in owned or birth <= recorded)}
            while children := {pid for pid, (parent, _, _, _) in table.items() if parent in members} - members:
                members |= children
            if not locked and not members:
                continue
            cpu = {f"{pid}:{table[pid][2]}": table[pid][3] for pid in members}
            progress = at if any(key in before and value > before[key] for key, value in cpu.items()) else old.get("progress", 0)
            samples[run.stem] = dict(token=token, cpu=cpu, progress=progress)
            live[run.stem] = progress
        write_json(path, samples)
    return live
