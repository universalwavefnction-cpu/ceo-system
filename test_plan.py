"""Plans filed by location and their scheduling: real git/processes/files, stub models, no network."""
import copy
import json
import os
import random
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path

from ceo import Refused, claim, crew, decide_plans, held, parse_plan, plan_snapshot, wait_on
from test_ceo import order_text
from test_tick import TickCase

A, B, C = (f"orders/{n:03d}-{letter}.md" for n, letter in enumerate("abc", 1))
PLAN = f"# Three steps\nat once 1\n- {A} — Build A.\n- {B} after 001 — Build B.\n- {C} after 002 — Build C.\n"


class Generated(unittest.TestCase):
    def test_350_seeded_plans_through_passes_merges_cuts_edits_and_restarts(self):
        for seed in range(350):
            rng = random.Random(seed)
            count, limit = rng.randint(2, 14), rng.randint(1, 5)
            paths = [f"orders/{i:03d}-step.md" for i in range(count)]
            parents = {i: {j for j in range(i) if rng.random() < .25} for i in range(count)}
            text = f"# Seed {seed}\nat once {limit}\n" + "".join(
                f"- {p}" + (" after " + ", ".join(f"{j:03d}" for j in sorted(parents[i]))
                            if parents[i] else "") + "\n" for i, p in enumerate(paths))
            plan = parse_plan(text)  # filed: a plan in plans/ is the plan as it stands
            orders = {p: dict(valid=True, replaces="", round=1, cut=False, merged=False,
                              started=False, phase="") for p in paths}
            snapshot = dict(plans={"plans/random.md": plan}, orders=orders, members=paths)
            all_before = {}
            for i in range(count):
                all_before[i] = set(parents[i])
                for parent in parents[i]:
                    all_before[i].update(all_before[parent])
            starts = set()
            for step in range(100):
                action = rng.choice(("pass", "pass", "pass", "merge", "cut", "restart", "review"))
                if action == "cut":
                    orders[rng.choice(paths)]["cut"] = True
                elif action == "merge":
                    live = [p for p in paths if orders[p]["phase"]]
                    if live:
                        orders[rng.choice(live)].update(merged=True, phase="")
                elif action == "review":
                    live = [p for p in paths if orders[p]["phase"]]
                    if live:
                        orders[rng.choice(live)]["phase"] = "in review"
                before = copy.deepcopy(snapshot)
                decision = decide_plans(snapshot)
                self.assertEqual(snapshot, before, (seed, step, "not pure"))
                self.assertEqual(decision, decide_plans(json.loads(json.dumps(snapshot))),
                                 (seed, step, "restart changed the decision"))
                # Independent oracle, using the generated graph, not parsed dependency closures.
                room = limit - sum(bool(orders[p]["phase"]) for p in paths)
                expected = []
                for i, p in enumerate(paths):
                    ancestors = [paths[j] for j in all_before[i]]
                    if (room > 0 and p not in starts and not orders[p]["cut"]
                            and all(orders[q]["merged"] and not orders[q]["cut"] for q in ancestors)):
                        expected.append(p)
                        room -= 1
                self.assertEqual(decision["start"], expected, (seed, step))
                if action == "pass":
                    for p in decision["start"]:
                        self.assertNotIn(p, starts)
                        self.assertFalse(orders[p]["cut"])
                        for j in all_before[paths.index(p)]:
                            self.assertTrue(orders[paths[j]]["merged"])
                            self.assertFalse(orders[paths[j]]["cut"])
                        starts.add(p)
                        orders[p].update(started=True, phase="running")
                    self.assertLessEqual(sum(bool(o["phase"]) for o in orders.values()), limit)
            # A generated back-edge in a chain must be rejected, not partially scheduled.
            cyclic = f"# Cycle\n- {paths[0]} after 001\n- {paths[1]} after 000\n"
            with self.assertRaisesRegex(Refused, "cycle"):
                parse_plan(cyclic)


class Plans(TickCase):
    def setUp(self):
        super().setUp()
        for path in (A, B, C):
            self.commit_order(Path(path).name, order_text(f"test -f {Path(path).stem}.txt"))
        # Each worker builds its own order, so predecessor merges do not pre-pass the next gate.
        self.stub.write_text(self.stub.read_text().replace(
            'open("built", "w").write("built\\n")',
            'name = os.path.basename(os.getcwd()) + ".txt"\n    open(name, "w").write("built\\n")').replace(
            '["git", "add", "built"]', '["git", "add", name]'))
        (self.repo / "plans").mkdir()
        self.plan = self.repo / "plans" / "steps.md"
        self.save_plan(PLAN)

    def save_plan(self, text):
        self.plan.write_text(text)
        self.git("add", "plans", "orders")
        self.git("commit", "-qm", "plan", "--allow-empty")

    def test_a_filed_plan_that_cannot_be_read_starts_nothing_it_lists_and_says_why(self):
        work = ("--worker", self.model("build"))
        once = "at once must be one whole number above 0"
        for text, why in ((PLAN.replace("at once 1", "at once 0"), once), (PLAN.replace("at once 1", "at once two"), once),
                          (PLAN.replace("at once 1", "at once 1.5"), once), (PLAN.replace("at once 1", "at once +2"), once),
                          (PLAN.replace("at once 1", "at once 1\nat once 2"), once),
                          (PLAN.replace(f"- {A} —", f"- {A} after 002 —").replace(" after 001", ""),
                           "after must name earlier lines of the plan"),
                          (PLAN.replace("after 001", "after 099"), "after 099 names an order outside the plan"),
                          (PLAN.replace(f"- {A} —", f"- {A} after 003 —"), "the plan's after lines make a cycle"),
                          (PLAN.replace("# Three steps\n", ""), "a plan needs a '# Title' line and at least one order"),
                          (PLAN + f"- {A}\n", "order number 001 occurs twice in the plan"),
                          (PLAN + "- malformed\n", "every '- ' line must name an order")):
            with self.subTest(text=text):
                self.save_plan(text)
                self.assertNotIn("started the run", self.tick(*work))
                self.assertIn(f"- `plans/steps.md` steps: {why}", (self.repo / "STATUS.md").read_text())
                self.assertEqual(sorted(decide_plans(plan_snapshot(self.repo))["members"]), [A, B, C])
        self.assertFalse((self.repo / ".ceo" / "runs").exists())

    def test_a_plan_line_naming_no_filed_order_is_said_each_pass_until_it_is_filed(self):
        (self.repo / "drafts" / "orders").mkdir(parents=True)
        self.git("mv", B, "drafts/orders/")
        self.git("commit", "-qm", "B back to drafts")
        line = f"- `plans/steps.md` lists `{B}`, which is not in orders/: file it there, or mend the line"
        for _ in range(2):  # every pass, once
            self.idle_tick()
            status = (self.repo / "STATUS.md").read_text().splitlines()
            self.assertEqual([one for one in status if one == line], [line])
            self.assertGreater(status.index(line), status.index("## Needs attention"))
            self.assertIn(f"  - `{B}` — Build B. — not in orders/", status)
        self.assertNotIn(B, decide_plans(plan_snapshot(self.repo))["alone"])
        self.git("mv", f"drafts/{B}", "orders/")
        self.git("commit", "-qm", "B filed")
        self.idle_tick()
        status = (self.repo / "STATUS.md").read_text()
        self.assertNotIn(line, status)
        self.assertIn(f"`{B}` — Build B. — waiting for 001", status)

    def test_three_tick_passes_start_a_then_b_then_c_once_each(self):
        work = ("--worker", self.model("build"), "--reviewer", self.model("clean"))
        for i, path in enumerate((A, B, C)):
            out = self.tick(*work)
            self.assertEqual([line for line in out.splitlines() if "started the run" in line],
                             [f"ceo: started the run of {path}"])
            slug = Path(path).stem
            self.wait_for((self.run_dir(slug) / "result.json").exists, "the stub worker finished")
            for later in (A, B, C)[i+1:]:
                self.assertFalse(self.run_dir(Path(later).stem).exists())
            # The actual stub reviewer runs before the test's local landing and merge record.
            review = self.ceo("review", path, "--reviewer", self.model("clean"))
            self.assertEqual(review.returncode, 0, review.stderr)
            self.git("merge", "-q", "--ff-only", f"order/{slug}")
            result = self.read(slug)
            result["merged"] = self.git("rev-parse", "main")
            (self.run_dir(slug) / "result.json").write_text(json.dumps(result))
        self.assertNotIn("started the run", self.tick(*work))
        self.assertEqual(sorted(p.name for p in (self.repo / ".ceo" / "plan-starts").iterdir()),
                         [Path(p).stem for p in (A, B, C)])
        status = (self.repo / "STATUS.md").read_text()
        self.assertIn("## Plans", status)
        for path, letter in zip((A, B, C), "ABC"):
            self.assertIn(f"`{path}` — Build {letter}. — merged", status)
        for path in (A, B, C):  # and no tick checked a merge against the vision
            self.assertFalse((self.run_dir(Path(path).stem) / "vision.json").exists())

    def test_answering_an_unstarted_order_starts_its_worker_and_keeps_the_answer(self):
        slug, words = Path(A).stem, "Continue with the authorized order."
        (self.repo / ".ceo").mkdir()
        wait_on(self.repo, slug, dict(order=A, decision="fixture-stop", question="May this order proceed?"))
        said = self.ceo("answer", slug, "answer", "--comment", words)
        self.assertEqual(said.returncode, 0, said.stderr)
        self.idle_tick()
        folder = self.run_dir(slug)
        self.assertEqual({path.name for path in folder.iterdir()}, {"owner.json"})
        owner = (folder / "owner.json").read_bytes()
        self.assertEqual(json.loads(owner)["words"][0]["answer"], words)
        self.assertFalse((self.repo / ".ceo" / "waiting" / f"{slug}.json").exists())
        self.assertEqual(decide_plans(plan_snapshot(self.repo))["start"], [A])
        self.assertIn(f"started the run of {A}", self.tick("--worker", self.model("build")))
        result = self.wait_for_run(slug)
        self.assertEqual((result["state"], result["worker_exit"], result["done_when_exit"]), ("done", 0, 0))
        self.assertEqual((folder / "owner.json").read_bytes(), owner)

    def test_prose_edits_a_draft_plan_cut_and_round_ancestry_survive_restart(self):
        def decision():
            snapshot = plan_snapshot(self.repo)
            result = decide_plans(snapshot)
            # New interpreter reads the same files; no process-global scheduling state survives.
            script = ("import ceo,json; from pathlib import Path; d = ceo.decide_plans(ceo.plan_snapshot(Path.cwd())); "
                      "print(json.dumps([d['start'], d['alone']]))")
            import subprocess, sys
            out = subprocess.check_output([sys.executable, "-B", "-c", script], cwd=self.repo,
                                           env={**self.env, "PYTHONPATH": str(Path(__file__).parent)}, text=True)
            self.assertEqual([result["start"], result["alone"]], json.loads(out))
            return result
        # Filed, the plan holds its orders as it stands: only A may start.
        self.assertEqual((decision()["start"], decision()["alone"]), ([A], []))
        self.assertEqual([decision()["states"]["plans/steps.md", path] for path in (A, B, C)],
                         ["eligible", "waiting for 001", "waiting for 001"])
        # A prose edit after filing changes nothing, in the folder or committed (IDEAS 44).
        self.plan.write_text(PLAN + "A note the CEO added after filing.\n")
        self.assertEqual(decision()["start"], [A])
        self.save_plan(PLAN + "A note the CEO added after filing.\n")
        self.assertEqual(decision()["start"], [A])
        # Back in drafts/, the plan holds nothing: its filed orders are no plan's, and start alone, oldest filed first.
        (self.repo / "drafts" / "plans").mkdir(parents=True)
        self.git("mv", "plans/steps.md", "drafts/plans/")
        self.git("commit", "-qm", "the plan back to drafts")
        self.assertEqual((decision()["start"], decision()["alone"], decision()["states"]), ([], [A, B, C], {}))
        self.git("mv", "drafts/plans/steps.md", "plans/")
        self.git("commit", "-qm", "the plan filed again")
        self.assertEqual((decision()["start"], decision()["alone"]), ([A], []))
        r2, r3 = "orders/001-round-2.md", "orders/001-round-3.md"
        for path, old, n in ((r2, A, 2), (r3, r2, 3)):
            self.commit_order(Path(path).name, f"# Round\nROUND: {n}\nREPLACES: {old}\n" + order_text("false"))
        (self.repo / r2).with_suffix(".cut").touch()  # an old cut is superseded
        folder = self.run_dir(Path(r3).stem)
        folder.mkdir(parents=True)
        # A commit on another branch is not a merge into main.
        foreign = self.git("commit-tree", self.git("rev-parse", "HEAD^{tree}"), "-m", "foreign")
        (folder / "result.json").write_text(json.dumps({"merged": foreign}))
        self.assertEqual(decision()["start"], [])  # a replacement has a run record; B still waits
        (folder / "result.json").write_text(json.dumps({"merged": self.git("rev-parse", "main")}))
        self.assertEqual(decision()["start"], [B])
        (self.repo / r3).with_suffix(".cut").touch()
        self.assertEqual(decision()["start"], [])
        self.assertEqual(decision()["states"]["plans/steps.md", C], "blocked by cut 001")
        self.idle_tick()
        status = (self.repo / "STATUS.md").read_text()
        self.assertIn(f"`{A}` — Build A. — cut", status)
        self.assertIn(f"`{C}` — Build C. — blocked by cut 001", status)

    def test_parallel_limit_counts_runs_and_reviews_and_remembers_launch_intent(self):
        self.save_plan(PLAN.replace("at once 1", "at once 2").replace(" after 001", "").replace(" after 002", ""))
        work = ("--worker", self.model("wait", str(self.tmp / "pid"), str(self.release)),
                "--reviewer", self.model("wait", str(self.tmp / "review"), str(self.tmp / "review-release")))
        self.addCleanup((self.tmp / "review-release").touch)
        self.assertEqual(self.tick(*work).splitlines(), [f"ceo: started the run of {p}" for p in (A, B)])
        for path in (A, B):
            self.wait_for((self.tmp / f"pid-{Path(path).stem}").exists, "worker started")
        self.assertEqual(self.tick(*work), "")
        self.assertIn("— running", (self.repo / "STATUS.md").read_text())
        self.release.touch()
        for path in (A, B):
            self.wait_for((self.run_dir(Path(path).stem) / "result.json").exists, "worker finished")
        self.assertNotIn(f"started the run of {C}", self.tick(*work))
        self.assertIn("— in review", (self.repo / "STATUS.md").read_text())
        for path in (A, B):
            self.wait_for((self.tmp / f"review-{Path(path).stem}").exists, "review started")
        self.assertEqual(decide_plans(plan_snapshot(self.repo))["start"], [])
        (self.tmp / "review-release").touch()
        for path in (A, B):
            slug = Path(path).stem
            self.wait_for(lambda: (self.run_dir(slug) / "review.json").exists()
                          and not held(self.repo / ".ceo" / "locks" / f"{slug}.review"), "review finished")
        # A marker without a living launch or a run record cannot consume the remaining order.
        marker = self.repo / ".ceo" / "plan-starts" / Path(C).stem
        marker.write_text(C)
        self.assertEqual(decide_plans(plan_snapshot(self.repo))["start"], [C])

    def test_launch_lock_reserves_capacity_and_its_record_prevents_a_restart(self):
        import os
        self.save_plan(PLAN.replace(" after 001", "").replace(" after 002", ""))
        marker = self.repo / ".ceo" / "plan-starts" / Path(A).stem
        lock = claim(marker)
        try:
            self.assertEqual(decide_plans(plan_snapshot(self.repo))["start"], [])
        finally:
            os.close(lock)
        self.assertEqual(decide_plans(plan_snapshot(self.repo))["start"], [A])
        self.run_dir(Path(A).stem).mkdir(parents=True)
        self.assertEqual(decide_plans(plan_snapshot(self.repo))["start"], [B])
        self.assertTrue(marker.exists(), "a stopped launch lost its record")

    def test_sweeping_keeps_the_predecessor_merge_proof(self):
        import os, time
        folder = self.run_dir(Path(A).stem)
        folder.mkdir(parents=True)
        result = dict(order=A, state="done", merged=self.git("rev-parse", "main"), cleared="earlier")
        (folder / "result.json").write_text(json.dumps(result))
        old = time.time() - 3 * 86400
        for path in (folder / "result.json", folder):
            os.utime(path, (old, old))
        done = self.ceo("sweep")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(folder.exists())
        self.assertEqual(decide_plans(plan_snapshot(self.repo))["start"], [B])
        kept = self.repo / ".ceo" / "swept" / Path(A).stem
        self.assertEqual(json.loads(kept.read_text())["merged"], result["merged"])

    def test_a_malformed_plan_holds_its_orders_and_a_removed_line_leaves_its_order_alone(self):
        self.plan.write_text(PLAN + "- malformed\n")  # in the folder, the tree a tick with no origin reads
        self.assertNotIn("started the run", self.tick("--worker", self.model("build")))
        self.assertEqual(sorted(decide_plans(plan_snapshot(self.repo))["members"]), [A, B, C])
        # An edited line counts from the next pass: its order is filed still, and no plan's.
        self.plan.write_text(PLAN.replace(f"- {A} — Build A.\n", "").replace(" after 001", ""))
        decision = decide_plans(plan_snapshot(self.repo))
        self.assertEqual((decision["start"], decision["alone"]), ([B], [A]))
        self.plan.unlink()
        self.assertEqual(decide_plans(plan_snapshot(self.repo))["alone"], [A, B, C])

    def test_a_floor_refusal_does_not_consume_the_plan_order(self):
        (self.repo / "ceo.toml").write_text('disk_floor_gb = 1000000\nnight = ""\n')
        said = self.tick("--worker", self.model("build"))
        self.assertIn("below the disk floor", said)
        self.assertFalse((self.repo / ".ceo" / "plan-starts" / Path(A).stem).exists())
        self.assertEqual(decide_plans(plan_snapshot(self.repo))["start"], [A])

    def test_an_archived_run_is_still_a_run_record(self):
        archive = self.repo / ".ceo" / "interrupted" / (Path(A).stem + "-earlier")
        archive.mkdir(parents=True)
        (archive / "record.json").write_text(json.dumps({"order": A}))
        self.assertEqual(decide_plans(plan_snapshot(self.repo))["start"], [A])
        # An archive cannot bypass a worktree that recovery has not finished clearing.
        (self.repo / ".ceo" / "wt" / Path(A).stem).mkdir(parents=True)
        self.assertEqual(decide_plans(plan_snapshot(self.repo))["start"], [])

    def wait_for_run(self, slug):
        result = self.run_dir(slug) / "result.json"
        self.wait_for(lambda: result.exists() and self.read(slug).get("ended")
                      and not held(self.repo / ".ceo" / "plan-starts" / slug), "run exited")
        return self.read(slug)

    def test_a_refusal_inside_run_retries_on_the_next_pass(self):
        conf = self.repo / "ceo.toml"
        floors = conf.read_text()
        quota = self.tmp / "quota.sh"
        quota.write_text('pid=$$\nwhile [ "$pid" -gt 1 ]; do\n  set -- $(ps -o args= -p "$pid")\n'
                         '  if [ "${2##*/}" = ceo.py ]; then [ "$3" = run ] && echo 1 || echo 50; exit; fi\n'
                         '  pid=$(ps -o ppid= -p "$pid" | tr -d " ")\ndone\necho 50\n')
        conf.write_text(floors + f'quota_cmd.claude = "bash {quota}"\n')
        work = ("--worker", self.model("build"))
        self.assertIn(f"started the run of {A}", self.tick(*work))
        marker = self.repo / ".ceo" / "plan-starts" / Path(A).stem
        self.wait_for(lambda: "refused" in (self.repo / ".ceo" / "tick.log").read_text()
                      and not held(marker), "run refused by its own floor check")
        self.assertTrue(marker.exists())
        self.assertFalse(self.run_dir(Path(A).stem).exists())
        conf.write_text(floors)
        self.assertIn(f"started the run of {A}", self.tick(*work))
        self.assertEqual(self.wait_for_run(Path(A).stem)["state"], "done")
        self.assertFalse(self.run_dir(Path(B).stem).exists())

    def test_an_api_error_retries_in_its_existing_worktree(self):
        limited = self.tmp / "limited"
        limited.touch()
        self.assertIn(f"started the run of {A}", self.tick("--worker", self.model("limit", str(limited))))
        self.assertEqual(self.wait_for_run(Path(A).stem)["state"], "retry")
        wt = self.repo / ".ceo" / "wt" / Path(A).stem
        inode = wt.stat().st_ino
        (wt / "kept").write_text("same worktree\n")
        self.git("-C", str(wt), "add", "kept")  # the retry's worker commits the preserved work too
        self.assertIn(f"started the run of {A}", self.tick("--worker", self.model("build")))
        self.assertEqual(self.wait_for_run(Path(A).stem)["state"], "done")
        self.assertEqual(wt.stat().st_ino, inode)
        self.assertEqual((wt / "kept").read_text(), "same worktree\n")
        self.assertEqual(self.git("-C", str(wt), "show", "HEAD:kept"), "same worktree")
        self.assertFalse(self.run_dir(Path(B).stem).exists())

    def test_a_merged_replacement_unblocks_orders_after_a_cut_round(self):
        (self.repo / A).with_suffix(".cut").write_text("Cut on the owner's word.\n")
        replacement = "004-a-round-2.md"
        self.commit_order(replacement, f"# A, round 2\n\nROUND: 2\nREPLACES: {A}\n" +
                          order_text("test -f built"))
        folder = self.run_dir(Path(replacement).stem)
        folder.mkdir(parents=True)
        (folder / "result.json").write_text(json.dumps({"merged": self.git("rev-parse", "main")}))
        said = self.tick("--worker", self.model("build"))
        self.assertEqual([line for line in said.splitlines() if "started the run" in line],
                         [f"ceo: started the run of {B}"])
        self.assertEqual(self.wait_for_run(Path(B).stem)["state"], "done")
        self.assertFalse(self.run_dir(Path(A).stem).exists())
        self.assertFalse(self.run_dir(Path(C).stem).exists())
        self.assertIn(f"`{A}` — Build A. — cut", (self.repo / "STATUS.md").read_text())

    def test_an_orders_own_cut_stops_its_retry_after_a_new_round_replaces_it(self):
        limited = self.tmp / "limited"
        limited.touch()
        self.assertIn(f"started the run of {A}", self.tick("--worker", self.model("limit", str(limited))))
        slug = Path(A).stem
        self.assertEqual(self.wait_for_run(slug)["state"], "retry")
        before = (self.run_dir(slug) / "result.json").read_bytes()
        (self.repo / A).with_suffix(".cut").write_text("Cut on the owner's word.\n")
        replacement = "004-a-round-2.md"
        self.commit_order(replacement, f"# A, round 2\n\nROUND: 2\nREPLACES: {A}\n" +
                          order_text("test -f built"))
        said = self.tick("--worker", self.model("build"))  # the round after the cut starts, as any filed round
        self.assertEqual([line for line in said.splitlines() if "started the run" in line],
                         [f"ceo: started the run of orders/{replacement}"])
        self.wait_for_run(Path(replacement).stem)
        self.assertEqual((self.run_dir(slug) / "result.json").read_bytes(), before)
        status = (self.repo / "STATUS.md").read_text()
        self.assertIn(f"`{A}` — Build A. — cut", status)
        self.assertIn(f"`{B}` — Build B. — waiting for 001", status)
        self.assertFalse((self.repo / ".ceo" / "wt" / slug / f"{slug}.txt").exists())

    def test_a_cut_off_run_recovers_and_restarts_from_its_cut_branch_in_one_pass(self):
        self.stub.write_text(self.stub.read_text().replace(
            '    while not os.path.exists(args[1]):',
            '    open("half", "w").write("kept work\\n")\n    while not os.path.exists(args[1]):'))
        self.assertIn(f"started the run of {A}", self.tick(
            "--worker", self.model("wait", str(self.tmp / "pid"), str(self.release))))
        slug = Path(A).stem
        wt = self.repo / ".ceo" / "wt" / slug
        self.wait_for((wt / "half").exists, "worker left unfinished work")
        locks = self.repo / ".ceo" / "locks"
        meta = json.loads((locks / f"{slug}.json").read_text())
        pid = int((locks / f"{slug}.run").read_text().split()[0])
        # Stop the controller first, before a killed worker lets it start Git completion checks.
        for group in dict.fromkeys((pid, *meta["groups"])):  # only this test's process groups
            subprocess.run(["ps", "-o", "pid=,pgid=,args=", "-p", str(group)], capture_output=True)
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass  # the initial done-when group has already exited
        self.wait_for(lambda: not held(locks / f"{slug}.run")
                      and not held(self.repo / ".ceo" / "plan-starts" / slug), "cut-off processes exited")
        said = self.tick("--worker", self.model("build"))
        self.assertIn(f"recovered {slug}", said)
        self.assertIn(f"started the run of {A}", said)
        result = self.wait_for_run(slug)
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["resumed_from"], f"order/{slug}-cut-1")
        self.assertEqual((wt / "half").read_text(), "kept work\n")
        self.assertEqual(self.git("show", f"order/{slug}-cut-1:half"), "kept work")
        self.assertEqual(len(self.records(slug)), 1)
        self.assertFalse(self.run_dir(Path(B).stem).exists())

    def test_only_retry_records_are_eligible_again_and_swept_always_stays_stopped(self):
        slug = Path(A).stem
        folder = self.run_dir(slug)
        folder.mkdir(parents=True)
        for state in ("done", "failed", "blocked", "waiting", "retry"):
            with self.subTest(state=state):
                (folder / "result.json").write_text(json.dumps({"state": state}))
                self.assertEqual(decide_plans(plan_snapshot(self.repo))["start"], [A] if state == "retry" else [])
        swept = self.repo / ".ceo" / "swept" / slug
        swept.parent.mkdir()
        swept.touch()
        self.assertEqual(decide_plans(plan_snapshot(self.repo))["start"], [])

    def test_max_is_an_unknown_flag(self):
        done = self.ceo("tick", "--max", "2")
        self.assertEqual(done.returncode, 2)
        self.assertIn("unrecognized arguments: --max 2", done.stderr)


class Crew(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self.conf = self.repo / "ceo.toml"
        self.conf.write_text(
            '[workers]\nsol = "sol-cmd"\nastra = "astra-cmd"\nopus = "opus-cmd"\n'
            '[reviewers]\nopenai = "shared-openai-reviewer"\nclaude = "sol-cmd"\n'
            'astra = "opus-high-cmd"\nsol = "claude-medium-cmd"\n'
            '[vendor]\nsol-cmd = "openai"\nastra-cmd = "openai"\n'
            'opus-high-cmd = "claude"\nclaude-medium-cmd = "claude"\n')

    def named(self, name):
        return order_text("true") + f"\n## Worker\n{name}\n"

    def test_worker_name_reviewer_wins_over_vendor_with_vendor_fallback(self):
        for name, worker, reviewer in (("astra", "astra-cmd", "opus-high-cmd"),
                                       ("sol", "sol-cmd", "claude-medium-cmd"),
                                       ("opus", "opus-cmd", "sol-cmd")):
            with self.subTest(worker=name):
                self.assertEqual(crew(self.repo, self.named(name), "flag-worker", "flag-reviewer"),
                                 (worker, reviewer))

    def test_worker_reviewer_override_keeps_the_same_vendor_refusal_and_opt_in(self):
        self.conf.write_text(self.conf.read_text().replace('astra = "opus-high-cmd"',
                                                          'astra = "sol-cmd"'))
        with self.assertRaisesRegex(Refused, "both of the vendor openai"):
            crew(self.repo, self.named("astra"))
        self.conf.write_text("same_vendor_review = true\n" + self.conf.read_text())
        self.assertEqual(crew(self.repo, self.named("astra")), ("astra-cmd", "sol-cmd"))

    def test_without_either_reviewer_mapping_the_worker_is_the_fallback(self):
        self.conf.write_text('[workers]\nastra = "astra-cmd"\n[vendor]\nastra-cmd = "openai"\n')
        with self.assertRaisesRegex(Refused, "both of the vendor openai"):
            crew(self.repo, self.named("astra"))
        self.conf.write_text("same_vendor_review = true\n" + self.conf.read_text())
        self.assertEqual(crew(self.repo, self.named("astra")), ("astra-cmd", "astra-cmd"))


class Documentation(unittest.TestCase):
    def test_readme_fits_both_its_line_and_column_caps(self):
        lines = (Path(__file__).resolve().parent / "README.md").read_text().splitlines()
        self.assertLessEqual(len(lines), 100)
        self.assertEqual([(i, len(line)) for i, line in enumerate(lines, 1) if len(line) > 131], [])


if __name__ == "__main__":
    unittest.main()
