"""Order families across stalls, plans and process entry points; real files, git and local stubs."""
import json
import os
import signal
import shlex
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ceo
import ceo_decisions as decisions
from test_ceo import kernel_patch, order_text
from test_door import DoorCase
from test_tick import TickCase


class Families(TickCase):
    def test_parseable_deep_line(self):
        # The decoder's nesting limit depends on this Python and its C stack.
        def parseable(depth):
            try:
                json.loads("[" * depth + "]" * depth)
            except RecursionError:
                return False
            return True

        low, high = 0, 1
        while parseable(high):
            low, high = high, high * 2
        while high - low > 1:
            middle = (low + high) // 2
            if parseable(middle):
                low = middle
            else:
                high = middle
        # Allow for dispatch's deeper call stack; a shallow control also survives the encoder.
        depths = (1, max(1, low - 100), high + 100)
        journal = self.repo / "decisions.md"
        base = json.dumps(dict(id="0" * 64, order="orders/009-other.md", decision="cut", reason="Older"))[:-1]
        journal.write_text("# Decisions\n" + "".join('- %s, "stop": %s%s}\n' % (base, "[" * n, "]" * n)
                                                  for n in depths))
        self.assertTrue(decisions.entries(journal.read_text()))
        self.git("add", "decisions.md")
        self.git("commit", "-qm", "deep")
        stub = self.tmp / "decide.py"
        stub.write_text('import json, sys\nsys.stdin.read()\n'
                        'print(json.dumps(dict(decision="wait", reason="Another interval")))\n')
        with (self.repo / "ceo.toml").open("a") as conf:
            conf.write("ceo = " + json.dumps(shlex.join([sys.executable, str(stub)])) + "\n")
        self.round("002-stop", ready=True)
        self.result("002-stop", state="failed")
        self.age()
        self.assertEqual(self.stops(), {"002-stop"})
        children = {}
        with patch.dict(os.environ, self.env):
            decisions.dispatch(self.repo, 20, children)
        proc = list(children.values())[-1]
        self.addCleanup(lambda: proc.wait(timeout=60))
        self.assertEqual(proc.wait(timeout=60), 0, (self.repo / ".ceo/tick.log").read_text()[-600:])
        self.assertTrue(any(one["order"] == "orders/002-stop.md" for one in decisions.entries(journal.read_text())))
        key = next(iter(children))[1]
        applied = ceo.read_json(self.repo / ".ceo/decisions" / key / "applied.json")
        self.assertEqual(applied["decision"], "wait")
        self.assertIn("skipped decision: RecursionError", (self.repo / ".ceo/tick.log").read_text())

    def test_revision_after_cut_round_three_starts_before_its_next_round(self):
        first = self.round("054-first")
        self.round("054-round-2", 2, "054-first")
        cut = self.round("054-round-3", 3, "054-round-2")
        (self.repo / cut).with_suffix(".cut").touch()
        revision = self.round("057-revised", 1, "054-round-3")
        self.plan([first])
        for slug, latest in (("057-revised", revision), ("057-round-2", "orders/057-round-2.md")):
            if slug == "057-round-2":
                self.round(slug, 2, "057-revised")
            for source in (None, "main"):
                with self.subTest(slug=slug, source=source):
                    family = ceo.order_families(ceo.order_snapshot(self.repo, source))[first]
                    self.assertEqual(family["latest"], latest)
                    self.assertFalse(family["finished"])
                    self.assertTrue(ceo.order_active(self.repo, slug, source))
                    self.assertTrue(ceo.order_active(self.repo, slug, source, for_merge=True))
                    self.assertFalse(ceo.order_active(self.repo, "054-round-3", source))
            self.assertEqual(ceo.decide_plans(ceo.plan_snapshot(self.repo))["start"], [latest])
            self.idle_tick()  # a pass finds it eligible, a login outage holds its start: its clock begins
            rows = ceo.stall_snapshot(self.repo, ceo.ready_times(self.repo))
            self.assertEqual({name for name, row in rows.items() if ceo.stalled(row, time.time() + 7200)}, {slug})
            self.assertIn(f"started the run of {latest}", self.tick("--worker", self.model("build")))
            self.assertEqual(self.wait_for_run(slug)["state"], "done")
            ceo.write_json(self.run_dir(slug) / "review.json", dict(verdict="fails"))
        self.assertFalse(ceo.order_active(self.repo, "057-revised"))

    def test_unreadable_and_unappliable_journal_lines_do_not_block_other_stops(self):
        old = dict(slug="001-old", since=ceo.now(), last_progress=1, state="failed", last_log="")
        key = decisions.identity(old)
        folder = self.repo / ".ceo/decisions" / key
        folder.mkdir(parents=True)
        ceo.write_json(folder / "stall.json", old)
        self.round("001-old")
        entry = dict(id=key, order="orders/001-old.md", decision="ask", reason="Needs the owner")
        missing = "- " + json.dumps(entry)
        nested = '- ' + '[' * 2000 + '0' + ']' * 2000
        entry["question"] = "Which approach?"
        blocked = "- " + json.dumps(entry)
        (self.repo / ".ceo/waiting/001-old.json").mkdir(parents=True)
        journal = self.repo / "decisions.md"
        for name, line in (("missing question", missing), ("nested JSON", nested), ("cannot apply", blocked)):
            with self.subTest(case=name), patch.dict(os.environ, self.env):
                journal.write_text("# Decisions\n" + line + "\n")
                self.git("add", "decisions.md")
                self.git("commit", "-qm", name)
                decisions.dispatch(self.repo, 20, {})
                self.assertFalse((folder / "applied.json").exists())
                self.assertIn("decisions.md:2", (self.repo / ".ceo/tick.log").read_text())
        journal.write_text("# Decisions\n" + "\n".join((missing, nested, blocked)) + "\n")
        stub = self.tmp / "decide.py"
        stub.write_text('import json, sys\nsys.stdin.read()\n'
                        'print(json.dumps(dict(decision="wait", reason="Another interval")))\n')
        with (self.repo / "ceo.toml").open("a") as conf:
            conf.write("ceo = " + json.dumps(shlex.join([sys.executable, str(stub)])) + "\n")
        for n in (2, 3):
            self.round(f"{n:03d}-stop", ready=True)
            self.result(f"{n:03d}-stop", state="failed")
        self.age()
        self.assertEqual(self.stops(), {"002-stop", "003-stop"})
        children = {}
        with patch.dict(os.environ, self.env):
            for n in (1, 2):
                decisions.dispatch(self.repo, 20, children)
                self.assertEqual(len(children), n)
                proc = list(children.values())[-1]
                self.addCleanup(lambda proc=proc: proc.wait(timeout=30))
                self.assertEqual(proc.wait(timeout=30), 0)
        for slug in ("002-stop", "003-stop"):
            self.assertTrue(any(one["order"] == f"orders/{slug}.md" and one["decision"] == "wait"
                                for one in decisions.entries(journal.read_text())))
        self.assertFalse((folder / "applied.json").exists())
        log = (self.repo / ".ceo/tick.log").read_text()
        for number in (2, 3, 4):
            self.assertIn(f"decisions.md:{number}", log)

    def test_cut_done_order_releases_the_plans_only_slot(self):
        first = self.round("001-first")
        second = self.round("002-next")
        (self.repo / "plans").mkdir()
        (self.repo / "plans/steps.md").write_text(f"# Steps\nat once 1\n- {first}\n- {second}\n")
        self.git("add", "plans", "orders")
        self.git("commit", "-qm", "independent plan steps")
        self.result("001-first", state="done")
        (self.repo / first).with_suffix(".cut").touch()
        decision = ceo.decide_plans(ceo.plan_snapshot(self.repo))
        self.assertEqual(decision["states"]["plans/steps.md", first], "cut")
        self.assertEqual(decision["start"], [second])
        out = self.tick("--worker", self.model("build"))
        self.assertIn(f"started the run of {second}", out)
        self.assertNotIn("started the review", out)
        self.assertEqual(self.wait_for_run("002-next")["state"], "done")

    def test_nested_order_never_joins_a_family_from_either_source(self):
        first = self.round("001-first")
        (self.repo / "orders/proof").mkdir()
        (self.repo / "orders/proof/001-z-notes.md").write_text("ROUND: 9\nnotes only\n")
        self.git("add", "orders")
        self.git("commit", "-qm", "nested evidence")
        for source in (None, "main"):
            with self.subTest(source=source):
                self.assertEqual(set(ceo.order_snapshot(self.repo, source)), {first})
                self.assertTrue(ceo.order_active(self.repo, "001-first", source))

    def test_nonfiles_with_md_names_do_not_end_a_tick(self):
        first = self.round("001-first")
        (self.repo / "orders/002-directory.md").mkdir()
        (self.repo / "orders/003-broken.md").symlink_to("missing")
        self.assertEqual(set(ceo.order_snapshot(self.repo)), {first})
        self.assertEqual(set(ceo.stall_snapshot(self.repo)), {"001-first"})
        result = self.idle_ceo("tick")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("stalls could not be read", result.stderr)

    def test_door_stopped_review_does_not_hold_cut_family_open(self):
        first = self.round("001-first")
        latest = self.round("001-round-2", 2, "001-first")
        (self.repo / latest).with_suffix(".cut").touch()
        head = self.git("rev-parse", "HEAD")
        self.result("001-first", state="done", head=head, pr=1)
        ceo.write_json(self.run_dir("001-first") / "review.json", dict(verdict="survives", commit=head))
        for stopped in ("attention", "cleared"):
            with self.subTest(stopped=stopped):
                self.result("001-first", state="done", head=head, pr=1, **{stopped: "door stopped"})
                family = ceo.order_families(ceo.order_snapshot(self.repo))[first]
                self.assertTrue(family["finished"])
                self.assertFalse(ceo.order_active(self.repo, "001-first", for_merge=True))
                self.assertEqual(ceo.door_runs(self.repo), [])

    def test_malformed_decision_lines_are_logged_and_other_stops_are_decided(self):
        stub = self.tmp / "decide.py"
        stub.write_text('import json, sys\nsys.stdin.read()\n'
                        'print(json.dumps(dict(decision="wait", reason="Another interval")))\n')
        with (self.repo / "ceo.toml").open("a") as conf:
            conf.write("ceo = " + json.dumps(shlex.join([sys.executable, str(stub)])) + "\n")
        for n in (1, 2):
            self.round(f"{n:03d}-stop", ready=True)
            self.result(f"{n:03d}-stop", state="failed")
        (self.repo / "decisions.md").write_text('# Decisions\n- {broken\n- {}\n- []\n- not JSON\n')
        self.git("add", "decisions.md")
        self.git("commit", "-qm", "malformed journal")
        self.age()
        self.assertEqual(len(self.stops()), 2)
        children = {}
        with patch.dict(os.environ, self.env):
            for n in (1, 2):
                decisions.dispatch(self.repo, 20, children)
                self.assertEqual(len(children), n)
                proc = list(children.values())[-1]
                self.addCleanup(lambda proc=proc: proc.wait(timeout=30))
                self.assertEqual(proc.wait(timeout=30), 0)
        self.assertEqual([row["decision"] for row in decisions.entries((self.repo / "decisions.md").read_text())],
                         ["wait", "wait"])
        log = (self.repo / ".ceo/tick.log").read_text()
        for number in range(2, 6):
            self.assertIn(f"decisions.md:{number}", log)

    def test_source_tick_git_process_count_is_constant_with_200_orders_and_nested_files(self):
        origin = self.tmp / "origin.git"
        self.git("init", "-q", "--bare", "-b", "main", str(origin))
        self.git("-C", str(origin), "config", "maintenance.auto", "false")
        self.git("remote", "add", "origin", str(origin))
        (self.repo / "orders/proof").mkdir()
        (self.repo / ".ceo").mkdir()
        (self.repo / ".ceo/last-sweep").touch()
        args = SimpleNamespace(worker=self.model("build"), reviewer=None, checker=None, timeout=20, every=None)
        popen = subprocess.Popen
        counts = []
        for size in (2, 200):
            for n in range(1, size + 1):
                (self.repo / f"orders/{n:03d}-first.md").write_text(order_text("test -f built"))
                (self.repo / f"orders/proof/{n:03d}-z-notes.md").write_text("ROUND: 9\nnotes\n")
            self.git("add", "orders", "ceo.toml", ".gitignore")
            self.git("commit", "-qm", f"{size} orders and nested files")
            self.git("push", "-q", "-u", "origin", "main")
            calls = []
            def counted(cmd, *args, **kwargs):
                if Path(cmd[0]).name == "git":
                    calls.append(cmd[1:])
                    if counts:
                        self.assertLessEqual(len(calls), counts[0], "more orders started more git processes")
                return popen(cmd, *args, **kwargs)
            with patch.dict(os.environ, self.env), kernel_patch("healthy", return_value=False), \
                    patch.object(subprocess, "Popen", side_effect=counted):
                ceo.tick_pass(self.repo, args, {}, set())
                ceo.watch_stalls(self.repo)
            counts.append(len(calls))
            self.assertIn("orders run from origin", (self.repo / "STATUS.md").read_text())
            self.assertFalse(list((self.repo / ".ceo/runs").glob("*/result.json")))
        self.assertEqual(counts[0], counts[1])
        print(f"Source tick git processes: 2 orders = {counts[0]}; 200 orders = {counts[1]}")

    def round(self, slug, number=1, replaces="", ready=False):
        """A round filed in orders/; ready, with the record of a pass that found it eligible now."""
        text = f"# Round {number}\nROUND: {number}\n"
        if replaces:
            text += f"REPLACES: orders/{replaces}.md\n"
        self.commit_order(slug + ".md", text + order_text("test -f built"))
        if ready:
            self.ready(slug, time.time())
        return f"orders/{slug}.md"

    def ready(self, slug, at):
        (self.repo / ".ceo/ready").mkdir(parents=True, exist_ok=True)
        since = datetime.fromtimestamp(at, timezone.utc).isoformat()
        ceo.write_json(self.repo / ".ceo/ready" / f"{slug}.json", dict(since=since, reason="", passes=0))

    def result(self, slug, **fields):
        self.run_dir(slug).mkdir(parents=True, exist_ok=True)
        ceo.write_json(self.run_dir(slug) / "result.json", dict(order=f"orders/{slug}.md", **fields))

    def wait_for_run(self, slug):
        self.wait_for((self.run_dir(slug) / "result.json").exists, "the owned worker finished")
        self.wait_for(lambda: not ceo.held(self.repo / ".ceo/locks" / (slug + ".run")), "run released its lock")
        return self.read(slug)

    def age(self):
        old = time.time() - 7200
        for root in (self.repo / "orders", self.repo / ".ceo" / "runs"):
            for path in root.rglob("*"):
                if path.is_file():
                    os.utime(path, (old, old))
        for slug in ceo.ready_times(self.repo):  # and the pass that found each ready
            self.ready(slug, old)
        return old

    def stops(self):
        snapshot = ceo.stall_snapshot(self.repo, ceo.ready_times(self.repo))
        ceo.record_stalls(self.repo, snapshot, time.time())
        return {p.stem for p in (self.repo / ".ceo" / "stalls").glob("*.json")}

    def plan(self, paths):
        folder = self.repo / "plans"
        folder.mkdir(exist_ok=True)
        text = "# Steps\nat once 1\n" + "".join(
            f"- {path}" + (f" after {Path(paths[n-1]).name[:3]}" if n else "") + "\n"
            for n, path in enumerate(paths))
        (folder / "steps.md").write_text(text)
        self.git("add", "plans", "orders")
        self.git("commit", "-qm", "plan")

    def test_linked_merged_round_clears_the_old_stall(self):
        self.round("001-first", ready=True)
        self.result("001-first", state="failed")
        self.age()
        self.assertEqual(self.stops(), {"001-first"})
        self.round("002-replacement", 2, "001-first")
        self.result("002-replacement", state="done", merged=self.git("rev-parse", "main"))
        self.age()
        self.assertEqual(self.stops(), set())
        self.assertFalse(ceo.order_active(self.repo, "001-first"))
        self.assertFalse(ceo.order_active(self.repo, "002-replacement"))

    def test_old_numbered_rounds_need_no_replaces_line(self):
        self.round("015-first", ready=True)
        self.round("015-round-2", 2)
        self.result("015-first", state="failed")
        self.result("015-round-2", state="done", merged=self.git("rev-parse", "main"))
        self.age()
        self.assertEqual(self.stops(), set())
        self.assertNotIn("started the run", self.tick("--worker", self.model("build")))

    def test_later_round_waits_for_its_plan_predecessor_with_no_clock(self):
        first = self.round("001-before")
        second = self.round("002-failed")
        self.result("002-failed", state="failed")
        self.plan([first, second])
        latest = self.round("002-round-2", 2, "002-failed")
        self.result("001-before", state="failed")
        self.assertNotIn("started the run", self.tick("--worker", self.model("build")))
        self.assertNotIn("002-round-2", ceo.ready_times(self.repo))  # held by its after: never ready
        self.result("001-before", state="done", merged=self.git("rev-parse", "main"))
        out = self.tick("--worker", self.model("build"))
        self.assertIn(f"started the run of {latest}", out)
        self.assertEqual(self.wait_for_run("002-round-2")["state"], "done")
        self.assertIn("002-round-2", ceo.ready_times(self.repo))
        self.assertEqual(self.read("002-failed")["state"], "failed")

    def test_standalone_later_round_starts_and_its_failed_round_never_again(self):
        self.round("001-first")
        self.result("001-first", state="failed")
        self.round("001-round-2", 2)
        out = self.tick("--worker", self.model("build"))
        self.assertEqual(out, "ceo: started the run of orders/001-round-2.md\n")
        self.assertEqual(self.wait_for_run("001-round-2")["state"], "done")
        self.assertEqual(self.read("001-first")["state"], "failed")

    def test_replacement_filed_after_a_cut_in_a_plan_is_its_next_start(self):
        original = self.round("001-first")
        self.result("001-first", state="failed")
        (self.repo / original).with_suffix(".cut").touch()
        self.plan([original])
        self.assertEqual(ceo.decide_plans(ceo.plan_snapshot(self.repo))["start"], [])
        self.assertNotIn("started the run", self.tick("--worker", self.model("build")))
        replacement = self.round("001-round-2", 2, "001-first")  # filed, it needs no other word
        self.assertEqual(ceo.decide_plans(ceo.plan_snapshot(self.repo))["start"], [replacement])
        out = self.tick("--worker", self.model("build"))
        self.assertIn(f"started the run of {replacement}", out)
        self.assertEqual(self.wait_for_run("001-round-2")["state"], "done")
        self.assertEqual(self.read("001-first")["state"], "failed")

    def test_latest_cut_and_hand_landed_families_never_start_or_stall(self):
        for n, reason in ((1, "cut"), (2, "landed")):
            first, latest = f"{n:03d}-first", f"{n:03d}-round-2"
            self.round(first)
            self.round(latest, 2, ready=True)
            self.result(first, state="failed")
            if reason == "cut":
                (self.repo / "orders" / (latest + ".cut")).touch()
            else:
                kept = self.repo / ".ceo" / "landed-by-hand" / first
                kept.parent.mkdir(parents=True)
                self.run_dir(first).rename(kept)
        self.age()
        self.assertEqual(self.stops(), set())
        self.assertNotIn("started the run", self.tick("--worker", self.model("build")))
        self.assertEqual(ceo.ready_times(self.repo), {})  # never run and eligible no more: their records went
        for slug in ("001-round-2", "002-round-2"):
            out = self.ceo("run", f"orders/{slug}.md", "--worker", self.model("build"))
            self.assertEqual(out.returncode, 2, out.stderr)
            self.assertFalse(self.run_dir(slug).exists())

    def test_round_filed_after_a_cut_starts_alone_on_the_next_pass(self):
        original = self.round("001-first")
        self.result("001-first", state="failed")
        (self.repo / original).with_suffix(".cut").touch()
        self.assertNotIn("started the run", self.tick("--worker", self.model("build")))
        self.assertFalse(ceo.order_active(self.repo, "001-first"))
        latest = self.round("001-round-2", 2, "001-first")
        self.assertTrue(ceo.order_active(self.repo, "001-round-2"))  # filed after the cut: no other word
        self.assertEqual(ceo.decide_plans(ceo.plan_snapshot(self.repo))["alone"], [latest])
        self.assertIn(f"started the run of {latest}", self.tick("--worker", self.model("build")))
        self.assertEqual(self.wait_for_run("001-round-2")["state"], "done")
        self.assertEqual(self.read("001-first")["state"], "failed")

    def test_plan_rows_keep_the_failed_rounds_state_after_its_replacement_merges(self):
        original = self.round("001-first")
        self.plan([original])
        self.result("001-first", state="failed")
        self.round("001-round-2", 2, "001-first")
        self.result("001-round-2", state="done", merged=self.git("rev-parse", "main"))
        decision = ceo.decide_plans(ceo.plan_snapshot(self.repo))
        self.assertEqual(decision["states"]["plans/steps.md", original], "failed")
        self.assertEqual(decision["start"], [])

    def test_latest_clock_begins_at_the_first_pass_that_finds_it_eligible_then_stalls(self):
        self.round("001-first", ready=True)
        self.result("001-first", state="failed")
        old = self.age()
        self.round("001-round-2", 2)
        row = ceo.stall_snapshot(self.repo, ceo.ready_times(self.repo))["001-round-2"]
        self.assertEqual((row["ready"], row["state"]), (None, "filed"))  # not its family's clock, nor its writing
        self.idle_tick()  # a pass finds it eligible; a login outage holds its start
        since = ceo.ready_times(self.repo)["001-round-2"]
        rows = ceo.stall_snapshot(self.repo, ceo.ready_times(self.repo))
        self.assertEqual(rows["001-round-2"]["last_progress"], since)
        self.assertGreater(since, old)
        self.assertFalse(ceo.stalled(rows["001-round-2"], since + 2700))
        self.assertTrue(ceo.stalled(rows["001-round-2"], since + 2701))
        self.assertFalse(ceo.stalled(rows["001-first"], since + 2701))
        self.age()
        self.assertEqual(self.stops(), {"001-round-2"})

    def test_earlier_merge_unblocks_a_plan_after_the_latest_round_was_cut(self):
        original = self.round("001-first")
        successor = self.round("002-next")
        self.plan([original, successor])
        latest = self.round("001-round-2", 2, "001-first")
        (self.repo / latest).with_suffix(".cut").touch()
        self.result("001-first", state="done", merged=self.git("rev-parse", "main"))
        decision = ceo.decide_plans(ceo.plan_snapshot(self.repo))
        self.assertEqual(decision["start"], [successor])
        self.assertEqual(decision["states"]["plans/steps.md", original], "merged")

    def test_picked_mock_releases_capacity_and_unblocks_its_successor(self):
        mock = self.round("001-mocks", ready=True)
        successor = self.round("002-build")
        self.plan([mock, successor])
        self.result("001-mocks", state="done", mocks=[{"spin": 1}], picked=1)
        decision = ceo.decide_plans(ceo.plan_snapshot(self.repo))
        self.assertEqual(decision["start"], [successor])
        out = self.tick("--worker", self.model("build"))
        self.assertIn(f"started the run of {successor}", out)
        self.assertNotIn("started the review", out)
        self.assertEqual(self.wait_for_run("002-build")["state"], "done")
        self.age()
        self.assertNotIn("001-mocks", self.stops())

    def test_finished_family_blocks_review_pr_and_door(self):
        self.round("001-first")
        self.round("001-round-2", 2)
        head = self.git("rev-parse", "main")
        self.result("001-first", state="done", head=head, pr=1)
        self.result("001-round-2", state="done", merged=head)
        ceo.write_json(self.run_dir("001-first") / "review.json", dict(verdict="survives", commit=head))
        self.assertEqual(ceo.door_runs(self.repo), [])
        self.assertEqual(self.ceo("review", "orders/001-first.md", "--reviewer", self.model("clean")).returncode, 2)
        with kernel_patch("origin", return_value=("unused", "owner/repo")), kernel_patch("gh") as gh:
            self.assertIsNone(ceo.ensure_pr(self.repo, "001-first", dict(order="orders/001-first.md", head=head)))
            ceo.merge(self.repo, "001-first", self.read("001-first"), None, 10)
            gh.assert_not_called()
        # A door interrupted after recording a merge still finishes clearing its worktree and branch.
        self.result("001-round-2", state="done", head=head, merged=head, pr=2)
        ceo.write_json(self.run_dir("001-round-2") / "review.json", dict(verdict="survives", commit=head))
        self.assertEqual([slug for slug, _ in ceo.door_runs(self.repo)], ["001-round-2"])

    def test_cut_run_recovered_as_done_gets_no_review_or_pull_request(self):
        slug = "047-round-3"
        self.round(slug, 3)
        proc = self.ceo_bg("run", f"orders/{slug}.md", "--worker", self.model("build"),
                           env=self.shim("rev-parse HEAD"))
        self.wait_for((self.tmp / "paused").exists, "run paused before result")
        (self.repo / "orders" / (slug + ".cut")).touch()
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=30)
        self.assertFalse((self.run_dir(slug) / "result.json").exists())
        out = self.tick("--worker", self.model("build"), "--reviewer", self.model("clean"))
        self.assertIn(f"recovered {slug}: done", out)
        self.assertNotIn("started the review", out)
        result = self.read(slug)
        self.assertTrue(result["recovered"])
        self.assertEqual(result["state"], "done")
        self.assertFalse((self.run_dir(slug) / "review.json").exists())
        with kernel_patch("origin", return_value=("unused", "owner/repo")), kernel_patch("gh") as gh:
            self.assertIsNone(ceo.ensure_pr(self.repo, slug, result))
            gh.assert_not_called()

    def test_three_stops_start_one_ceo_even_after_tick_restart(self):
        stub = self.tmp / "decision.py"
        stub.write_text('''import json, sys, time
from pathlib import Path
sys.stdin.read()
Path("ready").touch()
while not Path("release").exists():
    time.sleep(.02)
print(json.dumps(dict(decision="wait", reason="Waiting for the next pass")))
''')
        import shlex, sys
        with (self.repo / "ceo.toml").open("a") as conf:
            conf.write("ceo = " + json.dumps(shlex.join([sys.executable, str(stub)])) + "\n")
        for n in range(1, 4):
            slug = f"{n:03d}-stop"
            self.round(slug, ready=True)
            self.result(slug, state="failed")
        self.age()
        self.assertEqual(len(self.stops()), 3)
        children = {}
        def finish():
            for folder in (self.repo / ".ceo/decisions").glob("*"):
                (folder / "release").touch()
            for proc in children.values():
                proc.wait(timeout=30)
        self.addCleanup(finish)
        with patch.dict(os.environ, self.env):
            decisions.dispatch(self.repo, 20, children)
            self.assertEqual(len(children), 1)
            key = next(iter(children))[1]
            folder = self.repo / ".ceo/decisions" / key
            self.wait_for((folder / "ready").exists, "first decision at work")
            restarted = {}
            decisions.dispatch(self.repo, 20, restarted)
            self.assertEqual(restarted, {})
            finish()
            decisions.dispatch(self.repo, 20, children)
            self.assertEqual(len(children), 2)
            self.assertEqual(sum(proc.poll() is None for proc in children.values()), 1)
            self.assertEqual(len(list((self.repo / ".ceo/decisions").glob("*/stall.json"))), 2)


class FamilyDoor(DoorCase):
    def test_cut_round_three_leaves_reviewed_round_two_for_the_door(self):
        slug, latest = "047-round-2", "047-round-3"
        self.commit_order(f"{slug}.md", "# Round 2\nROUND: 2\n" + order_text("test -f built"))
        run = self.ceo("run", f"orders/{slug}.md", "--worker", self.model("build"))
        self.assertEqual(run.returncode, 0, run.stderr)
        reviewed = self.ceo("review", f"orders/{slug}.md", "--reviewer", self.model("clean"))
        self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
        self.assertEqual(self.read(slug, "review.json")["verdict"], "survives")
        self.commit_order(f"{latest}.md", f"# Round 3\nROUND: 3\nREPLACES: orders/{slug}.md\n" +
                          order_text("test -f built"))
        (self.repo / "orders" / f"{latest}.cut").touch()
        self.commit_all("cut round three")
        snapshot = ceo.order_snapshot(self.repo)
        family = ceo.order_families(snapshot)[f"orders/{latest}.md"]
        self.assertFalse(family["finished"])
        self.assertFalse(ceo.order_active(self.repo, latest))
        self.assertFalse(ceo.order_active(self.repo, slug))
        self.assertEqual([name for name, _ in ceo.door_runs(self.repo)], [slug])
        for change in ({"cut": True}, {"reviewed": False}):
            with self.subTest(change=change):
                altered = {p: dict(row) for p, row in snapshot.items()}
                altered[f"orders/{slug}.md"].update(change)
                self.assertTrue(ceo.order_families(altered)[f"orders/{latest}.md"]["finished"])
        landed = self.door()
        self.assertEqual(landed.returncode, 0, landed.stderr)
        self.assertEqual(self.server()[0]["state"], "MERGED")
        self.assertEqual(self.read(slug)["merged"], self.main())
        self.assertTrue(self.read(slug)["cleared"])
        self.assertFalse(self.run_dir(latest).exists())
        self.assertTrue(ceo.order_families(ceo.order_snapshot(self.repo))[f"orders/{latest}.md"]["finished"])


if __name__ == "__main__":
    unittest.main()
