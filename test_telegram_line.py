"""The line ear as a process: the kernel's own board writer over real ticks, real git and the door, a real ear,
and a Bot API on a free loopback port, the one boundary stood in for."""
import json, os, signal, subprocess, sys, time, unittest
from pathlib import Path
import ceo
from test_ceo import order_text
from test_door import DoorCase
from test_filed import stall_clock
from test_telegram_board import OWNER, TOKEN, StandIn
EAR = Path(ceo.__file__).resolve().parent / "ears" / "telegram-line.py"
DAY, NEXT = "2026-11-10", "2026-11-11"
NOON = DAY + "T12:00:00"  # local time, as the ear's clock reads it: no pass of a test meets a digest by chance
ORDERS = {"001-cart": "Add the cart", "002-pay": "Take the payment", "003-later": "Later work"}
PLANS = {"checkout": "# Checkout flow\n- orders/001-cart.md — The cart.\n- orders/002-pay.md — The payment.\n",
         "later": "# Later\n- orders/003-later.md — Cut at once.\n"}  # filed with it, so the newer: the wave keeps it
STANDING = "Checkout flow · 0 of 2 in · 0 building"
class Line(DoorCase):
    def setUp(self):
        super().setUp()
        self.home, self.shop = Path(self.env["CEO_HOME"]), Path(self.env["CEO_HOME"]) / "board" / "shop.json"
        self.shop.parent.mkdir(parents=True, exist_ok=True)
        (self.repo / ".ceo").mkdir(exist_ok=True)
        self.api = StandIn(self)  # the Bot API on a free loopback port: the one boundary a test stands in for
        self.ear_env = {key: value for key, value in self.env.items()
                        if not key.startswith("TELEGRAM_") and not key.lower().endswith("_proxy")}
        self.ear_env.update(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_OWNER_ID=str(OWNER), TELEGRAM_API=self.api.url,
                            NO_PROXY="*")
        for slug, title in ORDERS.items():
            (self.repo / "orders" / f"{slug}.md").write_text(order_text(f"test -f {slug}.txt", title=title))
        (self.repo / "orders" / "003-later.cut").write_text("cut\n")
        (self.repo / "plans").mkdir()
        for name, text in PLANS.items():
            (self.repo / "plans" / f"{name}.md").write_text(text)
        self.commit_all("three orders, one cut, and two plans")
    def ask(self, slug, question, title=None):
        ceo.wait_on(self.repo, slug, {"order": f"orders/{slug}.md", "title": title or slug, "question": question,
                                      "choices": [{"pick": "agree", "text": "agree"}]})
    def ear(self, *args, env=None):
        return subprocess.run([sys.executable, "-B", str(EAR), *args], cwd=self.tmp, env=env or self.ear_env,
                              capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    def board(self):
        done = self.ceo("board", "--name", "shop", "--out", str(self.shop))
        self.assertEqual(done.returncode, 0, done.stderr)
    def told(self, at=NOON):
        """The board as the kernel writes it, one real pass of the ear at the local time at, and what it sent."""
        before = len(self.api.snapshot()[1])
        self.board()
        done = self.ear("--once", "--now", at)
        self.assertEqual(done.returncode, 0, done.stderr)
        sent = self.api.snapshot()[1][before:]
        self.assertTrue(all(one["chat_id"] == OWNER for one in sent), sent)
        return [one["text"] for one in sent]
    def test_the_last_order_of_a_plan_merging_sends_one_line_naming_the_project_the_plan_and_its_count(self):
        self.gate()
        self.assertEqual(self.told(), [], "the first sight of a project was told")
        work = ("--worker", self.model("own"), "--reviewer", self.model("clean"))
        for slug, lines in (("001-cart", []),
                            ("002-pay", ["shop · Checkout flow closed · 2 of 2 in · nothing needs you"])):
            self.tick_while(lambda: "cleared" in ceo.read_json(self.run_dir(slug) / "result.json"),
                            f"{slug} merged", *work)
            self.assertEqual(self.told(), lines, "a merge alone was told" if not lines else "")
        wave = json.loads(self.shop.read_text())["wave"]["plans"]
        self.assertEqual([plan["plan"] for plan in wave], ["plans/later.md"], "the closed plan is still the wave")
        self.assertEqual(self.told(), [], "the closed plan was told twice")
    def test_a_new_bell_sends_one_line_with_its_words_and_the_orders_title(self):
        conf = self.repo / "ceo.toml"  # each start refused below the disk floor: an unstarted bell, and no CEO
        conf.write_text(conf.read_text().replace("disk_floor_gb = 0", "disk_floor_gb = 1000000"))
        stall_clock(self, 0)
        self.assertEqual(self.told(), [])
        self.tick_while(lambda: any((self.repo / ".ceo" / "bells").glob("unstarted-*.json")), "a bell rang",
                        "--worker", self.model("build"))
        self.ask("002-pay", "Card or cash?")  # found on the bell's pass: what needs the owner goes first
        question, line = self.told()
        self.assertEqual(question, f"shop · “Take the payment” asks you: Card or cash? · {STANDING} · 1 question"
                                   " waits for you")
        self.assertTrue(line.startswith("shop · “Add the cart” is eligible and not started: below the disk floor: "),
                        line)
        self.assertTrue(line.endswith(f" · {STANDING} · 1 question waits for you"), line)
        self.assertLessEqual(len(line), 300)
        self.assertEqual(self.told(), [], "the bell was told twice")
        self.assertEqual(self.told(DAY + "T21:00:00"), [f"shop · {STANDING} · 1 question waits for you · 1 bell in"
                                                        " the last day"], "the evening digest")
    def test_a_new_question_sends_one_line_and_one_waiting_at_first_sight_none(self):
        self.ask("002-pay", "Card or cash?")
        self.assertEqual(self.told(), [], "the first sight of a project was told")
        self.ask("001-cart", "Agree or decline?")
        self.assertEqual(self.told(), [f"shop · “Add the cart” asks you: Agree or decline? · {STANDING}"
                                       " · 2 questions wait for you"])
        self.assertEqual(self.told(), [], "the question was told twice")
    def test_a_day_sends_at_most_six_lines_and_the_next_digest_counts_the_events_it_had_no_room_for(self):
        self.assertEqual(self.told(DAY + "T08:00:00"), [])
        for n in range(4, 9):  # five questions at once: four lines, and the fifth has no room
            self.ask(f"00{n}-ask", f"Question {n}?", f"Ask {n}")
        lines = self.told(DAY + "T08:30:00")
        self.assertEqual(len(lines), 4, lines)
        self.assertEqual(lines[0], f"shop · “Ask 4” asks you: Question 4? · {STANDING} · 5 questions wait for you")
        self.assertEqual(self.told(DAY + "T09:00:00"), [f"shop · {STANDING} · 5 questions wait for you\n"
                                                        "and 1 more event"])
        self.assertEqual(self.told(DAY + "T09:30:00"), [], "the morning digest went twice")
        self.ask("009-ask", "Question 9?", "Ask 9")
        self.assertEqual(self.told(DAY + "T10:00:00"), [], "a seventh line went out in one day")
        self.assertEqual(self.told(DAY + "T21:00:00"), [f"shop · {STANDING} · 6 questions wait for you\n"
                                                        "and 1 more event"])
        self.assertEqual(self.told(DAY + "T23:59:00"), [])
        self.ask("010-ask", "Question 10?", "Ask 10")
        self.assertEqual(len(self.told(NEXT + "T08:00:00")), 1, "a new day had no room")
        self.assertEqual(len(self.told(NEXT + "T21:30:00")), 1, "the day's digest was not sent once")
    def test_a_missing_token_or_owner_says_which_sends_nothing_and_exits_non_zero(self):
        self.ask("001-cart", "Agree or decline?")
        self.board()
        for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID"):
            done = self.ear("--once", "--now", NOON, env={k: v for k, v in self.ear_env.items() if k != name})
            self.assertNotEqual(done.returncode, 0, name)
            self.assertIn(name, done.stderr)
        self.assertEqual(self.api.snapshot()[0], [], "a call reached the Bot API")
        self.assertFalse((self.home / "telegram-line.json").exists())
    def test_every_seconds_the_ear_passes_again_until_it_is_stopped(self):
        self.board()
        ear = subprocess.Popen([sys.executable, "-B", str(EAR), "--every", "0.2", "--now", NOON], cwd=self.tmp,
                               env=self.ear_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(lambda: ear.poll() is None and (os.killpg(ear.pid, signal.SIGKILL), ear.wait()))
        self.wait_for((self.home / "telegram-line.json").exists, "the first pass")
        self.ask("001-cart", "Agree or decline?")
        self.board()
        self.wait_for(lambda: len(self.api.snapshot()[1]) == 1, "a later pass told the question")
        time.sleep(0.6)
        self.assertEqual(len(self.api.snapshot()[1]), 1, "a pass told it again")
        self.assertIsNone(ear.poll(), "the ear stopped by itself")
import tempfile
from datetime import date, timedelta
from test_telegram_board import row
def plan(path, title, orders, landed=0, cut=0, building=0):
    return dict(plan=f"plans/{path}.md", title=title, orders=orders, landed=landed, cut=cut, building=building)
class Plain(unittest.TestCase):
    """Boards written by hand, so that every outcome of one pass is exact."""
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="telegram-line-test-")
        self.addCleanup(scratch.cleanup)
        self.home, self.api = Path(scratch.name), StandIn(self)
        (self.home / "board").mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("TELEGRAM_") and not key.lower().endswith("_proxy")}
        self.env.update(CEO_HOME=str(self.home), TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_OWNER_ID=str(OWNER),
                        TELEGRAM_API=self.api.url, NO_PROXY="*")
    def write(self, name, rows=(), every=(), bells=(), orders=()):
        board = dict(ceo=name, status=dict(rows=list(rows)), wave=dict(plans=[], orders=list(orders), every=list(every)),
                     bells=list(bells))
        (self.home / "board" / f"{name}.json").write_text(json.dumps(board))
    def ear(self, at=NOON, *more):
        return subprocess.run([sys.executable, "-B", str(EAR), "--once", "--now", at, *more], env=self.env,
                              capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    def told(self, at=NOON):
        before = len(self.api.snapshot()[1])
        done = self.ear(at)
        self.assertEqual(done.returncode, 0, done.stderr)
        return [one["text"] for one in self.api.snapshot()[1][before:]]
    def test_within_one_pass_questions_go_before_closed_plans_and_those_before_bells_whichever_the_project(self):
        self.write("alpha", every=[plan("a", "Alpha work", 3, landed=1)])
        self.write("beta", every=[plan("b", "Beta work", 2, landed=1)])
        self.assertEqual(self.told(), [])
        bell = dict(key="idle-x", kind="idle", line="`007-x` has not moved for 3 hours, `009-y` is unknown",
                    time="2026-11-10T11:00:00+00:00")
        self.write("alpha", every=[plan("a", "Alpha work", 3, landed=2, cut=1)], bells=[bell],
                   orders=[dict(latest="007-x", title="Seven")])
        self.write("beta", rows=[row("004-pick", sentence="Pick one.", question="Which?")],
                   every=[plan("b", "Beta work", 2, landed=1)])
        self.assertEqual(self.told(), [
            "beta · “Pick one” asks you: Which? · Beta work · 1 of 2 in · 0 building · 1 question waits for you",
            "alpha · Alpha work closed · 2 of 3 in · 1 cut · nothing needs you",
            "alpha · “Seven” has not moved for 3 hours, 009-y is unknown · no open plan · nothing needs you"])
    def test_a_title_is_cut_at_a_word_and_an_event_line_is_one_line_of_at_most_300_characters(self):
        words = " ".join(["longer"] * 30)
        self.write("alpha", every=[plan("a", words, 2)])
        self.assertEqual(self.told(), [])
        self.write("alpha", rows=[row("005-long", sentence=words + ".", question=f"{words}\n{words}")],
                   every=[plan("a", words, 2)], orders=[dict(latest="005-long", title="Title " + words)])
        [line] = self.told()
        self.assertTrue(280 < len(line) <= 300 and "\n" not in line, line)
        self.assertTrue(line.startswith("alpha · “Title " + "longer " * 6 + "longer…” asks you: longer longer"), line)
        self.assertIn("…", line.split(" asks you: ")[1])
    def test_a_slug_a_waiting_row_leaves_and_comes_back_to_is_new_and_a_missing_project_keeps_its_memory(self):
        self.write("alpha", rows=[row("001-a")])
        self.assertEqual(self.told(), [])
        self.write("alpha", rows=[row("001-a", state="in progress")])
        self.assertEqual(self.told(), [])
        self.write("alpha", rows=[row("001-a", answered="agree")])
        self.assertEqual(self.told(), [], "an answered row is no question")
        (self.home / "board" / "alpha.json").rename(self.home / "away.json")
        self.assertEqual(self.told(), [])
        self.write("alpha", rows=[row("001-a")])
        self.assertEqual(len(self.told()), 1)
    def test_a_failed_send_stops_the_pass_hides_the_token_and_the_events_not_recorded_are_tried_again(self):
        self.write("alpha")
        self.assertEqual(self.told(), [])
        self.write("alpha", rows=[row("001-a", sentence="First.", question="First?"),
                              row("002-b", sentence="Second.", question="Second?")])
        self.api.fail(text="Second?")
        done = self.ear()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(len(done.stderr.splitlines()), 1, done.stderr)
        self.assertTrue(done.stderr.startswith("telegram-line: "), done.stderr)
        self.assertIn("<token>", done.stderr)
        self.assertNotIn(TOKEN, done.stderr)
        self.assertEqual([one["text"][:15] for one in self.api.snapshot()[1]], ["alpha · “First”"])
        self.api.fail()
        self.assertEqual([one[:16] for one in self.told()], ["alpha · “Second”"])
        self.assertEqual(self.told(), [])
    def test_a_digest_of_nothing_says_so_and_a_digest_time_of_an_earlier_day_is_never_sent_late(self):
        self.assertEqual(self.told(DAY + "T08:00:00"), [])
        self.assertEqual(self.told(DAY + "T21:30:00"), ["Nothing is open and nothing waits for you"])
        self.assertEqual(self.told(NEXT + "T08:00:00"), [])
        self.assertEqual(self.told(NEXT + "T21:00:00"), ["Nothing is open and nothing waits for you"])
        self.assertEqual(self.told(NEXT + "T21:05:00"), [])
    def test_a_digest_names_each_project_with_something_open_and_its_bells(self):
        bell = dict(key="k1", line="`001-a` is stuck", time="2026-11-10T11:00:00+00:00")
        self.write("alpha", every=[plan("a", "Alpha work", 4, landed=1, building=2)], bells=[bell, dict(bell, key="k2")])
        self.write("beta", every=[plan("b", "Beta work", 1, landed=1)])
        self.write("gamma", rows=[row("003-c", question="Yes?")])
        self.assertEqual(self.told(DAY + "T08:00:00"), [])
        self.assertEqual(self.told(DAY + "T09:00:00"), [
            "alpha · Alpha work · 1 of 4 in · 2 building · nothing needs you · 2 bells in the last day\n"
            "gamma · no open plan · 1 question waits for you"])
    def test_a_blank_token_or_an_owner_that_is_not_digits_sends_nothing_and_exits_2(self):
        self.write("alpha", rows=[row("001-a")])
        env = dict(self.env)
        for name, value in (("TELEGRAM_OWNER_ID", "12ab"), ("TELEGRAM_BOT_TOKEN", "  "), ("TELEGRAM_OWNER_ID", "")):
            self.env = dict(env, **{name: value})
            done = self.ear()
            self.assertEqual(done.returncode, 2, (name, done.stderr))
            self.assertIn(name, done.stderr)
        self.assertEqual(self.api.snapshot()[0], [])
        self.assertFalse((self.home / "telegram-line.json").exists())
    def test_a_bell_key_seen_once_is_never_told_again_though_a_board_pass_left_it_out(self):
        self.write("alpha")
        self.assertEqual(self.told(), [])
        bell = dict(key="same-key", kind="attention", line="Work stopped", time="2026-11-10T11:00:00+00:00")
        self.write("alpha", bells=[bell])
        self.assertEqual(len(self.told()), 1)
        self.write("alpha")
        self.assertEqual(self.told(), [])
        self.write("alpha", bells=[bell])
        self.assertEqual(self.told(), [], "A previously seen bell key must not be sent again after an absent board pass")
    def test_a_clock_stepping_back_across_midnight_finds_the_earlier_dates_allowance_spent(self):
        self.write("alpha")
        self.assertEqual(self.told(DAY + "T08:00:00"), [])
        self.write("alpha", rows=[row(f"{n:03d}-q") for n in range(1, 5)])
        self.assertEqual(len(self.told(DAY + "T08:05:00")), 4)
        self.assertEqual(len(self.told(DAY + "T09:00:00")), 1)
        self.assertEqual(len(self.told(DAY + "T21:00:00")), 1)
        self.assertEqual(self.told(NEXT + "T00:01:00"), [])
        self.write("alpha", rows=[row(f"{n:03d}-q") for n in range(1, 9)])
        self.assertEqual(self.told(DAY + "T23:59:00"), [], "a rollback reopened the prior date's event or digest")
        self.assertEqual(len(self.api.snapshot()[1]), 6)
        [digest] = self.told(NEXT + "T09:00:00")  # the four it had no room for wait for the next date's digest
        self.assertTrue(digest.endswith("\nand 4 more events"), digest)
    def test_a_board_of_more_bells_than_the_memory_keeps_is_not_told_its_oldest_on_the_second_pass(self):
        bells = [dict(key=f"bell-{n}", kind="attention", line=f"Work stopped {n}", time=DAY + "T11:00:00+00:00")
                 for n in range(501)]
        self.write("alpha", bells=bells)
        self.assertEqual(self.told(), [], "the first sight was told")
        self.assertEqual(self.told(), [], "a second pass told a bell recorded at first sight")
        self.assertEqual(self.told(), [], "a third pass told a bell")
    def test_a_clock_stepping_back_after_seven_later_dates_finds_the_forgotten_dates_allowance_spent(self):
        self.write("alpha")
        self.assertEqual(self.told(DAY + "T08:00:00"), [])
        self.write("alpha", rows=[row(f"{n:03d}-q") for n in range(1, 5)])
        self.assertEqual(len(self.told(DAY + "T08:05:00")), 4)
        self.assertEqual(len(self.told(DAY + "T09:00:00")), 1)
        self.assertEqual(len(self.told(DAY + "T21:00:00")), 1)
        for step in range(1, 8):
            self.assertEqual(self.told(str(date.fromisoformat(DAY) + timedelta(days=step)) + "T00:01:00"), [])
        self.write("alpha", rows=[row(f"{n:03d}-q") for n in range(1, 9)])
        self.assertEqual(self.told(DAY + "T23:59:00"), [], "a rollback reopened a forgotten date's lines or digests")
        self.assertEqual(len(self.api.snapshot()[1]), 6)
        later = str(date.fromisoformat(DAY) + timedelta(days=8))  # a date after all of them has its own allowance
        [digest] = self.told(later + "T09:00:00")
        self.assertTrue(digest.endswith("\nand 4 more events"), digest)
class Every(DoorCase):
    def test_the_board_counts_every_filed_plan_even_the_older_one_the_wave_no_longer_keeps(self):
        for slug in ORDERS:
            (self.repo / "orders" / f"{slug}.md").write_text(order_text(f"test -f {slug}.txt"))
        (self.repo / "orders" / "003-later.cut").write_text("cut\n")
        (self.repo / "plans").mkdir()
        for name, text in PLANS.items():
            (self.repo / "plans" / f"{name}.md").write_text(text)
        self.commit_all("three orders, one cut, and two plans")
        (self.repo / ".ceo" / "landed-by-hand").mkdir(parents=True)
        out = self.tmp / "shop.json"
        counts = []
        for landed in ((), ("001-cart",), ("001-cart", "002-pay")):
            for slug in landed:
                (self.repo / ".ceo" / "landed-by-hand" / slug).write_text("")
            self.assertEqual(self.ceo("board", "--name", "shop", "--out", str(out)).returncode, 0)
            wave = json.loads(out.read_text())["wave"]
            counts.append(([one["plan"] for one in wave["plans"]], [(one["plan"][6:-3], one["orders"], one["landed"],
                           one["cut"], one["building"]) for one in wave["every"]]))
        self.assertEqual(counts, [
            (["plans/later.md", "plans/checkout.md"], [("later", 1, 0, 1, 0), ("checkout", 2, 0, 0, 0)]),
            (["plans/later.md", "plans/checkout.md"], [("later", 1, 0, 1, 0), ("checkout", 2, 1, 0, 0)]),
            (["plans/later.md"], [("later", 1, 0, 1, 0), ("checkout", 2, 2, 0, 0)])])
        self.assertEqual(sorted(wave), ["every", "orders", "plans"])
if __name__ == "__main__":
    unittest.main()
