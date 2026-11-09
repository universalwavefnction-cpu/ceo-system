"""ceo.py end to end: real git, real processes, real files. Only the model is replaced, by a stub."""
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ceo import ENDING

CEO = Path(__file__).resolve().parent / "ceo.py"
LIMIT = ("the worker, its process group and every process still below it are stopped; "
         "a process that detached itself on purpose may keep running.")

# One stub worker; its first argument says how to behave.
STUB = r'''
import os, signal, subprocess, sys, time
mode, order = sys.argv[1], sys.stdin.buffer.read()
if mode == "build":
    open("built", "w").write("built\n")
    subprocess.run(["git", "add", "built"], check=True)
    subprocess.run(["git", "commit", "-qm", "build"], check=True)
elif mode == "wrong":
    open("other", "w").write("not what the order asked for\n")
elif mode == "echo":
    open("received.md", "wb").write(order)
    subprocess.run(["git", "add", "received.md"], check=True)
    subprocess.run(["git", "commit", "-qm", "keep received order"], check=True)
elif mode == "mark":
    open(sys.argv[2], "w").write("the worker ran\n")
elif mode == "hang":
    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
    child = subprocess.Popen(sleeper)
    escaped = subprocess.Popen(sleeper, start_new_session=True)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)  # only a kill can stop this one
    open(sys.argv[2], "w").write(f"{os.getpid()} {child.pid} {escaped.pid}")
    time.sleep(60)
    open("finished", "w").write("")
elif mode == "breed":
    def breed(*_):
        # A child in a session of its own, started while the worker is being stopped.
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                 start_new_session=True)
        open(sys.argv[2], "a").write(f"{child.pid} ")
    signal.signal(signal.SIGTERM, breed)
    while True:
        time.sleep(1)
'''


class kernel_patch:
    """A patch of one kernel name as it reached while ceo.py was one file: one replacement in ceo and in every
    ceo_*.py module that holds the name, since each module's functions read the module's own copy."""
    def __init__(self, name, *args, **kwargs):
        self.name, self.args, self.kwargs, self.patches = name, args, kwargs, []

    def start(self):
        held = getattr(sys.modules["ceo"], self.name)
        first, *rest = [module for key, module in sorted(sys.modules.items())
                        if (key == "ceo" or key.startswith("ceo_")) and getattr(module, self.name, None) is held]
        self.patches = [mock.patch.object(first, self.name, *self.args, **self.kwargs)]
        made = self.patches[0].start()
        for module in rest:
            self.patches.append(mock.patch.object(module, self.name, made))
            self.patches[-1].start()
        return made

    def stop(self):
        for one in reversed(self.patches):
            one.stop()

    __enter__ = start

    def __exit__(self, *exc):
        self.stop()


def order_text(done_when, title="An order", goal="Do the thing.", drop=None):
    parts = {"Goal": goal, "Done when": f"```bash\n{done_when}\n```", "Not asked": "Anything else."}
    head = f"# {title}\n" if title else ""
    return head + "".join(f"\n## {name}\n{body}\n" for name, body in parts.items()
                          if name != drop)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class CeoRun(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        # The owner's own git config and hooks must not reach a scratch repository.
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "Stub", "GIT_AUTHOR_EMAIL": "stub@example.invalid",
                    "GIT_COMMITTER_NAME": "Stub", "GIT_COMMITTER_EMAIL": "stub@example.invalid"}
        self.env["CEO_HOME"] = str(self.tmp / "ceo")  # nor the owner's own OWNER.md
        self.git("init", "-q", "-b", "main")
        (self.repo / ".gitignore").write_text(".ceo/\n")
        # Host space and load are not under test here: test_sweep and test_flaky cover their policies.
        (self.repo / "ceo.toml").write_text("disk_floor_gb = 0\nram_floor_gb = 0\nrecheck_wait = 0\n")
        (self.repo / "orders").mkdir()
        self.stub = self.tmp / "stub.py"
        self.stub.write_text(STUB)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit_order(self, name, text):
        (self.repo / "orders" / name).write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", f"order {name}")

    def ceo_cmd(self, name, *stub_args, timeout=None):
        worker = shlex.join([sys.executable, str(self.stub), *stub_args])
        cmd = [sys.executable, str(CEO), "run", str(self.repo / "orders" / name), "--worker", worker]
        return cmd + (["--timeout", str(timeout)] if timeout else [])

    def ceo(self, name, *stub_args, timeout=None):
        return subprocess.run(self.ceo_cmd(name, *stub_args, timeout=timeout), cwd=self.repo,
                              env=self.env, capture_output=True, encoding="utf-8", timeout=120)

    def result(self, slug):
        return json.loads((self.repo / ".ceo" / "runs" / slug / "result.json").read_text())

    def assert_refused(self, name, text):
        self.commit_order(f"{name}.md", text)
        marker = self.tmp / f"ran-{name}"
        run = self.ceo(f"{name}.md", "mark", str(marker))
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertRegex(run.stderr, r"^ceo: refused: [^\n]+\.\n$")
        self.assertFalse(marker.exists(), "the worker was started")
        self.assertFalse((self.repo / ".ceo").exists(), "something was created")
        self.assertEqual(self.git("worktree", "list", "--porcelain").count("worktree "), 1)
        self.assertEqual(self.git("branch", "--list", "order/*"), "")

    def assert_all_gone(self, pids):
        self.addCleanup(lambda: [os.kill(pid, signal.SIGKILL) for pid in pids if alive(pid)])
        # Orphans stay visible as zombies until the system reaps them.
        deadline = time.monotonic() + 5
        while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual([pid for pid in pids if alive(pid)], [], "processes outlived the run")

    def test_passing_order_ends_done(self):
        self.commit_order("010-build.md", order_text("test -f built"))
        run = self.ceo("010-build.md", "build")
        self.assertEqual(run.returncode, 0, run.stderr)
        result = self.result("010-build")
        self.assertEqual((result["state"], result["worker_exit"], result["done_when_exit"]),
                         ("done", 0, 0))
        self.assertEqual(result["order"], "orders/010-build.md")
        self.assertEqual(result["head"], self.git("rev-parse", "order/010-build"))
        self.assertNotEqual(result["head"], self.git("rev-parse", "main"))
        self.assertTrue((self.repo / result["log"]).is_file())
        self.assertTrue((self.repo / result["worktree"] / "built").is_file())
        self.assertLessEqual(result["started"], result["ended"])

    def test_failing_done_when_ends_failed(self):
        self.commit_order("011-wrong.md", order_text("test -f never"))
        run = self.ceo("011-wrong.md", "wrong")
        self.assertEqual(run.returncode, 1, run.stderr)
        result = self.result("011-wrong")
        self.assertEqual((result["state"], result["worker_exit"], result["done_when_exit"]),
                         ("failed", 0, 1))

    def test_order_missing_a_section_is_refused_and_starts_nothing(self):
        broken = {"no-goal": order_text("test -f built", drop="Goal"),
                  "no-done-when": order_text("test -f built", drop="Done when"),
                  "no-not-asked": order_text("test -f built", drop="Not asked"),
                  "empty-block": order_text("")}
        for name, text in broken.items():
            with self.subTest(name):
                self.assert_refused(name, text)

    def test_title_line_inside_a_fence_does_not_count(self):
        done_when = "# no title anywhere in this order\ntest -f made"
        self.assert_refused("fenced-title", order_text(done_when, title=None))

    def test_worker_past_timeout_ends_blocked_and_leaves_nothing_alive(self):
        self.commit_order("012-hang.md", order_text("test -f finished"))
        pidfile = self.tmp / "pids"
        run = self.ceo("012-hang.md", "hang", str(pidfile), timeout=2)
        pids = [int(pid) for pid in pidfile.read_text().split()]
        self.assert_all_gone(pids)
        self.assertEqual(run.returncode, 2, run.stderr)
        result = self.result("012-hang")
        self.assertEqual((result["state"], result["worker_exit"]), ("blocked", None))
        self.assertIn("wall clock", result["reason"])
        self.assertTrue(result["reason"].endswith(LIMIT), result["reason"])
        self.assertEqual(result["survivors"], [])
        self.assertNotEqual(result["done_when_exit"], 0)

    def test_child_started_while_the_worker_is_stopped_is_stopped_too(self):
        self.commit_order("015-breed.md", order_text("test -f finished"))
        pidfile = self.tmp / "pids"
        run = self.ceo("015-breed.md", "breed", str(pidfile), timeout=2)
        pids = [int(pid) for pid in pidfile.read_text().split()]
        self.assert_all_gone(pids)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertEqual(self.result("015-breed")["survivors"], [])

    def test_sigterm_to_ceo_stops_the_worker_and_leaves_an_interrupted_run(self):
        self.commit_order("016-term.md", order_text("test -f finished"))
        pidfile = self.tmp / "pids"
        ceo = subprocess.Popen(self.ceo_cmd("016-term.md", "hang", str(pidfile)), cwd=self.repo,
                               env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               encoding="utf-8")
        self.addCleanup(ceo.kill)
        deadline = time.monotonic() + 30
        while not (pidfile.exists() and len(pidfile.read_text().split()) == 3):
            self.assertLess(time.monotonic(), deadline, "the worker never started")
            time.sleep(0.1)
        ceo.send_signal(signal.SIGTERM)
        _, stderr = ceo.communicate(timeout=60)
        self.assert_all_gone([int(pid) for pid in pidfile.read_text().split()])
        self.assertEqual(ceo.returncode, 2, stderr)
        self.assertRegex(stderr, r"^ceo: interrupted by SIGTERM[^\n]*\n$")
        self.assertTrue(stderr.endswith(f"{LIMIT}\n"), stderr)
        # An interruption, never a result: the run is left for the next tick to settle.
        self.assertFalse((self.repo / ".ceo" / "runs" / "016-term" / "result.json").exists())
        self.assertTrue((self.repo / ".ceo" / "wt" / "016-term").is_dir())

    def test_order_text_with_quotes_and_non_ascii_reaches_worker_unchanged(self):
        done_when = "\n".join(["set -e",
                               'head -c "$(wc -c < orders/020-quotes.md)" received.md | cmp -s - orders/020-quotes.md',
                               r'''grep -qF "it's \"quoted\" \`ticked\`" received.md''',
                               "grep -qF 'ünïcödé — ✓' received.md"])
        text = order_text(done_when, title="Don't \"break\" `this` — ünïcödé ✓",
                          goal="Keep it's \"quoted\" `ticked` and ünïcödé — ✓ exactly as written.")
        self.commit_order("020-quotes.md", text)
        run = self.ceo("020-quotes.md", "echo")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.result("020-quotes")["state"], "done")
        received = self.repo / ".ceo" / "wt" / "020-quotes" / "received.md"
        self.assertEqual(received.read_bytes(), (self.repo / "orders" / "020-quotes.md").read_bytes() + ENDING)

    def test_order_whose_done_when_already_passes_is_refused(self):
        self.commit_order("013-done.md", order_text("test -f .gitignore"))
        marker = self.tmp / "ran"
        run = self.ceo("013-done.md", "mark", str(marker))
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertRegex(run.stderr, r"^ceo: [^\n]*its done-when already passes on [0-9a-f]{40}: it proves nothing\.\n$")
        self.assertFalse(marker.exists(), "the worker was started")
        result = self.result("013-done")
        self.assertEqual((result["state"], result["worker_exit"], result["done_when_exit"]),
                         ("blocked", None, 0))


if __name__ == "__main__":
    unittest.main()
