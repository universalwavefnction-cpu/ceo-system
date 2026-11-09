"""The board pull, tools/fleet.py, run as its job runs it: a subprocess over scratch projects, each a git repository
with one commit, under a scratch CEO_HOME, with stand-ins for ssh, scp and launchctl first on PATH, so no real host
and no launchd is ever reached."""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOL = ROOT / "tools" / "fleet.py"
OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
LOGGED = '''import json, os, shutil, subprocess, sys
from pathlib import Path
with open(os.environ["FLEET_LOG"], "a") as log:
    log.write(json.dumps([Path(sys.argv[0]).name, *sys.argv[1:]]) + "\\n")
'''
# A host named down.* refuses; any other runs the command in $FLEET_THERE, as its home, as a login there would.
SSH = LOGGED + '''args = sys.argv[1:]
while args[0] == "-o":
    args = args[2:]
if args[0].startswith("down."):
    sys.stderr.write(f"ssh: connect to host {args[0]} port 22: Connection refused\\n")
    sys.exit(255)
there = os.environ["FLEET_THERE"]
sys.exit(subprocess.run(["sh", "-c", " ".join(args[1:])], cwd=there, env=dict(os.environ, HOME=there)).returncode)
'''
SCP = LOGGED + '''args, files = sys.argv[1:], []
while len(args) > 1:
    if args[0] in ("-q", "-o"):
        args = args[1 + (args[0] == "-o"):]
    else:
        files.append(args.pop(0))
host, folder = args[0].split(":", 1)
if host.startswith("down."):
    sys.exit(f"ssh: connect to host {host} port 22: Connection refused")
for file in files:
    shutil.copy(file, Path(os.environ["FLEET_THERE"], folder))
'''


class Fleet(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.tmp = Path(scratch.name).resolve()
        self.home, self.there, self.log = self.tmp / "home", self.tmp / "there", self.tmp / "commands.jsonl"
        self.board, stand_ins = self.home / "board", self.tmp / "bin"
        stand_ins.mkdir()
        self.there.mkdir()
        for name, body in (("ssh", SSH), ("scp", SCP), ("launchctl", LOGGED)):
            (stand_ins / name).write_text(f"#!{sys.executable}\n{body}")
            (stand_ins / name).chmod(0o755)
        (stand_ins / "python3").symlink_to(sys.executable)  # the python3 a host's PATH gives, this one
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(CEO_HOME=str(self.home), FLEET_LOG=str(self.log), FLEET_THERE=str(self.there),
                        CLAUDE_CONFIG_DIR=str(self.tmp / "claude"),  # no notebook of the owner's Claude Code
                        PATH=f"{stand_ins}{os.pathsep}{os.environ.get('PATH', '')}", PYTHONDONTWRITEBYTECODE="1")
        self.written = None

    def project(self, path):
        path.mkdir(parents=True)
        for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "base"]):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=path,
                           env=self.env, check=True, capture_output=True)
        return path

    def status_file(self, name):
        """A status file of name, as the kernel's own board command writes one."""
        if self.written is None:
            self.written = json.loads(subprocess.run(
                [sys.executable, "-B", str(ROOT / "ceo.py"), "board", "--name", "kept", "--host", "old"],
                cwd=self.project(self.tmp / "kept"), env=self.env, check=True, capture_output=True, timeout=60).stdout)
        return (json.dumps({**self.written, "ceo": name}, indent=2) + "\n").encode()

    def fake(self, name, body):
        """A python that is not one: whatever it is asked, it runs body."""
        path = self.tmp / f"{name}.sh"
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
        return str(path)

    def run_tool(self, *args, env=None):
        done = subprocess.run([sys.executable, "-B", str(TOOL), *args], cwd=self.tmp, env=env or self.env,
                              capture_output=True, text=True, timeout=300)
        return done, (done.stdout + done.stderr).splitlines()

    def pull(self, fleet):
        self.home.mkdir(exist_ok=True)
        (self.home / "fleet.json").write_text(json.dumps(fleet))
        return self.run_tool("pull")

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def said(self, lines, name):
        return [line for line in lines if line.startswith(f"{name}: ")]

    def test_each_ceo_is_pulled_or_keeps_its_last_good_file_and_says_why(self):
        shop = self.project(self.tmp / "shop")
        self.project(self.there / "blog")
        shops = self.tmp / "shop-file.json"
        shops.write_bytes(self.status_file("shop"))
        failing = {  # each one's python, and what its line says
            "half": (self.fake("half", """printf '{"ceo": "half", "status": {'"""), "not JSON"),
            "deep": (self.fake("deep", "printf '%*s' 100000 '' | tr ' ' '['"), "not JSON"),  # past what json nests
            "bare": (self.fake("bare", """echo '{"ceo": "bare"}'"""), "not a status file of bare"),
            "other": (self.fake("other", f"cat '{shops}'"), "not a status file of other: it is shop's"),
            "loud": (self.fake("loud", "echo first >&2; echo '  last   words' >&2; exit 3"), "exited 3: last words"),
            # its child holds the output open: only stopping the whole group ends it
            "slow": (self.fake("slow", "sleep 300 & wait"), "timed out after 5 s"),
            "lost": (sys.executable, f"could not start: No such file or directory: {self.tmp / 'gone'}")}
        ceos = [dict(name=name, host="lab", folder=str(self.tmp / "gone" if name == "lost" else shop), python=python)
                for name, (python, _) in failing.items()]
        ceos += [dict(name="gone", host="far", ssh="down.example.com", folder="~/gone", kernel="~/k/ceo.py"),
                 dict(name="shop", host="lab", folder=str(shop)),
                 dict(name="blog", host="far", ssh="blog.example.com", folder="~/blog", python=sys.executable,
                      kernel=str(ROOT / "ceo.py"))]
        self.board.mkdir(parents=True)
        (self.board / "shop.json").write_bytes(self.status_file("shop"))  # an older one, to be replaced
        kept = {name: self.status_file(name) for name in [*failing, "gone"]}
        for name, data in kept.items():
            (self.board / f"{name}.json").write_bytes(data)
        started = time.monotonic()
        done, lines = self.pull(dict(timeout=5, ceos=ceos, copies=[
            dict(ssh="down.example.com", folder=".ceo/board"), dict(ssh="board.example.com", folder=".ceo/board")]))
        self.assertEqual(done.returncode, 0, lines)
        self.assertLess(time.monotonic() - started, 60, "a CEO past its time limit held up the pull")
        for name, host in (("shop", "lab"), ("blog", "far")):
            found = json.loads((self.board / f"{name}.json").read_text(encoding="utf-8"))
            self.assertEqual((found["ceo"], found["host"]), (name, host))
            self.assertEqual(self.said(lines, name), [])
        for name, data in kept.items():
            self.assertEqual((self.board / f"{name}.json").read_bytes(), data, f"{name}'s last good file was replaced")
            self.assertEqual(len(self.said(lines, name)), 1, lines)
            self.assertTrue(self.said(lines, name)[0].startswith(f"{name}: kept the last file: "), lines)
        for name, (_, why) in failing.items():
            self.assertEqual(self.said(lines, name), [f"{name}: kept the last file: {why}"])
        self.assertEqual(self.said(lines, "gone"), [
            "gone: kept the last file: exited 255: ssh: connect to host down.example.com port 22: Connection refused"])
        there = f"cd ~/blog && {sys.executable} {ROOT / 'ceo.py'} board --name blog --host far"
        self.assertIn(["ssh", *OPTIONS, "blog.example.com", there], self.commands())
        self.assertTrue(lines[-1].startswith("fleet: ") and lines[-1].endswith(": 2 of 10 CEOs pulled, 10 on the page"))
        page = (self.board / "index.html").read_text(encoding="utf-8")
        names = [ceo["name"] for ceo in ceos]
        for name in names:
            self.assertIn(f'data-view="{name}"', page)
        self.assertEqual(sorted(path.name for path in self.board.iterdir()),
                         sorted(["index.html", *(f"{name}.json" for name in names)]), "a half-written file is left")
        # A failed copy says so, and the next one still gets every status file.
        self.assertEqual([line for line in lines if line.startswith("copy")], [
            "copy to down.example.com:.ceo/board failed: exited 255: ssh: connect to host down.example.com port 22: "
            "Connection refused"])
        self.assertIn(["ssh", *OPTIONS, "board.example.com", "mkdir -p .ceo/board"], self.commands())
        copied = self.there / ".ceo" / "board"
        self.assertEqual(sorted(path.name for path in copied.iterdir()), sorted(f"{name}.json" for name in names))
        for name in names:
            self.assertEqual((copied / f"{name}.json").read_bytes(), (self.board / f"{name}.json").read_bytes())

    def test_a_ceo_without_a_good_file_is_left_off_the_page(self):
        shop = self.project(self.tmp / "shop")
        broken = self.fake("broken", "exit 1")
        self.board.mkdir(parents=True)
        (self.board / "junk.json").write_text("{")  # as a pull that only checked for a non-empty file kept one
        done, lines = self.pull(dict(ceos=[dict(name="new", host="lab", folder=str(shop), python=broken),
                                           dict(name="junk", host="lab", folder=str(shop), python=broken),
                                           dict(name="shop", host="lab", folder=str(shop))]))
        self.assertEqual(done.returncode, 0, lines)
        self.assertEqual(self.said(lines, "new"), ["new: has no file yet: exited 1"])
        self.assertEqual(self.said(lines, "junk"), ["junk: kept the last file: exited 1",
                                                    "junk: its file is left off the page: not JSON"])
        self.assertEqual((self.board / "junk.json").read_text(), "{")
        self.assertFalse((self.board / "new.json").exists())
        page = (self.board / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-view="shop"', page)
        self.assertNotIn('data-view="new"', page)
        self.assertNotIn('data-view="junk"', page)

    def test_a_relative_ceo_home_is_taken_from_where_the_pull_runs(self):
        shop = self.project(self.tmp / "shop")
        self.home.mkdir()
        (self.home / "fleet.json").write_text(json.dumps(dict(
            ceos=[dict(name="shop", host="lab", folder=str(shop))],
            copies=[dict(ssh="board.example.com", folder=".ceo/board")])))
        done, lines = self.run_tool("pull", env=dict(self.env, CEO_HOME=self.home.name))  # it runs in self.tmp
        self.assertEqual(done.returncode, 0, lines)
        self.assertEqual(json.loads((self.board / "shop.json").read_text(encoding="utf-8"))["ceo"], "shop")
        self.assertIn('data-view="shop"', (self.board / "index.html").read_text(encoding="utf-8"))
        self.assertEqual((self.there / ".ceo" / "board" / "shop.json").read_bytes(),
                         (self.board / "shop.json").read_bytes())

    def test_with_no_status_file_to_render_it_fails_and_makes_no_page(self):
        done, lines = self.pull(dict(ceos=[dict(name="new", host="lab", folder=str(self.tmp),
                                                python=self.fake("broken", "exit 1"))]))
        self.assertNotEqual(done.returncode, 0, lines)
        self.assertEqual(lines[0], "new: has no file yet: exited 1")
        self.assertIn("has a status file", lines[-1])
        self.assertFalse((self.board / "index.html").exists())

    def test_a_fleet_file_it_cannot_pull_from_stops_it_before_anything(self):
        shop = dict(name="shop", host="lab", folder=str(self.tmp))
        cases = {"missing": None, "not JSON": "{", "no CEO": {"ceos": []},
                 "no folder": {"ceos": [{"name": "shop", "host": "lab"}]},
                 "ssh with no kernel": {"ceos": [{**shop, "ssh": "blog.example.com"}]},
                 "a name twice": {"ceos": [shop, shop]}, "a path for a name": {"ceos": [{**shop, "name": "../x"}]},
                 "a NUL in a folder": {"ceos": [{**shop, "folder": "a\0b"}]},
                 "no time limit": {"timeout": 0, "ceos": [shop]},
                 "a copy with no folder": {"ceos": [shop], "copies": [{"ssh": "board.example.com"}]}}
        for case, fleet in cases.items():
            with self.subTest(case):
                shutil.rmtree(self.home, ignore_errors=True)
                if fleet is not None:
                    self.home.mkdir()
                    (self.home / "fleet.json").write_text(fleet if isinstance(fleet, str) else json.dumps(fleet))
                done, lines = self.run_tool("pull")
                self.assertNotEqual(done.returncode, 0, lines)
                self.assertEqual(len(lines), 1, lines)
                self.assertIn(str(self.home / "fleet.json"), lines[0])
                self.assertIn("tools/fleet.example.json", lines[0])
                self.assertFalse(self.board.exists())
        self.assertEqual(self.commands(), [], "a command ran")

    def test_install_writes_the_five_minute_job_and_never_loads_it(self):
        agents = self.tmp / "Library" / "LaunchAgents"
        done, lines = self.run_tool("install", str(agents))
        self.assertEqual(done.returncode, 0, lines)
        log = str(self.board / "fleet.log")
        self.assertEqual(plistlib.loads((agents / "ceo-kernel.fleet.plist").read_bytes()), dict(
            Label="ceo-kernel.fleet", ProgramArguments=[os.path.abspath(sys.executable), str(TOOL), "pull"],
            StartInterval=300, EnvironmentVariables={"CEO_HOME": str(self.home)}, StandardOutPath=log,
            StandardErrorPath=log))
        self.assertEqual(lines[-1], f"launchctl bootstrap gui/$(id -u) {agents / 'ceo-kernel.fleet.plist'}")
        self.assertTrue(self.board.is_dir(), "launchd would have no folder to log in")
        self.assertEqual(self.commands(), [], "launchctl ran")

    def test_the_example_is_a_working_fleet(self):
        stranger = self.tmp / "stranger"  # the home of whoever cloned the kernel; FLEET_THERE is the server's
        self.project(stranger / "dev" / "shop")
        self.project(self.there / "dev" / "blog")
        (self.there / "ceo-kernel").symlink_to(ROOT)
        self.home.mkdir()
        shutil.copy(ROOT / "tools" / "fleet.example.json", self.home / "fleet.json")
        done, lines = self.run_tool("pull", env=dict(self.env, HOME=str(stranger)))
        self.assertEqual(done.returncode, 0, lines)
        self.assertEqual(len(lines), 1, lines)
        for name, host in (("shop", "laptop"), ("blog", "server")):
            found = json.loads((self.board / f"{name}.json").read_text(encoding="utf-8"))
            self.assertEqual((found["ceo"], found["host"]), (name, host))
            self.assertTrue((self.there / ".ceo" / "board" / f"{name}.json").is_file())


if __name__ == "__main__":
    unittest.main()
