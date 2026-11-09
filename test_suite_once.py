"""The whole suite once per order, at the door: real ticks, workers, reviewers and doors in scratch projects whose
gate.sh is this kernel's own. Only the models are stand-ins, test_door's stub, and its fake gh."""
import shutil
import subprocess

from ceo import bug_order, held, read_json
from test_ceo import order_text
from test_door import KERNEL, DoorCase

# The scratch project's one test: it writes down the folder of each full run, and never sees the door's record.
SEEN = ('import os, unittest\n\nclass Seen(unittest.TestCase):\n    def test_seen(self):\n'
        '        self.assertNotIn("CEO_GATE_RECORD", os.environ)\n'
        '        open({seen!r}, "a").write(os.getcwd() + "\\n")\n')


class Project(DoorCase):
    def setUp(self):
        super().setUp()
        self.env.pop("CEO_GATE_RECORD", None)  # a door running these tests names a record of its own
        self.seen, self.green = self.tmp / "seen", self.repo / ".ceo" / "gate-green"
        shutil.copy(KERNEL / "gate.sh", self.repo)
        (self.repo / "test_seen.py").write_text(SEEN.format(seen=str(self.seen)))
        self.commit_all("gate")

    def lines(self, path):
        return path.read_text().splitlines() if path.exists() else []

    def ordered(self, slug, text, worker="own", until=None):
        """File the order, committed in orders/, and tick until its door merged or parked it, or until() holds."""
        (self.repo / "orders" / f"{slug}.md").write_text(text)
        self.commit_all(f"order {slug} filed")
        result = lambda: read_json(self.run_dir(slug) / "result.json")
        self.tick_while(until or (lambda: {"cleared", "attention"} & set(result())), f"{slug} merged or parked",
                        "--worker", self.model(worker), "--reviewer", self.model("clean"), timeout=300)
        self.wait_for(lambda: not held(self.repo / ".ceo" / "locks" / "door.lock"), "the door ended", 300)
        return result()


class SuiteOnce(Project):
    def test_a_bug_fix_order_runs_the_suite_once_at_the_door(self):
        slug = "050-a-fix"  # not the bug door's name for it: no report of a bug moves with it
        (self.repo / "bugs" / "1").mkdir(parents=True)
        (self.repo / "bugs" / "1" / "repro.sh").write_text(f"test -f {slug}.txt\n")
        self.commit_all("bug 1: a script that fails on main")
        merge = self.main()
        result = self.ordered(slug, bug_order(self.repo, merge, 1, "fix", merge)[1])
        self.assertIn("cleared", result, result)
        self.assertEqual((self.lines(self.seen), len(self.lines(self.green))), ([str(self.repo / ".ceo" / "door")], 1))

    def test_a_done_when_that_calls_the_gate_shares_the_doors_one_run(self):
        slug = "051-gated"
        result = self.ordered(slug, order_text(f"set -e; test -f {slug}.txt; ./gate.sh"))
        self.assertIn("cleared", result, result)
        self.assertEqual(self.lines(self.seen), [str(self.repo / result["worktree"]), str(self.repo / ".ceo" / "door")])
        log = (self.run_dir(slug) / "door.log").read_text()
        self.assertEqual((log.count("door: ./gate.sh on the joined commit"), log.count("not run again")), (1, 1))
        self.assertEqual(len(self.lines(self.green)), 1)


class Record(Project):
    def gate_run(self, record=True):
        env = {**self.env, "CEO_GATE_RECORD": str(self.green)} if record else self.env
        done = subprocess.run(["./gate.sh"], cwd=self.repo, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return "not run again" in done.stdout

    def test_only_a_clean_tree_the_door_saw_pass_passes_at_once(self):
        self.green = self.tmp / "gate-green"
        python = subprocess.run(["python3", "--version"], env=self.env, capture_output=True, text=True).stdout
        key = f"{self.git('rev-parse', 'HEAD^{tree}')} {self.git('hash-object', 'gate.sh')} {python.strip()}"
        skipped = [self.gate_run(), self.gate_run(), self.gate_run(record=False)]
        self.assertEqual((skipped, len(self.lines(self.seen))), ([False, True, False], 2))
        (self.repo / "dirty.txt").write_text("not committed\n")
        self.assertEqual([self.gate_run(), self.gate_run()], [False, False])
        self.assertEqual(self.lines(self.seen), [str(self.repo)] * 4)
        self.assertEqual(self.lines(self.green), [key])
