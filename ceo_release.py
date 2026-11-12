from ceo_night import *  # The kernel's own code, its tick restarting onto new code and the jobs that keep ticks alive.
TICK_FLAGS = ("worker", "reviewer", "checker", "every", "timeout")  # what ceo.py keep starts

def kernel_code():
    """The bytes of the kernel's own Python files, as gate.sh counts them."""
    return {path.name: path.read_bytes() for path in KERNEL.parent.glob("*.py")
            if not path.name.startswith("test_")}

def tick_idle(repo, children):
    """No child, agent or decision of this project still needs the image it started with."""
    return not (any(proc.poll() is None for proc in children.values()) or agents(repo)
                or any(held(path) for name in ("launch", "lock")
                       for path in (repo / ".ceo" / "decisions").glob(f"*/{name}")))

def restart_kernel(repo, children, started, rejected, seen=None):
    """At a pass's end, exec changed, compilable code only while the tick's project is idle."""
    try:
        code = kernel_code()
    except OSError:
        return  # a file moving while read is checked again next pass
    current = {name: hashlib.sha256(data).digest() for name, data in code.items()}
    changed = sorted(name for name in started.keys() | current.keys() if started.get(name) != current.get(name))
    content = frozenset(current.items())
    if seen is not None:
        previous, seen[:] = seen[:], [content]
        if previous != [content]:
            return  # new code runs only once two pass ends in a row read it alike, never a pull caught mid-write
    if not changed or content in rejected:
        return
    for name in changed:
        if name not in code:
            continue  # a removed file is a change too; nothing remains to compile
        try:
            compile(code[name], str(KERNEL.parent / name), "exec")
        except (SyntaxError, ValueError) as err:
            print(f"ceo: the kernel's new code does not compile ({name}: {err}), "
                  "so this tick keeps running its old code", flush=True)
            rejected.add(content)
            return
    if not tick_idle(repo, children):
        return
    line = f"the kernel's code changed ({', '.join(changed)}), so this tick restarts"
    print(f"ceo: {line}")
    event(repo, "restarted", "", line)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, *sys.argv])

def keep(args):
    """Write the agent that keeps this repository's tick alive, with the flags of its last start, and print
    the one command loading it; --remove deletes it. The kernel never loads one itself."""
    repo = toplevel()
    name, saved = slugify(repo.name), read_json(repo / ".ceo" / "tick.json")
    try:
        conf = settings(repo)
    except Refused:
        if not args.remove:
            raise
        conf = {}  # --remove still takes the tick's agent away when ceo.toml cannot be read
    # What keep_env names a hand start keeps for its models, and so does the agent; --remove needs none.
    named = [] if args.remove else str(conf.get("keep_env", "")).split()
    env = {key: os.environ[key] for key in ("PATH", "CEO_HOME", *named) if key in os.environ}
    if not (args.remove or saved.get("every")):
        raise Refused("no tick has started here with --every, so there is no tick to keep alive")
    cmd = [sys.executable, str(KERNEL), "tick", *(f"--{flag}={saved[flag]}" for flag in TICK_FLAGS
                                                  if saved.get(flag) is not None)]
    return keep_one(args, repo, name, cmd, repo / ".ceo" / "tick.log", env)

def keep_one(args, repo, name, cmd, log, env):
    """Write the agent ceo.<name> that keeps cmd alive in repo, logging to log, and print the one command
    loading it; --remove deletes it."""
    if sys.platform == "darwin":
        path = Path.home() / "Library" / "LaunchAgents" / f"ceo.{name}.plist"
        text = plistlib.dumps({"Label": f"ceo.{name}", "ProgramArguments": cmd, "KeepAlive": True,
                               "RunAtLoad": True, "WorkingDirectory": str(repo),
                               "EnvironmentVariables": env, "StandardOutPath": str(log),
                               "StandardErrorPath": str(log)}).decode()
        load = f"launchctl bootstrap gui/{os.getuid()} {shlex.quote(str(path))}"
        unload = f"launchctl bootout gui/{os.getuid()}/ceo.{name}"
    else:  # systemd's quoting: C escapes in double quotes, % doubled, and $ where it expands
        path = Path.home() / ".config" / "systemd" / "user" / f"ceo-{name}.service"
        spec = lambda text: str(text).replace("%", "%%")
        word = lambda text: '"' + re.sub(r'([\\"])', r"\\\1", spec(text)) + '"'
        text = (f"[Service]\nWorkingDirectory={spec(repo)}\n"
                + "".join(f"Environment={word(f'{key}={value}')}\n" for key, value in env.items())
                + f"ExecStart={' '.join(word(part).replace('$', '$$') for part in cmd)}\n"
                # Only the tick goes when it stops: what it started lives in sessions of its own.
                f"Restart=always\nRestartSec=10\nKillMode=process\nStandardOutput=append:{spec(log)}\n"
                f"StandardError=append:{spec(log)}\n\n[Install]\nWantedBy=default.target\n")
        # Lingering keeps the user's services, and so the tick, running after the last login ends.
        load = (f"systemctl --user daemon-reload && systemctl --user enable --now {path.name} "
                "&& loginctl enable-linger")
        unload = f"systemctl --user stop {path.name}; systemctl --user disable {path.name}"
    if args.remove:
        path.unlink(missing_ok=True)
        print(f"ceo: removed {path}; while it is loaded, stop it with: {unload}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_whole(path, text)
        print(f"ceo: wrote {path}; load it with: {load}")
    return 0
