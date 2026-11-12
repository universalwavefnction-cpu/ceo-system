#!/usr/bin/env python3
"""The `ceo` command: one word in any folder opens its project's CEO (standard library only).

    ceo              reopen the project's last CEO conversation; a new folder is set up first
    ceo new          a new CEO conversation
    ceo <name>       the same for the project called name, from any folder: the listed project of that name
                     wherever it lives ($CEO_HOME/projects.txt), else ~/dev/<name>, made when it is missing
    ceo <name> new   a new CEO conversation there
    ceo try          one ready-made order, in ~/dev/ceo-try and a private GitHub repository, built by an agent and
                     checked by a second until it merges, each step said as it happens; then the line undoing it all

A folder whose top has no ceo.toml is set up in four steps, each a line that gets its check mark as it ends: git
init; a private GitHub repository, made with gh as the folder's origin; the CEO's files, ceo.py init's commit,
pushed there; and the CEO opened. No tick starts: it starts with the project's first order filed. Beside each step, the
seconds it took over past launches ($CEO_HOME/launches.json); with none, its name alone. In a terminal the lines
give way to one, `ceo · <folder> · set up in <s>s`; a pipe or TERM=dumb gets plain lines. In a terminal with room, a
wave of dots above the lines collapses into the folder's name as the steps go, and a reopen plays one second of it,
left to right. A step that fails says so in one line and keeps what is done. Two folders are guarded: the home
folder and / are never set up (ceo <name> makes a project in ~/dev instead), and a repository that has commits
already is set up only on a y at the question, since its setup commits there and pushes to its origin; no answer is
no. Install it as a link to this file:

    ln -s <kernel>/tools/launch.py ~/.local/bin/ceo
"""
import argparse
import fcntl
import json
import math
import os
import random
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

KERNEL = Path(__file__).resolve().parent.parent / "ceo.py"  # through the link, the kernel beside this file
STEPS = ("git init", "private GitHub repo", "the CEO's files", "open the CEO")
KEPT = 20  # past launches the history keeps; each step's estimate is their median
DEV = "~/dev"  # where ceo <name> makes a project no list names
# The wave's face: 3x5 dots a character, one octal digit a row, its bits left to right; any other character is a ?.
FONT = dict(zip("abcdefghijklmnopqrstuvwxyz0123456789-_.", (
    "25755 65656 34443 65556 74647 74644 34553 55755 72227 11152 55655 44447 57755 65555 25552 65644 25563 65655 "
    "34216 72222 55557 55552 55775 55255 55222 71247 75557 26227 61247 61216 55711 74616 34652 71222 25252 25316 "
    "00700 00007 00002").split()))
UNKNOWN = "71202"
FPS, SETTLE, SHINE = 20, 0.4, 0.8  # frames a second; a dot's flight; the shine's pass over the landed name
GUESS = (0.5, 3.0, 3.0)  # each step's seconds while no launch has measured them
DENSE, MID, FAINT, FLIGHT, LANDED, GLINT = 111, 68, 60, 159, 255, 222  # xterm-256: the wave by density, then the name


class Failed(Exception):
    """A step did not finish: the one line that says what failed, what is kept and what to do."""


def run(fails, *cmd, cwd):
    """cmd in cwd, its output kept off the screen; Failed with fails naming its last line when it fails."""
    done = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if done.returncode:
        said = (done.stderr.strip() or done.stdout.strip()).splitlines()
        raise Failed(fails.format(said[-1] if said else f"{cmd[0]} exited {done.returncode}"))


def git_init(folder):
    run("git init failed ({}). Nothing kept; run ceo to retry.", "git", "init", "-q", "-b", "main", cwd=folder)


def github(folder):
    """A private repository named as the folder, made its origin; none when it has an origin already."""
    if subprocess.run(["git", "remote", "get-url", "origin"], cwd=folder, capture_output=True).returncode:
        run("GitHub repo not created ({}). git init kept; run ceo to retry.",
            "gh", "repo", "create", folder.name, "--private", "--source", str(folder), "--remote", "origin", cwd=folder)


def files(folder):
    """ceo.py init's commit, then the first push: gh refuses --push while nothing is committed."""
    run("the CEO's files not made ({}). git init and the GitHub repo kept; run ceo to retry.",
        sys.executable, str(KERNEL), "init", str(folder), cwd=folder)
    run("the CEO's files not pushed ({}). They are committed; git push -u origin HEAD sends them.",
        "git", "push", "-q", "-u", "origin", "HEAD", cwd=folder)


def setup(folder, report):
    """The three steps before the handover, each reported as report(event, step, seconds) when it starts, ends
    or fails: the one seam the launch animation will read. Their seconds, or Failed."""
    took = []
    for step, act in enumerate((git_init, github, files)):
        report("start", step, None)
        began = time.monotonic()
        try:
            act(folder)
        except Failed:
            report("fail", step, time.monotonic() - began)
            raise
        took.append(time.monotonic() - began)
        report("end", step, took[-1])
    return took


def ceo_home():
    """$CEO_HOME, ~/.ceo by default, as the kernel's home."""
    return Path(os.environ.get("CEO_HOME") or "~/.ceo").expanduser()


def history():
    """$CEO_HOME/launches.json: one list of step seconds a past launch."""
    return ceo_home() / "launches.json"


def named(name):
    """The folder ceo <name> opens: the one listed project of that name, its list read as ceo.py open reads it,
    else ~/dev/<name>, made when it is missing. Failed when two listed projects share the name."""
    try:
        text = (ceo_home() / "projects.txt").read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        text = ""
    listed = dict.fromkeys(line.strip() for line in text.splitlines() if line.strip())
    matches = [Path(entry) for entry in listed if Path(entry).name == name and Path(entry).is_dir()]
    if len(matches) > 1:
        raise Failed(f"{len(matches)} listed projects are called {name}: {', '.join(map(str, matches))}. "
                     "Run ceo inside the one you mean.")
    if matches:
        return matches[0]
    folder = Path(DEV).expanduser() / name
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        raise Failed(f"{folder} could not be made ({err.strerror}). Nothing done.") from None
    return folder


def yes(question):
    """True on a y or yes to question, asked on stderr; any other answer, or a closed stdin, says nothing done."""
    sys.stderr.write(question)
    sys.stderr.flush()
    if sys.stdin.readline().strip().lower() in ("y", "yes"):
        return True
    print("ceo: nothing done.", file=sys.stderr)
    return False


def agreed(folder):
    """True when setting folder up rewrites no one's history: no repository, or one without a commit yet. A
    repository with commits only on a y at the question, as its setup commits there and pushes to its origin."""
    count = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=folder, capture_output=True, text=True)
    commits = count.stdout.strip()
    return bool(count.returncode) or yes(
        f"ceo: {folder} is a git repository with {commits} commit{'s' * (commits != '1')} and no ceo.toml. Make it "
        "a CEO project? Its setup commits the CEO's files there and pushes them to its origin. [y/N] ")


def estimates():
    """Each step's median seconds over the past launches, or None when there are none."""
    try:
        past = json.loads(history().read_text(encoding="utf-8"))
        return [statistics.median(float(one[step]) for one in past) for step in range(3)] if past else None
    except (OSError, ValueError, TypeError, IndexError, KeyError):
        return None


def learn(took):
    """This launch added to the history, the last KEPT kept; a history that cannot be written only costs the
    next launch its estimates."""
    path = history()
    try:
        past = json.loads(path.read_text(encoding="utf-8"))
        past = past if isinstance(past, list) else []
    except (OSError, ValueError):
        past = []
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = path.with_name(f".launches.{os.getpid()}")
        fresh.write_text(json.dumps((past + [[round(one, 3) for one in took]])[-KEPT:]) + "\n", encoding="utf-8")
        os.replace(fresh, path)
    except OSError:
        pass


class Wave:
    """A wave of dots, each with its place in the name or none, that collapses into the name: cells(t, collapsed)
    are rows of (character, colour) at t seconds with collapsed (0 to 1) of the dots let go. They leave at random,
    or left to right for a sweep; the pattern is the name's own, the same at every launch."""

    def __init__(self, name, columns, order="random", settle=SETTLE, shine=True):
        rng, word, self.settle, self.shine, self.done = random.Random(name), name.lower(), settle, shine, None
        scale = 2 if 8 * len(word) + 6 <= columns else 1 if 4 * len(word) + 6 <= columns else 0
        if scale:
            self.height, wide = 9, 4 * scale * len(word) - scale
            spots = [(x * scale + i * 4 * scale, 2 + y, "●") for i, ch in enumerate(word)
                     for y, row in enumerate(FONT.get(ch, UNKNOWN)) for x in range(3) if int(row) >> (2 - x) & 1]
        else:  # too long for the face: its own letters, on one row
            self.height, wide, spots = 5, len(name), [(i, 2, ch) for i, ch in enumerate(name)]
        self.width = min(columns - 2, max(wide + 10, 44))
        self.fits = wide <= self.width  # the whole name, or no wave at all
        spots = [(x + max(0, self.width - wide) // 2, y, mark) for x, y, mark in spots]
        rng.shuffle(spots)
        self.dots = [{"x": rng.uniform(0, self.width - 1), "phase": rng.gauss(0, 0.4), "drift": rng.uniform(0, 6.3),
                      "home": spots[i] if i < len(spots) else None, "left": None, "from": None}
                     for i in range(max(len(spots) * 13 // 10, 90))]
        leaving = (sorted(self.dots, key=lambda dot: (dot["home"] or (dot["x"],))[0]) if order == "sweep"
                   else rng.sample(self.dots, len(self.dots)))
        for rank, dot in enumerate(leaving):
            dot["rank"] = (rank + 1) / len(leaving)

    def on_wave(self, dot, t):
        middle = (self.height - 1) / 2
        swell = 0.55 + 0.45 * math.sin(0.11 * dot["x"] + 0.9 * t)
        return (dot["x"] + 0.8 * math.sin(1.3 * t + dot["drift"]),
                middle + (middle - 0.4) * swell * math.sin(0.3 * dot["x"] - 2.6 * t + dot["phase"]))

    def cells(self, t, collapsed):
        grid = [[(" ", None)] * self.width for _ in range(self.height)]
        layers = ([], [], [])  # on the wave, in flight, landed: drawn in that order
        for dot in self.dots:
            if dot["left"] is None and collapsed >= dot["rank"]:
                dot["left"], dot["from"] = t, self.on_wave(dot, t)
            if dot["left"] is None:
                spread = abs(dot["phase"])
                layers[0].append((*self.on_wave(dot, t), *(("•", DENSE) if spread < 0.35 else ("∙", MID)
                                                             if spread < 0.8 else ("·", FAINT))))
                continue
            u, (fx, fy), home = min(1.0, (t - dot["left"]) / self.settle), dot["from"], dot["home"]
            if home is None:  # a dot the name has no place for fades where it let go
                layers[0].extend([(fx, fy, "·", FAINT)] * (u < 1))
            elif u < 1:
                k = 1 - (1 - u) ** 3
                layers[1].append((fx + (home[0] - fx) * k, fy + (home[1] - fy) * k, "•", FLIGHT))
            else:
                layers[2].append((*home, LANDED))
        if self.done is None and all(dot["left"] is not None and t - dot["left"] >= self.settle for dot in self.dots):
            self.done = t
        for layer in layers:
            for x, y, mark, colour in layer:
                col, row = round(x), round(y)
                if 0 <= row < self.height and 0 <= col < self.width:
                    if colour == LANDED and self.shine and self.done is not None:  # one pass across the name
                        colour = GLINT if abs(col - (t - self.done) / SHINE * (self.width + 12) + 6) < 2.5 else colour
                    grid[row][col] = (mark, colour)
        return grid


def field(name, below, **how):
    """The wave for name when the terminal has room for it and below more rows, else None: drawing past the
    terminal's top would scramble it."""
    columns, rows = shutil.get_terminal_size()
    wave = Wave(name, columns, **how) if columns >= 46 else None
    return wave if wave and wave.fits and wave.height + below < rows else None


def ansi(grid):
    rows = []
    for row in grid:
        text, last = "", None
        for mark, colour in row:
            text += (f"\x1b[38;5;{colour}m" if colour else "\x1b[0m") if colour != last else ""
            text, last = text + mark, colour
        rows.append(text.rstrip() + "\x1b[0m")
    return rows


def paint(rows, drawn):
    """rows drawn over the drawn rows above the cursor; how many are drawn now."""
    sys.stdout.write(f"\x1b[{drawn}A" * bool(drawn) + "".join(f"\r\x1b[2K{row}\n" for row in rows))
    sys.stdout.flush()
    return len(rows)


def sweep(name):
    """A reopen in a terminal with room: one second of the wave sweeping into the name left to right, then wiped.
    A drawing fault only ends it early."""
    wave, drawn, began = field(name, 1, order="sweep", settle=0.25, shine=False), 0, time.monotonic()
    if wave is None:
        return
    try:
        sys.stdout.write("\x1b[?25l")
        while True:
            t = min(1.0, time.monotonic() - began)
            drawn = paint(ansi(wave.cells(t, min(1.0, max(0.0, t - 0.1) / 0.55))), drawn)
            if t >= 1.0:
                break
            time.sleep(1 / FPS)
    except Exception:  # noqa: BLE001 - the animation never stands between him and his CEO
        pass
    finally:
        sys.stdout.write(f"\x1b[{drawn}A\r\x1b[J" * bool(drawn) + "\x1b[?25h")
        sys.stdout.flush()


def seconds(value):
    return f"{value:.2f}s" if value < 0.095 else f"{value:.1f}s" if value < 9.95 else f"{value:.0f}s"


class Lines:
    """What the step events print. Plain (a pipe, TERM=dumb): a check-mark line as each step ends. In a
    terminal: the folder and the four steps, drawn again in place, then wiped for the one line left; with room,
    the wave above them collapses into the folder's name as the steps go, drawn by its own thread FPS times a
    second. Seconds only with a history: estimates while a step waits, then what it took."""

    def __init__(self, folder, about, terminal):
        self.folder, self.about, self.terminal = folder, about, terminal
        self.marks, self.took, self.drawn, self.collapsed = ["·"] * 4, [None] * 4, 0, 0.0
        self.lock, self.stopped, self.began = threading.Lock(), threading.Event(), time.monotonic()
        self.since, self.painter = self.began, None
        with self.lock:  # the painter's first frame waits for this one
            try:  # a wave that cannot be made or started costs the wave, never the setup
                self.wave = field(folder, len(STEPS) + 2) if terminal else None
                if self.wave:
                    self.painter = threading.Thread(target=self.paint, daemon=True)
                    self.painter.start()
            except Exception:  # noqa: BLE001
                self.wave = self.painter = None
            if self.painter:
                sys.stdout.write("\x1b[?25l")
            if terminal:
                self.draw()

    def __call__(self, event, step, value):
        with self.lock:
            self.marks[step] = {"start": "›", "end": "✓", "fail": "×"}[event]
            self.took[step], self.since = value if event == "end" else None, time.monotonic()
            if self.terminal and not self.painter:
                self.draw()
        if not self.terminal and event == "end":
            print(f"✓ {STEPS[step]}" + (f"  {seconds(value)}" if self.about else ""), flush=True)

    def share(self):
        """How much of the setup is done, in its steps' estimated seconds (GUESS without a history): a step
        counts at most 90 % of its own until it ends."""
        ended, guess = sum(took is not None for took in self.took[:3]), self.about or GUESS
        if ended == 3:
            return 1.0
        now = min(0.9 * guess[ended], time.monotonic() - self.since)
        return (sum(guess[:ended]) + now) / max(sum(guess), 1e-9)

    def paint(self):
        try:
            while not self.stopped.wait(1 / FPS):
                with self.lock:
                    self.collapsed += (self.share() - self.collapsed) * 3 / FPS
                    self.draw()
        except Exception:  # noqa: BLE001 - a drawing fault costs the animation, never the setup
            self.stopped.set()

    def draw(self):
        rows = [*ansi(self.wave.cells(time.monotonic() - self.began, self.collapsed)), ""] if self.painter else []
        rows.append(f"ceo · {self.folder}")
        for step, name in enumerate(STEPS):
            done, about = self.took[step] is not None, self.about and step < 3
            note = seconds(self.took[step]) if done and self.about else f"~{seconds(self.about[step])}" if about else ""
            rows.append(f"{self.marks[step]} {name:<20} {note}".rstrip())
        self.drawn = paint(rows, self.drawn)

    def finish(self):
        """The dots still on the wave let go at once; back once the name has landed and its shine has passed."""
        if self.painter and not self.stopped.is_set():
            with self.lock:
                self.collapsed = 1.0
            time.sleep(SETTLE + SHINE + 2 / FPS)

    def wipe(self):
        """The drawing stopped and erased and the cursor shown again; a second wipe does nothing."""
        self.stopped.set()
        if self.painter:
            self.painter.join()
        if self.drawn:
            sys.stdout.write(f"\x1b[{self.drawn}A\r\x1b[J" + "\x1b[?25h" * bool(self.painter))
            sys.stdout.flush()
        self.drawn, self.painter = 0, None


def open_ceo(folder, again):
    """The handover: this process becomes ceo.py session in the folder, which opens the owner's command there;
    again, with --continue, its last conversation in that folder."""
    os.chdir(folder)
    os.execv(sys.executable, [sys.executable, str(KERNEL), "session", *["--continue"] * again])


TRY, SLUG = Path("~/dev/ceo-try"), "001-hello"
BOX = KERNEL.parent / "tools" / "box"
CODEX = "codex exec --dangerously-bypass-approvals-and-sandbox -"  # HARNESSES.md's
HELLO = """# A hello file

## Goal
HELLO.md at the top of the project, with one line greeting whoever opens it.

## Done when
```bash
grep -qi hello HELLO.md
```

## Not asked
Any other file.
"""


def claude():
    """The kernel's default worker, as every project without a worker line gets it: claude-auto's command while it
    is on PATH, else plain claude's; None with neither."""
    if str(KERNEL.parent) not in sys.path:
        sys.path.insert(0, str(KERNEL.parent))
    from ceo_base import default_worker
    return default_worker()


def try_lines(agent, walled):
    """What the try adds to its ceo.toml: agent's worker line, behind the wall only for claude, and no night."""
    boxed, line = walled and agent == "claude", claude() if agent == "claude" else CODEX
    worker = f"{shlex.quote(str(BOX))} {line}" if boxed else line
    return f"worker = {json.dumps(worker)}\n" + 'isolation.box = "--setting-sources project"\n' * boxed + 'night = ""\n'


def locked(path):
    """True while some process holds path's flock, as the kernel's held reads it, though nothing is made."""
    try:
        with open(path) as file:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        pass
    return False


def said(*lines):
    print(*lines, sep="\n", file=sys.stderr)
    return 1


def try_it():
    """ceo try: the guards, the questions, then the try's folder and repository with the hello order filed."""
    folder = TRY.expanduser()
    if locked(folder / ".ceo" / "tick.lock"):
        return said(f"ceo: a tick is running in {folder}; ceo try starts none beside it.")
    quiet = {"stdin": subprocess.DEVNULL, "capture_output": True, "text": True}
    who = subprocess.run(["gh", "api", "user", "--jq", ".login"], **quiet)
    login = who.stdout.strip() * (who.returncode == 0)
    if not re.fullmatch(r"[\w.-]+", login):
        return said("ceo: gh names no GitHub login: log in with gh auth login, then run ceo try.")
    repo = f"{login}/ceo-try"
    undo = (f"Undo it all: gh auth refresh -s delete_repo && gh repo delete {repo} --yes && "
            f"rm -rf {shlex.quote(str(folder))}")
    earlier = subprocess.run(["git", "-C", str(folder), "rev-parse", "-q", "--verify", "HEAD"], **quiet)
    if (folder / ".git").exists() and earlier.returncode == 0:
        return said(f"ceo: {folder} holds an earlier try. {undo}")
    agent = "claude" if claude() else "codex" if shutil.which("codex") else None
    if agent is None:
        return said("ceo: neither claude nor codex is on PATH: install one and log in, then run ceo try.")
    try:
        box = subprocess.run([str(BOX), "true"], cwd=KERNEL.parent, **quiet)
    except OSError as err:
        box = subprocess.CompletedProcess(BOX, 125, "", f"tools/box cannot run: {err.strerror}")
    walled = box.returncode == 0 and agent == "claude"
    if not yes(f"ceo try makes the private GitHub repository {repo} and the folder {folder}, and files one "
               f"ready-made order, a hello file: {agent} builds it and a second, fresh {agent} checks it, until it "
               "merges. [y/N] "):
        return 1
    if not walled:
        why = ((box.stderr.strip().splitlines() or [f"tools/box exited {box.returncode}"])[-1] if box.returncode
               else "the wall holds only claude so far")
        said(f"ceo: this machine has no wall for {agent} ({why}), so it could write anywhere you can.")
        if not yes("Run the try without the wall? [y/N] "):
            return 1
    try:
        folder.mkdir(parents=True, exist_ok=True)
        setup(folder, lambda event, step, took: event == "end" and print(f"✓ {STEPS[step]}", flush=True))
        with open(folder / "ceo.toml", "a", encoding="utf-8") as conf:
            conf.write(try_lines(agent, walled))
        (folder / "orders" / f"{SLUG}.md").write_text(HELLO, encoding="utf-8")
        for step in (("add", "ceo.toml", f"orders/{SLUG}.md"), ("commit", "-qm", "The try's order: a hello file"),
                     ("push", "-q", "origin", "HEAD")):
            run("the hello order is not filed ({}).", "git", *step, cwd=folder)
    except (Failed, OSError) as why:
        return said(f"ceo: {str(why).split('; run ceo')[0].rstrip('.')}.", undo)
    except KeyboardInterrupt:  # the repository may be made already: the undo is said all the same
        return said("ceo: stopped; what is made is kept.", undo) and 130
    print(f"✓ the hello order filed; {agent} works on it now", flush=True)
    code = watch(folder, repo)
    print(undo, flush=True)
    return code


def record(path):
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def took(found):
    """Whole seconds from a record's started to its ended; None while it has not ended."""
    if found.get("ended") and found.get("started"):
        return int((datetime.fromisoformat(found["ended"]) - datetime.fromisoformat(found["started"])).total_seconds())
    return None


def watch(folder, repo):
    """One-shot ticks two seconds apart, each in a session of its own that ends by itself, until the hello order
    merges; each station said once, as the kernel records it. 1 on its failure or 20 minutes, 130 on Ctrl-C."""
    ceo, tick, last, began, shown = folder / ".ceo", None, 0.0, time.monotonic(), 0
    ceo.mkdir(exist_ok=True)
    try:
        with open(ceo / "try.log", "ab") as log:
            while shown < 4:
                if (tick is None or tick.poll() is not None) and time.monotonic() - last >= 2:
                    last, tick = time.monotonic(), subprocess.Popen(
                        [sys.executable, str(KERNEL), "tick"], cwd=folder, stdin=subprocess.DEVNULL, stdout=log,
                        stderr=log, start_new_session=True)
                try:
                    lines = (ceo / "events.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    lines = []
                seen = set()
                for line in lines:
                    try:
                        one = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(one, dict) and one.get("slug") == SLUG:
                        if one.get("event") in ("failed", "blocked", "waiting"):
                            return said(f"ceo: {one.get('sentence')}")
                        seen.add(one.get("event"))
                result, review = (record(ceo / "runs" / SLUG / name) for name in ("result.json", "review.json"))
                station = ("started" in seen and "the test fails first",
                           "done" in seen and took(result) is not None and f"an agent built it, {took(result)} s",
                           review.get("verdict") == "survives" and took(review) is not None
                           and f"a second agent checked it, {took(review)} s",
                           "merged" in seen and result.get("pr")
                           and f"merged: https://github.com/{repo}/pull/{result['pr']}")[shown]
                if station:
                    print(station, flush=True)
                    shown += 1
                elif time.monotonic() - began > 1200:
                    return said(f"ceo: no merge in 20 minutes; {folder / 'STATUS.md'} says where the order stands.")
                else:
                    time.sleep(0.5)
        tick.wait()  # one-shot, it ends by itself; then the door's lock free: none of the try's is left
        end = time.monotonic() + 60
        while time.monotonic() < end and locked(ceo / "locks" / "door.lock"):
            time.sleep(0.5)
        return 0
    except KeyboardInterrupt:
        print("ceo: stopped; the tick at work ends by itself.", file=sys.stderr)
        return 130


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ceo", description=__doc__.split("\n\n")[0])
    parser.add_argument("words", nargs="*", metavar="[name] [new]",
                        help="a project's name, from any folder; new for a new CEO conversation")
    words = parser.parse_args(argv).words
    if words[:1] == ["try"]:
        if len(words) > 1:
            parser.error("ceo try takes no other word")
        try:
            return try_it()
        except KeyboardInterrupt:
            return said("ceo: stopped.") and 130
    fresh = words[-1:] == ["new"]
    names = words[:-1] if fresh else words
    if len(names) > 1 or any("/" in name or name.startswith(".") for name in names):
        parser.error("one project name at most: a folder's name, without / and not starting with a dot")
    began = time.monotonic()
    # The retry hint names what he typed: in another folder a bare ceo would set that folder up instead.
    command = " ".join(["ceo", *names])
    try:
        start = named(names[0]) if names else Path.cwd()
    except Failed as why:
        print(f"ceo: {why}", file=sys.stderr)
        return 1
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start, capture_output=True, text=True)
    folder = Path(top.stdout.strip()) if top.returncode == 0 else start  # never a repository inside one
    terminal = sys.stdout.isatty() and os.environ.get("TERM") != "dumb"
    if (folder / "ceo.toml").is_file():  # what ceo.py init writes: a project, its CEO reopened
        if terminal:
            try:
                sweep(folder.name)
            except KeyboardInterrupt:
                print("ceo: stopped.", file=sys.stderr)
                return 130
        print(f"ceo · {folder.name}", flush=True)
        return open_ceo(folder, again=not fresh)
    if folder.resolve() in (Path.home().resolve(), Path("/")):
        print(f"ceo: {folder} is never set up as a project; ceo <name> makes one in {DEV}/<name>.", file=sys.stderr)
        return 1
    if not agreed(folder):
        return 1
    lines = Lines(folder.name, estimates(), terminal)
    try:
        took = setup(folder, lines)
        lines.finish()
    except (Failed, KeyboardInterrupt) as why:
        lines.wipe()
        told = str(why).replace("run ceo to", f"run {command} to")
        print(f"ceo: {told}" if isinstance(why, Failed) else "ceo: stopped; what is done is kept.", file=sys.stderr)
        return 1 if isinstance(why, Failed) else 130
    finally:
        lines.wipe()  # a fault of any other kind gives the cursor back too
    learn(took)
    print(f"ceo · {folder.name} · set up in {time.monotonic() - began:.1f}s", flush=True)
    return open_ceo(folder, again=False)


if __name__ == "__main__":
    sys.exit(main())
