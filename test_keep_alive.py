"""The tick keeper, the health line and clean workers end to end: real git, real processes, real files,
and a HOME of the test's own, so that no agent of this machine is ever touched. Only the model is
replaced: by the stub of test_door, and by a fake claude first on PATH that keeps what it was given."""
import json
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from ceo import ENDING, ROUTING, healthy, held
from test_ceo import order_text
from test_door import STUB, DoorCase
from test_tick import CEO

# The last line the claude CLI printed when the weekly limit was reached, as a worker's log of
# 2026-10-07 holds it; only its ids and the place of its reset are made neutral.
API_ERROR = json.dumps({
    "duration_api_ms": 0, "stop_reason": "stop_sequence", "session_id": "0" * 8, "total_cost_usd": 0,
    "usage": {"input_tokens": 0, "output_tokens": 0}, "modelUsage": {}, "permission_denials": [],
    "terminal_reason": "api_error", "fast_mode_state": "off", "safety_stops": 0, "is_error": True,
    "num_turns": 1, "subtype": "success", "api_error_status": 429, "api_error": "usage_limit_reached",
    "result": "You've hit your weekly limit · resets Oct 13 at 10am (UTC)", "type": "result",
    "duration_ms": 678, "uuid": "0" * 8, "queued_turn_count": 0, "result_index": 0},
    separators=(",", ":"), ensure_ascii=False)
LIMIT = "You've hit your weekly limit · resets Oct 13 at 10am (UTC)"
PROBE = b"Reply with the single word ok.\n"
HEALTH = r"^Health: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00, "

STUB += r'''
elif mode in ("flaky", "dead"):  # the CLI's api error: always, or on the first call in each folder
    part, slug = os.path.basename(os.path.dirname(os.getcwd())), os.path.basename(os.getcwd())
    calls = os.path.join(args[0], f"{part}-{slug}")
    open(calls, "a").write("a call\n")
    if mode == "dead" or len(open(calls).readlines()) == 1:
        print(open(args[1], encoding="utf-8").read(), flush=True)
        sys.exit(1)
    if part == "wt":
        build()
    else:
        answer()
elif mode == "quiet":  # dies at once without a word
    sys.exit(1)
elif mode == "recovers":  # meets the api error, goes on and answers: its last result line is completed
    print(open(args[0], encoding="utf-8").read(), flush=True)
    answer()
    print(json.dumps({"type": "result", "terminal_reason": "completed", "result": "done"}), flush=True)
elif mode == "draft":  # a worker that writes, commits nothing and exits 0 at once
    open("draft", "w").write("not what the order asked for\n")
'''

# A claude first on PATH: it keeps its arguments, environment and input, answers the login probe (with
# the api error while a .down file lies beside what it keeps) and plays the part its folder names.
FAKE_CLAUDE = r'''
import json, os, subprocess, sys
brief, kept = sys.stdin.buffer.read(), os.environ["FAKE_CLAUDE_KEPT"]
part, slug = os.path.basename(os.path.dirname(os.getcwd())), os.path.basename(os.getcwd())
with open(kept, "a") as calls:
    calls.write(json.dumps({"argv": sys.argv, "env": dict(os.environ), "cwd": os.getcwd(),
                            "stdin": brief.decode()}) + "\n")
if brief == b"Reply with the single word ok.\n":
    if os.path.exists(kept + ".down"):
        print(open(kept + ".down", encoding="utf-8").read(), flush=True)
        sys.exit(1)
    print(json.dumps({"type": "result", "is_error": False, "result": "ok"}))
elif part == "wt":
    open(slug + ".txt", "w").write(slug + "\n")
    subprocess.run(["git", "add", slug + ".txt"], check=True)
    subprocess.run(["git", "commit", "-qm", slug], check=True)
elif part == "review":
    json.dump({"summary": "checked the work", "findings": []}, open("REVIEW.json", "w"))
'''


class KeepAliveCase(DoorCase):
    def setUp(self):
        super().setUp()
        self.stub.write_text(STUB)
        self.golden = self.tmp / "api-error.json"
        self.golden.write_text(API_ERROR, encoding="utf-8")
        self.calls = self.tmp / "calls"  # the stub's count of its calls, one file per part and order
        self.calls.mkdir()
        fake = self.tmp / "agents"
        fake.mkdir()
        for name in ("claude", "othercli"):
            (fake / name).write_text(f"#!{sys.executable}\n{FAKE_CLAUDE}")
            (fake / name).chmod(0o755)
        self.claude_auto(fake)
        self.kept = self.tmp / "kept.jsonl"
        self.env.update(PATH=f"{fake}{os.pathsep}{self.env['PATH']}", FAKE_CLAUDE_KEPT=str(self.kept))

    def given(self):
        """Every call of the fake claude, in order: its arguments, environment, folder and input."""
        lines = self.kept.read_text().splitlines() if self.kept.exists() else []
        return [json.loads(line) for line in lines]

    def health(self):
        return [line for line in (self.repo / "STATUS.md").read_text().splitlines()
                if line.startswith("Health: ")]

    def count(self, part, slug):
        path = self.calls / f"{part}-{slug}"
        return len(path.read_text().splitlines()) if path.exists() else 0

    def ended(self, slug, kind, check):
        """True once the record of kind (run, review) is written and passes check, and its process is
        gone: the next pass finds its place free."""
        path = self.run_dir(slug) / {"run": "result.json", "review": "review.json"}[kind]
        return (path.exists() and check(json.loads(path.read_text()))
                and not held(self.repo / ".ceo" / "locks" / f"{slug}.{kind}"))

    def file(self, slug, done_when=None):
        """Commit and push the order into orders/: filed, the next tick starts it."""
        self.commit_order(f"{slug}.md", order_text(done_when or "test -f built"))

    def looping(self, *flags, env=None):
        """A tick with --every in a process group of its own, stopped by its group once it has made
        one pass; return what it saved in .ceo/tick.json."""
        status = self.repo / "STATUS.md"
        before = status.read_text() if status.exists() else ""
        proc = subprocess.Popen([sys.executable, str(CEO), "tick", *flags], cwd=self.repo,
                                env=env or self.env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(lambda: proc.poll() is None and os.killpg(proc.pid, signal.SIGKILL))
        self.wait_for(lambda: status.exists() and status.read_text() != before, "the tick made a pass")
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)
        return json.loads((self.repo / ".ceo" / "tick.json").read_text())


class Keeper(KeepAliveCase):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"  # HOME for every process of these tests: agents go nowhere else
        self.home.mkdir()
        self.env["HOME"] = str(self.home)

    def files_at_home(self):
        return sorted(str(path.relative_to(self.home)) for path in self.home.rglob("*") if path.is_file())

    @unittest.skipUnless(sys.platform == "darwin", "launchd is macOS's")
    def test_keep_writes_a_launch_agent_running_the_last_tick_started_and_remove_deletes_it(self):
        refused = self.ceo("keep")
        self.assertEqual((refused.returncode, refused.stderr), (2, "ceo: refused: no tick has started "
                         "here with --every, so there is no tick to keep alive.\n"))
        self.assertEqual(self.files_at_home(), [])
        worker, reviewer = self.model("build"), self.model("clean")
        saved = self.looping("--every", "30", "--timeout", "900", "--worker", worker,
                             "--reviewer", reviewer)
        self.assertEqual(saved, {"worker": worker, "reviewer": reviewer, "checker": None,
                                 "every": 30.0, "timeout": 900})
        kept = self.ceo("keep")
        agent = self.home / "Library" / "LaunchAgents" / "ceo.repo.plist"
        self.assertEqual((kept.returncode, kept.stdout), (0, f"ceo: wrote {agent}; load it with: "
                         f"launchctl bootstrap gui/{os.getuid()} {agent}\n"))
        self.assertEqual(self.files_at_home(), ["Library/LaunchAgents/ceo.repo.plist"])
        if shutil.which("plutil"):
            lint = subprocess.run(["plutil", "-lint", str(agent)], capture_output=True, text=True)
            self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
        plist = plistlib.loads(agent.read_bytes())
        log = str(self.repo / ".ceo" / "tick.log")
        self.assertEqual(plist, {
            "Label": "ceo.repo", "KeepAlive": True, "RunAtLoad": True,
            "ProgramArguments": [sys.executable, str(CEO), "tick", f"--worker={worker}",
                                 f"--reviewer={reviewer}", "--every=30.0", "--timeout=900"],
            "WorkingDirectory": str(self.repo), "StandardOutPath": log, "StandardErrorPath": log,
            "EnvironmentVariables": {"PATH": self.env["PATH"], "CEO_HOME": self.env["CEO_HOME"]}})
        # What launchd would start, started here by hand: the same tick, which saves the same flags.
        (self.repo / "STATUS.md").unlink()
        with open(log, "ab") as out:
            tick = subprocess.Popen(plist["ProgramArguments"], cwd=plist["WorkingDirectory"],
                                    env={**plist["EnvironmentVariables"], "HOME": str(self.home)},
                                    stdout=out, stderr=out, start_new_session=True)
        self.addCleanup(lambda: tick.poll() is None and os.killpg(tick.pid, signal.SIGKILL))
        self.wait_for((self.repo / "STATUS.md").exists, "the agent's tick made a pass")
        os.killpg(tick.pid, signal.SIGTERM)
        tick.wait(timeout=30)
        self.assertEqual(json.loads((self.repo / ".ceo" / "tick.json").read_text()), saved)
        removed = self.ceo("keep", "--remove")
        self.assertEqual((removed.returncode, removed.stdout), (0, f"ceo: removed {agent}; while it is "
                         f"loaded, stop it with: launchctl bootout gui/{os.getuid()}/ceo.repo\n"))
        self.assertEqual(self.files_at_home(), [])

    def test_on_linux_the_keeper_is_a_systemd_user_unit_quoted_as_systemd_reads_it(self):
        worker = """sh -c 'echo "50%" $HOME'"""
        self.looping("--every", "60", "--worker", worker)
        linux = ("import os, runpy, sys; sys.platform = 'linux'; sys.path.insert(0, os.path.dirname(sys.argv[1])); sys.argv = [sys.argv[1], 'keep']; "
                 "runpy.run_path(sys.argv[0], run_name='__main__')")
        kept = subprocess.run([sys.executable, "-c", linux, str(CEO)], cwd=self.repo, env=self.env,
                              capture_output=True, text=True, timeout=60)
        unit = self.home / ".config" / "systemd" / "user" / "ceo-repo.service"
        self.assertEqual((kept.returncode, kept.stdout), (0, f"ceo: wrote {unit}; load it with: systemctl "
                         "--user daemon-reload && systemctl --user enable --now ceo-repo.service && "
                         "loginctl enable-linger\n"))
        log = self.repo / ".ceo" / "tick.log"
        self.assertEqual(unit.read_text().splitlines(), [
            "[Service]", f"WorkingDirectory={self.repo}",
            f'Environment="PATH={self.env["PATH"]}"', f'Environment="CEO_HOME={self.env["CEO_HOME"]}"',
            f'ExecStart="{sys.executable}" "{CEO}" "tick" '
            r'''"--worker=sh -c 'echo \"50%%\" $$HOME'" "--every=60.0" "--timeout=1800"''',
            "Restart=always", "RestartSec=10", "KillMode=process", f"StandardOutput=append:{log}",
            f"StandardError=append:{log}", "", "[Install]", "WantedBy=default.target"])
        if shutil.which("systemd-analyze"):
            verify = subprocess.run(["systemd-analyze", "--user", "verify", str(unit)],
                                    capture_output=True, text=True)
            self.assertEqual(verify.returncode, 0, verify.stderr)

    def probed(self, cmd, env):
        """Start cmd, a tick, in a group of its own; stop the group once a pass wrote the health line;
        return the environment that pass's probe of claude, the default worker, was given."""
        status = self.repo / "STATUS.md"
        status.unlink(missing_ok=True)
        (self.repo / ".ceo" / "login.json").unlink(missing_ok=True)  # so that it probes anew
        proc = subprocess.Popen(cmd, cwd=self.repo, env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(lambda: proc.poll() is None and os.killpg(proc.pid, signal.SIGKILL))
        self.wait_for(lambda: status.exists() and "\nHealth: " in status.read_text(), "a pass probed")
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)
        return self.given()[-1]["env"]

    def test_the_keeper_carries_what_keep_env_names_so_it_and_a_hand_start_agree(self):
        with open(self.repo / "ceo.toml", "a") as conf:  # and where the fake claude keeps its calls
            conf.write('keep_env = "ANTHROPIC_BASE_URL FAKE_CLAUDE_KEPT"\n')
        env = {**self.env, "ANTHROPIC_BASE_URL": "http://127.0.0.1:9", "ANTHROPIC_API_KEY": "a key"}
        hand = self.probed([sys.executable, str(CEO), "tick", "--every", "60"], env)
        self.assertEqual((hand.get("ANTHROPIC_BASE_URL"), hand.get("ANTHROPIC_API_KEY")),
                         ("http://127.0.0.1:9", None))
        carried = {"PATH": self.env["PATH"], "CEO_HOME": self.env["CEO_HOME"],
                   "ANTHROPIC_BASE_URL": "http://127.0.0.1:9", "FAKE_CLAUDE_KEPT": str(self.kept)}
        if sys.platform == "darwin":
            self.assertEqual(self.ceo("keep", env=env).returncode, 0)
            agent = self.home / "Library" / "LaunchAgents" / "ceo.repo.plist"
            plist = plistlib.loads(agent.read_bytes())
            self.assertEqual(plist["EnvironmentVariables"], carried)
            # What launchd would start, started here by hand: its probe gets what the hand start's got.
            keeper = self.probed(plist["ProgramArguments"],
                                 {**plist["EnvironmentVariables"], "HOME": str(self.home)})
            self.assertEqual({name: keeper.get(name) for name in ROUTING},
                             {name: hand.get(name) for name in ROUTING})
        linux = ("import os, runpy, sys; sys.platform = 'linux'; sys.path.insert(0, os.path.dirname(sys.argv[1])); sys.argv = [sys.argv[1], 'keep']; "
                 "runpy.run_path(sys.argv[0], run_name='__main__')")
        subprocess.run([sys.executable, "-c", linux, str(CEO)], cwd=self.repo, env=env, check=True,
                       capture_output=True, timeout=60)
        unit = (self.home / ".config" / "systemd" / "user" / "ceo-repo.service").read_text().splitlines()
        self.assertEqual([line for line in unit if line.startswith("Environment=")],
                         [f'Environment="{name}={value}"' for name, value in carried.items()])


class Retry(KeepAliveCase):
    def test_an_api_error_is_no_verdict_the_run_and_the_review_are_made_again_next_pass(self):
        self.file("080-flaky")
        flaky = self.model("flaky", str(self.calls), str(self.golden))
        work = ("--worker", flaky, "--reviewer", flaky)
        self.assertEqual(self.tick(*work), "ceo: started the run of orders/080-flaky.md\n")
        self.wait_for(lambda: self.ended("080-flaky", "run", bool), "the first run ended")
        first = self.read("080-flaky")
        self.assertEqual((first["state"], first["worker_exit"], len(first["retries"])), ("retry", 1, 1))
        self.assertEqual(first["reason"], f"the worker ended with an api error, so the next pass runs "
                                          f"it again: {LIMIT}")
        self.assertTrue((self.repo / ".ceo" / "wt" / "080-flaky").is_dir(), "the worktree went")
        self.assertEqual(self.tick(*work), "ceo: started the run of orders/080-flaky.md\n")
        self.assertRegex(self.health()[0], HEALTH + "login ok, 1 retries today$")  # its record stays
        self.wait_for(lambda: self.ended("080-flaky", "run", lambda found: found["state"] == "done"),
                      "the run was made again")
        self.assertEqual(self.read("080-flaky")["retries"], first["retries"])
        self.assertEqual(self.count("wt", "080-flaky"), 2)
        self.assertEqual(self.tick(*work), "ceo: started the review of orders/080-flaky.md\n")
        self.wait_for(lambda: self.ended("080-flaky", "review", bool), "the first review ended")
        review = self.read("080-flaky", "review.json")
        self.assertEqual((review["verdict"], review["reviewer_exit"], len(review["retries"])),
                         ("retry", 1, 1))
        self.assertEqual(review["reason"], f"the reviewer ended with an api error, so the next pass "
                                           f"runs it again: {LIMIT}")
        self.assertEqual(self.tick(*work), "ceo: started the review of orders/080-flaky.md\n")
        self.wait_for(lambda: self.ended("080-flaky", "review",
                                         lambda found: found["verdict"] == "survives"),
                      "the review was made again")
        self.assertEqual(self.count("review", "080-flaky"), 2)
        self.assertEqual(self.tick(*work), "ceo: started the door\n")
        self.assertRegex(self.health()[0], HEALTH + "login ok, 2 retries today$")
        self.wait_for(lambda: self.settled("080-flaky") and not held(
            self.repo / ".ceo" / "locks" / "door.lock"), "the final tick's door ended")

    def test_three_retries_a_day_then_the_record_is_blocked_with_its_reason(self):
        self.file("081-dead")
        work = ("--worker", self.model("dead", str(self.calls), str(self.golden)))
        for attempt in range(1, 5):
            self.assertEqual(self.tick(*work), "ceo: started the run of orders/081-dead.md\n")
            last = ("retry", attempt) if attempt < 4 else ("blocked", 3)
            self.wait_for(lambda: self.ended("081-dead", "run", lambda found: (
                found["state"], len(found["retries"])) == last), f"attempt {attempt} ended")
        result = self.read("081-dead")
        self.assertEqual((result["state"], len(result["retries"])), ("blocked", 3))
        self.assertEqual(result["reason"], f"the worker ended with an api error after three retries "
                                           f"today: {LIMIT}")
        self.assertEqual(self.tick(*work), "", "a blocked run was started again")
        self.assertEqual(self.count("wt", "081-dead"), 4)
        self.assertRegex(self.health()[0], HEALTH + "login ok, 3 retries today$")

    def test_a_model_that_exits_at_once_without_an_answer_is_retry_whatever_its_exit_code(self):
        self.commit_order("082-quiet.md", order_text("test -f built"))
        quiet = self.ceo("run", "orders/082-quiet.md", "--worker", self.model("quiet"))
        self.assertEqual(quiet.returncode, 2, quiet.stderr)
        self.assertEqual((self.read("082-quiet")["state"], self.read("082-quiet")["reason"]),
                         ("retry", "the worker ended with an api error, so the next pass runs it "
                                   "again: it exited 1 in its first minute without an answer"))
        self.commit_order("083-silent.md", order_text("test -f built"))
        silent = self.ceo("run", "orders/083-silent.md", "--worker", self.model("silent"))
        self.assertEqual((silent.returncode, self.read("083-silent")["state"]), (2, "retry"))
        self.assertEqual(self.read("083-silent")["reason"], "the worker ended with an api error, so the "
                         "next pass runs it again: it exited 0 in its first minute without an answer")
        # Work left in the worktree, committed or not, is an answer: the order's test judges it.
        self.commit_order("089-draft.md", order_text("test -f built"))
        draft = self.ceo("run", "orders/089-draft.md", "--worker", self.model("draft"))
        self.assertEqual((draft.returncode, self.read("089-draft")["state"]), (1, "failed"))
        # A reviewer the same: without REVIEW.json, its exit 0 in its first minute is no verdict.
        self.commit_order("090-built.md")
        self.assertEqual(self.ceo("run", "orders/090-built.md", "--worker", self.model("build"))
                         .returncode, 0)
        review = self.ceo("review", "orders/090-built.md", "--reviewer", self.model("silent"))
        self.assertEqual(review.returncode, 2, review.stderr)
        self.assertEqual(self.read("090-built", "review.json")["reason"], "the reviewer ended with an api "
                         "error, so the next pass runs it again: it exited 0 in its first minute without "
                         "an answer")

    def test_only_the_last_result_line_decides_so_an_api_error_recovered_from_is_judged(self):
        self.commit_order("088-recovered.md")
        self.assertEqual(self.ceo("run", "orders/088-recovered.md", "--worker", self.model("build"))
                         .returncode, 0)
        review = self.ceo("review", "orders/088-recovered.md", "--reviewer",
                          self.model("recovers", str(self.golden)))
        self.assertEqual(review.returncode, 0, review.stderr)
        found = self.read("088-recovered", "review.json")
        self.assertEqual((found["verdict"], found["reviewer_exit"], found["retries"]), ("survives", 0, []))
        self.assertIn(LIMIT, (self.run_dir("088-recovered") / "review.log").read_text())  # it met it


class Login(KeepAliveCase):
    def file_named(self, slug, worker):
        text = order_text(f"test -f {slug}.txt")
        text = text.replace("\n## Done when", f"\n## Worker\n{worker}\n\n## Done when")
        self.commit_order(f"{slug}.md", text)

    def test_a_failed_login_holds_only_the_starts_of_its_own_command(self):
        with open(self.repo / "ceo.toml", "a") as conf:
            conf.write("same_vendor_review = true\n"
                       f"[workers]\nstub = {json.dumps(self.model('own'))}\n"
                       f"[reviewers]\nclaude = {json.dumps(self.model('clean'))}\n")
        self.commit_all("workers")
        self.reviewed("201-ready")
        self.file_named("202-stub", "stub")
        self.file("204-claude", "test -f 204-claude.txt")
        self.kept.with_name("kept.jsonl.down").write_text(API_ERROR, encoding="utf-8")
        said = self.tick()  # no --worker: every pass probes the default claude
        self.assertIn("ceo: started the run of orders/202-stub.md\n", said)
        self.assertIn("LOGIN FAILED", said)
        self.assertIn("ceo: started the door\n", said)
        self.assertNotIn("204-claude", said)
        self.wait_for(lambda: self.ended("202-stub", "run", lambda r: r["state"] == "done"),
                      "the stub built while claude's login failed")
        self.tick_while(lambda: self.settled("201-ready"), "the door merged while claude's login failed")
        self.assertEqual(self.git("--git-dir", str(self.origin), "show", "main:201-ready.txt"),
                         "201-ready.txt")
        self.assertFalse(self.run_dir("204-claude").exists())
        self.assertRegex(self.health()[0], HEALTH + "LOGIN FAILED, 0 retries today$")
        self.assertEqual([call["stdin"].encode() for call in self.given()], [PROBE])

    def test_a_second_cli_whose_login_answers_starts_while_another_fails(self):
        auto = self.tmp / "agents" / "claude-auto"
        auto.write_text(f"#!{sys.executable}\nimport os\n"
                        "os.environ['FAKE_CLAUDE_KEPT'] += '.b'\n" + FAKE_CLAUDE)
        auto.chmod(0o755)
        with open(self.repo / "ceo.toml", "a") as conf:
            conf.write("same_vendor_review = true\n[workers]\nauto = \"claude-auto -p\"\n"
                       f"[reviewers]\nclaude = {json.dumps(self.model('clean'))}\n")
        self.commit_all("workers")
        self.file_named("203-auto", "auto")
        self.kept.with_name("kept.jsonl.down").write_text(API_ERROR, encoding="utf-8")
        said = self.tick("--worker", "claude -p")  # the default is claude-auto now
        self.assertIn("LOGIN FAILED", said)
        self.assertIn("ceo: started the run of orders/203-auto.md\n", said)
        self.wait_for(lambda: self.ended("203-auto", "run", lambda r: r["state"] == "done"),
                      "the second CLI built while claude's login failed")
        login = json.loads((self.repo / ".ceo" / "login.json").read_text())
        self.assertEqual({model: why for model, (_, why) in login.items()},
                         {"claude -p": LIMIT, "claude-auto -p": ""})
        self.assertTrue(all(time.time() - when < 600 for when, _ in login.values()))
        self.assertEqual([call["stdin"].encode() for call in self.given()], [PROBE])
        calls = [json.loads(line) for line in self.kept.with_name("kept.jsonl.b").read_text().splitlines()]
        self.assertEqual([call["stdin"].encode() == PROBE for call in calls], [True, False])
        self.assertEqual(calls[0]["argv"][1:], ["-p", "--setting-sources", "project"])
        self.assertRegex(self.health()[0], HEALTH + "LOGIN FAILED, 0 retries today$")

    def test_a_hold_on_every_start_written_by_hand_holds_the_door_too(self):
        with open(self.repo / "ceo.toml", "a") as conf:
            conf.write("same_vendor_review = true\n"
                       f"[workers]\nstub = {json.dumps(self.model('own'))}\n")
        self.commit_all("workers")
        self.reviewed("201-ready")
        self.file_named("202-stub", "stub")
        self.file("204-claude", "test -f 204-claude.txt")
        before = self.main()
        (self.repo / ".ceo" / "login.json").write_text(json.dumps(
            {"*": [time.time(), "every start held by hand"]}))
        self.assertEqual(self.tick(), "")
        self.assertFalse(self.run_dir("202-stub").exists())
        self.assertFalse(self.run_dir("204-claude").exists())
        self.assertEqual(self.main(), before)
        self.assertNotIn("merged", self.read("201-ready"))
        self.assertEqual([pr["state"] for pr in self.server()], ["OPEN"])
        self.assertRegex(self.health()[0], HEALTH + "LOGIN FAILED, 0 retries today$")
        self.assertEqual([call["stdin"].encode() for call in self.given()], [PROBE])

    def test_a_reviewer_that_fails_the_login_probe_holds_only_its_reviews(self):
        self.commit_order("084-built.md")
        self.assertEqual(self.ceo("run", "orders/084-built.md", "--worker", self.model("build"))
                         .returncode, 0)
        self.file("085-waits")
        down = self.kept.with_name("kept.jsonl.down")
        down.write_text(API_ERROR, encoding="utf-8")  # the probe meets the weekly limit
        work = ("--worker", self.model("build"), "--reviewer", "claude -p")
        said = f"ceo: LOGIN FAILED, so nothing new starts with claude -p: it did not answer: {LIMIT}.\n"
        self.assertEqual(self.tick(*work), said + "ceo: started the run of orders/085-waits.md\n")
        self.assertRegex(self.health()[0], HEALTH + "LOGIN FAILED, 0 retries today$")
        events = [json.loads(line) for line in
                  (self.repo / ".ceo" / "events.jsonl").read_text().splitlines()]
        self.assertEqual([(found["event"], found["slug"], found["sentence"]) for found in events
                          if not found["slug"]],  # beside them, the runs' own (order 5)
                         [("login", "", said[5:-1])])
        probe, = self.given()  # one call: the probe, clean, in an empty folder of its own
        self.assertEqual((probe["argv"][1:], probe["stdin"].encode()),
                         (["-p", "--setting-sources", "project"], PROBE))
        self.assertFalse(probe["cwd"].startswith(str(self.repo)), probe["cwd"])
        self.wait_for(lambda: self.ended("085-waits", "run", lambda r: r["state"] == "done"),
                      "the stub built while its reviewer's login failed")
        self.assertEqual(self.tick(*work), "", "a probe younger than ten minutes was made again")
        self.assertEqual(len(self.given()), 1)
        self.assertFalse((self.run_dir("085-waits") / "review.json").exists())
        self.assertFalse((self.run_dir("084-built") / "review.json").exists())
        # Ten minutes on, the login works again: the next pass probes once more and starts the work.
        login = self.repo / ".ceo" / "login.json"
        login.write_text(json.dumps({model: [when - 600, why] for model, (when, why)
                                     in json.loads(login.read_text()).items()}))
        down.unlink()
        started = self.tick(*work)
        self.assertEqual(started.splitlines(), ["ceo: started the review of orders/084-built.md",
                                                "ceo: started the review of orders/085-waits.md"])
        self.assertRegex(self.health()[0], HEALTH + "login ok, 0 retries today$")
        self.wait_for(lambda: self.ended("084-built", "review", bool), "the review ended")
        self.wait_for(lambda: self.ended("085-waits", "review", bool), "the waiting review ended")
        self.assertEqual(self.read("084-built", "review.json")["verdict"], "survives")
        self.assertEqual(self.read("085-waits", "review.json")["verdict"], "survives")
        self.assertEqual([call["stdin"].encode() == PROBE for call in self.given()], [True, True, False, False])

    def test_every_pass_idle_or_not_probes_the_worker_and_trusts_the_probe_ten_minutes(self):
        down = self.kept.with_name("kept.jsonl.down")
        down.write_text(API_ERROR, encoding="utf-8")  # the worker's claude meets the weekly limit
        work = ("--worker", "claude -p")  # no order: a pass that starts nothing
        said = f"ceo: LOGIN FAILED, so nothing new starts with claude -p: it did not answer: {LIMIT}.\n"
        self.assertEqual(self.tick(*work), said)
        self.assertRegex(self.health()[0], HEALTH + "LOGIN FAILED, 0 retries today$")
        self.assertEqual([call["stdin"].encode() for call in self.given()], [PROBE])
        self.assertEqual(self.tick(*work), "", "a probe younger than ten minutes was made again")
        self.assertRegex(self.health()[0], HEALTH + "LOGIN FAILED, 0 retries today$")
        login = self.repo / ".ceo" / "login.json"
        login.write_text(json.dumps({model: [when - 600, why] for model, (when, why)
                                     in json.loads(login.read_text()).items()}))
        down.unlink()
        self.assertEqual(self.tick(*work), "")
        self.assertRegex(self.health()[0], HEALTH + "login ok, 0 retries today$")
        self.assertEqual([call["stdin"].encode() for call in self.given()], [PROBE, PROBE])

    def test_cached_failures_block_only_their_vendor_and_survive_other_vendors_probes(self):
        with (self.repo / "ceo.toml").open("a") as conf:
            conf.write('\n[vendor]\nclaude = "claude"\nothercli = "openai"\n'
                       '[isolation]\nothercli = ""\n')
        login = self.repo / ".ceo" / "login.json"
        login.parent.mkdir(exist_ok=True)
        with patch.dict(os.environ, self.env):
            for failed, working in (("claude -p", "othercli"), ("othercli", "claude -p")):
                with self.subTest(failed=failed):
                    cached = [time.time(), LIMIT]
                    login.write_text(json.dumps({failed: cached}))
                    calls = len(self.given())
                    self.assertFalse(healthy(self.repo, failed + " --model another"))
                    self.assertEqual(len(self.given()), calls, "a blocked vendor was probed again")
                    self.assertTrue(healthy(self.repo, working))
                    self.assertEqual(len(self.given()), calls + 1)
                    self.assertTrue(healthy(self.repo, working))
                    self.assertEqual(len(self.given()), calls + 1, "a fresh success was probed again")
                    self.assertEqual(json.loads(login.read_text())[failed], cached)
                    self.assertFalse(healthy(self.repo, failed))
                    self.assertFalse(healthy(self.repo), "overall health hid the failed vendor")
                    login.write_text(json.dumps({failed: [cached[0] - 600, LIMIT]}))
                    self.assertTrue(healthy(self.repo, failed))
                    self.assertTrue(healthy(self.repo))


class Clean(KeepAliveCase):
    def test_every_worker_and_reviewer_sees_only_this_repository(self):
        self.file("086-clean", "test -f 086-clean.txt")
        routing = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9", "ANTHROPIC_AUTH_TOKEN": "a token",
                   "ANTHROPIC_API_KEY": "a key"}
        env = {**self.env, **routing}
        tick = subprocess.Popen([sys.executable, str(CEO), "tick", "--every", "0.5", "--worker",
                                 "claude -p"], cwd=self.repo, env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(lambda: tick.poll() is None and os.killpg(tick.pid, signal.SIGKILL))
        self.wait_for(lambda: (self.run_dir("086-clean") / "result.json").exists()
                      and self.settled("086-clean"), "the order merged", timeout=120)
        os.killpg(tick.pid, signal.SIGTERM)
        tick.wait(timeout=30)
        self.assertFalse((self.run_dir("086-clean") / "vision.json").exists())
        calls = self.given()
        parts = [os.path.basename(os.path.dirname(call["cwd"])) for call in calls]
        self.assertEqual(parts[1:], ["wt", "review"])  # after the one probe of claude -p
        order = (self.repo / "orders" / "086-clean.md").read_text()
        self.assertEqual(calls[1]["stdin"], order + ENDING.decode())
        for call in calls:
            self.assertEqual(call["argv"][1:], ["-p", "--setting-sources", "project"])
            self.assertEqual(set(routing) & set(call["env"]), set(), "the owner's routing reached a model")
        # A kept variable reaches the model; another CLI gets the isolation named.
        (self.repo / "ceo.toml").write_text('ram_floor_gb = 0\nkeep_env = "ANTHROPIC_BASE_URL"\n\n[isolation]\n'
                                            'othercli = "--only-here --and-this"\n')
        self.commit_order("087-other.md", order_text("test -f 087-other.txt"))
        run = self.ceo("run", "orders/087-other.md", "--worker", "othercli", env=env)
        self.assertEqual(run.returncode, 0, run.stderr)
        last = self.given()[-1]
        self.assertEqual(last["argv"][1:], ["--only-here", "--and-this"])
        self.assertEqual({name: last["env"].get(name) for name in routing},
                         {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9", "ANTHROPIC_AUTH_TOKEN": None,
                          "ANTHROPIC_API_KEY": None})
        self.assertIn("worker starts: othercli\n", (self.run_dir("087-other") / "worker.log").read_text())


if __name__ == "__main__":
    unittest.main()
