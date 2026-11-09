"""The installed ceo link: real git and kernel, scratch accounts, fake GitHub and agent, and a real pty."""
import errno
import fcntl
import io
import json
import os
import pty
import re
import select
import struct
import subprocess
import sys
import tempfile
import termios
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import launch

KERNEL = Path(__file__).resolve().parent
FILES = [".gitignore", "CEO.md", "LESSONS.md", "VISION.md", "ceo.toml", "orders/README.md"]
GH = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE/gh.log"
if [ -e "$FAKE/gh-fails" ]; then echo "HTTP 502: Bad Gateway" >&2; exit 1; fi
[ "$1 $2" = "repo create" ] || exit 1
name=$3 source= remote=origin
shift 3
while [ $# -gt 0 ]; do
    case $1 in --source) source=$2; shift ;; --remote) remote=$2; shift ;; esac
    shift
done
git init -q --bare -b main "$FAKE/github/$name.git"
git -C "$source" remote add "$remote" "$FAKE/github/$name.git"
"""
AGENT = """#!/usr/bin/env python3
import json, os, re, sys, time
from pathlib import Path
with open(os.path.join(os.environ["FAKE"], "agent.log"), "a") as log:
    log.write(json.dumps({"at": time.time(), "cwd": os.getcwd(), "args": sys.argv[1:]}) + "\\n")
history = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", os.getcwd())
if "--continue" in sys.argv and not any(history.glob("*.jsonl")):
    sys.exit("No conversation found to continue")
history.mkdir(parents=True, exist_ok=True)
(history / "session.jsonl").write_text("{}\\n")
"""


def question(folder, commits):
    """What ceo asks before it sets up a repository that has commits already."""
    return (f"ceo: {folder} is a git repository with {commits} commits and no ceo.toml. Make it a CEO project? "
            "Its setup commits the CEO's files there and pushes them to its origin. [y/N] ")


def screen(written):
    """Play the pty bytes on a screen: CR, LF, cursor up, wipe a row and wipe below the cursor; colours and the
    cursor's hiding change no character."""
    rows, row, col, at = [""], 0, 0, 0
    while at < len(written):
        char = written[at]
        if char == "\x1b" and (look := re.match(r"\x1b\[(?:[\d;]*m|\?25[hl])", written[at:])):
            at += len(look[0])
            continue
        if char == "\x1b":
            escape = re.match(r"\x1b\[(\d*)([AKJ])", written[at:])
            if escape is None:
                raise AssertionError(f"unknown screen escape: {written[at:at + 20]!r}")
            amount, action = escape.groups()
            if action == "A":
                row = max(0, row - int(amount or "1"))
            elif action == "K" and amount == "2":
                rows[row] = ""
            elif action == "J" and amount in ("", "0"):
                rows[row:] = [rows[row][:col]]
            else:
                raise AssertionError(f"unsupported screen escape: {escape[0]!r}")
            at += len(escape[0])
            continue
        if char == "\r":
            col = 0
        elif char == "\n":
            row += 1
            if row == len(rows):
                rows.append("")
        else:
            line = rows[row].ljust(col)
            rows[row] = line[:col] + char + line[col + 1:]
            col += 1
        at += 1
    return "\n".join(rows).rstrip("\n")


class Launch(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.home, self.ceo_home, self.fake = (self.tmp / name for name in ("home", "ceo-home", "fake"))
        self.bin = self.fake / "bin"
        for folder in (self.home, self.bin, self.fake / "github"):
            folder.mkdir(parents=True)
        self.env = {key: value for key, value in os.environ.items()
                    if key not in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "COLUMNS", "LINES",
                                   "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
        self.env.update(HOME=str(self.home), CEO_HOME=str(self.ceo_home), FAKE=str(self.fake),
                        CLAUDE_CONFIG_DIR=str(self.fake / "claude"),
                        PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", TERM="xterm",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
                        GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid",
                        GIT_CEILING_DIRECTORIES=str(self.tmp), PYTHONDONTWRITEBYTECODE="1")
        for name, code in {"gh": GH, "claude": AGENT, **{
                name: f'#!/bin/sh\necho "{name} $*" >> "$FAKE/never.log"\n'
                for name in ("launchctl", "systemctl", "herdr")}}.items():
            path = self.bin / name
            path.write_text(code, encoding="utf-8")
            path.chmod(0o755)
        (self.bin / "ceo").symlink_to(KERNEL / "tools" / "launch.py")
        self.repo = self.folder("new-app")

    def folder(self, name):
        folder = self.tmp / name
        folder.mkdir()
        return folder

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.env, check=True,
                              capture_output=True, text=True, timeout=15).stdout.strip()

    def ceo(self, *args, cwd=None, terminal=False, term="xterm", answer=None):
        command, env = ["ceo", *args], {**self.env, "TERM": term}
        self.since = time.time()
        if not terminal:
            given = {"input": answer} if answer is not None else {"stdin": subprocess.DEVNULL}
            return subprocess.run(command, cwd=cwd or self.repo, env=env, capture_output=True, text=True,
                                  timeout=15, **given)
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))  # a terminal of 80 by 24
        try:
            child = subprocess.Popen(command, cwd=cwd or self.repo, env=env, stdin=subprocess.DEVNULL,
                                     stdout=slave, stderr=slave)
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        chunks, deadline = [], time.monotonic() + 15
        try:
            while time.monotonic() < deadline:
                if not select.select([master], [], [], 0.1)[0]:
                    continue
                try:
                    chunk = os.read(master, 65536)
                except OSError as err:
                    if err.errno != errno.EIO:
                        raise
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            else:
                self.fail("the terminal launch did not finish within 15 seconds")
            return subprocess.CompletedProcess(command, child.wait(timeout=1), b"".join(chunks).decode(), "")
        finally:
            os.close(master)
            if child.poll() is None:
                child.kill()
            child.wait()

    def records(self, name):
        path = self.fake / name
        return path.read_text().splitlines() if path.exists() else []

    def opened(self, repo=None, again=False, limit=5, prefix=(), prompt=None):
        last = json.loads(self.records("agent.log")[-1])
        elapsed = last.pop("at") - self.since
        self.assertLess(elapsed, limit)
        self.assertEqual(last, {"cwd": str(repo or self.repo),
                               "args": [*prefix, *(["--continue"] if again else []),
                                        "--append-system-prompt",
                                        prompt if prompt is not None else (KERNEL / "SESSION.md").read_text()]})

    def quiet(self, repo=None, home=()):
        self.assertFalse((self.fake / "never.log").exists(), "a service command ran")
        self.assertEqual(sorted(path.name for path in self.home.iterdir()), sorted(home), "the launch wrote into HOME")
        self.assertEqual(list(((repo or self.repo) / ".ceo").glob("tick*")), [])

    def succeeded(self, done):
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)

    def plain(self, output, name, learned=False):
        self.assertNotIn("\x1b", output)
        rows = output.splitlines()
        self.assertEqual(len(rows), 4, rows)
        for got, step in zip(rows[:3], launch.STEPS):
            self.assertRegex(got, "^" + re.escape("✓ " + step) + (r"  [0-9.]+s$" if learned else "$"))
        self.assertRegex(rows[-1], rf"^ceo · {re.escape(name)} · set up in \d+\.\ds$")

    def test_a_new_folder_gets_git_a_private_github_repo_the_ceos_files_and_its_ceo_and_no_tick(self):
        done = self.ceo()
        self.succeeded(done)
        self.plain(done.stdout, self.repo.name)
        self.opened()
        self.assertEqual(self.git("rev-parse", "--show-toplevel"), str(self.repo))
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertEqual(self.git("ls-files").splitlines(), FILES)
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), "1")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.records("gh.log"), [
            f"repo create {self.repo.name} --private --source {self.repo} --remote origin"])
        origin = self.fake / "github" / f"{self.repo.name}.git"
        self.assertEqual(self.git("--git-dir", str(origin), "rev-parse", "main"), self.git("rev-parse", "HEAD"))
        self.assertEqual((self.ceo_home / "projects.txt").read_text().splitlines(), [str(self.repo)])
        self.quiet()
        # A repository with an origin but no ceo.toml is set up at its top, even from inside it, on a y alone.
        existing = self.folder("existing-app")
        self.git("init", "-q", "-b", "main", cwd=existing)
        self.git("remote", "add", "origin", str(origin), cwd=existing)
        self.git("fetch", "-q", "origin", cwd=existing)
        self.git("checkout", "-q", "-b", "trunk", "origin/main", cwd=existing)
        self.git("rm", "-q", "--", *FILES, cwd=existing)
        self.git("commit", "-qm", "a project without CEO files", cwd=existing)
        nested = existing / "src"
        nested.mkdir()
        for answer in (None, "n\n"):
            done = self.ceo(cwd=nested, answer=answer)
            self.assertEqual((done.returncode, done.stdout), (1, ""))
            self.assertEqual(done.stderr, question(existing, 2) + "ceo: nothing done.\n")
            self.assertFalse((existing / "ceo.toml").exists())
            self.assertEqual(self.git("rev-list", "--count", "HEAD", cwd=existing), "2")
        done = self.ceo(cwd=nested, answer="y\n")
        self.assertEqual((done.returncode, done.stderr), (0, question(existing, 2)), done.stdout)
        self.opened(existing)
        self.assertEqual(self.git("branch", "--show-current", cwd=existing), "trunk")
        self.assertFalse((nested / ".git").exists())
        self.assertEqual(self.git("--git-dir", str(origin), "rev-parse", "trunk"),
                         self.git("rev-parse", "HEAD", cwd=existing))
        self.assertEqual(len(self.records("gh.log")), 1)
        self.quiet(existing)

    def test_a_name_opens_the_listed_project_called_so_from_any_folder_else_makes_it_in_dev(self):
        elsewhere = self.folder("elsewhere")
        made = self.home / "dev" / "meta"
        done = self.ceo("meta", cwd=elsewhere)
        self.succeeded(done)
        self.plain(done.stdout, "meta")
        self.opened(made)
        self.assertEqual(self.git("ls-files", cwd=made).splitlines(), FILES)
        self.assertEqual(self.records("gh.log"), [f"repo create meta --private --source {made} --remote origin"])
        for args in (("meta",), ("meta", "new")):
            with self.subTest(args=args):
                done = self.ceo(*args, cwd=elsewhere)
                self.succeeded(done)
                self.assertEqual(done.stdout, "ceo · meta\n")
                self.opened(made, again=args == ("meta",), limit=1)
        # A listed project is found by its name wherever it lives, and never made again in ~/dev.
        self.succeeded(self.ceo())
        done = self.ceo(self.repo.name, cwd=elsewhere)
        self.succeeded(done)
        self.opened(again=True, limit=1)
        self.assertFalse((self.home / "dev" / self.repo.name).exists())
        # A failed step's hint names the command typed, never a bare ceo that would set up another folder.
        flag = self.fake / "gh-fails"
        flag.touch()
        done = self.ceo("second", cwd=elsewhere)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stderr, "ceo: GitHub repo not created (HTTP 502: Bad Gateway). "
                         "git init kept; run ceo second to retry.\n")
        flag.unlink()
        done = self.ceo("second", cwd=elsewhere)
        self.succeeded(done)
        self.opened(self.home / "dev" / "second")
        self.assertEqual(len(self.records("gh.log")), 4)
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.quiet(made, home=["dev"])

    def test_home_is_never_set_up_and_a_name_is_one_plain_folder_name_listed_once(self):
        done = self.ceo(cwd=self.home)
        self.assertEqual((done.returncode, done.stdout), (1, ""))
        self.assertEqual(done.stderr, f"ceo: {self.home} is never set up as a project; "
                         "ceo <name> makes one in ~/dev/<name>.\n")
        for words in (("a/b",), (".hidden",), ("one", "two"), ("one", "two", "new")):
            with self.subTest(words=words):
                done = self.ceo(*words)
                self.assertEqual((done.returncode, done.stdout), (2, ""))
                self.assertIn("one project name at most", done.stderr)
        twins = [self.folder(side) / "twin" for side in ("left", "right")]
        for twin in twins:
            twin.mkdir()
        self.ceo_home.mkdir()
        (self.ceo_home / "projects.txt").write_text("".join(f"{twin}\n" for twin in twins))
        done = self.ceo("twin")
        self.assertEqual((done.returncode, done.stdout), (1, ""))
        self.assertEqual(done.stderr, f"ceo: 2 listed projects are called twin: {twins[0]}, {twins[1]}. "
                         "Run ceo inside the one you mean.\n")
        self.assertEqual(self.records("gh.log"), [])
        self.assertEqual(self.records("agent.log"), [])
        self.quiet()

    def test_a_project_reopens_its_last_conversation_from_any_folder_inside_and_new_starts_fresh(self):
        self.succeeded(self.ceo())
        nested = self.repo / "src" / "nested"
        nested.mkdir(parents=True)
        history = (self.ceo_home / "launches.json").read_bytes()
        for cwd in (self.repo, nested):
            for args in ((), ("new",)):
                with self.subTest(folder=cwd.name, args=args):
                    done = self.ceo(*args, cwd=cwd)
                    self.succeeded(done)
                    self.assertEqual(done.stdout, f"ceo · {self.repo.name}\n")
                    self.opened(again=not args, limit=1)
        # The configured session wrapper receives --continue before the appended prompt too.
        wrapper = self.bin / "claude-auto"
        wrapper.write_text('#!/bin/sh\nexec claude "$@"\n')
        wrapper.chmod(0o755)
        with (self.repo / "ceo.toml").open("a") as config:
            config.write('session = "claude-auto configured"\n')
        prompt = "This project's own session prompt.\n"
        (self.repo / "SESSION.md").write_text(prompt)
        self.succeeded(self.ceo(cwd=nested))
        self.opened(again=True, limit=1, prefix=("configured",), prompt=prompt)
        self.assertEqual(len(self.records("gh.log")), 1)
        self.assertEqual((self.ceo_home / "launches.json").read_bytes(), history)
        self.quiet()

    def test_an_initialized_projects_first_launch_opens_then_resumes_and_new_starts_fresh(self):
        self.git("init", "-q", "-b", "main")
        done = subprocess.run([sys.executable, "-B", str(KERNEL / "ceo.py"), "init"],
                              cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=15)
        self.succeeded(done)
        elsewhere = self.folder("elsewhere")
        for words, again in (((self.repo.name,), False), ((self.repo.name,), True),
                             ((self.repo.name, "new"), False)):
            with self.subTest(words=words, again=again):
                done = self.ceo(*words, cwd=elsewhere)
                self.succeeded(done)
                self.assertEqual(done.stdout, f"ceo · {self.repo.name}\n")
                self.opened(again=again, limit=1)
        self.assertEqual(len(self.records("agent.log")), 3)
        self.assertEqual(self.records("gh.log"), [])
        self.assertFalse((self.ceo_home / "launches.json").exists())
        self.quiet()

    def test_a_github_failure_keeps_git_init_says_one_line_and_ceo_again_finishes(self):
        flag = self.fake / "gh-fails"
        flag.touch()
        done = self.ceo()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "✓ git init\n")
        self.assertEqual(done.stderr, "ceo: GitHub repo not created (HTTP 502: Bad Gateway). "
                         "git init kept; run ceo to retry.\n")
        self.assertEqual(self.git("rev-parse", "--show-toplevel"), str(self.repo))
        self.assertFalse((self.repo / "ceo.toml").exists())
        self.assertFalse((self.ceo_home / "launches.json").exists())
        self.assertEqual(self.records("agent.log"), [])
        self.quiet()
        flag.unlink()
        done = self.ceo()
        self.succeeded(done)
        self.plain(done.stdout, self.repo.name)
        self.opened()
        self.assertEqual(len(self.records("gh.log")), 2)
        origin = self.fake / "github" / f"{self.repo.name}.git"
        self.assertEqual(self.git("--git-dir", str(origin), "rev-parse", "main"), self.git("rev-parse", "HEAD"))
        past = json.loads((self.ceo_home / "launches.json").read_text())
        self.assertEqual([len(one) for one in past], [3])
        self.quiet()

    def test_in_a_terminal_the_step_lines_give_way_to_one_line(self):
        for number, repo in enumerate((self.repo, self.folder("second-app"), self.folder("small-app"))):
            if number == 2:  # a terminal too short for the wave: the step lines alone, drawn at each event
                self.env["LINES"] = "12"
            done = self.ceo(cwd=repo, terminal=True)
            self.succeeded(done)
            self.opened(repo)
            self.assertIn("\x1b[J", done.stdout)
            if number < 2:  # the wave above the lines: it collapses into the folder's name, then the cursor is back
                self.assertRegex(done.stdout, r"\x1b\[38;5;\d+m")
                self.assertIn("●", done.stdout)
                self.assertTrue(done.stdout.index("\x1b[?25h") > done.stdout.rindex("●"))
                self.assertIn("\x1b[15A", done.stdout)
            else:
                self.assertIn("\x1b[5A", done.stdout)
                self.assertNotIn("●", done.stdout)
            self.assertRegex(screen(done.stdout), rf"^ceo · {repo.name} · set up in \d+\.\ds$")
            for step in launch.STEPS[:3]:
                self.assertIn("✓ " + step, done.stdout)
            self.assertIn("· open the CEO", done.stdout)
            steps = done.stdout.rsplit("ceo ·", 1)[0]
            if number == 0:
                self.assertNotRegex(steps, r"~|\d+(?:\.\d+)?s\b")
            else:
                for step in launch.STEPS[:3]:
                    self.assertRegex(steps, re.escape(step) + r"\s+~\d+(?:\.\d+)?s")
            self.quiet(repo)

    def test_a_reopen_in_a_terminal_plays_one_second_of_the_wave_then_its_one_line(self):
        self.succeeded(self.ceo())
        done = self.ceo(terminal=True)
        self.succeeded(done)
        self.opened(again=True, limit=4)
        self.assertGreaterEqual(json.loads(self.records("agent.log")[-1])["at"] - self.since, 1.0)
        self.assertIn("●", done.stdout)
        self.assertRegex(done.stdout, r"\x1b\[38;5;\d+m")
        self.assertIn("\x1b[?25l", done.stdout)
        self.assertTrue(done.stdout.index("\x1b[?25h") > done.stdout.rindex("●"))
        self.assertEqual(screen(done.stdout), f"ceo · {self.repo.name}")
        self.env["LINES"] = "10"  # no room: the one line alone, at once
        done = self.ceo(terminal=True)
        self.succeeded(done)
        self.opened(again=True, limit=1)
        self.assertEqual(done.stdout, f"ceo · {self.repo.name}\r\n")
        self.quiet()

    def test_the_wave_lands_as_the_names_letters_or_as_the_letters_themselves(self):
        wave = launch.Wave("ab-9", 80)
        wave.cells(0.0, 1.0)  # every dot lets go at once
        landed = ["".join(mark if colour == launch.LANDED else " " for mark, colour in row).rstrip()
                  for row in wave.cells(launch.SETTLE, 1.0)]
        self.assertEqual(textwrap.dedent("\n".join(landed)).strip("\n").splitlines(), [
            "  ●     ● ●               ●",
            "●   ●   ●   ●           ●   ●",
            "● ● ●   ● ●     ● ● ●     ● ●",
            "●   ●   ●   ●               ●",
            "●   ●   ● ●             ● ●",
        ])
        self.assertEqual(wave.done, launch.SETTLE)
        long = launch.Wave("x" * 30, 80)
        long.cells(0.0, 1.0)
        self.assertEqual("".join(mark for row in long.cells(1.0, 1.0) for mark, colour in row
                                 if colour == launch.LANDED), "x" * 30)
        with patch.dict(os.environ, COLUMNS="80", LINES="24"):  # a wave that would cut the name is none
            self.assertIsNone(launch.field("x" * 79, 1))
            self.assertIsNotNone(launch.field("x" * 60, 1))
        first = [dot["x"] for dot in launch.Wave("meta", 80).dots]
        self.assertEqual(first, [dot["x"] for dot in launch.Wave("meta", 80).dots])  # the name's own pattern

    def test_the_collapse_follows_the_steps_estimated_seconds_and_a_wave_that_will_not_start_is_none(self):
        with patch.object(launch.time, "monotonic", return_value=0.0):
            lines = launch.Lines("demo", [1.0, 98.0, 1.0], terminal=False)
            for event, step, value in (("start", 0, None), ("end", 0, 1.0), ("start", 1, None)):
                lines(event, step, value)
            self.assertAlmostEqual(lines.share(), 0.01)
        out = io.StringIO()
        with patch.dict(os.environ, COLUMNS="80", LINES="24"), patch("sys.stdout", out), \
                patch.object(launch.threading.Thread, "start", side_effect=RuntimeError("can't start new thread")):
            lines = launch.Lines("demo", None, terminal=True)
            lines("start", 0, None)
            lines.wipe()
        self.assertIsNone(lines.wave)
        self.assertNotIn("\x1b[?25l", out.getvalue())
        self.assertIn("› git init", out.getvalue())

    def test_a_dumb_terminal_gets_the_plain_lines(self):
        done = self.ceo(terminal=True, term="dumb")
        self.succeeded(done)
        self.plain(done.stdout, self.repo.name)
        self.opened()
        self.quiet()

    def test_estimates_are_each_steps_median_over_the_last_twenty_launches(self):
        with patch.dict(os.environ, self.env):
            self.assertEqual(launch.history(), self.ceo_home / "launches.json")
            self.assertIsNone(launch.estimates())
            launches = [[float(n), float(25 - n), float(n * n)] for n in range(25)]
            for took in launches:
                launch.learn(took)
            path = launch.history()
            self.assertEqual(json.loads(path.read_text()), launches[-20:])
            self.assertEqual(launch.estimates(), [14.5, 10.5, 210.5])
            self.assertEqual(list(self.ceo_home.glob(".launches.*")), [])
            for broken in ("not json", "{}", "[[]]", '[["bad", 1, 2]]'):
                path.write_text(broken)
                self.assertIsNone(launch.estimates())
            path.write_text("not json")
            launch.learn([0.12345, 0.56789, 1.23456])
            self.assertEqual(json.loads(path.read_text()), [[0.123, 0.568, 1.235]])


if __name__ == "__main__":
    unittest.main()
