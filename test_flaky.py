"""Completion checks and round admission: real git and a stub worker, without a remote or load."""
import json
import signal
import unittest
from unittest.mock import patch

import ceo
from test_ceo import kernel_patch, order_text
from test_tick import TickCase

SCOPE = ("Judge the work only against what the order states in its Goal and Done when; a fault outside "
         "them goes into the summary as an idea, never into findings.")


class Flaky(TickCase):
    def setUp(self):
        super().setUp()
        with (self.repo / "ceo.toml").open("a") as conf:
            conf.write("recheck_wait = 0\n")

    def check_runs(self, exits):
        cases = "\n".join(f"{n}) exit {code} ;;" for n, code in enumerate(exits, 1))
        script = """test -f built || { echo before-worker; exit 1; }
mkdir -p .ceo
n=$(cat .ceo/check-count 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" > .ceo/check-count
i=1
while test "$i" -le 45; do
    echo "attempt $n line $i" >&2
    i=$((i + 1))
done
case "$n" in
""" + cases + "\n*) exit 99 ;;\nesac\n"
        self.commit_order("044-check.md", order_text(script))
        run = self.ceo("run", "orders/044-check.md", "--worker", self.model("build"))
        result = self.read("044-check")
        self.assertEqual(run.returncode, 0 if exits[-1] == 0 else 1, run.stderr)
        self.assertEqual(result["state"], "done" if exits[-1] == 0 else "failed")
        self.assertEqual(result["done_when_exit"], exits[-1])
        attempts = result["done_when_runs"]
        self.assertEqual([one["exit_code"] for one in attempts], exits)
        for n, attempt in enumerate(attempts, 1):
            self.assertEqual(attempt["output"].splitlines(),
                             [f"attempt {n} line {i}" for i in range(6, 46)])
        tree = self.repo / result["worktree"]
        self.assertEqual((tree / ".ceo/check-count").read_text().strip(), str(len(exits)))
        self.assertEqual(self.git("log", "--format=%s", "main..order/044-check"), "build")
        self.assertEqual(self.git("remote"), "")

    def test_failure_then_pass_ends_done_with_two_runs(self):
        self.check_runs([7, 0])

    def test_two_failures_end_failed_with_two_runs(self):
        self.check_runs([7, 9])

    def test_first_pass_ends_done_with_one_run(self):
        self.check_runs([0])

    def test_recovery_records_one_or_two_checks_before_keeping_or_clearing_work(self):
        self.stub.write_text(self.stub.read_text() + '\nelif mode == "build-wait":\n'
                             '    build()\n    open(args[0], "w").write("ready")\n'
                             '    time.sleep(60)\n')
        for required, exits in ((1, [0]), (2, [1, 0]), (3, [1, 1])):
            with self.subTest(required=required):
                slug = f"044-recover-{required}"
                check = ("test -f built || exit 1\nmkdir -p .ceo\n"
                         "n=$(($(cat .ceo/count 2>/dev/null || echo 0) + 1))\n"
                         "echo $n > .ceo/count\necho attempt-$n\n"
                         f"test $n -ge {required}")
                self.commit_order(f"{slug}.md", order_text(check))
                ready = self.tmp / slug
                run = self.ceo_bg("run", f"orders/{slug}.md", "--worker",
                                  self.model("build-wait", str(ready)))
                self.wait_for(ready.exists, "the worker built and is waiting")
                run.send_signal(signal.SIGTERM)
                run.communicate(timeout=30)
                self.assertFalse((self.run_dir(slug) / "result.json").exists())
                self.idle_tick()
                result = (self.read(slug) if required < 3
                          else json.loads(self.records(slug)[0].read_text()))
                self.assertEqual(result["done_when_exit"], exits[-1])
                self.assertEqual(result["done_when_runs"], [
                    {"exit_code": code, "output": f"attempt-{n}"}
                    for n, code in enumerate(exits, 1)])
                if required < 3:
                    self.assertEqual((result["state"], result["recovered"]), ("done", True))
                else:
                    self.assertFalse(self.run_dir(slug).exists())

    def test_fourth_round_never_starts_and_is_said_once_over_three_passes(self):
        self.commit_order("044-cap.md", "ROUND: 4\n" + order_text("test -f built"))  # filed, so eligible each pass
        for _ in range(3):
            self.tick("--worker", self.model("build"), "--reviewer", self.model("clean"))
        self.assertFalse(self.run_dir("044-cap").exists())
        self.assertFalse((self.repo / ".ceo/wt/044-cap").exists())
        self.assertEqual(self.git("branch", "--list", "order/*"), "")
        reason = "ROUND: 4 exceeds round_cap 3"
        self.assertEqual((self.repo / ".ceo/tick.log").read_text().count(reason), 1)
        status = (self.repo / "STATUS.md").read_text()
        self.assertEqual(status.count(reason), 1)
        self.assertIn(reason, status.split("## Needs attention\n", 1)[1])

    def test_direct_run_obeys_configured_cap_before_creating_a_run(self):
        with (self.repo / "ceo.toml").open("a") as conf:
            conf.write("round_cap = 2\n")
        self.commit_order("044-direct.md", ("ROUND: 3\n" + order_text("test -f built")).replace(
            "\n", "\r\n"))
        run = self.ceo("run", "orders/044-direct.md", "--worker", self.model("build"))
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn("ROUND: 3 exceeds round_cap 2", run.stderr)
        self.assertFalse((self.repo / ".ceo").exists())
        self.assertEqual(self.git("branch", "--list", "order/*"), "")

    def test_plan_round_cap_is_said_once_without_a_launch(self):
        self.commit_order("044-plan-cap.md", "ROUND: 4\n" + order_text("test -f built"))
        (self.repo / "plans").mkdir()
        (self.repo / "plans/capped.md").write_text("# Capped\n- orders/044-plan-cap.md — Build.\n")
        self.git("add", "plans/capped.md")
        self.git("commit", "-qm", "plan")  # committed in plans/, the plan is live
        for _ in range(3):
            self.assertNotIn("started the run", self.tick("--worker", self.model("build")))
        reason = "ROUND: 4 exceeds round_cap 3"
        self.assertEqual((self.repo / ".ceo/tick.log").read_text().count(reason), 1)
        status = (self.repo / "STATUS.md").read_text()
        self.assertEqual(status.count(reason), 1)
        self.assertIn(reason, status.split("## Needs attention\n", 1)[1])
        self.assertFalse(self.run_dir("044-plan-cap").exists())
        self.assertFalse((self.repo / ".ceo/wt/044-plan-cap").exists())
        self.assertFalse((self.repo / ".ceo/plan-starts/044-plan-cap").exists())
        self.assertEqual(self.git("branch", "--list", "order/*"), "")

    def test_round_at_cap_and_a_round_example_in_the_goal_are_allowed(self):
        self.commit_order("044-allowed.md", "ROUND: 3\n" + order_text(
            "test -f built", goal="Build the file. Example:\n```text\nROUND: 4\n```"))
        run = self.ceo("run", "orders/044-allowed.md", "--worker", self.model("build"))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.read("044-allowed")["state"], "done")

    def test_reviewer_brief_limits_findings_to_the_order(self):
        self.assertIn(SCOPE, " ".join(ceo.BRIEF.split()))


class RecheckWait(unittest.TestCase):
    def wait(self, loads, times, seconds):
        with kernel_patch("settings", return_value={"recheck_wait": seconds}), \
                patch.object(ceo.os, "getloadavg", side_effect=[(load, 0, 0) for load in loads]), \
                patch.object(ceo.os, "cpu_count", return_value=4), \
                patch.object(ceo.time, "monotonic", side_effect=times), \
                patch.object(ceo.time, "sleep") as sleep:
            ceo.wait_recheck(None)
            return sleep.call_args_list

    def test_waits_until_load_is_strictly_below_cpu_count(self):
        self.assertEqual([call.args for call in self.wait([5, 4, 3], [0, 0, 1, 2], 300)],
                         [(1,), (1,)])

    def test_wait_is_bounded_even_if_load_stays_high(self):
        self.assertEqual([call.args for call in self.wait([5, 5], [0, 0, 1, 1.5], 1.5)],
                         [(1,), (0.5,)])

    def test_zero_wait_does_not_read_machine_load(self):
        self.assertEqual(self.wait([], [0, 0], 0), [])


if __name__ == "__main__":
    unittest.main()
