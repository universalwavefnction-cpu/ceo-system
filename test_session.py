"""The owner's session: real CLI, git and files, a stand-in session command, no network."""
import contextlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import ceo_init

KERNEL = Path(__file__).resolve().parent
EXPECTED = """\
You are the CEO of the project in this folder, in a live session with its owner.

Read first, in this order: `$CEO_HOME/OWNER.md`, `CEO.md`, `VISION.md`, `STATUS.md`, `IDEAS.md`, `decisions.md`. They hold
everything; nothing lives in this chat. Your duties, the owner's three asks and the rules are in `CEO.md`.

This session is for thinking with the owner, by those duties in their order of priority.

Write the plan as drafts, which the kernel never reads: `drafts/plans/<name>.md` for the whole, with how many orders
run at once (`at once <n>`) and why, from `STATUS.md`'s `Free:` line, and what waits on what (`after`); one
`drafts/orders/<NNN>-<slug>.md` per order, and commit them. Then read the plan back as a table, one line per order,
saying in one sentence what it builds and how we will know it worked. Answer questions; change drafts when asked.
Work starts once the owner signs an intent: outcome, why, proof, limits, wake me, ends, with what you would cut and
your recommendation. Under a signed intent, file each plan yourself: move it into `plans/` and its orders into
`orders/` and commit; filing alone starts the work. Never ask again inside its limits; outside them, ask.

When the owner asks for visual mocks, draft a mock order (`## Mocks`): one worker draws all variants for the owner's pick.

Speak to the owner in short, plain English. End each turn with what you need from them, or "nothing".
"""
STUB = '''import json, os, sys
from pathlib import Path
with Path(os.environ["SESSION_CAPTURE"] + ".calls").open("a") as calls:
    calls.write("run\\n")
Path(os.environ["SESSION_CAPTURE"]).write_text(json.dumps({
    "cwd": os.getcwd(), "args": sys.argv[1:], "input": sys.stdin.read()}))
print("session output")
print("session error", file=sys.stderr)
sys.exit(int(os.environ.get("SESSION_EXIT", "0")))
'''


class Session(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.nested = self.repo / "nested"
        self.nested.mkdir()
        self.capture = self.tmp / "session.json"
        self.stub = self.tmp / "session command.py"
        self.stub.write_text(STUB)
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "Stub", "GIT_AUTHOR_EMAIL": "stub@example.invalid",
                    "GIT_COMMITTER_NAME": "Stub", "GIT_COMMITTER_EMAIL": "stub@example.invalid",
                    "HOME": str(self.tmp / "home"), "CLAUDE_CONFIG_DIR": str(self.tmp / "claude config"),
                    "CEO_HOME": str(self.tmp / "ceo"), "SESSION_CAPTURE": str(self.capture)}
        self.git("init", "-q", "-b", "main")
        (self.repo / ".gitignore").write_text(".ceo/\n")
        (self.repo / "SESSION.md").write_text(EXPECTED)
        self.git("add", ".gitignore", "SESSION.md")
        self.git("commit", "-qm", "start")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def ceo(self, *args, words=""):
        return subprocess.run([sys.executable, "-B", str(KERNEL / "ceo.py"), *args], cwd=self.nested,
                              env=self.env, input=words, capture_output=True, text=True, timeout=30)

    def configure(self, *command):
        command = shlex.join(command or [sys.executable, str(self.stub), "configured argument"])
        (self.repo / "ceo.toml").write_text(f"session = {json.dumps(command)}\n")

    def fake_claude(self):
        commands = self.tmp / "bin"
        commands.mkdir()
        claude = commands / "claude"
        claude.write_text(f'#!/bin/sh\nexec {shlex.join([sys.executable, str(self.stub)])} "$@"\n')
        claude.chmod(0o755)
        self.env["PATH"] = f"{commands}{os.pathsep}{self.env['PATH']}"
        return claude

    def history(self, config):
        folder = config / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", str(self.repo))
        folder.mkdir(parents=True)
        return folder

    def test_session_prompt_is_exact_and_never_claude_md(self):
        self.assertEqual((KERNEL / "SESSION.md").read_bytes(), EXPECTED.encode())
        self.assertFalse((KERNEL / "CLAUDE.md").exists())

    def test_ceo_opens_with_the_four_duties_in_priority_order(self):
        lines = (KERNEL / "CEO.md").read_text().splitlines()
        duties = [line for line in lines[1:] if line.strip()][:4]
        for number, (line, duty) in enumerate(zip(duties, (
                "Develop ideas with the owner.", "Develop the to-do list.",
                "Push back on the owner.", "Widen the owner's thinking.")), 1):
            self.assertTrue(line.startswith(f"{number}. {duty}"), line)
        self.assertEqual(len(duties), 4)

    def test_configured_session_gets_top_folder_prompt_and_live_streams(self):
        self.configure()
        prompt = EXPECTED + "\nThe current project's session instructions.\n"
        (self.repo / "SESSION.md").write_text(prompt)
        for flags in ((), ("--continue",)):
            with self.subTest(flags=flags):
                done = self.ceo("session", *flags, words="owner's words\n")
                self.assertEqual((done.returncode, done.stdout, done.stderr),
                                 (0, "session output\n", "session error\n"))
                self.assertEqual(json.loads(self.capture.read_text()), {
                    "cwd": str(self.repo),
                    "args": ["configured argument", *flags, "--append-system-prompt", prompt],
                    "input": "owner's words\n"})

    def test_configured_wrapper_keeps_continue_with_its_own_history_configuration(self):
        selected = self.tmp / "wrapper-selected config"
        (self.history(selected) / "session.jsonl").write_text("{}\n")
        self.configure("env", f"CLAUDE_CONFIG_DIR={selected}", sys.executable, str(self.stub), "wrapper argument")
        done = self.ceo("session", "--continue", words="owner's words\n")
        self.assertEqual((done.returncode, done.stdout, done.stderr),
                         (0, "session output\n", "session error\n"))
        self.assertEqual(json.loads(self.capture.read_text()), {
            "cwd": str(self.repo),
            "args": ["wrapper argument", "--continue", "--append-system-prompt", EXPECTED],
            "input": "owner's words\n"})

    def test_codex_takes_session_md_as_its_developer_instructions_and_gemini_opens_without_it(self):
        # Codex has no system-prompt flag: -c reads developer_instructions as TOML, and a JSON string is one.
        # Gemini has none at all. Named so, by any folder, each gets its own flags; claude-auto keeps claude's.
        prompt = EXPECTED + 'A "quoted" line, a back\\slash, a\ttab and Привет.\n'
        (self.repo / "SESSION.md").write_text(prompt)
        programs = self.tmp / "programs"
        programs.mkdir()
        for name in ("codex", "gemini", "claude-auto"):
            (programs / name).write_text(f'#!/bin/sh\nexec {shlex.join([sys.executable, str(self.stub)])} "$@"\n')
            (programs / name).chmod(0o755)
        given = ["-c", "developer_instructions=" + json.dumps(prompt, ensure_ascii=False)]
        said = "ceo: gemini has no flag to take SESSION.md, so this session opens new and without it\n"
        for name, flags, args, error in (
                ("codex", (), given, ""), ("codex", ("--continue",), ["resume", "--last", *given], ""),
                ("gemini", (), [], said), ("gemini", ("--continue",), [], said),
                ("claude-auto", ("--continue",), ["--continue", "--append-system-prompt", prompt], "")):
            with self.subTest(name=name, flags=flags):
                self.configure(str(programs / name))
                done = self.ceo("session", *flags, words="owner's words\n")
                self.assertEqual((done.returncode, done.stdout, done.stderr),
                                 (0, "session output\n", error + "session error\n"))
                self.assertEqual(json.loads(self.capture.read_text()),
                                 {"cwd": str(self.repo), "args": args, "input": "owner's words\n"})

    def test_continue_needs_this_projects_top_level_transcript_and_new_stays_fresh(self):
        self.configure(str(self.fake_claude()), "configured argument")
        config = Path(self.env["CLAUDE_CONFIG_DIR"])
        history = self.history(config)
        (history / "not-a-transcript.jsonl").mkdir()
        (history / "subagents").mkdir()
        (history / "subagents" / "agent.jsonl").write_text("{}\n")
        other = config / "projects" / "other-project"
        other.mkdir()
        (other / "session.jsonl").write_text("{}\n")
        done = self.ceo("session", "--continue")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("--continue", json.loads(self.capture.read_text())["args"])
        (history / "session.jsonl").write_text("{}\n")
        for flags in (("--continue",), ()):
            with self.subTest(flags=flags):
                done = self.ceo("session", *flags, words="owner's words\n")
                self.assertEqual((done.returncode, done.stdout, done.stderr),
                                 (0, "session output\n", "session error\n"))
                self.assertEqual(json.loads(self.capture.read_text()), {
                    "cwd": str(self.repo),
                    "args": ["configured argument", *flags, "--append-system-prompt", EXPECTED],
                    "input": "owner's words\n"})

    def test_continue_uses_the_default_config_only_without_an_override(self):
        self.fake_claude()
        default = self.history(Path(self.env["HOME"]) / ".claude")
        (default / "session.jsonl").write_text("{}\n")
        configured = self.env.pop("CLAUDE_CONFIG_DIR")
        for value, again in ((None, True), ("", True), (configured, False)):
            with self.subTest(config=value):
                if value is not None:
                    self.env["CLAUDE_CONFIG_DIR"] = value
                done = self.ceo("session", "--continue")
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual("--continue" in json.loads(self.capture.read_text())["args"], again)

    def test_initialized_project_uses_the_kernels_session_prompt(self):
        self.repo = self.tmp / "initialized"
        self.repo.mkdir()
        self.nested = self.repo
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-qm", "start", "--allow-empty")
        initialized = self.ceo("init")
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        self.assertFalse((self.repo / "SESSION.md").exists())
        command = shlex.join([sys.executable, str(self.stub)])
        with (self.repo / "ceo.toml").open("a") as config:
            config.write(f"session = {json.dumps(command)}\n")
        done = self.ceo("session")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(self.capture.read_text()), {
            "cwd": str(self.repo),
            "args": ["--append-system-prompt", (KERNEL / "SESSION.md").read_text()],
            "input": ""})

    def test_session_defaults_to_claude(self):
        self.fake_claude()
        done = self.ceo("session")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(self.capture.read_text())["args"], ["--append-system-prompt", EXPECTED])

    def test_session_returns_the_commands_exit_code(self):
        self.configure()
        history = self.history(Path(self.env["CLAUDE_CONFIG_DIR"]))
        (history / "session.jsonl").write_text("{}\n")
        self.env["SESSION_EXIT"] = "37"
        done = self.ceo("session", "--continue")
        self.assertEqual(done.returncode, 37, done.stderr)
        self.assertIn("--continue", json.loads(self.capture.read_text())["args"])
        self.assertEqual(Path(str(self.capture) + ".calls").read_text().splitlines(), ["run"])

    def test_live_child_keeps_the_session_after_its_wrapper_is_killed(self):
        self.configure()
        self.stub.write_text('''import os, sys
from pathlib import Path
capture = Path(os.environ["SESSION_CAPTURE"])
pending = capture.with_suffix(".tmp")
pending.write_text(str(os.getpid()))
pending.replace(capture)
for line in sys.stdin:
    capture.with_suffix(".reply").write_text(line)
''')
        def wait_for(predicate):
            end = time.monotonic() + 10
            while not predicate() and time.monotonic() < end:
                time.sleep(0.02)
            self.assertTrue(predicate(), "session child did not respond")

        proc = subprocess.Popen([sys.executable, "-B", str(KERNEL / "ceo.py"), "session"],
                                cwd=self.nested, env=self.env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True)
        try:
            wait_for(self.capture.exists)
            child = int(self.capture.read_text())
            proc.kill()  # only the wrapper; the live command still has the owner's stdin
            proc.wait(timeout=10)
            proc.stdin.write("still here\n")
            proc.stdin.flush()
            reply = self.capture.with_suffix(".reply")
            wait_for(lambda: reply.exists() and reply.read_text() == "still here\n")
            for args in (("session",), ("session", "--continue")):
                done = self.ceo(*args)
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertIn("session is already running", done.stderr)
                self.assertIn(str(self.repo), done.stderr)
                self.assertEqual(int(self.capture.read_text()), child)
            os.kill(child, signal.SIGKILL)
            proc.communicate(timeout=10)  # EOF proves the orphan released its descriptors
            done = self.ceo("session", "--continue")
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertNotEqual(int(self.capture.read_text()), child)
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=10)

    def test_start_errors_and_failed_commands_release_the_session_in_the_same_process(self):
        self.stub.write_text("raise SystemExit(37)\n")
        source = self.repo / "SESSION.md"
        with mock.patch.object(ceo_init, "toplevel", return_value=self.repo):
            for failure in ("prompt", "command"):
                with self.subTest(failure=failure):
                    self.configure()
                    if failure == "prompt":
                        source.write_bytes(b"\xff")
                    else:
                        command = shlex.join([str(self.tmp / "absent command")])
                        (self.repo / "ceo.toml").write_text(f"session = {json.dumps(command)}\n")
                    with self.assertRaisesRegex(ceo_init.Refused, "the session cannot start"):
                        ceo_init.session()
                    source.write_text(EXPECTED)
                    self.configure()
                    for again in (False, True):
                        self.assertEqual(ceo_init.session(again), 37)

    def test_talk_is_an_unknown_command(self):
        done = self.ceo("talk", "1", "x")
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("invalid choice: 'talk'", done.stderr)

    def test_idea_still_appends_the_owners_words(self):
        for number in (1, 2):
            done = self.ceo("idea", "x")
            self.assertEqual((done.returncode, done.stdout), (0, f"{number}\n"), done.stderr)
        ideas = (self.repo / "IDEAS.md").read_text()
        self.assertEqual(ideas.count("\nx\n"), 2)
        self.assertIn("## 1 — ", ideas)
        self.assertIn("## 2 — ", ideas)
        self.assertEqual(self.git("status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
