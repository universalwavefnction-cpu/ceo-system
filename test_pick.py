"""The pick order end to end: an order of spins, the owner's pick, the review of the pick alone and the
door. Real git (with a local bare repository as origin for the door), real processes, real files. Only
the model is replaced, by worker scripts that each write a different preview, and GitHub, by the fake
gh of test_door."""
import json
import os
import signal
import subprocess
import time
import unittest
from pathlib import Path

from test_ceo import alive, order_text
from test_door import KERNEL, STUB, DoorCase
from test_owner import BLOCK, LESSONS, LINE, PAGE, STUB as OWNER_STUB
from test_tick import TickCase

STUB += r'''
elif mode == "spin":  # a worker that builds the spin it is told, with a preview that names it
    told = brief.decode().rsplit("Your spin: ", 1)[1].strip() if b"Your spin: " in brief else "plain"
    if args:  # it says it started, then waits to be let go
        open(f"{args[0]}-{os.path.basename(os.getcwd())}", "w").write(str(os.getpid()))
        while not os.path.exists(args[1]):
            if not os.path.isdir(os.path.dirname(args[1])):
                sys.exit(1)  # its test is over and took the release with it
            time.sleep(0.05)
    if "idle" not in told:  # an idle spin builds nothing; a blind one makes no preview
        open("built", "w").write(told + "\n")
        if "blind" not in told:
            open("preview.html", "w").write(f"<p>{told}</p>\n")
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "commit", "-qm", told], check=True)
    if "slow" in told:  # still at work when the clock runs out
        time.sleep(60)
elif mode == "look":  # a reviewer that keeps its brief and what it sees, and finds nothing
    open(args[0], "wb").write(brief)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    open(args[0] + ".seen", "w").write(head.strip() + " " + open("preview.html").read())
    answer()
'''


def spin_order(*spins, done_when="test -f built"):
    """An order of spins, one a line, each to make preview.html."""
    return (order_text(done_when) + "\n## Spins\n" + "".join(f"- {line}\n" for line in spins)
            + "\n## Preview\n`preview.html`\n")


def branches(case):
    """Each branch under order/ with its commit, one a line."""
    return case.git("for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads/order")


class SpinCase(TickCase):
    def setUp(self):
        super().setUp()
        self.stub.write_text(STUB)

    def spun(self, name, *spins, code=3, **kw):
        """Run an order of spins by hand; return the heads of its spin branches."""
        self.commit_order(f"{name}.md", spin_order(*spins))
        run = self.ceo("run", f"orders/{name}.md", "--worker", self.model("spin"), **kw)
        self.assertEqual(run.returncode, code, run.stderr)
        return [self.git("rev-parse", f"order/{name}-spin-{n}") for n in range(1, len(spins) + 1)]

    def ceo(self, *args, timeout=None, env=None):
        return super().ceo(*args, *(["--timeout", str(timeout)] if timeout else []), env=env)

    def waiting(self, name):
        return self.repo / ".ceo" / "waiting" / f"{name}.json"

    def choices(self, name):
        return json.loads(self.waiting(name).read_text())["spins"]

    def events(self):
        log = self.repo / ".ceo" / "events.jsonl"
        lines = log.read_text().splitlines() if log.exists() else []
        return [(event["event"], event["slug"]) for event in map(json.loads, lines)]

    def status_lines(self):
        """STATUS.md but the health, free and sweep lines orders 6 and 10 write under its title."""
        return [line for line in (self.repo / "STATUS.md").read_text().splitlines()
                if not line.startswith(("Health: ", "Free: disk ", "Sweep: "))]

    def assert_refused(self, done):
        self.assertEqual(done.returncode, 2, done.stdout)
        self.assertRegex(done.stderr, r"^ceo: refused: [^\n]+\.\n$")

    def snapshot(self):
        """What a refused pick must leave as it was: branches, worktrees and the run's records."""
        ceo = self.repo / ".ceo"
        return (self.git("for-each-ref", "refs/heads"), self.git("worktree", "list", "--porcelain"),
                sorted(str(path.relative_to(ceo)) for path in ceo.rglob("*")),
                [path.read_bytes() for path in sorted((ceo / "runs").glob("*/result.json"))])

    def cut_pick(self, name, number):
        """A pick stopped by SIGTERM after it recorded done, while git removes another spin's worktree."""
        pick = self.ceo_bg("pick", name, number, env=self.shim("worktree remove"))
        self.wait_for((self.tmp / "paused").exists, "the pick removes the other spins")
        pick.send_signal(signal.SIGTERM)
        pick.communicate(timeout=30)
        self.assertEqual(pick.returncode, -signal.SIGTERM)
        result = self.read(name)
        self.assertEqual((result["state"], result["picked"]), ("done", int(number)))
        left = self.repo / ".ceo" / "wt" / f"{name}-spin-3"
        self.assertTrue(self.waiting(name).exists() and left.exists(), "the pick ended before its cut")

    def assert_picked(self, name, heads):
        """Spin 2 of three picked to the end: its worktree alone, every branch kept, no choice left on
        show, and each event said once."""
        self.assertEqual(sorted(path.name for path in (self.repo / ".ceo" / "wt").iterdir()), [name])
        self.assertEqual(branches(self), f"order/{name} {heads[1]}\norder/{name}-spin-1 {heads[0]}\n"
                                         f"order/{name}-spin-3 {heads[2]}")
        self.assertFalse(self.waiting(name).exists())
        self.assertNotIn("## Waiting for your word", (self.repo / "STATUS.md").read_text())
        self.assertEqual(self.events(), [("waiting", name), ("picked", name)])


class Spins(SpinCase):
    def test_two_spins_wait_with_two_previews_and_nothing_is_reviewed_or_started(self):
        heads = self.spun("010-two", "red", "blue")
        result = self.read("010-two")
        self.assertEqual((result["state"], result["head"], result["worktree"]), ("waiting", None, None))
        waiting = json.loads(self.waiting("010-two").read_text())
        self.assertEqual((waiting["order"], waiting["title"]), ("orders/010-two.md", "An order"))
        self.assertEqual([(spin["spin"], spin["line"], spin["branch"], spin["head"])
                          for spin in waiting["spins"]],
                         [(1, "red", "order/010-two-spin-1", heads[0]),
                          (2, "blue", "order/010-two-spin-2", heads[1])])
        for spin in waiting["spins"]:  # each worker was told its own spin
            self.assertEqual((self.repo / spin["preview"]).read_text(), f"<p>{spin['line']}</p>\n")
        self.assertEqual(self.git("branch", "--list", "order/010-two"), "")
        # Nothing more starts for it, though it is filed and has a reviewer and places are free.
        status = self.repo / "STATUS.md"
        status.write_text("# Status\n\n## In progress\n- something\n")
        work = ("--worker", self.model("spin"), "--reviewer", self.model("clean"))
        self.assertEqual(self.tick(*work), "")
        self.assertEqual(self.tick(*work), "")
        self.assertEqual([path.name for path in self.run_dir("010-two").glob("*.json")], ["result.json"])
        self.assertEqual(self.status_lines()[2:], [
            "Orders: orders run from your folder; no origin", "", "## Waiting for your word",
            "- `orders/010-two.md` An order: `python3 ceo.py pick 010-two <n>`",
            "  - 1. red: `.ceo/wt/010-two-spin-1/preview.html`",
            "  - 2. blue: `.ceo/wt/010-two-spin-2/preview.html`",
            "", "## In progress", "- something"])
        self.assertEqual(self.events(), [("waiting", "010-two")])

    def test_pick_two_sends_that_spin_alone_to_review_and_keeps_the_other_branch(self):
        heads = self.spun("011-pick", "red", "blue")
        self.idle_tick()
        before = self.snapshot()
        self.assert_refused(self.ceo("pick", "011-pick", "3"))
        self.assertEqual(self.snapshot(), before, "a refused pick changed something")
        picked = self.ceo("pick", "011-pick", "2")
        self.assertEqual(picked.returncode, 0, picked.stderr)
        self.assertEqual(picked.stdout, "ceo: picked spin 2 of 011-pick: order/011-pick goes to review.\n")
        result = self.read("011-pick")
        self.assertEqual((result["state"], result["picked"], result["head"], result["worktree"]),
                         ("done", 2, heads[1], ".ceo/wt/011-pick"))
        wt = self.repo / ".ceo" / "wt"
        self.assertEqual(sorted(path.name for path in wt.iterdir()), ["011-pick"])
        self.assertEqual((wt / "011-pick" / "preview.html").read_text(), "<p>blue</p>\n")
        head = subprocess.run(["git", "symbolic-ref", "HEAD"], cwd=wt / "011-pick", capture_output=True,
                              text=True)
        self.assertEqual(head.stdout, "refs/heads/order/011-pick\n")
        self.assertEqual(branches(self), f"order/011-pick {heads[1]}\norder/011-pick-spin-1 {heads[0]}")
        self.assertFalse(self.waiting("011-pick").exists())
        brief = self.tmp / "brief"
        self.tick_until((self.run_dir("011-pick") / "review.json").exists, "the pick was reviewed",
                        "--reviewer", self.model("look", str(brief)))
        review = self.read("011-pick", "review.json")
        self.assertEqual((review["verdict"], review["commit"]), ("survives", heads[1]))
        self.assertEqual(Path(f"{brief}.seen").read_text(), f"{heads[1]} <p>blue</p>\n")
        self.assertTrue(brief.read_bytes().endswith(b"\nThe spin picked: blue\n"))
        self.assertEqual([path.name for path in (self.repo / ".ceo" / "runs").iterdir()], ["011-pick"])
        self.assertNotIn("## Waiting for your word", (self.repo / "STATUS.md").read_text())
        self.assert_refused(self.ceo("pick", "011-pick", "1"))  # it waits no more
        self.assertEqual(self.events(), [("waiting", "011-pick"), ("picked", "011-pick")])

    def test_a_pick_of_a_spin_that_did_not_finish_is_refused_and_changes_nothing(self):
        self.spun("012-idle", "red", "idle", "blue")
        self.assertEqual([spin["spin"] for spin in self.choices("012-idle")], [1, 3])
        before = self.snapshot()
        for number in ("2", "4", "0"):
            with self.subTest(number):
                refused = self.ceo("pick", "012-idle", number)
                self.assert_refused(refused)
                self.assertIn("finished spins are 1, 3", refused.stderr)
        self.assert_refused(self.ceo("pick", "012-nothing", "1"))
        self.assertEqual(self.snapshot(), before, "a refused pick changed something")

    def test_a_spin_whose_done_when_never_passes_leaves_the_run_failed_when_one_other_finished(self):
        self.spun("013-fail", "red", "idle", code=1)
        result = self.read("013-fail")
        self.assertEqual(result["state"], "failed")
        self.assertIn("1 of 2 spins finished", result["reason"])
        self.assertEqual([(spin["finished"], spin["done_when_exit"]) for spin in result["spins"]],
                         [(True, 0), (False, 1)])
        self.assertFalse(self.waiting("013-fail").exists())
        self.assert_refused(self.ceo("pick", "013-fail", "1"))
        self.assertEqual(self.events(), [])

    def test_a_spin_whose_preview_is_missing_is_no_choice_though_its_done_when_passes(self):
        self.spun("014-blind", "red", "blind", code=1)
        self.assertEqual([(spin["finished"], spin["done_when_exit"])
                          for spin in self.read("014-blind")["spins"]], [(True, 0), (False, 0)])

    def test_the_clock_ends_the_spins_and_the_two_finished_wait(self):
        started = time.monotonic()
        heads = self.spun("015-clock", "red", "slow idle", "slow blue", timeout=5)
        self.assertLess(time.monotonic() - started, 40, "the clock did not stop the slow spins")
        result = self.read("015-clock")
        # Stopped by the clock, a spin is judged as any other: by its done-when block and its preview.
        self.assertEqual([(spin["finished"], spin["worker_exit"]) for spin in result["spins"]],
                         [(True, 0), (False, None), (True, None)])
        self.assertEqual(result["survivors"], [])
        self.assertEqual([(spin["spin"], spin["head"]) for spin in self.choices("015-clock")],
                         [(1, heads[0]), (3, heads[2])])

    def test_the_owners_answer_file_is_read_by_the_tick_once_refused_or_picked(self):
        heads = self.spun("016-answer", "red", "blue")
        answers = self.repo / ".ceo" / "answers"
        answers.mkdir()
        (answers / "016-answer.json").write_text('{"pick": 5}')
        self.assertIn("ceo: refused: spin 5 is not a finished spin of 016-answer", self.idle_tick())
        self.assertEqual((list(answers.iterdir()), self.read("016-answer")["state"]), ([], "waiting"))
        (answers / "016-answer.json").write_text('{"pick": 1}')
        out = self.tick("--reviewer", self.model("clean"))
        self.assertEqual(out.splitlines(),
                         ["ceo: picked spin 1 of 016-answer: order/016-answer goes to review.",
                          "ceo: started the review of orders/016-answer.md"])
        self.assertEqual(list(answers.iterdir()), [])
        self.wait_for((self.run_dir("016-answer") / "review.json").exists, "the pick was reviewed")
        self.assertEqual(self.read("016-answer", "review.json")["commit"], heads[0])
        self.assertEqual(self.git("rev-parse", "order/016-answer-spin-2"), heads[1])

    def test_spins_and_plain_orders_have_no_global_cap(self):
        work = ("--worker", self.model("spin", str(self.tmp / "pid"), str(self.release)),
                "--reviewer", self.model("clean"))
        self.commit_order("017-spins.md", spin_order("red", "blue", "green"))  # filed first, so it starts first
        self.commit_order("018-plain.md")
        self.assertEqual(self.tick(*work).splitlines(),
                         ["ceo: started the run of orders/017-spins.md",
                          "ceo: started the run of orders/018-plain.md"])
        for n in (1, 2, 3):
            self.wait_for((self.tmp / f"pid-017-spins-spin-{n}").exists, f"spin {n} started")
        self.assertEqual(self.tick(*work), "", "an order started twice")
        self.release.touch()
        for name in ("017-spins", "018-plain"):
            self.wait_for((self.run_dir(name) / "result.json").exists, f"{name} ended")
        self.assertEqual((self.read("017-spins")["state"], self.read("018-plain")["state"]),
                         ("waiting", "done"))

    def test_a_run_of_spins_stopped_by_sigterm_is_cleared_with_its_spins_and_runs_again(self):
        self.commit_order("019-term.md", spin_order("red", "blue"))  # filed: once cleared, the tick starts it
        ceo = self.ceo_bg("run", "orders/019-term.md", "--worker",
                          self.model("spin", str(self.tmp / "pid"), str(self.tmp / "never")))
        marks = [self.tmp / f"pid-019-term-spin-{n}" for n in (1, 2)]
        for path in marks:
            self.wait_for(path.exists, f"{path.name} started")
        workers = [int(path.read_text()) for path in marks]
        self.kill_later(workers)
        ceo.send_signal(signal.SIGTERM)
        ceo.communicate(timeout=60)
        self.assertEqual(ceo.returncode, 2)
        self.wait_for(lambda: not any(map(alive, workers)), "the spins' workers were stopped")
        self.assertFalse((self.run_dir("019-term") / "result.json").exists())
        out = self.tick("--worker", self.model("spin"))
        self.assertIn("recovered 019-term: cleared", out)
        self.assertIn("started the run of orders/019-term.md", out)
        self.assertEqual(len(self.records("019-term")), 1)
        self.wait_for((self.run_dir("019-term") / "result.json").exists, "the order ran again")
        self.assertEqual(self.read("019-term")["state"], "waiting")

    def test_an_order_of_spins_that_cannot_be_read_is_refused_and_starts_nothing(self):
        two = spin_order("red", "blue")
        broken = {"one-spin": spin_order("red"), "seven-spins": spin_order(*"abcdefg"),
                  "no-preview": two[:two.index("\n## Preview")],
                  "two-previews": two + "`other.html`\n",
                  "not-a-picture": two.replace("preview.html", "preview.txt"),
                  "outside": two.replace("`preview.html`", "`../preview.html`"),
                  "absolute": two.replace("`preview.html`", "`/tmp/preview.html`")}
        for name, text in broken.items():
            with self.subTest(name):
                self.commit_order(f"{name}.md", text)
                self.assert_refused(self.ceo("run", f"orders/{name}.md", "--worker", self.model("spin")))
                self.assertFalse((self.repo / ".ceo").exists(), "something was created")
                self.assertEqual(self.git("branch", "--list", "order/*"), "")

    def test_a_pick_cut_off_after_it_recorded_done_is_finished_by_the_next_tick(self):
        heads = self.spun("021-cut", "red", "blue", "green")
        self.idle_tick()  # the row asking for the pick
        self.cut_pick("021-cut", "2")
        self.assertEqual(self.idle_tick(),
                         "ceo: picked spin 2 of 021-cut: order/021-cut goes to review.\n")
        self.assert_picked("021-cut", heads)
        self.assertEqual(self.idle_tick(), "", "the pick was finished twice")

    def test_a_pick_cut_off_after_it_recorded_done_is_finished_by_its_retry_and_no_other(self):
        heads = self.spun("022-again", "red", "blue", "green")
        self.idle_tick()
        self.cut_pick("022-again", "2")
        before = self.snapshot()
        self.assert_refused(self.ceo("pick", "022-again", "1"))  # spin 2 is picked: no other now
        self.assertEqual(self.snapshot(), before, "a refused pick changed something")
        again = self.ceo("pick", "022-again", "2")
        self.assertEqual((again.returncode, again.stdout),
                         (0, "ceo: picked spin 2 of 022-again: order/022-again goes to review.\n"))
        self.assertEqual(self.idle_tick(), "", "the retry left the tick work to finish")
        self.assert_picked("022-again", heads)
        self.assert_refused(self.ceo("pick", "022-again", "2"))  # finished, it waits no more

    def test_the_owners_lines_under_the_title_outlive_the_rows_asking_for_a_pick(self):
        self.spun("023-rows", "red", "blue")
        status = self.repo / "STATUS.md"
        rows = ["## Waiting for your word",
                "- `orders/023-rows.md` An order: `python3 ceo.py pick 023-rows <n>`",
                "  - 1. red: `.ceo/wt/023-rows-spin-1/preview.html`",
                "  - 2. blue: `.ceo/wt/023-rows-spin-2/preview.html`"]
        # What the owner has under the title, and what every pass leaves under its own line.
        layouts = {"nothing": ([], ["", *rows]),
                   "an empty line": (["", "## In progress"], ["", *rows, "", "## In progress"]),
                   "a list line": (["- Order 4: in progress"], ["", *rows, "", "- Order 4: in progress"])}
        for name, (owners, written) in layouts.items():
            with self.subTest(name):
                status.write_text("\n".join(["# Status", *owners]) + "\n")
                for _ in range(2):  # the rows written, then taken away and written anew
                    self.idle_tick()
                    lines = self.status_lines()
                    self.assertEqual((lines[0], lines[1][:11], lines[2:]),
                                     ("# Status", "Last tick: ",
                                      ["Orders: orders run from your folder; no origin", *written]))
        self.ceo("pick", "023-rows", "1")
        self.idle_tick()
        self.assertEqual(self.status_lines()[2:], ["Orders: orders run from your folder; no origin",
                                                  "", "- Order 4: in progress"])

    def test_the_readme_says_a_spin_the_clock_stopped_is_finished_as_the_kernel_counts_it(self):
        said = " ".join((KERNEL / "README.md").read_text().split())
        self.assertIn("A spin is finished when its done-when block passes and its preview exists, even if "
                      "the clock stopped its worker.", said)
        self.spun("024-said", "red", "slow blue", timeout=5)
        spins = self.read("024-said")["spins"]
        self.assertEqual([(spin["finished"], spin["worker_exit"]) for spin in spins],
                         [(True, 0), (True, None)])

    def test_each_spins_worker_reads_the_owners_file_first_then_the_lessons_page(self):
        self.stub.write_text(OWNER_STUB)  # a worker that keeps what it was given
        home = Path(self.env["CEO_HOME"])
        home.mkdir(exist_ok=True)
        (home / "OWNER.md").write_text(LINE + "\n")
        given = self.tmp / "given"
        given.mkdir()
        (self.repo / "LESSONS.md").write_text(PAGE + "\n")
        self.commit_order("025-told.md", spin_order("red", "blue"))
        run = self.ceo("run", "orders/025-told.md", "--worker", self.model("record", str(given)))
        self.assertEqual(run.returncode, 1, run.stderr)  # what it keeps is no build: no spin finished
        order = (self.repo / "orders" / "025-told.md").read_bytes()
        for n, line in ((1, "red"), (2, "blue")):
            self.assertEqual((given / f"wt-025-told-spin-{n}").read_bytes(),
                             BLOCK + LESSONS + order + f"\nYour spin: {line}\n".encode())

    def test_an_order_of_spins_cut_while_it_waits_is_swept_whole_unless_a_spin_holds_a_change(self):
        self.spun("026-cut", "red", "blue")
        self.spun("027-held", "red", "blue")
        (self.repo / ".ceo" / "wt" / "027-held-spin-2" / "loose.txt").write_text("not committed\n")
        for name in ("026-cut", "027-held"):
            (self.repo / "orders" / f"{name}.cut").touch()
        old = time.time() - 3 * 86400  # past the sweep's two days
        for root, dirs, files in os.walk(self.repo):
            for name in dirs + files:
                os.utime(os.path.join(root, name), (old, old), follow_symlinks=False)
        swept = self.ceo("sweep")
        self.assertEqual(swept.returncode, 0, swept.stderr)
        self.assertEqual(sorted(path.name for path in (self.repo / ".ceo" / "wt").iterdir()),
                         ["027-held-spin-1", "027-held-spin-2"])
        self.assertEqual(self.git("branch", "--list", "order/026-cut*"), "")
        self.assertFalse(self.waiting("026-cut").exists())
        self.assertIn(".ceo/wt/027-held-spin-2", swept.stdout)
        self.assertTrue(self.waiting("027-held").exists())


class PickDoor(DoorCase):
    def setUp(self):
        super().setUp()
        self.stub.write_text(STUB)

    def test_the_pick_alone_becomes_a_pull_request_and_merges_and_the_other_spin_stays_a_branch(self):
        self.commit_order("020-door.md", spin_order("red", "blue"))  # committed and pushed: filed
        work = ("--worker", self.model("spin"), "--reviewer", self.model("clean"))
        self.tick_while((self.run_dir("020-door") / "result.json").exists, "the spins ran", *work)
        self.assertEqual(self.read("020-door")["state"], "waiting")
        self.tick(*work)
        self.assertEqual((self.server(), self.main()), ([], self.git("rev-parse", "main")),
                         "a spin went to the server before the pick")
        heads = [self.git("rev-parse", f"order/020-door-spin-{n}") for n in (1, 2)]
        picked = self.ceo("pick", "020-door", "2")
        self.assertEqual(picked.returncode, 0, picked.stderr)
        self.assertEqual(self.read("020-door")["pr"], 1)
        self.tick_while(lambda: "cleared" in self.read("020-door"), "the pick was merged", *work)
        self.assertEqual([(pr["head"], pr["state"]) for pr in self.server()],
                         [("order/020-door", "MERGED")])
        landed = subprocess.run(["git", "--git-dir", str(self.origin), "show", "main:preview.html"],
                                capture_output=True, text=True).stdout
        self.assertEqual(landed, "<p>blue</p>\n")
        self.assertEqual(self.read("020-door", "review.json")["commit"], heads[1])
        self.assertEqual(branches(self), f"order/020-door-spin-1 {heads[0]}", "a spin's branch went")
        self.assertEqual(self.on_origin("refs/heads/order/020-door-spin-1"), "")
        self.assertEqual(list((self.repo / ".ceo" / "wt").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
