"""A live worker is never a stall, a folder removed mid-walk never stops the watch, and a capacity floor sends no CEO:
real processes, locks and ticks, stub models, no network (order 223)."""
import json, shlex, subprocess, sys, tempfile, time, unittest
from pathlib import Path
import ceo
from test_ceo import order_text
from test_filed import hold_ceo, stall_clock
from test_stalls import put
from test_tick import TickCase
HOLDER = ("import fcntl, os, sys\nfd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)\n"
          "fcntl.flock(fd, fcntl.LOCK_EX)\nprint(os.getpid(), flush=True)\nsys.stdin.read()\n")
CHURN = ("import os, shutil, sys\ni = 0\nwhile True:\n    d = os.path.join(sys.argv[1], 'tmp', str(i % 8))\n"
         "    os.makedirs(os.path.join(d, 'a', 'b', 'c'), exist_ok=True)\n"
         "    open(os.path.join(d, 'a', 'b', 'c', 'f'), 'w').close()\n    shutil.rmtree(d, ignore_errors=True)\n    i += 1\n")
class Snapshot(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.repo, self.old = Path(scratch.name), time.time() - 7200
    def order(self, slug, record=None):
        put(self.repo / "orders" / f"{slug}.md", "# Quiet work\n", self.old)
        put(self.repo / ".ceo" / "runs" / slug / "worker.log", "a check that prints nothing\n", self.old)
        if record:
            put(self.repo / ".ceo" / "runs" / slug / "result.json", json.dumps(record), self.old)
        return {slug: self.old}
    def hold(self, lock):  # a real process holds lock until the test lets it go, as ceo.py run and review do
        lock.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen([sys.executable, "-I", "-B", "-c", HOLDER, str(lock)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, start_new_session=True)
        self.addCleanup(lambda: (proc.stdin.close(), proc.wait(timeout=10), proc.stdout.close()))
        self.assertTrue(proc.stdout.readline().strip())
        if lock.suffix == ".run":
            ceo.write_json(lock.with_suffix(".json"), dict(order=f"orders/{lock.stem}.md", boot=ceo.boot(),
                                                           started="a silent run", groups=[proc.pid]))
        return proc
    def stops(self, ready):
        ceo.record_stalls(self.repo, ceo.stall_snapshot(self.repo, ready), time.time(), 0)
        return sorted(path.stem for path in (self.repo / ".ceo" / "stalls").glob("*.json"))
    def test_a_live_silent_run_is_never_a_stall(self):
        ready = self.order("001-work")
        proc = self.hold(self.repo / ".ceo" / "locks" / "001-work.run")
        row = ceo.stall_snapshot(self.repo, ready)["001-work"]
        self.assertEqual(row["last_progress"], self.old)
        self.assertFalse(ceo.stalled(row, time.time() + 30 * 86400, 0), "a live run was called a stall")
        self.assertEqual([self.stops(ready) for _ in range(3)], [[]] * 3)
        proc.stdin.close()
        proc.wait(timeout=10)
        self.assertEqual(self.stops(ready), ["001-work"], "once its run is gone, it stalls")
    def test_a_live_review_is_never_a_stall_and_covers_no_other_order(self):
        ready = {**self.order("001-work", {"state": "done"}), **self.order("002-other", {"state": "done"})}
        proc = self.hold(self.repo / ".ceo" / "locks" / "001-work.review")
        self.assertEqual(self.stops(ready), ["002-other"])
        proc.stdin.close()
        proc.wait(timeout=10)
        self.assertEqual(self.stops(ready), ["001-work", "002-other"])
    def test_a_folder_removed_while_the_watch_walks_it_never_stops_the_watch(self):
        ready = self.order("001-work")
        for root in (self.repo / ".ceo" / "review" / "001-work", self.repo / ".ceo" / "runs" / "001-work"):
            root.mkdir(parents=True, exist_ok=True)
            churn = subprocess.Popen([sys.executable, "-I", "-B", "-c", CHURN, str(root)])
            self.addCleanup(churn.wait)
            self.addCleanup(churn.kill)
            failed, passes, until = [], 0, time.monotonic() + 3
            while time.monotonic() < until or passes < 50:
                passes += 1
                try:
                    ceo.stall_snapshot(self.repo, ready)
                except OSError as why:
                    failed.append(str(why))
            churn.kill()
            self.assertEqual(failed[:1], [], f"{len(failed)} of {passes} stall watches stopped")
        put(self.repo / ".ceo" / "review" / "001-work" / "deep" / "er" / "check.log", "progress\n", time.time())
        self.assertGreater(ceo.stall_snapshot(self.repo, ready)["001-work"]["last_progress"], self.old)
class Ticks(TickCase):
    def test_a_running_order_whose_worker_is_silent_is_no_stall_on_a_zero_minute_clock(self):
        self.commit_order("001-silent.md")
        hold_ceo(self)
        stall_clock(self, 0)
        worker = self.model("wait", str(self.tmp / "started"), str(self.release))
        self.assertIn("ceo: started the run of orders/001-silent.md", self.tick("--worker", worker))
        self.wait_for(lambda: any(self.tmp.glob("started-*")), "the worker began")
        for _ in range(2):
            self.tick("--worker", worker)
            self.assertIn("001-silent", [slug for _, slug, _ in ceo.at_work(self.repo)])
            self.assertFalse((self.repo / ".ceo/stalls/001-silent.json").exists(), "a live run was stalled")
        self.release.touch()
        self.wait_for((self.run_dir("001-silent") / "result.json").exists, "the run ended")
    def test_a_start_a_capacity_floor_refuses_sends_no_ceo_and_keeps_its_alarm(self):
        conf, mark, script = self.repo / "ceo.toml", self.tmp / "ceo-started", self.tmp / "ceo.py"
        script.write_text("import json, sys\nopen(sys.argv[1], 'a').write('+')\nsys.stdin.read()\n"
                          "print(json.dumps({'decision': 'ask', 'reason': 'a test', 'question': 'Go on?'}))\n")
        conf.write_text(conf.read_text().replace("disk_floor_gb = 0", "disk_floor_gb = 1000000")
                        + "ceo = " + json.dumps(shlex.join([sys.executable, str(script), str(mark)])) + "\n")
        self.addCleanup(lambda: [self.wait_for(lambda: not ceo.held(one / "lock") and not ceo.held(one / "launch"),
                                               "a CEO ended") for one in (self.repo / ".ceo/decisions").glob("*")])
        stall_clock(self, 0)
        self.commit_order("001-floored.md")
        said = [self.tick("--worker", self.model("build")) for _ in range(3)]
        self.assertIn("ceo: not started: orders/001-floored.md: below the disk floor: ", said[0])
        self.assertEqual(list((self.repo / ".ceo/decisions").glob("*/stall.json")), [], "a CEO decided a floor")
        self.assertFalse(mark.exists(), "the CEO command started for a floor")
        self.assertEqual(list((self.repo / ".ceo/waiting").glob("*.json")), [])
        reason = ceo.read_json(self.repo / ".ceo/ready/001-floored.json")["reason"]
        line = f"- `001-floored` is eligible and not started: {reason.rstrip('.')}."  # order 208's alarm stays
        self.assertEqual((self.repo / "STATUS.md").read_text().split("## Needs attention\n", 1)[1].count(line), 1)
        conf.write_text(conf.read_text().replace("disk_floor_gb = 1000000", 'disk_floor_gb = "not a number"'))
        self.tick("--worker", self.model("build"))  # no capacity floor: a broken ceo.toml is a CEO's stop
        self.wait_for(mark.exists, "the CEO was sent for a start no floor refused")
    def test_the_floor_sentences_are_those_admit_and_draw_raise(self):
        for said, floor in (("below the disk floor: disk 1.0 GB free (floor 20 GB), RAM 9.0 GB free (floor 4 GB)", 1),
                            ("below the RAM floor: disk 90.0 GB free (floor 20 GB), RAM 1.0 GB free (floor 4 GB)", 1),
                            ("the claude quota is at 1 %, under its floor of 5 %", 1),
                            ("the machine is swapping: macOS memory pressure is warn", 1),
                            ("the machine's memory budget is spent: its agents hold 8 of 8 GB", 1),
                            ("the claude quota cannot be read: exit 1", 0), ("a floor in ceo.toml is not a number", 0),
                            ("the login of claude failed in the last ten minutes, or a '*' entry holds every start", 0),
                            ("this order has run before: its branch order/001-x exists", 0)):
            self.assertEqual(bool(ceo.FLOOR.match(said)), bool(floor), said)
if __name__ == "__main__":
    unittest.main()
