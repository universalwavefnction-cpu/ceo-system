import json, re, time, unittest
from datetime import datetime, timezone
from unittest import mock
import board
import ceo
import ceo_decisions as decisions
import ceo_status
from test_asked_again import TEXT, stub
from test_tick import TickCase
DAY = 86400
LONG = "".join(f"\nline {n} of a pasted log: FAILED test_{n}" for n in range(1, 50000))  # with its first: 50,000
BUG = dict(bug=3, question=ceo.CANNOT, detail="tried: carts of one item", choices=[{"pick": 1, "text": "drop"},
                                                                                {"pick": 2, "text": "add detail"}])
def week(at):
    return "asked %d-W%02d" % datetime.fromtimestamp(at, timezone.utc).isocalendar()[:2]
def asked(text):
    return dict(question=text, decision="d" * 64, choices=ceo.DECIDE_CHOICES)
class Old(TickCase):
    def setUp(self):
        super().setUp()
        (self.repo / ".ceo").mkdir()
        self.bells = self.repo / ".ceo" / "bells"
    def ask(self, slug, ago, row):
        """A waiting row by the kernel's own writer, on a clock stopped ago seconds back."""
        when = datetime.fromtimestamp(time.time() - ago, timezone.utc).isoformat(timespec="microseconds")
        with mock.patch.object(ceo_status, "now", lambda *_: when):
            ceo.wait_on(self.repo, slug, {"order": f"orders/{slug}.md", "title": slug, **row})
        return when, (self.repo / ".ceo" / "waiting" / f"{slug}.json").read_bytes()
    def rung(self, at=None):
        if at is not None:
            ceo.ring(self.repo, ceo.stall_snapshot(self.repo, ceo.ready_times(self.repo)), at)
        found = (json.loads(path.read_text()) for path in self.bells.glob("*.json"))
        return {one["key"]: one for one in found if one.get("kind") == "asked"}
    def test_five_questions_a_week_old_ring_one_bell_a_week_and_their_rows_stay(self):
        since, _ = self.ask("001-oldest", 8 * DAY, asked("Which way for the oldest?" + "\nthe log goes on" * 300))
        rows = {f"00{n}-old": self.ask(f"00{n}-old", 7 * DAY + n * 60, BUG if n == 2 else asked(f"Which way {n}?"))[1]
                for n in range(2, 6)}
        rows["006-fresh"] = self.ask("006-fresh", 600, asked("Which way now?"))[1]
        self.commit_order("002-old.md")
        for _ in range(3):
            self.idle_tick()
        snapshot = ceo.stall_snapshot(self.repo, {"002-old": time.time() - 8 * DAY})["002-old"]
        self.assertTrue(snapshot["waiting"])
        self.assertFalse(ceo.stalled(snapshot, time.time() + 7 * DAY), "the order stopped waiting for the owner's word")
        found = self.rung()
        self.assertEqual(list(found), [week(time.time())], "not one bell for the week's old questions")
        line = found[week(time.time())]["line"]
        self.assertRegex(line, r"(?<![\d-])5(?![\d-])", "the bell does not say how many questions wait")
        self.assertNotRegex(line, r"(?<![\d-])6(?![\d-])", "a fresh question was counted as old")
        for words in ("`001-oldest`", since[:16], "Which way for the oldest?"):
            self.assertIn(words, line)
        self.assertNotIn("\n", line)
        self.assertLess(len(line), 400, "the oldest question is not cut to one short line")
        self.assertEqual(self.rung(time.time() + 60), found, "it rang twice in one week")
        later = time.time() + 7 * DAY
        self.assertEqual(list(self.rung(later)), [week(later)], "the next week rang no bell of its own")
        self.assertEqual(list(self.rung(later + 3600)), [week(later)], "it rang twice in the next week")
        self.assertEqual([one["key"] for one in board.rung(self.repo) if one.get("kind") == "asked"], [week(later)],
                         "the board shows an asked bell older than its week")
        for slug, before in rows.items():
            self.assertEqual((self.repo / ".ceo" / "waiting" / f"{slug}.json").read_bytes(), before, slug)
        status, screen = ((self.repo / name).read_text() for name in ("STATUS.md", "RETURN.md"))
        for slug in ["001-oldest", *rows]:
            self.assertIn(f"python3 ceo.py answer {slug} ", status)
        self.assertIn("python3 ceo.py answer 006-fresh ", screen, "a new question is not on the return screen")
    def test_a_fresh_question_rings_nothing(self):
        self.ask("001-fresh", 3600, BUG)
        for _ in range(2):
            self.idle_tick()
        self.assertEqual(self.rung(), {})
        self.assertIn("python3 ceo.py answer 001-fresh ", (self.repo / "STATUS.md").read_text())
    def test_the_weeks_bell_leaves_once_no_question_is_old_and_rings_again_for_a_new_one(self):
        self.commit_order("001-oldest.md")
        self.ask("001-oldest", 3 * DAY, asked("Which way?"))
        self.idle_tick()
        self.assertEqual(len(self.rung()), 1, "the old question rang no bell")
        self.assertEqual(self.ceo("answer", "001-oldest", "cut").returncode, 0)
        for _ in range(2):
            self.idle_tick()
        self.assertFalse((self.repo / ".ceo" / "waiting" / "001-oldest.json").exists(), "the answer was not taken")
        self.assertEqual(list(self.bells.glob("asked-*.json")), [], "an asked bell outlived the last old question")
        self.assertEqual([one["key"] for one in board.rung(self.repo) if one.get("kind") == "asked"], [])
        self.ask("002-later", 2 * DAY, asked("And now?"))
        self.assertEqual(list(self.rung(time.time())), [week(time.time())], "a question old later rang no bell")
    def test_the_daily_bells_of_before_go_on_the_first_pass_and_no_other_kind_is_touched(self):
        self.bells.mkdir()
        legacy = {f"asked {slug} {days}": f"`{slug}` has waited for your word since 2026-11-01T09:00: Which way?"
                  for slug, days in (("001-oldest", 9), ("001-oldest", 8), ("002-gone", 3))}
        others = {"attention 009-failed 1793000000": "`009-failed` needs attention since 2026-10-26T09:00: failed",
                  "idle 0123456789ab": "no start for 12 hours, while IDEAS.md changed after the newest order"}
        for key, line in {**legacy, **others}.items():  # as a kernel before this one wrote them
            ceo.write_json(self.bells / f"{ceo.slugify(key)}.json", {"key": key, "kind": key.split()[0], "line": line,
                                                                     "time": ceo.now()})
        kept = {key: (self.bells / f"{ceo.slugify(key)}.json").read_bytes() for key in others}
        self.ask("001-oldest", 9 * DAY, asked("Which way?"))
        self.idle_tick()
        self.assertEqual(list(self.rung()), [week(time.time())], "a daily bell of before outlived the first pass")
        for key, before in kept.items():
            self.assertEqual((self.bells / f"{ceo.slugify(key)}.json").read_bytes(), before, f"{key} was touched")
    def test_the_oldest_is_the_earliest_instant_whatever_offset_each_time_was_written_in(self):
        (self.repo / ".ceo" / "waiting").mkdir()
        for slug, when in (("001-newer", "2026-11-01T23:00:00-12:00"), ("002-older", "2026-11-02T01:00:00+14:00")):
            ceo.write_json(self.repo / ".ceo" / "waiting" / f"{slug}.json", {"time": when, **asked("Which way?")})
        line = self.rung(datetime(2026, 11, 10, tzinfo=timezone.utc).timestamp())["asked 2026-W46"]["line"]
        self.assertIn("`002-older`", line, "a later instant written lexically earlier was named the oldest")
    def test_the_owners_lines_under_the_title_outlive_the_rows_asking_for_his_word(self):
        self.ask("023-rows", 600, asked("Which way?"))
        status = self.repo / "STATUS.md"
        rows = ["## Waiting for your word", "- `orders/023-rows.md` 023-rows: Which way? "
                "`python3 ceo.py answer 023-rows <choice> [--comment \"<text>\"]`", "  - answer", "  - cut"]
        def lines():  # STATUS.md but the health, free and sweep lines written under its title
            return [line for line in status.read_text().splitlines() if not line.startswith(("Health: ", "Free: disk ", "Sweep: "))]
        # What the owner has under the title, and what every pass leaves under its own line.
        layouts = {"nothing": ([], ["", *rows]),
                   "an empty line": (["", "## In progress"], ["", *rows, "", "## In progress"]),
                   "a list line": (["- Order 4: in progress"], ["", *rows, "", "- Order 4: in progress"])}
        for name, (owners, written) in layouts.items():
            with self.subTest(name):
                status.write_text("\n".join(["# Status", *owners]) + "\n")
                for _ in range(2):  # the rows written, then taken away and written anew
                    self.idle_tick()
                    found = lines()
                    self.assertEqual((found[0], found[1][:11], found[2:]), ("# Status", "Last tick: ",
                                     ["Orders: orders run from your folder; no origin", *written]))
        self.assertEqual(self.ceo("answer", "023-rows", "answer", "--comment", "this way").returncode, 0)
        self.idle_tick()
        self.assertEqual(lines()[2:], ["Orders: orders run from your folder; no origin", "", "- Order 4: in progress"])
class Short(TickCase):
    def test_a_cut_says_how_many_lines_are_wholly_left_out_and_stays_within_4000_with_a_long_path(self):
        short = ceo_status.clipped("x" * 3799 + "\n" + "\n".join(f"omitted-{n}" for n in range(49)))
        self.assertIn("[49 more lines left out]", short, "a cut ending on a newline counted one line too few")
        kept = [f"line-{n}" for n in range(39)] + [""]  # the fortieth kept line is blank
        short = ceo_status.clipped("\n".join(kept + [f"omitted-{n}" for n in range(20)]))
        self.assertEqual(short, "\n".join(kept) + " [20 more lines left out]", "a blank last kept line was counted")
        for where in (".ceo/waiting/001-" + "q" * 226 + ".json", ".ceo/waiting/001-q.json"):
            short = ceo_status.clipped("q" * 5000 + "".join(f"\nline {n}" for n in range(99999)), where)
            self.assertLessEqual(len(short), 4000, f"{len(short)} characters with a {len(where)}-character path")
            self.assertTrue(short.endswith(f"[99999 more lines left out; the whole text: `{where}`]"), short[-300:])
    def passes(self, slug, count=3):
        """count real passes; STATUS.md's line count after each, and the last STATUS.md and RETURN.md."""
        counts = []
        for _ in range(count):
            self.idle_tick()
            counts.append(len((self.repo / "STATUS.md").read_text().splitlines()))
        status, screen = (self.repo / "STATUS.md").read_bytes(), (self.repo / "RETURN.md").read_text().splitlines()
        self.assertLess(len(status), 200000, "STATUS.md does not open")
        self.assertLessEqual(len(set(counts[1:])), 1, f"STATUS.md grows each pass: {counts}")
        self.assertLess(len(screen), 40, "RETURN.md is longer than its screen")
        self.assertLess(len("\n".join(screen)), 20000, "RETURN.md carries the whole paste")
        text = status.decode()
        row = next(line for line in text.splitlines() if line.startswith(f"- `orders/{slug}.md` "))
        self.assertIn(f"`python3 ceo.py answer {slug} <choice> [--comment \"<text>\"]`", row)
        self.assertIn(f"{row}\n  - answer\n  - cut\n", text, "the choices did not stay under the answer line")
        self.assertTrue(any(f"python3 ceo.py answer {slug} " in line for line in screen), screen)
        return row
    def decided(self, whole):
        """A real decider whose CEO asks the owner whole; the decision's folder and key."""
        self.commit_order("001-stopped.md", TEXT)
        (answer := self.tmp / "answer.json").write_text(json.dumps(dict(decision="ask", reason="red", question=whole)))
        stub(self, "print(open(sys.argv[1]).read())", answer)
        self.git("add", "-A")
        self.git("commit", "-qm", "a CEO that asks")
        stall = dict(slug="001-stopped", since=ceo.now(), last_progress=time.time() - 600, state="failed", last_log="")
        (folder := self.repo / ".ceo" / "decisions" / (key := decisions.identity(stall))).mkdir(parents=True)
        ceo.write_json(folder / "stall.json", stall)
        done = self.ceo("decide", key, "--timeout", "120")
        self.assertEqual(done.returncode, 0, done.stderr)
        return folder, key
    def test_a_question_of_many_lines_answered_leaves_no_line_of_it_in_status(self):
        self.decided("Which way? The gate said:" + "".join(f"\n  pasted-{n} FAILED" * (n % 2) + f"\npasted-{n} FAILED"
                                                        for n in range(20)))  # 31 lines, some indented
        for _ in range(2):
            self.idle_tick()
        self.assertIn("pasted-19", (self.repo / "STATUS.md").read_text(), "the question was never shown")
        self.assertEqual(self.ceo("answer", "001-stopped", "cut").returncode, 0)
        for _ in range(2):
            self.idle_tick()
        self.assertFalse((self.repo / ".ceo" / "waiting" / "001-stopped.json").exists(), "the answer was not taken")
        left = [line for line in (self.repo / "STATUS.md").read_text().splitlines() if "pasted-" in line]
        self.assertEqual(left, [], "the answered question left lines of it in STATUS.md")
    def test_a_ceo_question_of_50000_lines_is_kept_and_shown_short_through_a_real_decider(self):
        folder, key = self.decided(whole := "Which way? The gate failed:" + LONG)
        kept = ceo.read_json(self.repo / ".ceo" / "waiting" / "001-stopped.json")["question"]
        self.assertLessEqual(len(kept.splitlines()), 40, "the row keeps the whole paste")
        self.assertLessEqual(len(kept), 4000, "the row keeps the whole paste")
        self.assertTrue(kept.startswith("Which way? The gate failed:\nline 1 of a pasted log"), kept[:200])
        self.assertRegex(kept, r"\b49,?960\b", "the cut does not say how many lines were left out")
        self.assertIn(f".ceo/decisions/{key}/applied.json", kept, "the cut does not say where the whole text is")
        self.assertEqual(ceo.read_json(folder / "applied.json")["question"], whole, "the whole text is not kept there")
        self.passes("001-stopped")
    def test_a_fat_question_already_waiting_shrinks_on_the_next_pass(self):
        (row := self.repo / ".ceo" / "waiting" / "001-fat.json").parent.mkdir(parents=True)
        row.write_text(json.dumps({"order": "orders/001-fat.md", "title": "001-fat", "decision": "e" * 64,  # as a
                                   "question": "How should this stop proceed?" + LONG, "choices": ceo.DECIDE_CHOICES,
                                   "time": ceo.now("microseconds")}))  # kernel before the cut wrote it, whole
        before = row.read_bytes()
        shown = self.passes("001-fat", 1)
        self.assertRegex(shown, r"\b49,?960\b")
        self.assertIn(".ceo/waiting/001-fat.json", shown, "the cut does not say where the whole text is")
        self.passes("001-fat")
        self.assertEqual(row.read_bytes(), before, "the owner's question was rewritten")
if __name__ == "__main__":
    unittest.main()
