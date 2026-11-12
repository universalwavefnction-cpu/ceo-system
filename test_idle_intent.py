"""An open intent idle for 30 minutes: one attention line, one wake of the CEO, then one bell; nothing else wakes it."""
import json
import shlex
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone

import ceo
from test_ceo import order_text
from test_tick import TickCase

HALF, DAY = 30 * 60, 86400
LINE = "- an intent is open (`intents/stage-1.md`) and nothing has run since "
NEXT = order_text("test -f built", title="Stage 2 starts")


class IdleIntent(TickCase):
    def setUp(self):
        super().setUp()
        self.ago = time.time() - 40 * 60  # the intent and its spent plan were filed 40 minutes ago
        for path, text in (("intents/stage-1.md", "# Stage 1\n\nOutcome: the bell rings less.\n"),
                           ("plans/stage-1.md", "# Stage 1\n- orders/001-first.md — The first step.\n"),
                           ("orders/001-first.md", order_text("test -f built", title="The first step")),
                           ("orders/001-first.cut", "Spent.\n")):
            (self.repo / path).parent.mkdir(exist_ok=True)
            (self.repo / path).write_text(text)
        self.mark, self.brief = self.tmp / "ceo-started", self.tmp / "ceo-brief"
        self.ceo_says({"decision": "file", "reason": "The next stage waits for the plan view."})

    def commit(self, message, at=None):
        when = datetime.fromtimestamp(self.ago if at is None else at, timezone.utc).isoformat()
        for args in (["add", "-A"], ["commit", "-qm", message, "--allow-empty"]):
            subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True,
                           env={**self.env, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})

    def ceo_says(self, answer, command=None):
        """A CEO command of the test's own, first in ceo.toml: it marks each start, keeps its brief and answers."""
        (script := self.tmp / "ceo-stub.py").write_text(
            f"import sys\nbrief = sys.stdin.read()\nopen({str(self.mark)!r}, 'a').write('+')\n"
            f"open({str(self.brief)!r}, 'w').write(brief)\nprint({json.dumps(json.dumps(answer))})\n")
        conf = self.repo / "ceo.toml"
        named = shlex.join([sys.executable, str(script)]) if command is None else command
        conf.write_text(f"ceo = {json.dumps(named)}\n" + "".join(
            line for line in conf.read_text().splitlines(True) if not line.startswith("ceo =")))
        self.commit("the CEO command")

    def ticks(self, times=1, worker="build"):
        for _ in range(times):
            self.tick("--worker", self.model(*worker.split()), "--reviewer", self.model("clean"))
            for folder in (self.repo / ".ceo" / "decisions").glob("*"):
                self.wait_for((folder / "applied.json").exists, "the woken CEO answered")
        status = (self.repo / "STATUS.md").read_text()
        return [line for line in status.split("## Needs attention\n", 1)[-1].splitlines() if line.startswith(LINE)]

    def snapshot(self):
        return ceo.stall_snapshot(self.repo, ceo.ready_times(self.repo))

    def bells(self, at):
        ceo.ring(self.repo, self.snapshot(), at)
        found = (json.loads(path.read_text()) for path in (self.repo / ".ceo" / "bells").glob("*.json"))
        return [one for one in found if one["kind"] == "idle"]

    def test_an_open_intent_idle_for_30_minutes_wakes_the_ceo_once_then_rings_once(self):
        self.assertEqual(len(self.ticks()), 1, "no one line under Needs attention")
        self.assertEqual(len(self.ticks(2)), 1)
        self.assertEqual(self.mark.read_text(), "+", "the CEO was not woken exactly once")
        brief = self.brief.read_text()
        for said in ("intents/stage-1.md", "Outcome: the bell rings less.", "plans/stage-1.md",
                     "nothing has run since"):
            self.assertIn(said, brief)
        self.assertIn("The next stage waits for the plan view.", self.git("show", "main:decisions.md"))
        self.assertEqual(self.bells(time.time()), [], "it rang before 30 minutes passed after the wake")
        found = self.bells(time.time() + HALF + 60)
        self.assertEqual(len(found), 1, found)
        self.assertIn("`intents/stage-1.md`", found[0]["line"])
        self.assertIn("The next stage waits for the plan view.", found[0]["line"])
        self.assertEqual(self.bells(time.time() + 3 * HALF), found, "it rang twice in one idle spell")

    def test_an_order_the_woken_ceo_files_starts_and_no_bell_rings(self):
        self.ceo_says({"decision": "file", "reason": "Stage 2 starts.", "order": NEXT})
        self.assertEqual(len(self.ticks()), 1)
        self.assertEqual(self.git("show", "main:orders/002-stage-2-starts.md"), NEXT.strip())
        self.assertEqual(self.ticks(), [], "the line outlived its idle spell")  # the pass that starts the order
        self.wait_for((self.repo / ".ceo/runs/002-stage-2-starts/result.json").exists, "the filed order ran")
        self.assertEqual(self.bells(time.time() + 3 * HALF), [])
        self.assertEqual(self.mark.read_text(), "+")

    def test_a_closed_intent_wakes_nobody_and_rings_nothing_until_it_is_open_again(self):
        (self.repo / "intents" / "stage-1.closed").write_text("Its proof is met.\n")
        self.commit("the intent is closed")
        self.assertEqual(self.ticks(2), [])
        self.assertFalse(self.mark.exists(), "a CEO was woken under a closed intent")
        self.assertEqual(self.bells(time.time() + 3 * HALF), [])
        self.git("rm", "-q", "intents/stage-1.closed")
        self.commit("the intent is open again", time.time())
        self.assertEqual(len(self.ticks()), 1)
        self.assertEqual(self.mark.read_text(), "+")

    def test_a_question_under_a_day_old_keeps_it_quiet(self):
        (self.repo / ".ceo").mkdir()
        ceo.wait_on(self.repo, "001-first", {"order": "orders/001-first.md", "title": "001-first",
                                             "question": "Which way?"})
        self.assertEqual(self.ticks(2), [])
        self.assertFalse(self.mark.exists())
        self.assertEqual(self.bells(time.time() + 3 * HALF), [])
        self.assertIsNotNone(ceo.idle_spell(self.repo, self.snapshot(), time.time() + DAY + 60), "a day-old question")

    def test_an_order_eligible_or_running_keeps_it_quiet(self):
        (self.repo / "orders" / "002-stuck.md").write_text(order_text("false") + "\n## Worker\nsame\n")
        with open(self.repo / "ceo.toml", "a") as conf:
            conf.write('[workers]\nsame = "same-cmd"\n')  # it shares its reviewer's vendor: refused at every start
        self.commit("an order refused at every start")
        self.assertEqual(self.ticks(2), [])
        self.assertIsNone(ceo.idle_spell(self.repo, self.snapshot(), time.time() + 3 * HALF))
        (self.repo / "orders" / "002-stuck.cut").write_text("Refused.\n")
        (self.repo / "orders" / "003-slow.md").write_text(order_text("test -f built"))
        self.commit("the refused order cut, a slow one filed")
        self.ticks(worker=f"wait {self.tmp / 'began'} {self.release}")
        self.wait_for(lambda: any(self.tmp.glob("began-*")), "the slow worker began")
        self.assertIsNone(ceo.idle_spell(self.repo, self.snapshot(), time.time() + 3 * HALF), "a worker runs")
        self.assertEqual(self.bells(time.time() + 3 * HALF), [])
        self.release.touch()
        self.wait_for((self.repo / ".ceo" / "runs" / "003-slow" / "result.json").exists, "the slow worker finished")
        (self.repo / "orders" / "003-slow.cut").write_text("Done with it.\n")
        self.commit("the slow order cut")
        self.assertIsNotNone(ceo.idle_spell(self.repo, self.snapshot(), time.time() + 3 * HALF))
        self.assertFalse(self.mark.exists())

    def test_no_intent_wakes_nothing_and_the_old_twelve_hour_bell_is_gone(self):
        self.git("rm", "-rq", "intents")
        (self.repo / "IDEAS.md").write_text("# Ideas\n\n## 1 — 2026-11-10\n\nAn idea after every order.\n")
        self.commit("no intent, and an idea", time.time() - 60)
        self.assertEqual(self.ticks(2), [])
        self.assertFalse(self.mark.exists())
        self.assertEqual(self.bells(time.time() + 13 * 3600), [], "an idle bell rang with no intent open")

    def test_with_no_ceo_command_nothing_is_woken_and_the_bell_rings_30_minutes_after_the_spell_began(self):
        self.ceo_says({}, command="")
        self.assertEqual(len(self.ticks(2)), 1)
        self.assertEqual(list((self.repo / ".ceo" / "decisions").glob("*")), [], "a CEO was sent")
        self.assertEqual(self.bells(time.time()), [], "it rang before the spell was 30 minutes old")
        self.assertEqual(len(self.bells(time.time() + HALF)), 1)


if __name__ == "__main__":
    unittest.main()
