"""Filing is where a file lives: an order in orders/ or a plan in plans/ runs, a draft never does, and orders no plan
lists start the oldest filed first. Real git, files and ticks, stub models, no network."""
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

import ceo
from ceo import FILED, decide_plans, order_snapshot, plan_snapshot
from test_ceo import kernel_patch, order_text
from test_tick import TickCase


def hold_ceo(case):
    """Hold every CEO a stall would send, as a decision at work does: the test measures the alarm alone."""
    (folder := case.repo / ".ceo" / "decisions" / "held").mkdir(parents=True, exist_ok=True)
    case.addCleanup(os.close, ceo.claim(folder / "lock"))


def stall_clock(case, minutes):
    """Set stall_minutes in ceo.toml, above any table: the clock the alarm of an eligible order not started rides."""
    conf = case.repo / "ceo.toml"
    conf.write_text(f"stall_minutes = {minutes}\n" + "".join(
        line for line in conf.read_text().splitlines(True) if not line.startswith("stall_minutes")))


class Filed(TickCase):
    def commit(self, message, day=None):
        env = {**self.env, **({"GIT_COMMITTER_DATE": f"2026-11-0{day}T08:00:00+00:00"} if day else {})}
        for args in (["add", "-A"], ["commit", "-qm", message]):
            subprocess.run(["git", *args], cwd=self.repo, env=env, check=True, capture_output=True)

    def write(self, path, text=None):
        (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / path).write_text(text or order_text("test -f built"))

    def test_only_a_numbered_lowercase_markdown_file_in_orders_is_an_order(self):
        names = {"orders/001-one.md": True, "orders/0042-four-digits.md": True, "orders/README.md": False,
                 "orders/notes.md": False, "orders/003-Upper.md": False, "orders/004_under.md": False,
                 "orders/005-five.txt": False, "orders/sub/006-deep.md": False, "drafts/orders/007-draft.md": False}
        for path, order in names.items():
            self.assertEqual(bool(FILED.fullmatch(path)), order, path)
            self.write(path)
        self.commit("orders, notes and a draft")
        filed = ["orders/001-one.md", "orders/0042-four-digits.md"]
        source = self.git("rev-parse", "HEAD")
        for snapshot in (order_snapshot(self.repo), order_snapshot(self.repo, source)):
            self.assertEqual(sorted(snapshot), filed)
        said = self.tick("--worker", self.model("build"))
        self.assertEqual(sorted(line for line in said.splitlines() if "started the run" in line),
                         [f"ceo: started the run of {path}" for path in filed])
        self.wait_for(lambda: all((self.run_dir(Path(path).stem) / "result.json").exists() for path in filed),
                      "both runs ended")
        self.assertEqual(sorted(path.name for path in (self.repo / ".ceo" / "runs").iterdir()),
                         ["001-one", "0042-four-digits"])

    def test_orders_no_plan_lists_start_the_oldest_filed_first_a_move_from_drafts_counting_as_filing(self):
        self.write("drafts/orders/001-first.md")
        self.write("orders/002-second.md")
        self.commit("002 filed, 001 a draft", day=1)
        self.write("orders/003-third.md")
        self.commit("003 filed", day=2)
        self.git("mv", "drafts/orders/001-first.md", "orders/")
        self.commit("001 filed last", day=3)
        self.assertEqual(decide_plans(plan_snapshot(self.repo))["alone"],
                         ["orders/002-second.md", "orders/003-third.md", "orders/001-first.md"])
        # Uncommitted in the folder, which a tick with no origin reads, an order counts from its file's own time.
        self.write("orders/004-fourth.md")
        later = os.stat(self.repo / "orders" / "002-second.md").st_mtime + 10 ** 6
        os.utime(self.repo / "orders" / "004-fourth.md", (later, later))
        self.assertEqual(decide_plans(plan_snapshot(self.repo))["alone"][-1], "orders/004-fourth.md")

    def test_the_plans_are_read_from_origins_main_once_fetched_and_from_the_folder_else(self):
        self.write("orders/001-one.md")
        self.write("orders/002-two.md")
        self.commit("two orders, no plan")
        self.git("checkout", "-qb", "elsewhere")
        self.write("plans/p.md", "# P\n- orders/001-one.md — one\n- orders/002-two.md after 001 — two\n")
        self.commit("the plan filed on origin's main by another checkout")
        source = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        self.assertFalse((self.repo / "plans").exists())
        upstream = decide_plans(plan_snapshot(self.repo, source))
        self.assertEqual((upstream["start"], upstream["alone"]), (["orders/001-one.md"], []))
        here = decide_plans(plan_snapshot(self.repo))
        self.assertEqual((here["start"], here["alone"]), ([], ["orders/001-one.md", "orders/002-two.md"]))
        # A plan only in drafts/plans/ is read by neither.
        self.write("drafts/plans/p.md", "# P\n- orders/001-one.md — one\n- orders/002-two.md after 001 — two\n")
        self.assertEqual(decide_plans(plan_snapshot(self.repo))["alone"], ["orders/001-one.md", "orders/002-two.md"])

    def test_an_order_with_a_waiting_row_does_not_start_and_its_plan_line_says_so(self):
        self.write("orders/001-one.md")
        self.write("plans/p.md", "# P\n- orders/001-one.md — one\n")
        self.write("orders/002-alone.md")
        self.commit("an order of a plan, and one alone")
        for slug in ("001-one", "002-alone"):
            (self.repo / ".ceo" / "waiting").mkdir(parents=True, exist_ok=True)
            (self.repo / ".ceo" / "waiting" / f"{slug}.json").write_text(json.dumps(
                {"order": f"orders/{slug}.md", "decision": "stop", "question": "Proceed?"}))
        decision = decide_plans(plan_snapshot(self.repo))
        self.assertEqual((decision["start"], decision["alone"]), ([], []))
        self.assertEqual(decision["states"]["plans/p.md", "orders/001-one.md"], "waiting for your word")
        self.assertNotIn("started the run", self.tick("--worker", self.model("build")))
        self.assertFalse((self.repo / ".ceo" / "ready").exists() and any((self.repo / ".ceo" / "ready").iterdir()))

    def test_an_invalid_order_of_a_plan_says_why_and_holds_no_slot(self):
        self.write("orders/001-broken.md", "# Broken\n\n## Goal\nNo test.\n")
        self.write("orders/002-fine.md")
        self.write("plans/p.md", "# P\n- orders/001-broken.md — broken\n- orders/002-fine.md — fine\n")
        self.commit("a plan with a broken order")
        decision = decide_plans(plan_snapshot(self.repo))
        self.assertEqual(decision["start"], ["orders/002-fine.md"])
        self.assertEqual(decision["states"]["plans/p.md", "orders/001-broken.md"],
                         "invalid: the order's '## Done when' section is missing or empty")

    def test_an_order_of_mocks_or_spins_due_in_a_plan_needs_attention_and_starts_nothing(self):
        self.write("orders/001-mocks.md", order_text("test -f built") + "\n## Mocks\n- red\n- blue\n")
        self.write("orders/002-spins.md", order_text("test -f built") + "\n## Spins\n- red\n- blue\n")
        self.write("plans/p.md", "# P\nat once 2\n- orders/001-mocks.md\n- orders/002-spins.md\n")
        self.commit("a plan of a mock order and a spin order")
        self.assertEqual(decide_plans(plan_snapshot(self.repo))["invalid"], ["orders/001-mocks.md", "orders/002-spins.md"])
        self.assertNotIn("started the run", self.tick("--worker", self.model("build")))
        attention = (self.repo / "STATUS.md").read_text().split("## Needs attention\n", 1)[1].split("\n## ", 1)[0]
        for slug in ("001-mocks", "002-spins"):
            self.assertIn(f"- `orders/{slug}.md` is filed and cannot start: {ceo.PICKS_LEFT}: mend it", attention.splitlines())

    def test_an_order_that_cannot_be_read_needs_attention_while_its_prerequisite_is_unfinished(self):
        self.write("orders/001-mocks.md", order_text("test -f built") + "\n## Mocks\n- red\n- blue\n")
        self.write("orders/002-spins.md", order_text("test -f built") + "\n## Spins\n- red\n- blue\n")
        self.write("orders/003-fine.md")
        self.write("plans/p.md", "# P\nat once 2\n- orders/001-mocks.md\n- orders/002-spins.md after 001\n"
                                 "- orders/003-fine.md after 001\n")
        self.commit("a plan whose spin and plain orders wait for a mock order")
        decision = decide_plans(plan_snapshot(self.repo))
        self.assertEqual((decision["start"], decision["invalid"]), ([], ["orders/001-mocks.md", "orders/002-spins.md"]))
        self.assertEqual(decision["states"]["plans/p.md", "orders/003-fine.md"], "waiting for 001")
        self.assertNotIn("started the run", self.tick("--worker", self.model("build")))
        attention = (self.repo / "STATUS.md").read_text().split("## Needs attention\n", 1)[1].split("\n## ", 1)[0]
        for slug in ("001-mocks", "002-spins"):
            self.assertIn(f"- `orders/{slug}.md` is filed and cannot start: {ceo.PICKS_LEFT}: mend it", attention.splitlines())
        self.assertNotIn("003-fine", attention)

    def test_a_draft_or_any_file_outside_orders_never_runs_even_by_hand(self):
        for path in ("drafts/orders/001-draft.md", "orders/sub/002-deep.md"):
            self.write(path)
        self.commit("a draft, and an order in a folder of orders/")
        for path in ("drafts/orders/001-draft.md", "orders/sub/002-deep.md"):
            ran = self.ceo("run", path, "--worker", self.model("build"))
            self.assertEqual(ran.returncode, 2, ran.stderr)
            self.assertIn(f"{path} is not filed: only an order in orders/ runs, never a draft", ran.stderr)
        self.assertFalse((self.repo / ".ceo").exists())
        self.assertEqual(self.git("branch", "--list", "order/*"), "")

    def test_a_round_over_round_cap_holds_no_slot_and_the_next_order_of_its_plan_starts_or_says_why(self):
        self.write("orders/001-over-cap.md", "ROUND: 4\n" + order_text("test -f built"))
        self.write("orders/002-eligible.md")
        self.write("plans/p.md", "# P\nat once 1\n- orders/001-over-cap.md — over\n- orders/002-eligible.md — next\n")
        self.commit("a capped round, then an order of its own")
        decision = decide_plans(plan_snapshot(self.repo))
        self.assertEqual((decision["start"], decision["capped"]), (["orders/002-eligible.md"], ["orders/001-over-cap.md"]))
        self.assertEqual(decision["states"]["plans/p.md", "orders/001-over-cap.md"], "over round_cap")
        stall_clock(self, 0)  # the alarm's clock, due at once
        for _ in range(2):
            self.idle_tick()  # a failed login holds every start: the eligible order says why, twice
        ready = ceo.read_json(self.repo / ".ceo" / "ready" / "002-eligible.json")
        self.assertEqual(sorted(ready), ["reason", "since"], ready)  # its clock and its why, and no count
        self.assertTrue(ready["reason"].startswith("the login of claude-auto failed"), ready)
        self.assertFalse((self.repo / ".ceo" / "ready" / "001-over-cap.json").exists())
        status = (self.repo / "STATUS.md").read_text()
        self.assertEqual(status.count("ROUND: 4 exceeds round_cap 3"), 1, status)
        self.assertIn("- `002-eligible` is eligible and not started: ", status.split("## Needs attention\n", 1)[1])

    def test_an_order_whose_branch_a_run_before_left_says_why_each_pass_and_starts_no_child(self):
        slug = "001-left"
        self.write(f"orders/{slug}.md")
        self.commit("an order, and a branch of a run before it")
        self.git("branch", f"order/{slug}")
        hold_ceo(self)
        stall_clock(self, 0)  # the alarm's clock, due at once
        why = f"this order has run before: its branch order/{slug} exists"
        for _ in range(2):
            self.assertIn(f"ceo: not started: orders/{slug}.md: {why}.", self.tick("--worker", self.model("build")))
            found = ceo.read_json(self.repo / ".ceo" / "ready" / f"{slug}.json")
            self.assertEqual({key: value for key, value in found.items() if key != "since"}, {"reason": why})
        self.assertFalse(self.run_dir(slug).exists())
        self.assertFalse((self.repo / ".ceo" / "locks" / f"{slug}.run").exists(), "a run child was started")
        self.assertIn(f"- `{slug}` is eligible and not started: {why}.", (self.repo / "STATUS.md").read_text())
        screen = (self.repo / "RETURN.md").read_text()
        self.assertTrue(screen.startswith(f"# {self.repo.name}: needs attention\n"), screen)
        self.assertIn(f"- `{slug}` is eligible and not started since ", screen)

    def test_a_run_child_refused_by_a_floor_read_again_leaves_its_reason_each_pass_until_one_starts(self):
        slug, record = "001-child-refused", self.repo / ".ceo" / "ready" / "001-child-refused.json"
        self.write(f"orders/{slug}.md")
        quota = self.tmp / "quota.sh"  # 50 % to the tick, 1 % to the run child it starts: the floor read again refuses
        quota.write_text('pid=$$\nwhile [ "$pid" -gt 1 ]; do\n  set -- $(ps -o args= -p "$pid")\n'
                         '  if [ "${2##*/}" = ceo.py ]; then [ "$3" = run ] && echo 1 || echo 50; exit; fi\n'
                         '  pid=$(ps -o ppid= -p "$pid" | tr -d " ")\ndone\necho 50\n')
        hold_ceo(self)
        stall_clock(self, 0)  # the alarm's clock, due at once
        conf = (self.repo / "ceo.toml").read_text()
        (self.repo / "ceo.toml").write_text(conf + f'quota_cmd.claude = "bash {quota}"\n')
        self.commit("an order, and a quota its run child reads under its floor")
        why, log, since = "the claude quota is at 1 %, under its floor of 5 %", self.repo / ".ceo" / "tick.log", None
        for passes in (1, 2, 3):
            self.assertIn(f"ceo: started the run of orders/{slug}.md", self.tick("--worker", self.model("build")))
            self.wait_for(lambda: log.read_text().count(f"ceo: refused: {why}") == passes, f"child {passes} refused")
            self.wait_for(lambda: not ceo.held(self.repo / ".ceo" / "locks" / f"{slug}.run"), "the child exited")
            self.assertEqual(ceo.read_json(record), {"since": since or ceo.read_json(record)["since"], "reason": why})
            since = ceo.read_json(record)["since"]  # the first refusal's, which no later one moves
        self.assertFalse((self.run_dir(slug) / "result.json").exists())
        line = f"- `{slug}` is eligible and not started: {why}."  # its stall clock passed: the alarm is due
        self.assertEqual((self.repo / "STATUS.md").read_text().split("## Needs attention\n", 1)[1].count(line), 1)
        self.assertTrue((self.repo / "RETURN.md").read_text().startswith(f"# {self.repo.name}: needs attention\n"))
        (self.repo / "ceo.toml").write_text(conf)  # the quota is back: the next child runs, and its reason clears
        self.commit("no quota command")
        self.tick("--worker", self.model("build"))
        self.wait_for((self.run_dir(slug) / "result.json").exists, "its run ended")
        self.idle_tick()
        self.assertEqual(ceo.read_json(record), {"since": since, "reason": ""})
        self.assertNotIn(line, (self.repo / "STATUS.md").read_text())
        self.write("drafts/orders/002-draft.md")  # a refused run of no filed order keeps no record
        self.commit("a draft")
        self.assertEqual(self.ceo("run", "drafts/orders/002-draft.md", "--worker", self.model("build")).returncode, 2)
        self.assertFalse((self.repo / ".ceo" / "ready" / "002-draft.json").exists())

    def test_a_run_child_refused_before_the_ticks_first_readiness_pass_keeps_its_reason(self):
        for slug in ("001-first", "002-second"):
            self.write(f"orders/{slug}.md")
        # 1 % to a run child; to the tick 50 %, its second read of each pass held until the child before it refused,
        # so the first child refuses before that pass has made .ceo/ready.
        quota = self.tmp / "quota.sh"
        quota.write_text('pid=$$\nwhile [ "$pid" -gt 1 ]; do\n  set -- $(ps -o args= -p "$pid")\n'
                         '  if [ "${2##*/}" = ceo.py ]; then\n    [ "$3" = run ] && { echo 1; exit; }\n'
                         '    n=0; [ -f .ceo/admissions ] && read -r n < .ceo/admissions; n=$((n + 1))\n'
                         '    echo "$n" > .ceo/admissions\n'
                         '    if [ $((n % 2)) -eq 0 ]; then\n'
                         '      until [ "$(grep -c "ceo: refused: the claude quota" .ceo/tick.log)" -ge $((n - 1)) ]; do\n'
                         '        sleep 0.05; done; fi\n    echo 50; exit; fi\n'
                         '  pid=$(ps -o ppid= -p "$pid" | tr -d " ")\ndone\necho 50\n')
        hold_ceo(self)
        stall_clock(self, 0)  # the alarm's clock, due at once
        with (self.repo / "ceo.toml").open("a") as conf:
            conf.write(f'quota_cmd.claude = "bash {quota}"\n')
        self.commit("two orders, and a quota their run children read under its floor")
        why, log, record = "the claude quota is at 1 %, under its floor of 5 %", self.repo / ".ceo" / "tick.log", None
        for passes in (1, 2):
            self.assertFalse((self.repo / ".ceo" / "ready").exists() and passes == 1)
            self.tick("--worker", self.model("build"))
            self.wait_for(lambda: log.read_text().count("ceo: refused: the claude quota") >= 2 * passes,
                          f"both children of pass {passes} refused")
            found = ceo.read_json(self.repo / ".ceo" / "ready" / "001-first.json")
            self.assertEqual(found, {"since": (record or found)["since"], "reason": why})  # the first refusal's since
            record = found
        self.assertFalse((self.run_dir("001-first") / "result.json").exists())
        self.assertIn(f"- `001-first` is eligible and not started: {why}.", (self.repo / "STATUS.md").read_text())

    def test_a_run_child_refused_after_a_pass_the_tick_refused_keeps_its_since_and_leaves_its_reason(self):
        slug, record = "001-mixed", self.repo / ".ceo" / "ready" / "001-mixed.json"
        self.write(f"orders/{slug}.md")
        # 50 % to the tick; 1 % to its run child, once released, so the child refuses after the pass that started it.
        quota, release = self.tmp / "quota.sh", self.repo / ".ceo" / "release"
        quota.write_text('pid=$$\nwhile [ "$pid" -gt 1 ]; do\n  set -- $(ps -o args= -p "$pid")\n'
                         '  case "${1##*/}" in python*|Python*)\n    if [ "${2##*/}" = ceo.py ] && [ "$3" = run ]; then\n'
                         '      until [ -f .ceo/release ]; do sleep 0.01; done; echo 1; exit\n    fi\n'
                         '    echo 50; exit;;\n  esac\n  pid=$(ps -o ppid= -p "$pid" | tr -d " ")\ndone\necho 50\n')
        hold_ceo(self)
        stall_clock(self, 0)  # the alarm's clock, due at once
        with (self.repo / "ceo.toml").open("a") as conf:
            conf.write(f'quota_cmd.claude = "bash {quota}"\n')
        self.commit("an order, and a quota its run child reads under its floor")
        self.idle_tick()  # a failed login holds its start: the tick's own reason
        self.addCleanup(release.touch)
        before = ceo.read_json(record)
        self.assertTrue(before["reason"].startswith("the login of claude-auto failed"), before)
        self.assertIn(f"ceo: started the run of orders/{slug}.md", self.tick("--worker", self.model("build")))
        self.assertEqual(ceo.read_json(record), before)  # a start whose run has not begun changes nothing
        release.touch()
        why = "the claude quota is at 1 %, under its floor of 5 %"
        self.wait_for(lambda: not ceo.held(self.repo / ".ceo" / "locks" / f"{slug}.run")
                      and ceo.read_json(record).get("reason") == why, "the child refused")
        self.assertEqual(ceo.read_json(record), {"since": before["since"], "reason": why})
        self.assertFalse(self.run_dir(slug).exists())
        self.idle_tick()
        self.assertEqual(ceo.read_json(record)["since"], before["since"])
        self.assertIn(f"- `{slug}` is eligible and not started: the login of claude-auto failed",
                      (self.repo / "STATUS.md").read_text())

    def test_a_refused_childs_reason_goes_once_the_child_started_after_it_has_recorded_its_run(self):
        slug, folder, since = "001-started", self.repo / ".ceo" / "ready", ceo.now()
        folder.mkdir(parents=True)
        ceo.write_json(folder / f"{slug}.json", {"since": since, "reason": "a floor"})
        ceo.readiness(self.repo, {slug: ""})  # its child may yet refuse: a start whose run has not begun changes nothing
        self.assertEqual(ceo.read_json(folder / f"{slug}.json"), {"since": since, "reason": "a floor"})
        (self.run_dir(slug)).mkdir(parents=True)
        (self.run_dir(slug) / "worker.log").write_text("")  # this pass's child began its run
        ceo.readiness(self.repo, {slug: ""})
        self.assertEqual(ceo.read_json(folder / f"{slug}.json"), {"since": since, "reason": ""})

    def test_a_run_childs_refusal_while_readiness_holds_its_record_lands_after_the_pass_and_stays(self):
        slug, record = "001-raced", self.repo / ".ceo" / "ready" / "001-raced.json"
        self.write(f"orders/{slug}.md")
        self.commit("an order")
        child = [sys.executable, "-B", "-c", f"import ceo; ceo.refused('orders/{slug}.md', 'a floor')"]
        env, read, refusing = {**self.env, "PYTHONPATH": str(Path(ceo.__file__).parent)}, ceo.read_json, []
        def refused_meanwhile(path, *none):  # the run child this pass started refuses once its record is read
            old = read(path, *none)
            if path == record and not refusing:
                refusing.append(subprocess.Popen(child, cwd=self.repo, env=env))
                with contextlib.suppress(subprocess.TimeoutExpired):
                    refusing[0].wait(timeout=2)  # it waits for the pass to write the record first
            return old
        since = None
        for _ in (1, 2):
            refusing.clear()
            with kernel_patch("read_json", refused_meanwhile):
                ceo.readiness(self.repo, {slug: ""})
            self.assertEqual(refusing[0].wait(timeout=30), 0)
            self.assertEqual(ceo.read_json(record), {"since": since or ceo.read_json(record)["since"], "reason": "a floor"})
            since = ceo.read_json(record)["since"]
        ceo.readiness(self.repo, {slug: ""})  # a start whose run has not begun leaves the child's reason
        self.assertEqual(ceo.read_json(record), {"since": since, "reason": "a floor"})

    def test_a_record_no_pass_found_eligible_waits_while_a_run_child_of_its_order_lives(self):
        slug, folder = "001-pending", self.repo / ".ceo" / "ready"
        folder.mkdir(parents=True)
        record = {"since": ceo.now(), "reason": "a floor"}
        for lock in (self.repo / ".ceo" / "plan-starts" / slug, self.repo / ".ceo" / "locks" / f"{slug}.run"):
            ceo.write_json(folder / f"{slug}.json", record)
            child = ceo.claim(lock)  # its plan's slot, or its run's lock: a child still to refuse or begin holds it
            ceo.readiness(self.repo, {})
            self.assertEqual(ceo.read_json(folder / f"{slug}.json"), record, lock.name)
            os.close(child)
            # Its child was at work as the pass read its orders, and refused and exited since: eligible again, it stays.
            ceo.readiness(self.repo, {}, {slug})
            self.assertEqual(ceo.read_json(folder / f"{slug}.json"), record, lock.name)
            ceo.readiness(self.repo, {})  # none lives, none ran, none was at work: eligible no more, it goes
            self.assertFalse((folder / f"{slug}.json").exists(), lock.name)

    def test_a_planned_run_child_waiting_on_a_floor_through_a_pass_keeps_the_since_of_the_pass_before_it(self):
        slug, record = "001-planned", self.repo / ".ceo" / "ready" / "001-planned.json"
        self.write(f"orders/{slug}.md")
        self.write("plans/p.md", f"# P\n- orders/{slug}.md — first\n")
        # 50 % to the tick; 1 % to its run child, once released, so the child waits through the next pass.
        quota, release = self.tmp / "quota.sh", self.repo / ".ceo" / "release"
        quota.write_text('pid=$$\nwhile [ "$pid" -gt 1 ]; do\n  set -- $(ps -o args= -p "$pid")\n'
                         '  case "${1##*/}" in python*|Python*)\n    if [ "${2##*/}" = ceo.py ] && [ "$3" = run ]; then\n'
                         '      until [ -f .ceo/release ]; do sleep 0.01; done; echo 1; exit\n    fi\n'
                         '    echo 50; exit;;\n  esac\n  pid=$(ps -o ppid= -p "$pid" | tr -d " ")\ndone\necho 50\n')
        with (self.repo / "ceo.toml").open("a") as conf:
            conf.write(f'quota_cmd.claude = "bash {quota}"\n')
        self.commit("a plan of one order, and a quota its run child reads under its floor")
        self.idle_tick()  # a failed login holds its start: the tick's own reason
        before = ceo.read_json(record)
        self.addCleanup(release.touch)
        self.assertIn(f"ceo: started the run of orders/{slug}.md", self.tick("--worker", self.model("build")))
        self.assertTrue(ceo.held(self.repo / ".ceo" / "plan-starts" / slug), "its child holds no plan slot")
        self.tick("--worker", self.model("build"))  # its child holds the slot: it is not eligible, and its record waits
        self.assertEqual(ceo.read_json(record), before)  # a start whose run has not begun changes nothing
        release.touch()
        why = "the claude quota is at 1 %, under its floor of 5 %"
        self.wait_for(lambda: not ceo.held(self.repo / ".ceo" / "plan-starts" / slug)
                      and ceo.read_json(record).get("reason") == why, "the child refused")
        self.assertEqual(ceo.read_json(record), {"since": before["since"], "reason": why})
        self.assertFalse(self.run_dir(slug).exists())

    def test_a_worker_begun_clears_the_reason_so_one_that_met_an_api_error_alarms_from_its_runs_progress(self):
        slug, record, limit = "001-retried", self.repo / ".ceo" / "ready" / "001-retried.json", self.tmp / "limit"
        self.write(f"orders/{slug}.md")
        self.commit("an order")
        for _ in range(2):
            self.idle_tick()
        since = ceo.read_json(record)["since"]
        self.assertTrue(ceo.read_json(record)["reason"].startswith("the login of claude-auto failed"))
        limit.touch()  # its worker meets the weekly limit, an api error: the run waits for a retry
        self.assertIn(f"ceo: started the run of orders/{slug}.md", self.tick("--worker", self.model("limit", str(limit))))
        self.wait_for(lambda: ceo.read_json(self.run_dir(slug) / "result.json").get("state") == "retry"
                      and not ceo.held(self.repo / ".ceo" / "locks" / f"{slug}.run"), "the run met an api error")
        self.assertEqual(ceo.read_json(record), {"since": since, "reason": ""})  # its worker began: the reason went
        # Eligible ten minutes before the run, under a one-minute clock: its alarm's clock is the run's last progress.
        since = ceo.datetime.fromtimestamp(ceo.time.time() - 600, ceo.timezone.utc).isoformat()
        ceo.write_json(record, {"since": since, "reason": ""})
        stall_clock(self, 1)
        self.idle_tick()  # eligible again, and held by a failed login: the first reason since its worker began
        self.assertEqual({key: value for key, value in ceo.read_json(record).items() if key != "reason"}, {"since": since})
        self.assertTrue(ceo.read_json(record)["reason"].startswith("the login of claude-auto failed"))
        self.assertNotIn(f"- `{slug}` is eligible and not started", (self.repo / "STATUS.md").read_text())

    def test_an_invalid_order_alone_has_no_clock_and_says_why_each_pass_until_it_is_mended(self):
        self.write("orders/001-invalid.md", "# Invalid\n\n## Goal\nNo done-when.\n")
        self.commit("an order that cannot be read")
        decision = decide_plans(plan_snapshot(self.repo))
        self.assertEqual((decision["alone"], decision["invalid"]), ([], ["orders/001-invalid.md"]))
        line = ("- `orders/001-invalid.md` is filed and cannot start: the order's '## Done when' section is missing or "
                "empty: mend it")
        for _ in range(2):
            self.idle_tick()
            self.assertFalse((self.repo / ".ceo" / "ready" / "001-invalid.json").exists())
            self.assertEqual((self.repo / "STATUS.md").read_text().split("## Needs attention\n", 1)[1].count(line), 1)
        self.write("orders/001-invalid.md")
        self.commit("mended")
        self.idle_tick()  # eligible now, and a failed login holds its start
        self.assertNotIn(line, (self.repo / "STATUS.md").read_text())
        self.assertTrue((self.repo / ".ceo" / "ready" / "001-invalid.json").exists())
