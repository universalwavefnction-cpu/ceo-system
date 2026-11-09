"""A test that fails on the joined tree gets its fix round, as a failed review does: real ticks, workers, reviewers
and doors in scratch projects whose gate.sh is this kernel's own; only the models and GitHub are stand-ins."""
import json

import ceo
from test_ceo import order_text
from test_door import STUB
from test_suite_once import Project

STUB += r'''
elif mode == "red":  # a worker that commits its order's file and a test of its own that fails; in its round, the fix
    head = brief.partition(b"git merge --no-edit ")[2][:40].decode()
    if head:  # its round: the run's head merged, then the failing test removed
        subprocess.run(["git", "merge", "-q", "--no-edit", head], check=True)
        subprocess.run(["git", "rm", "-q", "test_red.py"], check=True)
    else:
        open(os.path.basename(os.getcwd()) + ".txt", "w").write("red\n")
        open("test_red.py", "w").write("import unittest\n\nclass Red(unittest.TestCase):\n"
                                       "    def test_red(self):\n        self.fail('seeded')\n")
        subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", "red"], check=True)
'''


class DoorFix(Project):
    def setUp(self):
        super().setUp()
        self.stub.write_text(STUB)

    def ran(self, slug, name="result.json"):
        return ceo.read_json(self.run_dir(slug) / name)

    def failed_twice(self, slug, result):
        """Both door tries ran the suite and failed; the review is the door's own and fails, with no attention."""
        self.assertEqual([(one["what"], one["exit"]) for one in result["door_failures"]], [("./gate.sh", 1)] * 2)
        self.assertEqual(self.git("ls-tree", "--name-only", result["head"], "test_red.py"), "test_red.py")
        self.assertEqual((self.ran(slug, "review.json")["verdict"], "attention" in result), ("fails", False))
        self.assertIn("FAIL: test_red", self.ran(slug, "review.json")["door"])
        told = [json.loads(line) for line in (self.repo / ".ceo" / "events.jsonl").read_text().splitlines()]
        kinds = [one["event"] for one in told if one["slug"] == slug]
        self.assertEqual((kinds.count("failed"), kinds.count("blocked")), (1, 0))

    def test_a_test_failing_on_the_joined_tree_gets_a_fix_round_that_lands_without_the_owner(self):
        slug, round_ = "052-red", "052-round-2"
        done = lambda: "cleared" in self.ran(round_) or "attention" in {**self.ran(slug), **self.ran(round_)}
        result = self.ordered(slug, order_text(f"test -f {slug}.txt"), "red", until=done)
        self.failed_twice(slug, result)
        self.assertEqual(("cleared" in self.ran(round_), "attention" in self.ran(round_)), (True, False))
        origin = lambda *args: self.git("-C", str(self.origin), *args)
        text = origin("show", f"main:orders/{round_}.md")
        filed, said = origin("log", "--diff-filter=A", "--format=%H %s", "main", "--", f"orders/{round_}.md").split(" ", 1)
        self.assertEqual((said, origin("show", "--format=", "--name-only", filed)),  # the round alone, filed in orders/
                         (f"orders/{slug}.md: its fix round orders/{round_}.md", f"orders/{round_}.md"))
        self.assertEqual(origin("show", f"main:{slug}.txt"), "red")
        self.assertIn("FAIL: test_red", text)
        self.assertTrue(ceo.parse_order(text)[1].startswith(f"git merge-base --is-ancestor {result['head']} HEAD"))
        self.assertNotIn("gate.sh", ceo.parse_order(text)[1])
        self.assertEqual(origin("log", "--first-parent", "main", "--", "test_red.py"), "")  # never main, only the round
        self.assertEqual(self.lines(self.seen), [str(self.repo / ".ceo" / "door")] * 3)
        self.assertEqual(len(self.lines(self.green)), 1)
        self.assertFalse(any((self.repo / ".ceo" / "waiting").glob("*")), "the owner was asked")

    def test_at_round_cap_no_round_is_filed_and_the_failure_goes_the_way_of_a_failed_review(self):
        slug = "053-red"
        with (self.repo / "ceo.toml").open("a") as config:
            config.write("round_cap = 1\n")
        done = lambda: "attention" in self.ran(slug) or self.ran(slug, "review.json").get("verdict") == "fails"
        self.ordered(slug, order_text(f"test -f {slug}.txt"), "red", until=done)
        self.tick("--worker", self.model("red"), "--reviewer", self.model("clean"))  # a pass that could file a round
        self.failed_twice(slug, self.ran(slug))
        self.assertEqual((self.on_origin("main:orders/053-round-2.md"), list(self.repo.glob("orders/*round*"))),
                         ("", []))
        self.assertEqual((self.on_origin("main:test_red.py"), self.server()[0]["state"]), ("", "OPEN"))
        # The CEO's path: 46 minutes on, it stalls by its readiness clock, and its bell rings.
        snapshot = ceo.stall_snapshot(self.repo, ceo.ready_times(self.repo))
        ceo.record_stalls(self.repo, snapshot, snapshot[slug]["last_progress"] + 46 * 60, 45)
        ceo.ring(self.repo, snapshot, at=snapshot[slug]["state_time"] + 31 * 60)
        self.assertTrue((self.repo / ".ceo" / "stalls" / f"{slug}.json").exists())
        self.assertEqual(len(list((self.repo / ".ceo" / "bells").glob(f"attention-{slug}-*"))), 1)
