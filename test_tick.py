"""tick, recovery and review end to end: real git, real processes, real files and model stubs.
Focused restart checks also replace exec and the platform's awake probe."""
import json
import hashlib
import io
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ceo
from test_ceo import kernel_patch, alive, order_text

CEO = Path(__file__).resolve().parent / "ceo.py"
LIMIT = "You've hit your weekly limit"  # what the CLI's last result line said on 9 October 2026

# One stub for every part the model plays; its first argument says which.
STUB = r'''
import json, os, subprocess, sys, time
mode, args, brief = sys.argv[1], sys.argv[2:], sys.stdin.buffer.read()

def build():
    open("built", "w").write("built\n")
    subprocess.run(["git", "add", "built"], check=True)
    subprocess.run(["git", "commit", "-qm", "build"], check=True)

def answer(*findings):
    found = [{"claim": claim, "command": command} for claim, command in findings]
    json.dump({"summary": "checked the work", "findings": found}, open("REVIEW.json", "w"))

if mode == "build":
    build()
elif mode == "wait":  # a worker that says it started, then waits to be let go
    open(f"{args[0]}-{os.path.basename(os.getcwd())}", "w").write(str(os.getpid()))
    while not os.path.exists(args[1]):
        if not os.path.isdir(os.path.dirname(args[1])):
            sys.exit(1)  # its test is over and took the release with it
        time.sleep(0.05)
    build()
elif mode == "spawn":  # a worker whose child stays in its group without the lock
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    open(args[0], "w").write(f"{os.getpid()} {child.pid}")
    time.sleep(60)
elif mode == "sure":  # a reviewer that spoils its own copy and tries to move branches
    open(args[0], "wb").write(brief)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    open(args[0] + ".seen", "w").write(os.getcwd() + "\n" + head)
    os.remove("built")
    for branch in ("main", args[1]):
        subprocess.run(["git", "update-ref", f"refs/heads/{branch}", "HEAD~1"], check=True)
    answer(("built is missing", "test -f built"))
elif mode == "doubt":  # a reviewer whose proof lives only in its own copy
    open("proof", "w").write("made in the reviewer's own copy\n")
    answer(("proof is missing", "test -f proof"), ("built holds other text", "grep -qx built built"))
elif mode == "clean":  # a reviewer that finds nothing
    answer()
elif mode == "review-wait":
    open(args[0], "w").write(str(os.getpid()))
    while not os.path.exists(args[1]):
        if not os.path.isdir(os.path.dirname(args[1])):
            sys.exit(1)
        time.sleep(0.05)
    answer()
elif mode == "endless":  # a reviewer whose one finding never ends when replayed
    answer(("the check never ends", "sleep 60"))
elif mode == "late":  # a reviewer that answers, then runs on
    answer()
    time.sleep(60)
elif mode == "garbage":
    open("REVIEW.json", "w").write("{not json")
elif mode == "no-command":
    json.dump({"summary": "checked", "findings": [{"claim": "a claim"}]}, open("REVIEW.json", "w"))
elif mode == "limit":  # the CLI's api error while the weekly limit holds, as long as args[0] exists
    if os.path.exists(args[0]):
        print(json.dumps({"type": "result", "terminal_reason": "api_error",
                          "result": "You've hit your weekly limit"}), flush=True)
        sys.exit(1)
    answer()
'''


class TickCase(unittest.TestCase):
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
        self.env["CLAUDE_CONFIG_DIR"] = str(self.tmp / "claude")  # nor the notebooks of the owner's Claude Code
        self.git("init", "-q", "-b", "main")
        self.git("config", "maintenance.auto", "false")
        (self.repo / ".gitignore").write_text(".ceo/\n")
        # Host space and load are not under test here: test_sweep and test_flaky cover their policies.
        # Nor is the night, whose window would start one in any test run inside it: test_night has its own.
        (self.repo / "ceo.toml").write_text('disk_floor_gb = 0\nram_floor_gb = 0\nrecheck_wait = 0\nnight = ""\n')
        (self.repo / "orders").mkdir()
        # Every pass probes the worker's login, claude's by default: a claude of the test's own answers,
        # so that no test reaches the one of this machine.
        agent = self.tmp / "agent"
        agent.mkdir()
        (agent / "claude").write_text(f"#!{sys.executable}\nimport sys\nsys.stdin.read()\n"
                                      """print('{"type": "result", "result": "ok"}')\n""")
        (agent / "claude").chmod(0o755)
        self.env["PATH"] = f"{agent}{os.pathsep}{self.env['PATH']}"
        self.stub = self.tmp / "stub.py"
        self.stub.write_text(STUB)
        self.release = self.tmp / "release"
        self.addCleanup(self.release.touch)  # no waiting worker outlives its test

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit_order(self, name, text=None):
        (self.repo / "orders" / name).write_text(text or order_text("test -f built"))
        self.git("add", "-A")
        self.git("commit", "-qm", f"order {name}")

    def model(self, *args):
        return shlex.join([sys.executable, str(self.stub), *args])

    def ceo(self, *args, env=None):
        return subprocess.run([sys.executable, str(CEO), *args], cwd=self.repo, env=env or self.env,
                              capture_output=True, encoding="utf-8", timeout=120)

    def ceo_bg(self, *args, env=None):
        proc = subprocess.Popen([sys.executable, str(CEO), *args], cwd=self.repo,
                                env=env or self.env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, encoding="utf-8")
        self.addCleanup(lambda: (proc.kill(), proc.communicate()))
        return proc

    def tick(self, *args):
        done = self.ceo("tick", *args)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def idle_ceo(self, *args, **kwargs):
        """Exercise bookkeeping while a recorded login outage prevents new model work."""
        path = self.repo / ".ceo" / "login.json"
        path.parent.mkdir(exist_ok=True)
        previous = path.read_bytes() if path.exists() else None
        path.write_text(json.dumps({"*": [time.time(), "test login unavailable"]}))
        try:
            return self.ceo(*args, **kwargs)
        finally:
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous)

    def idle_tick(self, *args):
        done = self.idle_ceo("tick", *args)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def idle_until(self, check, what):
        self.wait_for(lambda: self.idle_tick() is not None and check(), what)

    def tick_until(self, check, what, *args):
        self.wait_for(lambda: self.tick(*args) is not None and check(), what)

    def run_dir(self, slug):
        return self.repo / ".ceo" / "runs" / slug

    def read(self, slug, name="result.json"):
        return json.loads((self.run_dir(slug) / name).read_text())

    def wait_for(self, check, what, timeout=30):
        deadline = time.monotonic() + timeout
        while not check():
            self.assertLess(time.monotonic(), deadline, f"this never happened: {what}")
            time.sleep(0.1)

    def records(self, slug):
        return sorted((self.repo / ".ceo" / "interrupted").glob(f"{slug}-*/record.json"))

    def has_branch(self, slug):
        return bool(self.git("branch", "--list", f"order/{slug}"))

    def shim(self, trigger):
        """A git that is slow to finish one call, as on a large repository: the call has done its
        work and still runs. It leaves a mark when it gets there."""
        bin_dir, real = self.tmp / "bin", shutil.which("git")
        bin_dir.mkdir()
        (bin_dir / "git").write_text(f'#!/bin/sh\n"{real}" "$@"; done=$?\nif [ "$1 $2" = "{trigger}" ]; '
                                     f'then touch "{self.tmp}/paused"; sleep 3; fi\nexit $done\n')
        (bin_dir / "git").chmod(0o755)
        return {**self.env, "PATH": f"{bin_dir}{os.pathsep}{self.env['PATH']}"}

    def kill_later(self, pids):
        self.addCleanup(lambda: [os.kill(pid, signal.SIGKILL) for pid in pids if alive(pid)])


class DryRun(TickCase):
    def marked_worker(self):
        marker = self.tmp / "worker-ran"
        self.stub.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + STUB)
        return marker, self.model("build")

    def lock_record(self, slug):
        return json.loads((self.repo / ".ceo" / "locks" / f"{slug}.json").read_text())

    def assert_refused_by_tick(self, slug, env=None):
        base = self.git("rev-parse", "HEAD")
        marker, worker = self.marked_worker()
        tick = self.ceo("tick", "--worker", worker, env=env)  # filed, so the pass starts it
        self.assertEqual(tick.returncode, 0, tick.stderr)
        self.wait_for((self.run_dir(slug) / "result.json").exists, "the first run ended")
        result, said = self.read(slug), f"its done-when already passes on {base}: it proves nothing"
        self.assertEqual((result["state"], result["worker_exit"], result["done_when_exit"]),
                         ("blocked", None, 0))
        self.assertEqual((result["reason"], result["attention"]), (said, said))
        self.assertEqual((result["dry_run"]["base"], result["dry_run"]["exit_code"]), (base, 0))
        self.assertEqual(result["dry_run"], self.lock_record(slug)["dry_run"])
        self.assertEqual(result["done_when_runs"], [])
        self.assertFalse(marker.exists(), "the order called its worker")
        self.assertFalse((self.repo / ".ceo" / f"{slug}.dry-run").exists())
        event_path = self.repo / ".ceo/events.jsonl"
        events = lambda: [json.loads(line) for line in event_path.read_text().splitlines()
                          if json.loads(line).get("slug") == slug] if event_path.exists() else []
        self.wait_for(lambda: events(), "the blocked event was written")
        self.assertEqual([one["event"] for one in events()], ["blocked"])
        self.assertIn(said, events()[0]["sentence"])
        self.assertNotIn("started the run", self.tick("--worker", worker))
        self.assertFalse(marker.exists())
        self.assertEqual([one["event"] for one in events()], ["blocked"])
        self.assertIn(f"- `orders/{slug}.md` needs attention: {said}",
                      (self.repo / "STATUS.md").read_text())

    def test_an_order_whose_done_when_passes_on_its_base_is_refused_before_its_worker(self):
        self.commit_order("001-proven.md", order_text("test -f ceo.toml"))
        self.assert_refused_by_tick("001-proven")

    def test_a_done_when_requiring_no_origin_remote_is_refused_in_the_clean_copy(self):
        script = "if git remote get-url origin >/dev/null 2>&1; then exit 1; fi"
        self.commit_order("002-origin.md", order_text(script))
        origin = self.tmp / "origin.git"
        self.git("init", "-q", "--bare", str(origin))
        self.git("remote", "add", "origin", str(origin))
        self.git("push", "-q", "origin", "main")
        control = subprocess.run(["bash", "-c", script], cwd=self.repo, env=self.env)
        self.assertEqual(control.returncode, 1)
        self.assert_refused_by_tick("002-origin")

    def test_a_done_when_that_passes_only_without_the_borrowed_python_environment_is_refused(self):
        borrowed, virtual, sibling = self.tmp / "borrowed", self.tmp / "venv", self.tmp / "venv-other"
        borrowed.mkdir()
        (borrowed / "borrowed.py").touch()
        for directory, name in ((virtual, "borrowed-root"), (virtual / "bin", "borrowed-tool"),
                                (virtual / "nested/bin", "borrowed-nested"), (sibling, "kept-tool")):
            directory.mkdir(parents=True, exist_ok=True)
            tool = directory / name
            tool.write_text("#!/bin/sh\n")
            tool.chmod(0o755)
        with open(self.repo / "ceo.toml", "a") as conf:
            conf.write('keep_env = "DRY_KEEP PYTHONPATH PYTHONHOME VIRTUAL_ENV"\n')
        script = '''set -e
test -z "${PYTHONPATH-}${PYTHONHOME-}${VIRTUAL_ENV-}${BORROWED_ENV-}"
test "$DRY_KEEP" = kept
test -n "$HOME" && test -n "$CEO_HOME"
for tool in borrowed-root borrowed-tool borrowed-nested; do ! command -v "$tool"; done
command -v kept-tool >/dev/null
! python3 -c 'import borrowed' 2>/dev/null
test "$GIT_CONFIG_GLOBAL" = /dev/null && test "$GIT_CONFIG_NOSYSTEM" = 1
git commit --allow-empty -qm proof
test "$(git log -1 --format='%an <%ae> / %cn <%ce>')" = 'CEO kernel <ceo@localhost> / CEO kernel <ceo@localhost>'
'''
        self.commit_order("003-environment.md", order_text(script))
        env = {**self.env, "PYTHONPATH": str(borrowed), "PYTHONHOME": sys.base_prefix,
               "VIRTUAL_ENV": str(virtual), "BORROWED_ENV": "borrowed", "DRY_KEEP": "kept",
               "PATH": os.pathsep.join(map(str, (virtual, virtual / "bin", virtual / "nested/bin",
                                                  sibling, self.env["PATH"])))}
        control = subprocess.run(["bash", "-c", script], cwd=self.repo, env=env)
        self.assertEqual(control.returncode, 1)
        self.assert_refused_by_tick("003-environment", env)

    def test_an_order_whose_done_when_fails_on_its_base_runs_as_before_with_its_dry_run_recorded(self):
        slug, calls, seen = "004-builds", self.tmp / "calls", self.tmp / "worker-saw.json"
        script = (f'if test -d .git; then echo clone; else test -f .git && echo worktree; fi >> "{calls}"\n'
                  'for n in $(seq 45); do echo "tail $n"; done\ntest -f built')
        self.commit_order(f"{slug}.md", order_text(script))
        base = self.git("rev-parse", "HEAD")
        lock = self.repo / ".ceo" / "locks" / f"{slug}.json"
        self.stub.write_text(f"from pathlib import Path\nPath({str(calls)!r}).open('a').write('worker\\n')\n"
                            f"Path({str(seen)!r}).write_bytes(Path({str(lock)!r}).read_bytes())\n" + STUB)
        done = self.ceo("run", f"orders/{slug}.md", "--source", "main", "--worker", self.model("build"))
        self.assertEqual(done.returncode, 0, done.stderr)
        result = self.read(slug)
        self.assertEqual((result["state"], result["worker_exit"]), ("done", 0))
        self.assertEqual(calls.read_text().splitlines(), ["clone", "worker", "worktree"])
        self.assertEqual(result["dry_run"], {"base": base, "exit_code": 1,
                         "output": "\n".join(f"tail {n}" for n in range(6, 46))})
        self.assertEqual(json.loads(seen.read_text())["dry_run"], result["dry_run"])
        self.assertEqual(self.lock_record(slug)["dry_run"], result["dry_run"])
        self.assertEqual([one["exit_code"] for one in result["done_when_runs"]], [0])
        self.assertFalse((self.repo / ".ceo" / f"{slug}.dry-run").exists())

    def interrupted_dry_run(self, slug):
        calls = self.tmp / "clone-calls"
        script = f'if test -d .git; then echo clone >> "{calls}"; fi\ntest -f built'
        self.commit_order(f"{slug}.md", order_text(script))
        proc = self.ceo_bg("run", f"orders/{slug}.md", "--worker",
                           self.model("wait", str(self.tmp / "pid"), str(self.release)))
        self.wait_for((self.tmp / f"pid-{slug}").exists, "the worker started after its dry run")
        dry = self.lock_record(slug)["dry_run"]
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 2)
        self.assertIn(f"recovered {slug}: cleared", self.idle_tick())
        self.assertTrue(self.records(slug))
        self.assertFalse(self.has_branch(slug))
        self.assertTrue(self.lock_record(slug)["cleared"])
        self.assertEqual(self.lock_record(slug)["dry_run"], dry)
        return calls, dry

    def test_a_run_cut_off_and_made_again_on_the_same_base_keeps_its_dry_run(self):
        slug = "005-recovered"
        calls, dry = self.interrupted_dry_run(slug)
        done = self.ceo("run", f"orders/{slug}.md", "--worker", self.model("build"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(calls.read_text().splitlines(), ["clone"])
        self.assertEqual(self.read(slug)["dry_run"], dry)
        self.assertEqual(self.lock_record(slug)["dry_run"], dry)
        self.assertNotIn("cleared", self.lock_record(slug))

    def test_a_round_that_replaces_another_skips_the_dry_run(self):
        calls, slug = self.tmp / "calls", "007-round"
        self.commit_order("006-original.md")
        (self.repo / "orders/006-original.cut").touch()
        script = (f'if test -d .git; then echo clone; else test -f .git && echo worktree; fi >> "{calls}"\n'
                  'test -f built')
        self.commit_order(f"{slug}.md", "# Next round\nROUND: 2\nREPLACES: orders/006-original.md\n"
                          + order_text(script))
        self.stub.write_text(f"from pathlib import Path\nPath({str(calls)!r}).open('a').write('worker\\n')\n"
                            + STUB)
        done = self.ceo("run", f"orders/{slug}.md", "--worker", self.model("build"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(calls.read_text().splitlines(), ["worktree", "worker", "worktree"])
        self.assertNotIn("dry_run", self.read(slug))
        self.assertNotIn("dry_run", self.lock_record(slug))

    def test_a_revision_is_a_first_run(self):
        self.commit_order("008-revision.md", "# Revision\nROUND: 1\nRevises: orders/old.md\n"
                          + order_text("test -f ceo.toml"))
        self.assert_refused_by_tick("008-revision")

    def test_a_virtual_environment_bin_that_links_outside_it_is_still_removed_from_path(self):
        virtual, outside = self.tmp / "venv", self.tmp / "outside"
        virtual.mkdir()
        outside.mkdir()
        (virtual / "bin").symlink_to(outside, target_is_directory=True)
        (outside / "borrowed-tool").write_text("#!/bin/sh\n")
        (outside / "borrowed-tool").chmod(0o755)
        self.commit_order("014-linked-venv.md", order_text(
            "test -f built || ! command -v borrowed-tool >/dev/null"))
        self.assert_refused_by_tick("014-linked-venv", {
            **self.env, "VIRTUAL_ENV": str(virtual),
            "PATH": f"{virtual / 'bin'}{os.pathsep}{self.env['PATH']}"})

    def test_a_copy_failure_starts_the_worker(self):
        slug, bin_dir, real = "009-copy", self.tmp / "bin", shutil.which("git")
        bin_dir.mkdir()
        (bin_dir / "git").write_text('#!/bin/sh\nif [ "$1" = clone ]; then echo no-copy >&2; exit 1; fi\n'
                                     f'exec "{real}" "$@"\n')
        (bin_dir / "git").chmod(0o755)
        self.commit_order(f"{slug}.md")
        done = self.ceo("run", f"orders/{slug}.md", "--worker", self.model("build"),
                        env={**self.env, "PATH": f"{bin_dir}{os.pathsep}{self.env['PATH']}"})
        self.assertEqual(done.returncode, 0, done.stderr)
        result = self.read(slug)
        self.assertEqual(result["state"], "done")
        self.assertIsNone(result["dry_run"]["exit_code"])
        self.assertIn("no-copy", result["dry_run"]["output"])
        self.assertEqual(result["dry_run"], self.lock_record(slug)["dry_run"])
        self.assertFalse((self.repo / ".ceo" / f"{slug}.dry-run").exists())

    def test_a_dry_run_timeout_starts_the_worker(self):
        slug = "010-timeout"
        self.commit_order(f"{slug}.md", order_text(
            'if test -d .git; then echo dry-clock; sleep 30; fi\ntest -f built'))
        done = self.ceo("run", f"orders/{slug}.md", "--worker", self.model("build"), "--timeout", "1")
        self.assertEqual(done.returncode, 0, done.stderr)
        result = self.read(slug)
        self.assertEqual(result["state"], "done")
        self.assertIsNone(result["dry_run"]["exit_code"])
        self.assertEqual(result["dry_run"]["output"], "dry-clock")
        self.assertEqual(result["survivors"], [])
        self.assertEqual(result["dry_run"], self.lock_record(slug)["dry_run"])
        self.assertFalse((self.repo / ".ceo" / f"{slug}.dry-run").exists())

    def test_a_retry_carries_its_dry_run_forward_without_running_it_again(self):
        slug, calls, limited = "011-retry", self.tmp / "clone-calls", self.tmp / "limited"
        limited.touch()
        self.commit_order(f"{slug}.md", order_text(
            f'if test -d .git; then echo clone >> "{calls}"; fi\ntest -f built'))
        failed = self.ceo("run", f"orders/{slug}.md", "--worker", self.model("limit", str(limited)))
        self.assertEqual(failed.returncode, 2, failed.stderr)
        first = self.read(slug)
        self.assertEqual(first["state"], "retry")
        done = self.ceo("run", f"orders/{slug}.md", "--worker", self.model("build"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(calls.read_text().splitlines(), ["clone"])
        self.assertEqual(self.read(slug)["dry_run"], first["dry_run"])
        self.assertEqual(self.lock_record(slug)["dry_run"], first["dry_run"])

    def test_a_changed_base_invalidates_the_recorded_dry_run(self):
        slug = "012-base"
        calls, dry = self.interrupted_dry_run(slug)
        self.git("commit", "--allow-empty", "-qm", "new base")
        done = self.ceo("run", f"orders/{slug}.md", "--worker", self.model("build"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(calls.read_text().splitlines(), ["clone", "clone"])
        self.assertNotEqual(self.read(slug)["dry_run"]["base"], dry["base"])

    def test_a_changed_done_when_invalidates_the_recorded_dry_run(self):
        slug = "013-text"
        calls, dry = self.interrupted_dry_run(slug)
        path = self.repo / "orders" / f"{slug}.md"
        path.write_text(path.read_text().replace("test -f built", "echo changed\ntest -f built"))
        done = self.ceo("run", f"orders/{slug}.md", "--worker", self.model("build"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(calls.read_text().splitlines(), ["clone", "clone"])
        self.assertEqual(self.read(slug)["dry_run"]["base"], dry["base"])
        self.assertEqual(self.read(slug)["dry_run"]["output"], "changed")


class CommitProof(TickCase):
    def check_completion(self, interrupted):
        self.git("config", "status.showUntrackedFiles", "no")
        (self.repo / ".gitignore").write_text(".ceo/\nbuilt-ignored\n")
        self.stub.write_text(r'''
import subprocess, sys, time
from pathlib import Path
sys.stdin.read()
path, change, ready, release = sys.argv[1:]
source = "build-input" if change == "ignored" else path
Path(source).write_text("built\n")
Path(".ceo/nested").mkdir(parents=True, exist_ok=True)
Path(".ceo/nested/scratch").write_text("scratch\n")
if change in ("staged", "committed", "ignored"):
    subprocess.run(["git", "add", source], check=True)
if change in ("committed", "ignored"):
    subprocess.run(["git", "commit", "-qm", "build"], check=True)
if change == "ignored":
    Path(path).write_text(Path(source).read_text())
if ready:
    Path(ready).touch()
    while not Path(release).exists():
        time.sleep(.02)
''')
        for number, change in enumerate(("committed", "untracked", "staged", "modified", "ignored"), 1):
            with self.subTest(change=change):
                slug, path = f"{number:03d}-{change}", f"built-{change}"
                if change == "modified":
                    (self.repo / path).write_text("unfinished\n")
                # Test artifacts must not turn committed work into a failed run.
                check = f'test "$(cat {path} 2>/dev/null)" = built && touch check-output'
                if change == "ignored":
                    check = f"cp build-input {path} && " + check
                self.commit_order(f"{slug}.md", order_text(check))
                ready = self.tmp / slug
                worker = self.model(path, change, str(ready) if interrupted else "", str(self.release))
                args = ("run", f"orders/{slug}.md", "--worker", worker, "--timeout", "20")
                if interrupted:
                    run = self.ceo_bg(*args)
                    self.wait_for(ready.exists, "the worker left its output")
                    run.send_signal(signal.SIGTERM)
                    run.communicate(timeout=30)
                    self.assertEqual(run.returncode, 2)
                    self.idle_tick()
                else:
                    run = self.ceo(*args)
                    self.assertEqual(run.returncode, 0 if change in ("committed", "ignored") else 1, run.stderr)
                result = self.read(slug)
                committed = change in ("committed", "ignored")
                self.assertEqual(result["state"], "done" if committed else "failed", result)
                self.assertEqual(result["done_when_exit"], 0)
                self.assertEqual([one["exit_code"] for one in result["done_when_runs"]], [0])
                self.assertEqual(result["uncommitted"], not committed)
                self.assertEqual(result.get("recovered", False), interrupted)
                self.assertEqual(result["head"], self.git("rev-parse", f"order/{slug}"))
                tree = self.repo / result["worktree"]
                self.assertEqual((tree / path).read_text(), "built\n")
                self.assertTrue((tree / "check-output").is_file())
                if not committed:
                    self.assertIn("uncommitted", result["reason"])
                copy = self.tmp / f"recorded-{change}"
                self.git("clone", "-q", str(self.repo), str(copy))
                self.git("-C", str(copy), "checkout", "-q", result["head"])
                replay = subprocess.run(["bash", "-c", check], cwd=copy, env=self.env)
                self.assertEqual(replay.returncode, 0 if committed else 1)

    def test_normal_completion_requires_committed_output(self):
        self.check_completion(interrupted=False)

    def test_recovery_requires_committed_output(self):
        self.check_completion(interrupted=True)


class Recovery(TickCase):
    def test_run_cut_off_while_its_worktree_is_made_is_recorded_cleared_and_runs_again(self):
        self.commit_order("010-setup.md")
        ceo = self.ceo_bg("run", "orders/010-setup.md", "--worker", self.model("build"),
                          env=self.shim("worktree add"))
        self.wait_for((self.tmp / "paused").exists, "git made the worktree and still runs")
        ceo.send_signal(signal.SIGTERM)
        ceo.communicate(timeout=30)
        self.assertEqual(ceo.returncode, -signal.SIGTERM)
        self.idle_tick()  # git, a process of the run, still runs: the run is left alone
        self.assertEqual(self.records("010-setup"), [])
        self.assertTrue((self.repo / ".ceo" / "wt" / "010-setup" / ".git").exists())
        self.idle_until(lambda: self.records("010-setup"), "the cut-off run was recorded")
        record = json.loads(self.records("010-setup")[0].read_text())
        # Its worker never started, so its done-when block was never run to judge it.
        self.assertEqual((record["order"], record["done_when_exit"]), ("orders/010-setup.md", None))
        self.assertTrue((self.records("010-setup")[0].parent / "worker.log").is_file())
        self.assertFalse((self.repo / ".ceo" / "wt" / "010-setup").exists())
        self.assertFalse(self.run_dir("010-setup").exists())
        self.assertFalse(self.has_branch("010-setup"))
        self.tick("--worker", self.model("build"))  # still filed, and no run of it recorded: it starts again
        self.wait_for((self.run_dir("010-setup") / "result.json").exists, "the order ran again")
        self.assertEqual(self.read("010-setup")["state"], "done")

    def test_run_cut_off_while_writing_its_result_keeps_the_finished_work(self):
        self.commit_order("011-write.md")
        ceo = self.ceo_bg("run", "orders/011-write.md", "--worker", self.model("build"),
                          env=self.shim("rev-parse HEAD"))
        self.wait_for((self.tmp / "paused").exists, "the run began to write its result")
        ceo.send_signal(signal.SIGTERM)
        ceo.communicate(timeout=30)
        self.assertNotEqual(ceo.returncode, 0)
        self.assertFalse((self.run_dir("011-write") / "result.json").exists())
        self.assertIn("recovered 011-write: done", self.idle_tick())
        result = self.read("011-write")
        self.assertEqual((result["state"], result["recovered"], result["done_when_exit"]),
                         ("done", True, 0))
        self.assertEqual(result["head"], self.git("rev-parse", "order/011-write"))
        self.assertTrue((self.repo / result["worktree"] / "built").is_file())
        self.assertEqual(self.records("011-write"), [])

    def test_run_whose_worker_outlives_ceo_is_untouched_until_it_ends(self):
        self.commit_order("012-orphan.md")
        pidfile = self.tmp / "pid"
        ceo = self.ceo_bg("run", "orders/012-orphan.md", "--worker",
                          self.model("wait", str(pidfile), str(self.release)))
        worker_pid = self.tmp / "pid-012-orphan"
        self.wait_for(worker_pid.exists, "the worker started")
        self.kill_later([int(worker_pid.read_text())])
        ceo.send_signal(signal.SIGKILL)
        ceo.communicate(timeout=30)
        self.idle_tick()
        self.assertTrue(alive(int(worker_pid.read_text())))
        self.assertEqual(self.records("012-orphan"), [])
        self.assertFalse((self.run_dir("012-orphan") / "result.json").exists())
        self.release.touch()
        self.wait_for(lambda: not alive(int(worker_pid.read_text())), "the worker finished")
        self.assertIn("recovered 012-orphan: done", self.idle_tick())
        self.assertTrue(self.read("012-orphan")["recovered"])
        self.assertTrue((self.repo / ".ceo" / "wt" / "012-orphan" / "built").is_file())

    def test_process_left_in_the_workers_group_keeps_the_run_alive(self):
        self.commit_order("013-group.md")
        pidfile = self.tmp / "pids"
        ceo = self.ceo_bg("run", "orders/013-group.md", "--worker", self.model("spawn", str(pidfile)))
        self.wait_for(lambda: pidfile.exists() and len(pidfile.read_text().split()) == 2,
                      "the worker and its child started")
        worker, child = map(int, pidfile.read_text().split())
        self.kill_later([worker, child])
        ceo.send_signal(signal.SIGKILL)
        ceo.communicate(timeout=30)
        os.kill(worker, signal.SIGKILL)
        self.wait_for(lambda: not alive(worker), "the worker died")
        self.idle_tick()
        self.assertTrue((self.repo / ".ceo" / "wt" / "013-group").is_dir())
        self.assertEqual(self.records("013-group"), [])
        os.kill(child, signal.SIGKILL)
        self.wait_for(lambda: not alive(child), "the child died")
        self.assertIn("recovered 013-group: cleared", self.idle_tick())
        self.assertEqual(len(self.records("013-group")), 1)
        self.assertFalse(self.has_branch("013-group"))

    def test_run_cut_off_before_its_worker_started_is_never_recorded_done(self):
        first = self.tmp / "first"
        # It would pass, so the uninterrupted run ends blocked with nothing to prove.
        self.commit_order("014-first.md", order_text(f'touch "{first}"; sleep 2; test -f .gitignore'))
        ceo = self.ceo_bg("run", "orders/014-first.md", "--worker", self.model("build"))
        self.wait_for(first.exists, "the first test began")
        ceo.send_signal(signal.SIGKILL)
        ceo.communicate(timeout=30)
        self.idle_until(lambda: self.records("014-first"), "the cut-off run was recorded")
        self.assertFalse((self.run_dir("014-first") / "result.json").exists())
        self.assertFalse(self.has_branch("014-first"))

    def test_run_stopped_by_sigterm_while_its_worker_runs_is_cleared_and_runs_again(self):
        self.commit_order("015-term.md")
        ceo = self.ceo_bg("run", "orders/015-term.md", "--worker",
                          self.model("wait", str(self.tmp / "pid"), str(self.release)))
        self.wait_for((self.tmp / "pid-015-term").exists, "the worker started")
        ceo.send_signal(signal.SIGTERM)
        ceo.communicate(timeout=30)
        self.assertEqual(ceo.returncode, 2)
        self.assertFalse((self.run_dir("015-term") / "result.json").exists())
        self.assertIn("recovered 015-term: cleared", self.tick("--worker", self.model("build")))
        self.assertEqual(len(self.records("015-term")), 1)
        self.wait_for((self.run_dir("015-term") / "result.json").exists, "the order ran again")
        self.assertEqual(self.read("015-term")["state"], "done")

    def test_run_stopped_by_sigterm_in_its_final_test_keeps_the_finished_work(self):
        final = self.tmp / "final"
        self.commit_order("016-final.md", order_text(
            f'if test -f built; then touch "{final}"; sleep 2; fi\ntest -f built'))
        ceo = self.ceo_bg("run", "orders/016-final.md", "--worker", self.model("build"))
        self.wait_for(final.exists, "the final test began")
        ceo.send_signal(signal.SIGTERM)
        ceo.communicate(timeout=30)
        self.assertEqual(ceo.returncode, 2)
        self.assertFalse((self.run_dir("016-final") / "result.json").exists())
        self.assertIn("recovered 016-final: done", self.idle_tick())
        result = self.read("016-final")
        self.assertEqual((result["state"], result["recovered"]), ("done", True))
        self.assertTrue((self.repo / result["worktree"] / "built").is_file())


class Tick(TickCase):
    def test_filed_orders_start_oldest_filed_first_without_a_global_cap(self):
        draft = self.repo / "drafts" / "orders" / "023-draft.md"  # a draft, committed too, is never read
        draft.parent.mkdir(parents=True)
        draft.write_text(order_text("test -f built"))
        stamp = int(time.time())
        for age, name in ((40, "023-draft"), (30, "022-c"), (20, "020-a"), (10, "021-b")):
            if name != "023-draft":
                (self.repo / "orders" / f"{name}.md").write_text(order_text("test -f built"))
            when = {"GIT_COMMITTER_DATE": f"{stamp - age} +0000", "GIT_AUTHOR_DATE": f"{stamp - age} +0000"}
            for args in (["add", "-A"], ["commit", "-qm", f"file {name}"]):
                subprocess.run(["git", *args], cwd=self.repo, env={**self.env, **when}, check=True, capture_output=True)
            if name != "023-draft":  # written in the opposite order: the commit that filed it counts
                os.utime(self.repo / "orders" / f"{name}.md", (stamp - 100 + age, stamp - 100 + age))
        work = ("--worker", self.model("wait", str(self.tmp / "pid"), str(self.release)),
                "--reviewer", self.model("doubt"))
        names = ("022-c", "020-a", "021-b")
        self.assertEqual(self.tick(*work).splitlines(),
                         [f"ceo: started the run of orders/{name}.md" for name in names])
        for name in names:
            self.wait_for((self.tmp / f"pid-{name}").exists, f"{name} started")
        self.assertEqual(self.tick(*work), "", "an order started twice")
        self.release.touch()
        for name in names:
            self.wait_for((self.run_dir(name) / "result.json").exists, f"{name} ended")
        started = self.tick(*work)
        self.assertEqual(set(started.splitlines()),
                         {f"ceo: started the review of orders/{name}.md" for name in names})
        for name in names:
            self.wait_for((self.run_dir(name) / "review.json").exists, f"{name} was reviewed")
        self.assertFalse((self.repo / ".ceo" / "wt" / "023-draft").exists(), "a draft ran")
        self.assertFalse(self.run_dir("023-draft").exists())
        self.assertEqual(draft.read_text(), order_text("test -f built"))

    def test_every_pass_writes_its_time_on_one_line_and_a_second_tick_refuses(self):
        status = self.repo / "STATUS.md"
        status.write_text("# Status\n\n## In progress\n- something\n")
        ticking = self.ceo_bg("tick", "--every", "0.5")
        lines = lambda: [line for line in status.read_text().splitlines() if line.startswith("Last")]
        self.wait_for(lambda: len(lines()) == 1, "the first pass wrote its time")
        first = lines()[0]
        second = self.ceo("tick")
        self.assertEqual(second.returncode, 2, second.stderr)
        self.assertRegex(second.stderr, r"^ceo: refused: [^\n]+\.\n$")
        self.wait_for(lambda: lines() != [first], "a later pass wrote its time", timeout=10)
        self.assertEqual(len(lines()), 1)
        text = status.read_text().splitlines()
        self.assertEqual(text[0], "# Status")
        self.assertRegex(text[1], r"^Last tick: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")
        self.assertRegex(text[2], r"^Health: \S+, login ok, 0 retries today$")
        self.assertRegex(text[3], r"^Free: disk \d+\.\d GB, RAM \d+\.\d GB$")
        self.assertEqual(text[4], "Orders: orders run from your folder; no origin")
        self.assertRegex(text[5], r"^Sweep: \S+, freed \d+\.\d\d GB$")  # the first pass of the day
        self.assertEqual(text[6:], ["", "## In progress", "- something"])
        ticking.send_signal(signal.SIGTERM)
        ticking.communicate(timeout=30)
        self.tick()  # the lock went with the first tick


class Restart(TickCase):
    def setUp(self):
        super().setUp()
        for path in CEO.parent.glob("*.py"):
            if not path.name.startswith("test_"):
                shutil.copyfile(path, self.repo / path.name)
        shutil.copytree(CEO.parent / "tools", self.repo / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        self.kernel = self.repo / "ceo.py"
        self.original = self.kernel.read_bytes()
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        # A real tick must not start the night if this test runs inside its window.
        offset = (12 - datetime.utcnow().hour + 12) % 24 - 12
        self.env["TZ"] = f"NOON{-offset}"

    def commit_kernel(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "the kernel")

    def start_tick(self, *args, repo=None):
        repo = repo or self.repo
        output = self.tmp / f"{repo.name}.out"
        with output.open("w") as log:
            proc = subprocess.Popen([sys.executable, str(self.kernel), "tick", "--every", "0.1", *args],
                                    cwd=repo, env=self.env, stdout=log, stderr=log)
        self.addCleanup(self.stop_tick, proc)
        return proc, output

    def stop_tick(self, proc):
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=10)

    def passes(self, repo=None, count=2):
        status = (repo or self.repo) / "STATUS.md"
        def last():
            return next((line for line in status.read_text().splitlines() if line.startswith("Last tick:")), "") \
                if status.exists() else ""
        for _ in range(count):
            previous = last()
            self.wait_for(lambda: last() != previous, "the tick finished another pass")

    def restarted(self, repo=None):
        return [row for row in ceo.events(repo or self.repo)[0] if row["event"] == "restarted"]

    def new_code(self):
        self.kernel.write_bytes(b'import os, sys\nif sys.argv[1:2] == ["tick"]:\n'
                                b'    print(f"new code in pid {os.getpid()}", flush=True)\n' + self.original)

    def test_the_kernels_own_tick_restarts_on_its_folders_new_code_once_nothing_of_it_is_at_work(self):
        slug = "001-hold"
        self.commit_order(f"{slug}.md")
        reviewer_ready, reviewer_release = self.tmp / "reviewer", self.tmp / "review-release"
        proc, output = self.start_tick(
            "--worker", self.model("wait", str(self.tmp / "pid"), str(self.release)),
            "--reviewer", self.model("review-wait", str(reviewer_ready), str(reviewer_release)))
        self.addCleanup(reviewer_release.touch)
        self.wait_for((self.tmp / f"pid-{slug}").exists, "the kernel's worker started")
        self.new_code()
        self.passes()
        self.assertEqual(self.restarted(), [])
        self.assertNotIn("new code in pid", output.read_text())
        # A run may finish after a pass decided what to start. Keep work active across that
        # handoff, so this test checks the review itself rather than relying on its timing.
        handoff = ceo.claim(self.repo / ".ceo" / "decisions" / "handoff" / "launch")
        try:
            self.release.touch()
            self.wait_for(reviewer_ready.exists, "the kernel's review started")
        finally:
            os.close(handoff)
        self.assertEqual(self.read(slug)["state"], "done")
        self.passes()
        self.assertEqual(self.restarted(), [], output.read_text())
        reviewer_release.touch()
        self.wait_for(lambda: f"new code in pid {proc.pid}\n" in output.read_text(),
                      "the same tick process ran the new code")
        self.passes()
        self.assertEqual(self.read(slug, "review.json")["verdict"], "survives")
        self.assertEqual(len(self.restarted()), 1)
        self.assertEqual(self.restarted()[0]["slug"], "")
        self.assertEqual(self.restarted()[0]["sentence"],
                         "the kernel's code changed (ceo.py), so this tick restarts")
        self.assertEqual(output.read_text().count("so this tick restarts\n"), 1)
        self.assertEqual(output.read_text().count(f"ceo: started the run of orders/{slug}.md\n"), 1)
        second = subprocess.run([sys.executable, str(self.kernel), "tick"], cwd=self.repo,
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(second.returncode, 2)
        self.assertIn("another tick is running", second.stderr)
        self.assertIsNone(proc.poll())
        if sys.platform == "darwin":
            rows = subprocess.run(["ps", "-A", "-o", "ppid=,command="], capture_output=True,
                                  text=True, check=True).stdout.splitlines()
            self.assertEqual(sum(row.split() == [str(proc.pid), *ceo.HOLD, str(proc.pid)]
                                 for row in rows), 1)

    def test_new_kernel_code_that_does_not_compile_keeps_the_kernels_tick_on_its_old_code_until_a_fix_compiles(self):
        self.commit_kernel()
        proc, output = self.start_tick()
        self.passes()
        bad = self.original + b"\ndef broken(:\n"
        self.kernel.write_bytes(bad)
        told = lambda: output.read_text().count("ceo: the kernel's new code does not compile (")
        self.wait_for(lambda: told() == 1, "the kernel's tick reported the syntax error")
        self.passes()
        self.assertEqual(told(), 1)
        self.assertIn("(ceo.py: invalid syntax", output.read_text())
        self.assertIn("so this tick keeps running its old code", output.read_text())
        self.kernel.write_bytes(self.original + b"\ndef other(:\n")
        self.wait_for(lambda: told() == 2, "different bad content was reported once too")
        self.kernel.write_bytes(bad)
        self.passes()
        self.assertEqual(told(), 2, "returning to rejected content repeated its warning")
        self.assertIsNone(proc.poll())
        self.assertEqual(self.restarted(), [])
        self.new_code()
        self.wait_for(lambda: f"new code in pid {proc.pid}\n" in output.read_text(), "the fix ran")
        self.passes()
        self.assertEqual(told(), 2)
        self.assertEqual(len(self.restarted()), 1)
        self.assertIsNone(proc.poll())

    def test_a_projects_tick_and_a_one_shot_tick_never_restart_by_themselves(self):
        self.commit_kernel()
        project = self.tmp / "project"
        self.git("clone", "-q", str(self.repo), str(project))
        proc, output = self.start_tick(repo=project)
        self.passes(project)
        self.new_code()
        self.passes(project)
        self.assertNotIn("new code in pid", output.read_text())
        self.assertNotIn("so this tick restarts", output.read_text())
        self.assertEqual(self.restarted(project), [])
        self.assertIsNone(proc.poll())
        shim = self.tmp / "shim"
        shim.mkdir()
        mark = self.tmp / "shimmed"
        (shim / "git").write_text(
            '#!/bin/sh\nif [ "$1 $2" = "worktree prune" ]; then\n'
            f"  printf '# changed during the pass\\n' >> {shlex.quote(str(self.kernel))}\n"
            f"  touch {shlex.quote(str(mark))}\nfi\nexec {shlex.quote(shutil.which('git'))} \"$@\"\n")
        (shim / "git").chmod(0o755)
        once = subprocess.run([sys.executable, str(self.kernel), "tick"], cwd=self.repo,
                              env={**self.env, "PATH": f"{shim}{os.pathsep}{self.env['PATH']}"},
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(once.returncode, 0, once.stderr)
        self.assertTrue(mark.exists())
        self.assertEqual(once.stdout.count("new code in pid"), 1)
        self.assertNotIn("so this tick restarts", once.stdout)
        self.assertEqual(self.restarted(), [])

    def test_rejected_decision_code_keeps_the_startup_dispatcher_until_a_fix_restarts(self):
        decisions = self.repo / "ceo_decisions.py"
        original = decisions.read_bytes().replace(
            b"def dispatch(repo, timeout, children):\n",
            b'def dispatch(repo, timeout, children):\n    print("startup dispatcher ran", flush=True)\n')
        decisions.write_bytes(original)
        self.commit_kernel()
        proc, output = self.start_tick()
        self.passes()
        decisions.write_bytes(original + b"\ndef broken(:\n")
        warning = "ceo: the kernel's new code does not compile (ceo_decisions.py:"
        self.wait_for(lambda: warning in output.read_text(), "the decision module's error was reported")
        self.passes()
        trigger = self.repo / ".ceo" / "decisions" / "repro" / "stall.json"
        trigger.parent.mkdir(parents=True)
        trigger.write_text("{}\n")
        self.wait_for(lambda: "startup dispatcher ran\n" in output.read_text() or proc.poll() is not None,
                      "the tick reached its previously unused decision dispatcher")
        self.assertIsNone(proc.poll(), output.read_text())
        self.assertIn("startup dispatcher ran\n", output.read_text())
        self.passes()
        self.assertEqual(output.read_text().count(warning), 1)
        self.assertEqual(self.restarted(), [])
        decisions.write_bytes(original + b'\nif sys.argv[1:2] == ["tick"]:\n'
                              b'    print(f"new decisions in pid {os.getpid()}", flush=True)\n')
        self.wait_for(lambda: f"new decisions in pid {proc.pid}\n" in output.read_text(),
                      "the fixed decision module ran after exec in the same process")
        self.passes()
        self.assertIsNone(proc.poll(), output.read_text())
        self.assertEqual(len(self.restarted()), 1)
        self.assertEqual(self.restarted()[0]["sentence"],
                         "the kernel's code changed (ceo_decisions.py), so this tick restarts")

    def test_each_live_agent_decision_and_child_keeps_a_restart_waiting_silently(self):
        started = {path.name: hashlib.sha256(path.read_bytes()).digest()
                   for path in self.repo.glob("*.py")}
        self.new_code()
        output = io.StringIO()
        with kernel_patch("KERNEL", self.kernel), patch.object(ceo.os, "execv") as execute, \
                patch.object(ceo.sys, "stdout", output):
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            try:
                ceo.restart_kernel(self.repo, {("run", "new"): child}, started, set())
                execute.assert_not_called()  # the child has not claimed a lock yet
            finally:
                child.terminate()
                child.wait(timeout=10)
            for name in ("locks/other.run", "locks/other.review", "locks/door.lock", "locks/night.lock",
                         "decisions/one/launch", "decisions/two/lock"):
                with self.subTest(lock=name):
                    lock = ceo.claim(self.repo / ".ceo" / name)
                    try:
                        ceo.sign(lock)
                        ceo.restart_kernel(self.repo, {}, started, set())
                        execute.assert_not_called()
                        self.assertEqual(self.restarted(), [])
                        self.assertEqual(output.getvalue(), "")
                    finally:
                        os.close(lock)
            ceo.restart_kernel(self.repo, {("run", "done"): child}, started, set())
            execute.assert_called_once_with(sys.executable, [sys.executable, *sys.argv])
        self.assertEqual(len(self.restarted()), 1)

    def test_hashes_ignore_touches_and_tests_but_find_same_size_edits_new_and_removed_files(self):
        (self.repo / ".ceo").mkdir()
        sample = self.repo / "sample.py"
        sample.write_bytes(b"# coding: latin-1\nword = '\xe9'\n")
        with kernel_patch("KERNEL", self.kernel), patch.object(ceo.os, "execv") as execute, \
                patch.object(ceo.sys, "stdout", io.StringIO()):
            started = {name: hashlib.sha256(data).digest() for name, data in ceo.kernel_code().items()}
            sample.touch()
            (self.repo / "test_ignored.py").write_text("def broken(:\n")
            (self.repo / "notes.txt").write_text("changed\n")
            ceo.restart_kernel(self.repo, {}, started, set())
            execute.assert_not_called()
            stamp = sample.stat()
            sample.write_bytes(b"# coding: latin-1\nword = '\xe8'\n")
            os.utime(sample, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            (self.repo / "added.py").write_text("added = True\n")
            (self.repo / "desk.py").unlink()
            ceo.restart_kernel(self.repo, {}, started, set())
            execute.assert_called_once()
        self.assertEqual(self.restarted()[0]["sentence"],
                         "the kernel's code changed (added.py, desk.py, sample.py), so this tick restarts")
        self.assertFalse((self.repo / "__pycache__").exists())

    def test_a_tick_reuses_its_existing_awake_hold(self):
        args = SimpleNamespace(worker=None, reviewer=None, checker=None, every=None, timeout=10)
        claim, locks = ceo.claim, []
        def recorded_claim(path):
            locks.append(claim(path))
            return locks[-1]
        try:
            with kernel_patch("toplevel", return_value=self.repo), \
                    kernel_patch("claim", side_effect=recorded_claim), \
                    kernel_patch("awake", return_value=True) as awake, \
                    kernel_patch("hold_awake") as hold, \
                    kernel_patch("tick_pass", side_effect=KeyboardInterrupt), \
                    patch.dict(os.environ, self.env):
                self.assertEqual(ceo.tick(args), 130)
                awake.assert_called_once_with(self.repo)
                hold.assert_not_called()
        finally:
            for lock in locks:
                if lock is not None:
                    os.close(lock)


class Review(TickCase):
    def test_filed_order_is_carried_to_a_review_and_nothing_the_reviewer_does_counts(self):
        self.commit_order("030-chain.md")
        main = self.git("rev-parse", "main")
        brief = self.tmp / "brief"
        work = ("--worker", self.model("build"),
                "--reviewer", self.model("sure", str(brief), "order/030-chain"))
        self.tick_until((self.run_dir("030-chain") / "review.json").exists,
                        "the order was carried to a review", *work)
        result, review = self.read("030-chain"), self.read("030-chain", "review.json")
        self.assertEqual((result["state"], review["verdict"], review["commit"]),
                         ("done", "survives", result["head"]))
        self.assertEqual(review["findings"], [{"claim": "built is missing", "command": "test -f built",
                                               "exit": 0, "counts": False}])
        sent = brief.read_bytes()
        self.assertTrue(sent.endswith((self.repo / "orders" / "030-chain.md").read_bytes()))
        self.assertIn(result["head"].encode(), sent)
        where, head = Path(brief.with_name("brief.seen")).read_text().split()
        self.assertEqual(head, result["head"])
        self.assertFalse(Path(where).exists(), "the reviewer's copy was kept")
        self.assertEqual(list((self.repo / ".ceo" / "review").iterdir()), [])
        self.assertEqual((self.git("rev-parse", "main"), self.git("rev-parse", "order/030-chain")),
                         (main, result["head"]))
        self.assertTrue((self.repo / result["worktree"] / "built").is_file())

    def done_run(self, name):
        self.commit_order(f"{name}.md")
        run = self.ceo("run", f"orders/{name}.md", "--worker", self.model("build"))
        self.assertEqual(run.returncode, 0, run.stderr)

    def review_after_a_run(self, name, *reviewer, timeout=1800):
        self.done_run(name)
        return self.ceo("review", f"orders/{name}.md", "--reviewer", self.model(*reviewer),
                        "--timeout", str(timeout))

    def test_finding_counts_only_when_its_command_fails_in_a_clean_copy(self):
        done = self.review_after_a_run("031-doubt", "doubt")
        self.assertEqual(done.returncode, 1, done.stderr)
        review = self.read("031-doubt", "review.json")
        self.assertEqual(review["verdict"], "fails")
        self.assertEqual([(f["exit"], f["counts"]) for f in review["findings"]], [(1, True), (0, False)])

    def test_answer_that_cannot_be_read_is_blocked_never_survives(self):
        for number, mode in enumerate(("garbage", "no-command")):
            with self.subTest(mode):
                done = self.review_after_a_run(f"03{number + 2}-{mode}", mode)
                self.assertEqual(done.returncode, 2, done.stderr)
                review = self.read(f"03{number + 2}-{mode}", "review.json")
                self.assertEqual(review["verdict"], "blocked")
                self.assertIn("cannot be read", review["reason"])
        # No answer at all, in the first minute, is an api error and no verdict: it is made again.
        done = self.review_after_a_run("034-silent", "silent")
        review = self.read("034-silent", "review.json")
        self.assertEqual((done.returncode, review["verdict"]), (2, "retry"))
        self.assertIn("in its first minute without an answer", review["reason"])

    def test_replay_stopped_by_the_clock_never_counts_as_passing(self):
        done = self.review_after_a_run("035-endless", "endless", timeout=5)
        self.assertEqual(done.returncode, 2, done.stderr)
        review = self.read("035-endless", "review.json")
        self.assertEqual((review["verdict"], review["findings"][0]["exit"]), ("blocked", None))
        self.assertIn("wall clock", review["reason"])

    def test_reviewer_stopped_by_the_clock_is_blocked_though_it_answered(self):
        done = self.review_after_a_run("036-late", "late", timeout=5)
        self.assertEqual(done.returncode, 2, done.stderr)
        review = self.read("036-late", "review.json")
        self.assertEqual((review["verdict"], review["reviewer_exit"]), ("blocked", None))
        self.assertIn("wall clock", review["reason"])

    def test_review_stopped_by_sigterm_is_made_again_by_the_next_tick(self):
        self.done_run("037-term")
        ceo = self.ceo_bg("review", "orders/037-term.md", "--reviewer",
                          self.model("wait", str(self.tmp / "pid"), str(self.release)))
        self.wait_for((self.tmp / "pid-037-term").exists, "the reviewer started")
        ceo.send_signal(signal.SIGTERM)
        ceo.communicate(timeout=30)
        self.assertEqual(ceo.returncode, 2)
        self.assertFalse((self.run_dir("037-term") / "review.json").exists())
        self.tick_until((self.run_dir("037-term") / "review.json").exists,
                        "the run was reviewed again", "--reviewer", self.model("clean"))
        self.assertEqual(self.read("037-term", "review.json")["verdict"], "survives")

    def blocked_by_the_limit(self, name, last="limit"):
        """A done run whose review met the weekly limit three times today, then ended as last says;
        and the file whose presence is the limit."""
        self.done_run(name)
        limit = self.tmp / "limit"
        limit.touch()
        for mode in ("limit", "limit", "limit", last):
            self.ceo("review", f"orders/{name}.md", "--reviewer", self.model(mode, str(limit)))
        review = self.read(name, "review.json")
        self.assertEqual((review["verdict"], len(review["retries"])), ("blocked", 3))
        return review, limit

    def a_day_ago(self, name):
        """Move the review's times a day back, so that the next pass is dated the next day; return
        the day of its retries now."""
        path = self.run_dir(name) / "review.json"
        found = json.loads(path.read_text())
        back = lambda when: (datetime.fromisoformat(when) - timedelta(days=1)).isoformat()
        found.update(retries=[*map(back, found["retries"])], started=back(found["started"]),
                     ended=back(found["ended"]))
        path.write_text(json.dumps(found))
        return found["retries"][-1][:10]

    def test_review_blocked_by_an_api_error_is_made_again_the_next_day_and_survives(self):
        review, limit = self.blocked_by_the_limit("039-limit")
        self.assertEqual(review["reason"], f"the reviewer ended with an api error after three retries "
                                           f"today: {LIMIT}")
        reviewer = ("--reviewer", self.model("limit", str(limit)))
        self.assertNotIn("started the review", self.tick(*reviewer), "made again the same day")
        day = review["retries"][-1][:10]
        self.assertIn(f"- `orders/039-limit.md` the review is blocked by an api error after three retries "
                      f"on {day}; usage limits retry after login recovery; otherwise the next day, "
                      f"{date.fromisoformat(day) + timedelta(1)}: "
                      f"{LIMIT}", (self.repo / "STATUS.md").read_text().splitlines())
        day = self.a_day_ago("039-limit")
        limit.unlink()  # the limit reset overnight
        self.idle_tick(*reviewer)  # the next day's first pass, during a login outage
        review = self.read("039-limit", "review.json")
        self.assertEqual((review["verdict"], review["reason"]), (
            "retry", f"the reviewer met an api error after three retries on {day}, so the next day's "
                     f"first pass makes it again: {LIMIT}"))
        self.assertEqual(self.tick(*reviewer), "ceo: started the review of orders/039-limit.md\n")
        self.wait_for(lambda: self.read("039-limit", "review.json")["verdict"] != "retry",
                      "the review was made again")
        review = self.read("039-limit", "review.json")
        self.assertEqual((review["verdict"], len(review["retries"])), ("survives", 3), review["reason"])

    def test_unreadable_review_retries_even_after_the_api_retry_cap(self):
        review, _ = self.blocked_by_the_limit("040-unreadable", last="garbage")
        self.idle_tick()
        found = self.read("040-unreadable", "review.json")
        self.assertEqual((found["verdict"], found["reason"], found["retries"]),
                         ("retry", review["reason"], review["retries"]))

    def test_review_limit_heals_same_day_only_after_a_new_successful_login(self):
        self.done_run("045-recovered")
        limit = self.tmp / "limit"
        limit.touch()
        agent = self.tmp / "agent/claude"
        agent.write_text(f"#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\n"
                         "given = sys.stdin.buffer.read()\n"
                         f"if Path({str(limit)!r}).exists():\n"
                         "    print(json.dumps(dict(type='result', terminal_reason='api_error',\n"
                         "                          result=\"You've hit your session limit\")))\n"
                         "    sys.exit(1)\n"
                         "if given == b'Reply with the single word ok.\\n':\n"
                         "    print('ok')\n"
                         "else:\n"
                         "    Path('REVIEW.json').write_text(json.dumps(dict(summary='checked', findings=[])))\n")
        model = shlex.join([str(agent), '-p'])
        for _ in range(4):
            self.ceo("review", "orders/045-recovered.md", "--reviewer", model)
        review = self.read("045-recovered", "review.json")
        self.assertEqual(review["verdict"], "blocked")
        login = self.repo / ".ceo/login.json"
        failed_at = datetime.fromisoformat(review["ended"]).timestamp()
        # A successful probe cannot lift a non-limit API block's daily retry cap.
        record = self.run_dir("045-recovered") / "review.json"
        generic = {**review, "reason": review["reason"].replace("You've hit your session limit", "400 prompt is too long")}
        record.write_text(json.dumps(generic))
        login.write_text(json.dumps({model: [time.time(), ""]}))
        flags = ("--worker", self.model("build"), "--reviewer", model)
        self.assertNotIn("started the review", self.tick(*flags))
        self.assertEqual(self.read("045-recovered", "review.json")["verdict"], "blocked")
        record.write_text(json.dumps(review))
        login.write_text(json.dumps({model: [failed_at - 1, ""]}))
        self.assertNotIn("started the review", self.tick(*flags), "a success before the block cannot heal it")
        login.write_text(json.dumps({model: [time.time() - 601, ""]}))
        self.assertNotIn("started the review", self.tick(*flags), "the limit still holds")
        self.assertTrue(json.loads(login.read_text())[model][1])
        limit.unlink()
        self.assertNotIn("started the review", self.tick(*flags), "a failed probe is trusted ten minutes")
        login.write_text(json.dumps({model: [time.time() - 601, "limit"]}))
        self.assertIn("started the review", self.tick(*flags))
        self.wait_for(lambda: self.read("045-recovered", "review.json")["verdict"] == "survives",
                      "the same day's review passed")
        self.assertEqual(self.read("045-recovered", "review.json")["retries"], review["retries"])

    def test_review_of_an_order_cut_stays_blocked_by_its_api_error_the_next_day(self):
        _, limit = self.blocked_by_the_limit("041-cut")
        (self.repo / "orders" / "041-cut.cut").touch()
        self.a_day_ago("041-cut")
        limit.unlink()
        self.assertNotIn("started the review", self.tick("--reviewer", self.model("clean")))
        self.assertEqual(self.read("041-cut", "review.json")["verdict"], "blocked")
        self.assertNotIn("next day", (self.repo / "STATUS.md").read_text())  # no try is promised

    def test_records_the_kernel_never_writes_leave_the_tick_running(self):
        self.done_run("043-odd")
        for odd in ({"order": "orders/043-odd.md", "verdict": "blocked", "reason": 5, "retries": []},
                    {"order": None, "verdict": "survives", "reason": "", "retries": []}):
            (self.run_dir("043-odd") / "review.json").write_text(json.dumps(odd))
            self.idle_tick()  # exit 0, as tick() asserts

    def test_review_killed_while_its_copy_is_made_holds_off_the_next_until_git_ends(self):
        self.done_run("038-copy")
        ceo = self.ceo_bg("review", "orders/038-copy.md", "--reviewer", self.model("clean"),
                          env=self.shim("clone -q"))
        self.wait_for((self.tmp / "paused").exists, "git made the copy and still runs")
        ceo.send_signal(signal.SIGKILL)
        ceo.communicate(timeout=30)
        work = ("--reviewer", self.model("clean"))
        self.assertNotIn("started the review", self.tick(*work), "a review started into the copy")
        self.tick_until((self.run_dir("038-copy") / "review.json").exists, "the run was reviewed",
                        *work)
        self.assertEqual(self.read("038-copy", "review.json")["verdict"], "survives")


if __name__ == "__main__":
    unittest.main()
