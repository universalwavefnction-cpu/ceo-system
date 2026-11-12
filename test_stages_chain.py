"""Stages chain: an order may wait for an order of another filed plan, and starts on the first pass after it merges."""
import json
import os
import time
import unittest
from unittest import mock
import ceo
import ceo_decisions
from test_ceo import order_text
from test_door import DoorCase
from test_tick import TickCase
ONE, TWO, THREE = "orders/001-first.md", "orders/002-next.md", "orders/003-more.md"
def plan(title, *lines):
    return f"# {title}\n" + "".join(f"- {line}\n" for line in lines)
class Quiet:
    """No agent a test's ticks started outlives the test: it would write into the folder removed after it."""
    def setUp(self):
        super().setUp()
        self.addCleanup(self.quiet)  # runs before the folder's own cleanup, registered earlier
    def quiet(self):
        held = lambda *globs: any(ceo.held(path) for root, glob in globs for path in root.glob(glob) if path.is_file())
        home, ours = self.tmp / "ceo", self.repo / ".ceo"
        # A tick claims these before it spawns the child and hands them down: held from its start to its end.
        self.wait_for(lambda: not held((home, "memory/*"), (ours, "plan-starts/*"), (ours, "decisions/*/launch")),
                      "every agent a tick started ended", 120)
        # Then nothing is left to claim its own lock: a door, or a worker group outliving its run.
        self.wait_for(lambda: not held((ours, "locks/*"), (ours, "decisions/*/lock")), "every lock was let go", 120)
class Chain(Quiet, DoorCase):
    def test_the_next_stages_order_starts_on_the_first_pass_after_the_order_it_names_merges(self):
        for path, text in ((ONE, order_text("test -f 001-first.txt")), (TWO, order_text("test -f 002-next.txt")),
                           ("plans/one.md", plan("Stage one", f"{ONE} — The first stage.")),
                           ("plans/also.md", plan("Also", f"{ONE} — The same order, listed by a second plan.")),
                           ("plans/two.md", plan("Stage two", f"{TWO} after 001 — The next stage."))):
            (self.repo / path).parent.mkdir(exist_ok=True)
            (self.repo / path).write_text(text)
        self.commit_all("two stages filed at once, the second ahead of time")
        work = ("--worker", self.model("own"), "--reviewer", self.model("clean"))
        door, deadline, starts = self.repo / ".ceo/locks/door.lock", time.monotonic() + 600, []
        while True:  # passes and nothing else: the kernel's own run, review, door, pull and start
            merged = bool(ceo.read_json(self.run_dir("001-first") / "result.json").get("merged")) and not ceo.held(door)
            said = self.tick(*work)
            starts += [line for line in said.splitlines() if "started the run" in line]
            if f"started the run of {TWO}" in said:
                break
            self.assertFalse(merged, "001 was merged before this pass, and this pass did not start 002")
            self.assertIn(f"`{TWO}` — The next stage. — waiting for 001", (self.repo / "STATUS.md").read_text())
            self.assertFalse(self.run_dir("002-next").exists(), "002 ran before 001 merged")
            self.assertLess(time.monotonic(), deadline, "002 never started")
            time.sleep(0.2)
        head = self.read("001-first")["merged"]
        self.assertEqual(self.git("merge-base", "--is-ancestor", head, "refs/remotes/origin/main"), "")
        self.assertEqual(starts, [f"ceo: started the run of {ONE}", f"ceo: started the run of {TWO}"])
        self.wait_for(lambda: ceo.read_json(self.run_dir("002-next") / "result.json").get("state") == "done",
                      "the next stage's run, which its own run did not refuse, finished", 120)
class Across(Quiet, TickCase):
    def file(self, **files):
        for path, text in files.items():
            (self.repo / path).parent.mkdir(exist_ok=True)
            (self.repo / path).write_text(text)
        self.git("add", "-A")
        self.git("commit", "-qm", "filed")
    def started(self):
        said = self.tick("--worker", self.model("build"), "--reviewer", self.model("clean"))
        return sorted(line.rsplit(" ", 1)[1] for line in said.splitlines() if "started the run" in line)
    def status(self):
        return (self.repo / "STATUS.md").read_text()
    def test_an_after_naming_a_number_no_filed_plan_lists_is_refused_and_says_the_number(self):
        self.file(**{ONE: order_text("test -f built"), TWO: order_text("test -f built"),
                     "plans/one.md": plan("Stage one", f"{ONE} — The first stage."),
                     "plans/two.md": plan("Stage two", f"{TWO} after 009 — The next stage.")})
        self.assertEqual(self.started(), [ONE])
        self.assertIn("- `plans/two.md` two: after 009 names an order no filed plan lists", self.status())
        self.assertEqual(self.started(), [], "an order of a refused plan started")
        self.assertIn(TWO, ceo.decide_plans(ceo.plan_snapshot(self.repo))["members"])
    def test_a_plan_filed_before_the_plan_it_names_waits_from_the_pass_that_files_that_plan(self):
        self.file(**{TWO: order_text("test -f built"), "plans/two.md": plan("Stage two", f"{TWO} after 001 — Next.")})
        self.assertEqual(self.started(), [])
        self.assertIn("- `plans/two.md` two: after 001 names an order no filed plan lists", self.status())
        self.file(**{ONE: order_text("test -f built"), "plans/one.md": plan("Stage one", f"{ONE} — The first.")})
        self.assertEqual(self.started(), [ONE])
        self.assertIn("- `plans/two.md` Stage two\n", self.status())
        self.assertIn(f"`{TWO}` — Next. — waiting for 001", self.status())
    def test_a_cycle_across_two_plans_refuses_both_and_no_other_plan(self):
        four, five = "orders/004-after.md", "orders/005-free.md"
        self.file(**{path: order_text("test -f built") for path in (ONE, TWO, THREE, four, five)},
                  **{"plans/one.md": plan("Stage one", f"{ONE} after 002 — First.", f"{THREE} — Unrelated."),
                     "plans/two.md": plan("Stage two", f"{TWO} after 001 — Second."),
                     "plans/three.md": plan("Stage three", f"{four} after 001 — After it.", f"{five} — Free.")})
        self.assertEqual(self.started(), [five])
        for name in ("one", "two"):
            self.assertIn(f"- `plans/{name}.md` {name}: the plan's after lines make a cycle", self.status())
        self.assertIn("- `plans/three.md` Stage three\n", self.status())
        self.assertIn(f"`{four}` — After it. — waiting for 001", self.status())
        self.assertEqual(self.started(), [])
    def test_a_cycle_through_an_order_two_plans_share_refuses_each_plan_on_it(self):
        self.file(**{path: order_text("test -f built") for path in (ONE, TWO, THREE)},
                  **{"plans/one.md": plan("Stage one", f"{ONE} after 002 — Shared.", f"{THREE} after 001 — Last."),
                     "plans/two.md": plan("Stage two", ONE, f"{TWO} after 003 — Middle.")})
        self.assertEqual(self.started(), [])  # 001 waits for 002 in one plan, though the other lists it free
        for name in ("one", "two"):
            self.assertIn(f"- `plans/{name}.md` {name}: the plan's after lines make a cycle", self.status())
    def test_a_third_plan_listing_an_order_on_a_cycle_free_is_refused_and_never_starts_it(self):
        four = "orders/004-free.md"
        self.file(**{path: order_text("test -f built") for path in (ONE, TWO, THREE, four)},
                  **{"plans/one.md": plan("Stage one", f"{ONE} after 002 — On the cycle.", f"{four} — Free."),
                     "plans/two.md": plan("Stage two", f"{TWO} after 001 — On the cycle."),
                     "plans/three.md": plan("Stage three", ONE, f"{THREE} after 004 — Later.")})
        self.assertEqual(self.started(), [])  # 001 waits, through the first plan, for itself
        for name in ("one", "two", "three"):
            self.assertIn(f"- `plans/{name}.md` {name}: the plan's after lines make a cycle", self.status())
    def test_a_cut_order_another_plan_names_blocks_it_as_inside_one_plan(self):
        self.file(**{ONE: order_text("test -f built"), "orders/001-first.cut": "Cut on the owner's word.\n",
                     TWO: order_text("test -f built"), "plans/one.md": plan("Stage one", f"{ONE} — The first stage."),
                     "plans/two.md": plan("Stage two", f"{TWO} after 001 — The next stage.")})
        self.assertEqual(self.started(), [])
        self.assertIn(f"`{TWO}` — The next stage. — blocked by cut 001", self.status())
    def test_a_revision_keeps_reading_a_plan_that_names_another_plans_order(self):
        self.file(**{ONE: order_text("test -f built"), TWO: order_text("test -f built"),
                     "plans/one.md": plan("Stage one", f"{ONE} — The first stage."),
                     "plans/two.md": plan("Stage two", f"{TWO} after 001 — The next stage.")})
        stall = dict(slug="002-next", since="once", last_progress=1, state="failed", last_log="Its proof is wrong.")
        raw = json.dumps(dict(decision="revise", reason="Repair the proof.",
                              order=order_text("test -f built", title="Next, repaired")))
        with mock.patch.dict(os.environ, self.env):  # the CEO's answer, recorded on main by the kernel's own path
            entry = ceo_decisions.apply(self.repo, stall, raw, timeout=60)
        self.assertEqual(entry["decision"], "revise", entry)
        self.assertEqual(self.git("show", "main:plans/two.md"),
                         "# Stage two\n- orders/003-next-repaired.md after 001 — The next stage.")
if __name__ == "__main__":
    unittest.main()
