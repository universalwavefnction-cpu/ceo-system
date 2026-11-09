"""Conflict joins with real ticks, doors and git; only the models and GitHub are stubs."""
import shlex
import subprocess
import sys
import unittest

import ceo
from test_ceo import order_text
from test_door import DoorCase


JOINER = r'''
import os, re, subprocess, sys
asked = re.search(r"git merge --no-edit ([0-9a-f]{40})", sys.stdin.read())
if asked:  # a join merges its original head and keeps both sides of every conflicting line
    if subprocess.run(["git", "merge", "-q", "--no-edit", asked[1]]).returncode:
        for name in subprocess.run(["git", "diff", "--name-only", "--diff-filter=U"], capture_output=True,
                                   text=True, check=True).stdout.split():
            with open(name) as source:
                kept = [line for line in source if not line.startswith(("<<<<<<<", "=======", ">>>>>>>"))]
            with open(name, "w") as target:
                target.writelines(kept)
            subprocess.run(["git", "add", name], check=True)
        subprocess.run(["git", "commit", "-q", "--no-edit"], check=True)
else:
    with open("shared.txt", "w") as target:
        target.write(os.path.basename(os.getcwd()) + "\n")
    subprocess.run(["git", "add", "shared.txt"], check=True)
    subprocess.run(["git", "commit", "-qm", "same"], check=True)
'''


class Join(DoorCase):
    def setUp(self):
        super().setUp()
        joiner = self.tmp / "joiner.py"
        joiner.write_text(JOINER)
        self.worker = shlex.join([sys.executable, str(joiner)])
        self.work = ["--worker", self.worker, "--reviewer", self.model("clean")]
        with (self.repo / "ceo.toml").open("a") as config:
            config.write("round_cap = 1\n")
        self.names = ("201-x", "202-y")

    def origin_git(self, *args):
        done = subprocess.run(["git", "--git-dir", str(self.origin), *args], capture_output=True,
                              text=True, env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def joins_on_main(self):
        return [path for path in self.origin_git("ls-tree", "--name-only", "main", "orders/").split()
                if "-join-" in path]

    def parked(self):
        return next((name for name in self.names if (self.run_dir(name) / "result.json").exists()
                     and "attention" in self.read(name)), None)

    def conflict(self, start, door=None):
        """Two orders filed in orders/ that write one file, started alone, by their plan or by hand, and door() run
        until it met the conflict: the tick's own doors, or, given door, the owner's, with no tick at all."""
        for name in self.names:
            (self.repo / "orders" / f"{name}.md").write_text(
                order_text("test -f shared.txt", title=f"Order {name}"))
        if start == "plan":
            (self.repo / "plans").mkdir()
            (self.repo / "plans" / "shared.md").write_text(
                "# Two orders share a file\nat once 2\n" +
                "".join(f"- orders/{name}.md\n" for name in self.names))
        self.commit_all("two orders that write one file, filed")
        if start == "hand":
            for name in self.names:
                run = self.ceo("run", f"orders/{name}.md", "--worker", self.worker)
                self.assertEqual(run.returncode, 0, run.stderr)
                review = self.ceo("review", f"orders/{name}.md", "--reviewer", self.model("clean"))
                self.assertEqual(review.returncode, 0, review.stderr)
        if door:  # the first merges one, the second meets the conflict
            self.assertEqual([door().returncode for _ in self.names], [0, 1])
        self.tick_while(self.parked, "the door met a conflict with main", *self.work, timeout=300)
        slug = self.parked()
        result = self.read(slug)
        self.assertEqual(result["conflicts"], ["shared.txt"])
        self.assertNotIn("merged", result)
        self.assertIn("conflicts with main in shared.txt", result["attention"])
        for name in self.names:
            self.assertEqual((self.repo / ".ceo" / "plan-starts" / name).exists(), start == "plan")
        return slug, result

    def assert_join_merges(self, start):
        slug, result = self.conflict(start)
        join = f"orders/{slug[:3]}-join-{result['head'][:7]}.md"
        for _ in range(3):
            if join in self.joins_on_main():
                break
            self.tick(*self.work)
        self.assertIn(join, self.joins_on_main(), "no join round reached origin's main")
        text = self.origin_git("show", f"main:{join}")
        filed, said = self.origin_git("log", "--format=%H %s", "-1", "main", "--", join).strip().split(" ", 1)
        self.assertEqual((said, self.origin_git("show", "--format=", "--name-only", filed).split()),
                         (f"orders/{slug}.md: its join round {join}", [join]))  # the round alone, filed in orders/
        self.assertIn(f"\nROUND: 1\nREPLACES: orders/{slug}.md\n", text.split("\n## ", 1)[0])
        goal = text.split("\n## Goal\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn(f"1. Run `git merge --no-edit {result['head']}`", goal)
        self.assertIn("shared.txt", goal)
        self.assertEqual(ceo.parse_order(text)[1],
                         f"git merge-base --is-ancestor {result['head']} HEAD || {{ "
                         f"echo \"the run's head {result['head']} is not merged into this head\"; exit 1; }}\n"
                         "test -f shared.txt\n")
        joined = join[7:-3]
        self.tick_while(lambda: "cleared" in ceo.read_json(self.run_dir(joined) / "result.json"),
                        "the join round merged", *self.work, timeout=300)
        self.wait_for(lambda: not ceo.held(self.repo / ".ceo" / "locks" / "door.lock"), "the door ended")
        self.assertFalse((self.run_dir(joined) / "vision.json").exists())
        landed = self.read(joined)
        self.assertTrue(landed.get("merged"), landed)
        self.assertTrue(landed.get("cleared"), landed)
        self.assertEqual(self.server()[landed["pr"] - 1]["state"], "MERGED")
        other = next(name for name in self.names if name != slug)
        for commit in (self.read(other)["merged"], result["head"], landed["merged"]):
            self.origin_git("merge-base", "--is-ancestor", commit, "main")
        self.assertEqual(sorted(self.origin_git("show", "main:shared.txt").split()), sorted(self.names))
        for _ in range(2):
            self.tick(*self.work)
        self.assertEqual(self.joins_on_main(), [join])
        self.assertTrue((self.repo / result["worktree"]).is_dir())

    def test_a_filed_order_whose_door_meets_a_conflict_is_joined_by_a_filed_round_and_merges(self):
        self.assert_join_merges("alone")

    def test_an_order_its_plan_started_whose_door_meets_a_conflict_is_joined_and_merges(self):
        self.assert_join_merges("plan")

    def test_a_filed_order_run_by_hand_whose_door_meets_a_conflict_is_joined_as_well_and_merges(self):
        self.assert_join_merges("hand")

    def test_a_run_whose_door_met_a_conflict_and_whose_order_was_then_cut_is_parked_and_never_joined(self):
        slug, result = self.conflict("hand", door=self.door)  # no tick yet, so no join round either
        (self.repo / "orders" / f"{slug}.cut").write_text("Cut on the owner's word.\n")
        self.commit_all(f"cut {slug}")
        for _ in range(3):
            self.tick(*self.work)
            self.assertEqual(self.joins_on_main(), [])
        current = self.read(slug)
        for key in ("head", "conflicts", "attention"):
            self.assertEqual(current[key], result[key])
        self.assertNotIn("merged", current)
        self.assertEqual(self.server()[result["pr"] - 1]["state"], "OPEN")
        self.assertTrue((self.repo / result["worktree"]).is_dir())

    def test_a_parked_run_whose_join_is_on_origins_main_never_stalls_while_the_folder_lags_it(self):
        for name in self.names:
            (self.repo / "orders" / f"{name}.md").write_text(
                order_text("test -f shared.txt", title=f"Order {name}"))
        self.commit_all("two orders that write one file, filed")
        (self.repo / "shared.txt").write_text("the owner's work, not committed\n")  # main here cannot follow
        self.tick_while(self.parked, "the door met a conflict with main", *self.work, timeout=300)
        slug = self.parked()
        join = f"orders/{slug[:3]}-join-{self.read(slug)['head'][:7]}.md"
        for _ in range(3):
            if join in self.joins_on_main():
                break
            self.tick(*self.work)
        self.assertIn(join, self.joins_on_main(), "no join round reached origin's main")
        self.assertNotEqual(self.git("rev-parse", "main"), self.main(), "the folder follows origin's main")
        self.assertFalse((self.repo / join).exists())
        late = 45 * 60 + 1
        snapshot = ceo.stall_snapshot(self.repo, ceo.ready_times(self.repo))
        ceo.record_stalls(self.repo, snapshot, snapshot[slug]["last_progress"] + late, 45)
        self.assertFalse((self.repo / ".ceo" / "stalls" / f"{slug}.json").exists(),
                         "the parked run stalls although its join is on origin's main")
        (self.repo / "shared.txt").unlink()  # the owner's work is gone: the folder follows origin's main again
        self.git("merge", "-q", "--ff-only", "origin/main")
        joined = join[7:-3]
        self.wait_for(lambda: (self.run_dir(joined) / "result.json").exists(), "the join's run ended", 120)
        row = ceo.stall_snapshot(self.repo, ceo.ready_times(self.repo))[joined]  # a live run is no stall: once it ends
        self.assertTrue(ceo.stalled(row, row["last_progress"] + late, 45), f"the join itself never stalls: {row}")


if __name__ == "__main__":
    unittest.main()
