"""Quiet CPU work, idle workers and queued rounds: real subprocesses, plus process-table edge cases."""
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import ceo_activity as activity
import ceo_status as status
from test_stalls import put
from test_tick import TickCase


WORKER = '''import os, sys, time
if sys.argv[1] == "descendant":
    pid = os.fork()
    if pid:
        os.waitpid(pid, 0)
        sys.exit()
    os.setsid()
print(os.getpid(), flush=True)
while sys.stdin.buffer.read(1):
    until = time.process_time() + 0.15
    while time.process_time() < until:
        sum(i * i for i in range(1000))
    print("+", flush=True)
'''


class Activity(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.repo, self.old = Path(scratch.name), time.time() - 7200
        self.ready, self.workers = {}, []
        self.addCleanup(self.stop)

    def stop(self):
        for proc, worker, lock in self.workers:
            if proc.poll() is None:
                with status.contextlib.suppress(ProcessLookupError):
                    os.kill(worker, signal.SIGTERM)
                proc.terminate()
            proc.wait(timeout=10)
            proc.stdin.close()
            proc.stdout.close()
            os.close(lock)
        self.workers.clear()

    def order(self, slug, previous=""):
        text = "# Quiet work\n" + (f"ROUND: 2\nREPLACES: orders/{previous}.md\n" if previous else "")
        put(self.repo / "orders" / f"{slug}.md", text, self.old)
        self.ready[slug] = self.old

    def worker(self, slug="001-work", mode="direct"):
        self.order(slug)
        path = self.repo / ".ceo/locks" / f"{slug}.run"
        lock = status.claim(path)
        status.sign(lock)
        proc = subprocess.Popen([sys.executable, "-I", "-B", "-c", WORKER, mode],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, start_new_session=True,
                                pass_fds=(lock,))
        self.assertTrue(select.select([proc.stdout], [], [], 10)[0])
        worker = int(proc.stdout.readline())
        self.workers.append((proc, worker, lock))
        status.write_json(path.with_suffix(".json"), dict(order=f"orders/{slug}.md", boot=status.boot(),
                                                         started="old run", groups=[proc.pid]))
        put(self.repo / ".ceo/runs" / slug / "worker.log", "quiet command\n", self.old)
        return proc

    def pulse(self, proc):
        proc.stdin.write(b"+")
        proc.stdin.flush()
        self.assertTrue(select.select([proc.stdout], [], [], 15)[0], "worker made no CPU progress")
        self.assertEqual(proc.stdout.readline(), b"+\n")

    def observe(self):
        return status.stall_snapshot(self.repo, self.ready)

    def test_idle_live_worker_never_stalls_until_it_ends_and_only_cpu_deltas_move_it(self):
        proc = self.worker()
        self.pulse(proc)  # Historical CPU before the first observation is no proof of current work.
        first = self.observe()["001-work"]
        self.assertFalse(status.stalled(first, time.time()), "a live run was called a stall")
        self.assertEqual(first["last_progress"], self.old)
        second = self.observe()["001-work"]
        self.assertEqual(second["last_progress"], self.old)
        self.pulse(proc)
        moving = self.observe()["001-work"]
        self.assertGreater(moving["last_progress"], self.old)
        self.assertFalse(status.stalled(moving, moving["last_progress"] + 2701), "a live run was called a stall")
        for _ in range(2):
            self.assertEqual(self.observe()["001-work"]["last_progress"], moving["last_progress"])
        log = self.repo / ".ceo/runs/001-work/worker.log"
        self.assertEqual((log.read_text(), log.stat().st_mtime), ("quiet command\n", self.old))
        self.stop()  # once its run is gone, it stalls on its last progress
        self.assertTrue(status.stalled(self.observe()["001-work"], moving["last_progress"] + 2701))

    def test_descendant_in_another_session_and_fresh_observer_process(self):
        proc = self.worker(mode="descendant")
        self.observe()
        self.pulse(proc)
        code = ("import json, sys; from pathlib import Path; import ceo_status as s; "
                "print(json.dumps(s.stall_snapshot(Path(sys.argv[1]), {'001-work': float(sys.argv[2])})))")
        done = subprocess.run([sys.executable, "-B", "-c", code, str(self.repo), str(self.old)],
                              capture_output=True, text=True, check=True, timeout=20)
        row = json.loads(done.stdout)["001-work"]
        self.assertFalse(status.stalled(row, time.time()))
        self.assertGreater(row["last_progress"], self.old)

    def test_queued_family_waits_for_idle_live_predecessor_then_stalls_when_it_exits(self):
        self.worker()
        self.order("002-next", "001-work")
        self.order("003-other")
        rows = self.observe()
        self.assertTrue(rows["002-next"]["waiting"])
        self.assertFalse(status.stalled(rows["002-next"], time.time()))
        self.assertTrue(status.stalled(rows["003-other"], time.time()))
        self.stop()
        rows = self.observe()
        self.assertFalse(rows["002-next"]["waiting"])
        self.assertTrue(status.stalled(rows["002-next"], time.time()))

    def test_started_later_round_does_not_borrow_predecessors_liveness(self):
        self.worker()
        self.order("002-next", "001-work")
        put(self.repo / ".ceo/runs/002-next/worker.log", "already started\n", self.old)
        self.assertTrue(status.stalled(self.observe()["002-next"], time.time()))

    def test_terminal_result_or_other_boot_ends_the_exemption_despite_a_live_process(self):
        proc = self.worker()
        self.order("002-next", "001-work")
        self.observe()
        self.pulse(proc)
        self.observe()
        path = self.repo / ".ceo/runs/001-work/result.json"
        for state in ("failed", "retry"):
            put(path, json.dumps(dict(state=state, started="old run")), time.time())
            self.assertNotIn("001-work", activity.run_activity(self.repo))
            self.assertTrue(status.stalled(self.observe()["002-next"], time.time()))
        path.unlink()
        meta = self.repo / ".ceo/locks/001-work.json"
        status.write_json(meta, {**status.read_json(meta), "boot": "another boot"})
        self.assertNotIn("001-work", activity.run_activity(self.repo))
        self.assertTrue(status.stalled(self.observe()["002-next"], time.time()))

    def test_retry_new_attempt_uses_its_own_cpu_baseline(self):
        proc = self.worker()
        put(self.repo / ".ceo/runs/001-work/result.json", json.dumps(dict(state="retry", started="prior run")), self.old)
        first = self.observe()["001-work"]  # the new attempt's first observation is its baseline, no progress
        self.assertEqual(first["last_progress"], self.old)
        self.assertFalse(status.stalled(first, time.time()), "a live retry was called a stall")
        self.pulse(proc)
        moving = self.observe()["001-work"]
        self.assertGreater(moving["last_progress"], self.old)
        self.assertFalse(status.stalled(moving, time.time()))
        self.stop()  # once its run is gone, it stalls on its last progress
        self.assertTrue(status.stalled(self.observe()["001-work"], moving["last_progress"] + 2701))

    def test_pid_reuse_and_new_run_generation_cannot_supply_a_cpu_delta(self):
        proc = self.worker()
        for birth, cpu, expected in ((1, 200, 0), (2, 300, 0), (2, 301, 1)):
            with patch.object(activity, "cpu_processes", return_value={proc.pid: (0, proc.pid, birth, cpu)}):
                observed = activity.run_activity(self.repo)["001-work"]
                self.assertEqual(observed > 0, bool(expected))
        status.sign(self.workers[0][2])  # Same PID, new run lock generation.
        with patch.object(activity, "cpu_processes", return_value={proc.pid: (0, proc.pid, 2, 400)}):
            self.assertEqual(activity.run_activity(self.repo)["001-work"], 0)

    def test_cpu_increase_survives_other_member_exit(self):
        proc = self.worker()
        with patch.object(activity, "cpu_processes", return_value={
                proc.pid: (0, proc.pid, 1, 1000), 12: (proc.pid, 12, 2, 100)}):
            self.assertEqual(activity.run_activity(self.repo)["001-work"], 0)
        with patch.object(activity, "cpu_processes", return_value={12: (1, 12, 2, 101)}):
            self.assertGreater(activity.run_activity(self.repo)["001-work"], 0)

    def test_recycled_group_and_signed_pid_do_not_revive_an_ended_run(self):
        proc = self.worker()
        path = self.repo / ".ceo/locks/001-work.run"
        self.stop()
        for cpu in (100, 101):
            with patch.object(activity, "cpu_processes", return_value={
                    proc.pid: (0, proc.pid, time.time() + 10, cpu)}):
                self.assertNotIn("001-work", activity.run_activity(self.repo))
        # An orphan can still hold the old lock after its signed parent PID is reused.
        lock = status.claim(path)
        try:
            path.write_text("12\n")
            os.utime(path, (self.old, self.old))
            born = time.time() + 10
            for cpu in (102, 103):
                with patch.object(activity, "cpu_processes", return_value={
                        12: (0, 12, born, 1), proc.pid: (12, proc.pid, born, cpu)}):
                    self.assertEqual(activity.run_activity(self.repo)["001-work"], 0)
        finally:
            os.close(lock)

    def test_macos_ps_cpu_precision_birth_and_zombies(self):
        output = (" 12 1 12 S Tue Nov 3 10:00:00 2026 2:03.45\n"
                  " 13 1 13 Z Tue Nov 3 10:00:00 2026 0:30.00\n")
        with patch.object(Path, "exists", return_value=False), patch.object(
                activity.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output)):
            rows = activity.cpu_processes()
        self.assertEqual(set(rows), {12})
        self.assertEqual(rows[12][:2], (1, 12))
        self.assertEqual(time.localtime(rows[12][2])[:6], (2026, 11, 3, 10, 0, 0))
        self.assertAlmostEqual(rows[12][3], 123.45)


class RetryAttempts(TickCase):
    def test_live_retry_with_the_same_started_second_as_its_previous_result(self):
        # Exercise two real run attempts; fix only their displayed second so slow hosts hit the collision too.
        driver = self.tmp / "same-second.py"
        driver.write_text(f"import sys\nsys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
                          "import ceo, ceo_run\n"
                          f"ceo_run.now = lambda: {status.now()!r}\n"
                          "sys.exit(ceo.main())\n")
        self.stub.write_text('''import json, os, sys
from pathlib import Path
sys.stdin.buffer.read()
if sys.argv[1] == "api":
    print(json.dumps(dict(type="result", terminal_reason="api_error", result="transient failure")))
    sys.exit(1)
Path(sys.argv[2]).write_text(str(os.getpid()))
while True:
    sum(i * i for i in range(1000))
''')
        slug, marker = "001-retry", self.tmp / "cpu-worker"
        self.commit_order(slug + ".md")
        with patch("test_tick.CEO", driver):
            first = self.ceo("run", f"orders/{slug}.md", "--worker", self.model("api"), "--timeout", "20")
            result = self.read(slug)
            self.assertEqual(result["state"], "retry", first.stderr)
            second = self.ceo_bg("run", f"orders/{slug}.md", "--worker", self.model("cpu", str(marker)),
                                 "--timeout", "20")
        try:
            self.wait_for(marker.exists, "the second attempt's CPU worker starts")
            lock = self.repo / ".ceo/locks" / f"{slug}.run"
            meta = status.read_json(lock.with_suffix(".json"))
            self.assertEqual(result["started"], meta["started"])
            self.assertLess((self.run_dir(slug) / "result.json").stat().st_mtime_ns, lock.stat().st_mtime_ns)
            activity.run_activity(self.repo)
            pid = int(marker.read_text())
            before = activity.cpu_processes()[pid][3]
            self.wait_for(lambda: activity.cpu_processes()[pid][3] > before, "real CPU use after the baseline")
            self.assertGreater(activity.run_activity(self.repo).get(slug, 0), 0)
        finally:
            if second.poll() is None:
                second.terminate()
            second.communicate(timeout=20)


if __name__ == "__main__":
    unittest.main()
