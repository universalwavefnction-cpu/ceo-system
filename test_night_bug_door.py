"""The night's findings go to the bug door and ask nothing: a proven finding becomes one bug, one an older
night left as a draft goes with its bug's commit, none is sent twice, a night's bug that cannot be reproduced
closes with what was tried and sends no CEO, even while main here lags origin's and when the tick that pushed its
close is killed before noting it, and an old night row leaves with its bells. Real nights, ticks, bug door and git;
only the models are stand-ins."""
import json, re, signal, sys, time, unittest
import board
import ceo
import test_bug_door, test_door, test_night
from test_filed import stall_clock
from test_night import CRASH, DECOY, FLAKY_FOUND, NightCase
BUGS = re.compile(r"bugs/\d+\.md")
DRAFT = "drafts/orders/004-night-parse-crashes-with-a-traceback-on-a-line.md"  # as an older night left it
DECLINED = ("# Night findings declined\n\n## 2026-11-01 09:00, drafts/orders/003-night-test-flaky.md\n\n"
            f"Claim: {json.dumps(FLAKY_FOUND['claim'])}\nCommand: {json.dumps(FLAKY_FOUND['command'])}\n"
            'Comment: "A set\'s order is no bug in a test."\n')
# origin's hook: once origin's main takes BUG's close, it kills the tick that pushed it, the tick's lock naming it
KILL = """import os, signal, subprocess, sys
from pathlib import Path
for line in sys.stdin:
    old, new, ref = line.split()
    if ref == "refs/heads/main" and "\\n## Closed\\n" in subprocess.run(["git", "show", f"{new}:{BUG}"],
                                                                     capture_output=True, text=True).stdout:
        Path(__file__).unlink()
        os.kill(int(Path(LOCK).read_text()), signal.SIGKILL)
"""
class NightBugDoor(NightCase):
    def setUp(self):
        super().setUp()  # the night's stand-in model, and the bug door's stand-in worker beside it
        self.stub.write_text(test_night.STUB + test_bug_door.STUB[len(test_door.STUB):])
        self.gate("")  # the project's own tests pass: each finding stands or falls by its command
    def new_bugs(self, before):
        return sorted(path for path in set(self.files()) - set(self.files(before)) if BUGS.fullmatch(path))
    def nothing_asked(self):
        self.assertEqual(list((self.repo / ".ceo" / "waiting").glob("*.json")), [], "the owner was asked")
        self.assertEqual([path for path in self.files() if path.startswith("drafts/")], [], "a night draft")
        told = self.repo / ".ceo" / "events.jsonl"
        lines = told.read_text().splitlines() if told.exists() else []
        self.assertNotIn("waiting", [json.loads(line)["event"] for line in lines], "the owner was told to answer")
    def test_a_proven_finding_goes_to_the_bug_door_and_one_it_could_not_prove_only_to_the_morning_note(self):
        self.answering(CRASH, DECOY)
        date, before = self.night_at(-12), self.main()
        record = self.night(date, self.tmp / "brief")
        self.nothing_asked()
        bugs = self.new_bugs(before)
        self.assertEqual(len(bugs), 1, bugs)
        report = self.shown(bugs[0])
        for line in (f"Claim: {json.dumps(CRASH['claim'])}", f"Command: {json.dumps(CRASH['command'])}"):
            self.assertIn(line, report)
        self.assertNotIn(DECOY["claim"], report)
        note = self.shown(f"routines/night-{date}.md")
        self.assertIn(f"{DECOY['claim']} `{DECOY['command']}`: exits 0", note)  # what it could not prove: listed
        self.assertNotIn("your word", note)
        night = next(line for line in (self.repo / "STATUS.md").read_text().splitlines() if line.startswith("Night: "))
        self.assertIn("1 sent to the bug door", night)
        self.assertNotIn("your word", night + record["said"])
        # The bug door's next step starts on the next tick, with no answer from anyone.
        took = self.tick("--worker", self.model("wait", str(self.tmp / "began"), str(self.release)))
        n = re.fullmatch(r"bugs/(\d+)\.md", bugs[0])[1]
        self.assertRegex(took, rf"ceo: started the run of orders/\d+-bug-{n}-reproduce\.md\n")
        self.assertFalse((self.repo / ".ceo" / "answers").exists())
    def test_a_draft_an_older_night_left_goes_to_the_bug_door_in_one_commit_and_no_finding_is_sent_twice(self):
        (self.repo / "drafts" / "orders").mkdir(parents=True)
        found = f"Claim: {json.dumps(CRASH['claim'])}\nCommand: {json.dumps(CRASH['command'])}\n"
        (self.repo / DRAFT).write_text(f"# Night finding: parse() crashes\n\n## Goal\n{found}")
        (self.repo / "routines").mkdir()
        (self.repo / "routines" / "night-declined.md").write_text(DECLINED)
        self.commit_all("an older night's draft, and a decline")
        self.answering(CRASH, FLAKY_FOUND)
        before = self.main()
        record = self.night(self.night_at(-12), self.tmp / "brief")
        self.assertEqual([one["claim"] for one in record["declined"]], [FLAKY_FOUND["claim"]])  # dropped as before
        bugs = self.new_bugs(before)
        self.assertEqual(len(bugs), 1, bugs)
        filed = self.origin_git("log", "--format=%H", "--diff-filter=A", "main", "--", bugs[0]).stdout.split()
        moved = self.origin_git("show", "--name-status", "--format=", filed[0]).stdout.splitlines()
        self.assertIn(f"D\t{DRAFT}", moved)
        self.assertIn(f"A\t{bugs[0]}", moved)
        self.nothing_asked()
        # The next night proves it again: its bug is open, so nothing is sent twice.
        date, before = self.night_at(14), self.main()
        self.night_now(date, 120)  # no tick: its bug's reproduce order is not started meanwhile
        self.assertEqual((self.new_bugs(before), [path for path in self.files() if BUGS.fullmatch(path)]),
                         ([], ["bugs/1.md", bugs[0]]))
        self.assertEqual(self.shown(bugs[0]).count("\n### "), 1, "the finding was reported again")
        self.assertIn(bugs[0], self.shown(f"routines/night-{date}.md"))
        self.nothing_asked()
    def test_a_night_bug_that_cannot_be_reproduced_closes_with_what_was_tried_and_asks_no_one(self):
        self.answering(CRASH)
        before = self.main()
        self.night(self.night_at(-12), self.tmp / "brief")
        bugs = self.new_bugs(before)
        self.assertEqual(len(bugs), 1, bugs)
        path = bugs[0]
        with open(self.repo / "ceo.toml", "a") as conf:  # a failed done-when runs again at once, whatever the load
            conf.write("recheck_wait = 0\n")
        self.tick_while(lambda: "## Closed" in self.shown(path), "the night's bug closed",
                        "--worker", self.model("bug", "cannot"), "--reviewer", self.model("clean"))
        self.assertRegex(self.shown(path), r"\n## Closed\n\n\S+ \S+: .*Tried carts of one item and of two: both right")
        stall_clock(self, 0)
        for _ in range(3):
            self.tick("--worker", self.model("bug", "cannot"), "--reviewer", self.model("clean"))
        self.nothing_asked()
        self.assertEqual(list((self.repo / ".ceo" / "decisions").glob("*/stall.json")), [], "a CEO was sent")
    def test_a_night_bug_closed_and_cut_on_origin_while_main_here_holds_unpushed_work_sends_no_ceo(self):
        self.closed_on_origin(killed=False)
    def test_a_tick_killed_after_pushing_a_night_bugs_close_and_before_noting_it_sends_no_ceo_after(self):
        self.closed_on_origin(killed=True)
    def closed_on_origin(self, killed):
        """A night's bug closed and its order cut on origin while main here holds unpushed work, so main here
        cannot follow; killed, the tick that pushed the close dies before noting it, and a later pass notes it."""
        self.answering(CRASH)
        before = self.main()
        self.night(self.night_at(-12), self.tmp / "brief")
        path, = self.new_bugs(before)
        (self.repo / "private.txt").write_text("work not pushed yet\n")  # main here cannot follow origin's now
        self.git("add", "private.txt")
        self.git("commit", "-qm", "private work")
        self.stub.write_text(self.stub.read_text() + '\nelif mode == "decide-ask":  # a CEO sent asks the owner\n'
                             '    print(json.dumps({"decision": "ask", "reason": "it stalled", "question": "Now?"}))\n')
        with open(self.repo / "ceo.toml", "a") as conf:
            conf.write("recheck_wait = 0\nceo = " + json.dumps(self.model("decide-ask")) + "\n")
        worker, runs = ("--worker", self.model("bug", "cannot"), "--reviewer", self.model("clean")), self.repo / ".ceo" / "runs"
        if killed:
            self.tick(*worker)
            self.wait_for(lambda: [ceo.read_json(one).get("state") for one in runs.glob("*/result.json")] == ["failed"],
                          "the reproduce run failed")
            hook = self.origin / "hooks" / "post-receive"
            hook.write_text(f"#!{sys.executable}\nBUG, LOCK = {path!r}, {str(self.repo / '.ceo' / 'tick.lock')!r}\n{KILL}")
            hook.chmod(0o755)
            ran = self.ceo("tick", *worker)
            self.assertEqual(ran.returncode, -signal.SIGKILL, (ran.stdout, ran.stderr))
        else:
            self.tick_while(lambda: "## Closed" in self.shown(path), "the night's bug closed", *worker)
        self.assertIn("\n## Closed\n", self.shown(path))
        n = re.fullmatch(r"bugs/(\d+)\.md", path)[1]
        cut, = [one for one in self.files() if re.fullmatch(rf"orders/\d+-bug-{n}-reproduce\.cut", one)]
        self.assertNotIn(cut, self.git("ls-tree", "-r", "--name-only", "main").split(), "main here followed")
        result, = runs.glob("*/result.json")
        self.assertEqual("closed" in ceo.read_json(result), not killed, "a killed tick noted its close, or a living one did not")
        stall_clock(self, 0)
        for _ in range(3):
            self.tick(*worker)
        sent = list((self.repo / ".ceo" / "decisions").glob("*/stall.json"))
        for one in sent:  # whatever was sent ends before the test does
            self.wait_for(lambda one=one: (one.parent / "applied.json").exists(), "the stand-in CEO finished")
        self.assertEqual(sent, [], "a CEO was sent")
        self.nothing_asked()
        self.assertRegex(ceo.read_json(result).get("closed", ""), r"^not reproduced by orders/\d+-bug-\d+-reproduce\.md: tried: ")
    def test_a_night_findings_row_and_its_bell_leave_on_the_next_tick_its_draft_stays_and_no_answer_is_taken(self):
        slug = re.sub(r"\.md$", "", DRAFT.rsplit("/", 1)[1])
        (self.repo / "drafts" / "orders").mkdir(parents=True)
        (self.repo / DRAFT).write_text("# Night finding: parse() crashes\n")
        self.commit_all("an older night's draft")
        (self.repo / ".ceo").mkdir(exist_ok=True)
        ceo.wait_on(self.repo, slug, {"order": DRAFT, "title": "Night finding: parse() crashes", "night": "2026-11-01",
                                      "question": "Agree, decline or comment?", "claim": CRASH["claim"],
                                      "command": CRASH["command"], "choices": [{"pick": "agree", "text": "agree"},
                                                                               {"pick": "decline", "text": "decline"}]})
        ceo.ring(self.repo, {}, time.time() + 2 * 86400)  # two days unanswered: the board rings it
        rang = lambda: [one for one in board.rung(self.repo) if slug in one["line"]]
        self.assertEqual(len(rang()), 1)
        self.idle_tick()
        self.assertEqual((list((self.repo / ".ceo" / "waiting").glob("*.json")), rang()), ([], []))
        self.assertEqual(list((self.repo / ".ceo" / "bells").glob(f"*{slug}*")), [])
        self.assertTrue((self.repo / DRAFT).is_file())
        self.assertIn(DRAFT, self.files())
        said = self.ceo("answer", slug, "agree")
        self.assertEqual(said.returncode, 2, said.stdout)
        self.assertIn(f"no row {slug} waits for your word", said.stderr)
if __name__ == "__main__":
    unittest.main()
