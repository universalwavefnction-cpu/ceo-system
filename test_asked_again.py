import json, shlex, sys, time, unittest
from datetime import datetime, timezone
import ceo
import ceo_decisions as decisions
from test_ceo import order_text
from test_filed import stall_clock
from test_tick import TickCase
T, DAY = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc).timestamp(), 86400  # a question's time, after BELL_SINCE
BODY = order_text("test -f built").split("\n", 1)[1]
TEXT, ROUND = "# Stopped\n\nORDER: 001\nROUND: 1\n" + BODY, "# Stopped\n\nORDER: 001\nROUND: 2\nREPLACES: orders/001-stopped.md\n" + BODY
STALL = dict(slug="001-stopped", since="2026-11-02T09:00:00+00:00", last_progress=1, state="failed", last_log="")
NO_CEO = "- `ceo.toml` names no `ceo` command: no stalled order is decided until it names one"
def stub(case, body, *args):
    """A CEO command of the test's own, in ceo.toml."""
    (script := case.tmp / "ceo-stub.py").write_text(f"import json, sys\nsys.stdin.read()\n{body}\n")
    conf = case.repo / "ceo.toml"
    conf.write_text("".join(line for line in conf.read_text().splitlines(True) if not line.startswith("ceo ="))
                    + "ceo = " + json.dumps(shlex.join([sys.executable, str(script), *map(str, args)])) + "\n")
class Asked(TickCase):
    def setUp(self):
        super().setUp()
        self.commit_order("001-stopped.md", TEXT)
        self.row = self.repo / ".ceo" / "waiting" / "001-stopped.json"
        self.row.parent.mkdir(parents=True)
    def ask(self, at):
        self.row.write_text(json.dumps({"order": "orders/001-stopped.md", "title": "001-stopped", "question": "Which way?",
                                        "decision": "d" * 64, "choices": ceo.DECIDE_CHOICES,
                                        "time": datetime.fromtimestamp(at, timezone.utc).isoformat()}))
        return self.row.read_bytes()
    def rung(self, at=None):
        if at is not None:
            ceo.ring(self.repo, ceo.stall_snapshot(self.repo, ceo.ready_times(self.repo)), at)
        found = (json.loads(path.read_text()) for path in (self.repo / ".ceo" / "bells").glob("*.json"))
        return {one["key"]: one["line"] for one in found if "001-stopped" in one["line"]}
    def test_a_question_unanswered_for_a_day_rings_again_once_a_day_and_still_waits(self):
        row = self.ask(T)
        self.assertEqual(self.rung(T + DAY - 60), {}, "it rang again before a day passed")
        first = self.rung(T + DAY + 60)
        self.assertEqual(len(first), 1, first)
        self.assertIn("Which way?", next(iter(first.values())))
        for later in (T + DAY + 3600, T + 2 * DAY - 60):
            self.assertEqual(self.rung(later), first, "it rang twice in one day")
        self.assertEqual(len(self.rung(T + 2 * DAY + 60)), 2)
        self.assertEqual(len(self.rung(T + 3 * DAY + 60)), 3)
        self.assertEqual(self.row.read_bytes(), row, "the bell touched the owner's question")
        snapshot = ceo.stall_snapshot(self.repo, {"001-stopped": T})["001-stopped"]
        self.assertTrue(snapshot["waiting"])
        self.assertFalse(ceo.stalled(snapshot, T + 3 * DAY), "the order stopped waiting for the owner's word")
    def test_a_real_tick_rings_a_question_a_day_old_once_and_leaves_it_waiting(self):
        row = self.ask(time.time() - DAY - 600)
        for _ in range(2):
            self.idle_tick()
        self.assertEqual(len(self.rung()), 1, self.rung())
        self.assertEqual(self.row.read_bytes(), row)
        self.assertIn("python3 ceo.py answer 001-stopped", (self.repo / "STATUS.md").read_text())
    def test_a_fresh_question_and_a_row_without_a_time_ring_nothing(self):
        self.ask(T)
        self.assertEqual(self.rung(T + 3600), {})
        self.row.write_text(json.dumps({"order": "orders/001-stopped.md", "title": "001-stopped", "question": "Q?"}))
        self.assertEqual(self.rung(T + 5 * DAY), {})
class AlreadyFiled(TickCase):
    ANSWER = json.dumps(dict(decision="round", reason="Round 2 fixes the finding.", order=ROUND))
    def test_a_round_already_filed_is_recorded_and_writes_no_order(self):
        files = {"orders/001-stopped.md": TEXT, "orders/001-round-2.md": ROUND, "decisions.md": ""}
        entry, writes = decisions.transform(self.ANSWER, files, STALL, 3, "2026-11-08T09:00:00+00:00")
        self.assertEqual(entry["decision"], "round", entry)
        self.assertIn("orders/001-round-2.md is already filed", entry["reason"])
        self.assertNotIn(decisions.REFUSED, entry["reason"])
        self.assertEqual((set(writes), decisions.entries(writes["decisions.md"])), ({"decisions.md"}, [entry]))
    def test_a_real_decider_records_the_filed_round_and_asks_the_owner_nothing(self):
        (self.repo / "orders" / "001-stopped.md").write_text(TEXT)
        (self.repo / "orders" / "001-round-2.md").write_text(ROUND)  # filed meanwhile, by the kernel's fix round
        (answer := self.tmp / "answer.json").write_text(self.ANSWER)
        stub(self, "print(open(sys.argv[1]).read())", answer)
        self.git("add", "-A")
        self.git("commit", "-qm", "an order and its round 2")
        stall = {**STALL, "since": ceo.now(), "last_progress": time.time() - 600}
        (folder := self.repo / ".ceo" / "decisions" / decisions.identity(stall)).mkdir(parents=True)
        ceo.write_json(folder / "stall.json", stall)
        done = self.ceo("decide", decisions.identity(stall), "--timeout", "60")
        self.assertEqual(done.returncode, 0, done.stderr)
        applied = ceo.read_json(folder / "applied.json")
        self.assertEqual(applied.get("decision"), "round", applied)
        self.assertIn("orders/001-round-2.md is already filed", applied["reason"])
        self.assertEqual(list((self.repo / ".ceo" / "waiting").glob("*.json")), [], "the owner was asked")
        journal = decisions.entries(self.git("show", "main:decisions.md"))
        self.assertEqual([(one["id"], one["decision"]) for one in journal], [(applied["id"], "round")])
        self.assertEqual(self.git("show", "main:orders/001-round-2.md"), ROUND.strip(), "the filed round was rewritten")
class NoCeo(TickCase):
    def test_an_empty_ceo_command_is_said_once_and_no_stall_is_sent_until_it_names_one(self):
        self.commit_order("001-left.md")
        self.git("branch", "order/001-left")  # each pass refuses its start: a stall on a zero-minute clock
        with open(self.repo / "ceo.toml", "a") as conf:
            conf.write('ceo = ""\n')
        stall_clock(self, 0)
        for _ in range(3):
            self.tick("--worker", self.model("build"))
        self.assertEqual(list((self.repo / ".ceo" / "decisions").glob("*/stall.json")), [], "a CEO was sent")
        self.assertEqual(list((self.repo / ".ceo" / "waiting").glob("*.json")), [], "the owner was asked per stall")
        self.assertTrue((self.repo / ".ceo" / "stalls" / "001-left.json").exists(), "the order never stalled")
        self.assertEqual((self.repo / ".ceo" / "tick.log").read_text().count("names no ceo command"), 1)
        self.assertEqual((self.repo / "STATUS.md").read_text().split("## Needs attention\n", 1)[1].count(NO_CEO), 1)
        mark = self.tmp / "ceo-started"
        stub(self, "open(sys.argv[1], 'a').write('+')\n"
                   "print(json.dumps({'decision': 'ask', 'reason': 'a test', 'question': 'Go on?'}))", mark)
        self.tick("--worker", self.model("build"))
        self.assertNotIn(NO_CEO, (self.repo / "STATUS.md").read_text(), "the line outlived its cause")
        self.wait_for(mark.exists, "the CEO was sent once ceo.toml named one")
        for folder in (self.repo / ".ceo" / "decisions").glob("*"):
            self.wait_for((folder / "applied.json").exists, "the CEO finished")
    def test_an_empty_ceo_command_is_said_once_with_no_stall_pending(self):
        with open(self.repo / "ceo.toml", "a") as conf:
            conf.write('ceo = ""\n')
        self.git("add", "-A")
        self.git("commit", "-qm", "an idle project")
        for _ in range(2):
            self.tick()
        self.assertEqual((self.repo / ".ceo" / "tick.log").read_text().count("names no ceo command"), 1)
        self.assertEqual((self.repo / "STATUS.md").read_text().split("## Needs attention\n", 1)[1].count(NO_CEO), 1)
if __name__ == "__main__":
    unittest.main()
