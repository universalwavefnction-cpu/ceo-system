"""The night end to end: a scratch project with three weaknesses planted, a flaky test, a fresh clone that
does not build and an unhandled error, and one decoy. The tick starts the night inside its window; the
project's own tests run in a fresh clone, then a stand-in model, in a read-only one, proposes findings
that are each replayed in a fresh clone. Real git with a local bare repository as origin, real processes
and files; only the model is a stand-in. Each night runs in a time zone of its own, 26 hours apart, so
that two nights a few seconds apart fall on two dates, as two real nights do."""
import contextlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ceo import BOUND, held, parse_order, read_json
from test_bug_door import BugCase, gone
from test_door import STUB, DoorCase
from test_tick import CEO

KERNEL = Path(__file__).resolve().parent
STUB += r'''
elif mode == "night":  # the night's researcher: it keeps its brief, tries to fix at night, then answers
    open(args[0], "wb").write(brief)
    try:
        open("app.py", "a").write("# fixed at night\n")
        tried = "it changed its copy"
    except OSError:
        tried = "its copy is read-only"
    open(args[0] + ".tried", "w").write(tried)
    json.dump(json.load(open(args[1])), open("../NIGHT.json", "w"))
    if args[2:]:  # and leaves behind a process in a session of its own, holding every descriptor it had
        child = subprocess.Popen(["sleep", "60"], start_new_session=True, close_fds=False)
        open(args[2], "w").write(str(child.pid))
'''
APP = 'def parse(line):\n    """The number of a line `key=value`."""\n    key, value = line.split("=", 1)\n' \
      '    return int(value)\n'
FLAKY = ('import unittest\n\n\nclass Fruit(unittest.TestCase):\n    def test_the_first_fruit(self):\n'
         '        self.assertEqual(next(iter({"apple", "pear"})), "apple")  # as the hash seed falls\n')
BUILD = 'import json\n\nprint(json.load(open("settings.local.json"))["name"])\n'  # a file never committed
GATE = "A fresh clone of main does not pass its own tests: `./gate.sh` exits 1."
CRASH = {"claim": "parse() crashes with a traceback on a line without an equals sign.",
         "command": "python3 -c 'import app; app.parse(\"no equals sign\")'"}
DECOY = {"claim": "parse() returns the value as text, not as a number.",
         "command": "python3 -c 'import app; assert app.parse(\"a=1\") == 1'"}
FLAKY_FOUND = {"claim": "test_flaky passes or fails with the hash seed: the order of a set decides it.",
               "command": "for seed in 0 1 2 3 4 5 6 7 8 9; do PYTHONHASHSEED=$seed python3 -m unittest -q "
                          "test_flaky 2>/dev/null || exit 1; done"}
IDEA = {"idea": "Read settings from the environment, as twelve-factor apps do, so that a fresh clone builds.",
        "sources": ["https://12factor.net/config"]}
DRAFTS = ["drafts/orders/001-night-a-fresh-clone-of-main-does-not-pass-its.md",
          "drafts/orders/002-night-parse-crashes-with-a-traceback-on-a-line.md",
          "drafts/orders/003-night-test-flaky-passes-or-fails-with-the-hash.md"]
FILED = DRAFTS[1][7:]  # where the owner's agree files the second


class NightCase(BugCase):
    def setUp(self):
        DoorCase.setUp(self)  # the agents its ticks start are stopped as BugCase stops them; no shop here
        self.stub.write_text(STUB)
        self.started = []
        self.addCleanup(self.stop_agents)
        self.home = Path(self.env["CEO_HOME"])
        self.home.mkdir(parents=True)
        (self.home / "OWNER.md").write_text("Keep every change small.\n")
        for name, text in {"app.py": APP, "test_flaky.py": FLAKY, "build.py": BUILD,
                           "settings.local.json": '{"name": "shop"}\n', "VISION.md": "A shop that never "
                           "loses an order.\n", "IDEAS.md": "# Ideas\n\n## 1 — 2026-10-01\n\nA shop on Telegram.\n",
                           "bugs/1.md": "# Bug 1\n\nThe total is wrong on a cart of one.\n"}.items():
            (self.repo / name).parent.mkdir(exist_ok=True)
            (self.repo / name).write_text(text)
        with open(self.repo / ".gitignore", "a") as ignored:  # ceo.toml too: each test sets its own night
            ignored.write("settings.local.json\nSTATUS.md\nceo.toml\n")
        self.git("rm", "-q", "--cached", "ceo.toml")
        self.gate("python3 build.py")  # which passes here, and nowhere settings.local.json is missing
        self.answer = self.tmp / "answer.json"
        self.answer.write_text(json.dumps({"summary": "Looked at reliability, structure, users and others.",
                                           "findings": [CRASH, DECOY, FLAKY_FOUND], "ideas": [IDEA]}))

    def night_at(self, hours, starts=-1, ends=59):
        """Put this test in a time zone `hours` east of UTC, with the night's window from `starts` to `ends`
        minutes around now there; return the date of that night, its window's end."""
        self.env["TZ"] = f"NIGHT{-hours:+d}"  # POSIX counts the hours west of UTC
        here = datetime.now(timezone(timedelta(hours=hours)))
        start, end = here + timedelta(minutes=starts), here + timedelta(minutes=ends)
        (self.repo / "ceo.toml").write_text(
            f'disk_floor_gb = 0\nram_floor_gb = 0\nnight = "{start:%H:%M}-{end:%H:%M}"\n')
        return f"{end:%Y-%m-%d}"

    def model_for(self, brief, *more):
        return self.model("night", str(brief), str(self.answer), *more)

    def answering(self, *findings, ideas=()):
        self.answer.write_text(json.dumps({"summary": "Looked.", "findings": list(findings), "ideas": list(ideas)}))

    def night(self, date, brief):
        """The night the tick starts now, with the stand-in model keeping its brief at brief: its record,
        once it has ended and let go of its lock."""
        self.assertIn("ceo: started the night\n", self.tick("--worker", self.model_for(brief)))
        record = self.repo / ".ceo" / "night" / date / "result.json"
        self.wait_for(lambda: read_json(record).get("ended"), "the night ended", timeout=120)
        self.wait_for(lambda: not held(self.repo / ".ceo" / "locks" / "night.lock"), "the night let go")
        return read_json(record)

    def night_now(self, date, timeout):
        """The night `ceo.py night` runs now with the stand-in model, its clock --timeout seconds: its record."""
        ran = self.ceo("night", "--model", self.model_for(self.tmp / "brief"), "--timeout", str(timeout))
        self.assertEqual(ran.returncode, 0, ran.stderr)
        return read_json(self.repo / ".ceo" / "night" / date / "result.json")

    def clone_main(self):
        """A clean clone of main as origin has it."""
        copy = self.tmp / "main"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(copy)], env=self.env, check=True)
        return copy

    def waiting(self):
        return sorted(row["slug"] for row in json.loads(self.ceo("status", "--json").stdout)["rows"]
                      if row["state"] == "waiting")

    def told(self):
        lines = (self.repo / ".ceo" / "events.jsonl").read_text().splitlines()
        return [one["slug"] for one in map(json.loads, lines) if one["event"] == "waiting"]


class Night(NightCase):
    def test_three_weaknesses_wait_as_drafts_a_decline_is_learned_and_the_next_night_repeats_nothing(self):
        date, before, brief = self.night_at(-12), self.main(), self.tmp / "brief-1"
        record = self.night(date, brief)
        self.assertEqual((record["reason"], record["tests"]), ("", {"command": "./gate.sh", "exit": 1}))
        self.assertEqual([(one["claim"], one["new"], one["order"]) for one in record["findings"]],
                         list(zip([GATE, CRASH["claim"], FLAKY_FOUND["claim"]], [True] * 3, DRAFTS)))
        self.assertEqual(record["dropped"], [{**DECOY, "exit": 0}])  # its command passes: no finding
        # Main gained one commit, of the three findings as drafts, the morning note and the idea: nothing
        # the project held changed, and nothing is filed in orders/.
        self.assertEqual((self.subjects()[0], self.origin_git("rev-parse", "main~1").stdout.strip()),
                         (f"Night {date}: 3 findings, 3 new for your word", before))
        self.assertEqual(self.origin_git("diff", "--name-status", before, "main").stdout.splitlines(),
                         ["M\tIDEAS.md", *(f"A\t{draft}" for draft in DRAFTS), f"A\troutines/night-{date}.md"])
        ideas = self.shown("IDEAS.md")
        self.assertRegex(ideas, r"\A# Ideas\n\n## 1 — 2026-10-01\n\nA shop on Telegram\.\n\n## 2 — [\d-]{10}\n\n"
                                rf"From the night of {date}, not the owner's words: {re.escape(IDEA['idea'])}\n"
                                r"Sources: https://12factor\.net/config\n\Z")
        for order, (claim, command) in zip(DRAFTS, [(GATE, "./gate.sh"), tuple(CRASH.values()),
                                                    tuple(FLAKY_FOUND.values())]):
            title, done_when = parse_order(self.shown(order))[:2]
            self.assertEqual((title, done_when), (f"Night finding: {claim.rstrip('.')}", f"{command}\n"
                             if command == "./gate.sh" else f"bash -c {shlex.quote(command)}\n"))
            self.assertIn(f"Claim: {json.dumps(claim)}\nCommand: {json.dumps(command)}\n", self.shown(order))
        self.assertEqual([path for path in self.files() if path.startswith("orders/")], [])
        # Each waits for the owner's word, told once to the ears.
        slugs = [Path(order).stem for order in DRAFTS]
        self.assertEqual((self.waiting(), self.told()), (slugs, slugs))
        row = self.row(slugs[1])
        self.assertEqual((row["question"], [one["text"] for one in row["choices"]], row["detail"]),
                         ("Agree, decline or comment?", ["agree", "decline", "comment"],
                          f"`{CRASH['command']}` exits 1 in a clean clone of main"))
        # The model was told the project, its owner, its ideas and its bugs,
        # in a copy it could not change; the copy is gone.
        given = brief.read_text()
        for part in ("# The night's research", "Keep every change small.", "A shop that never loses an order.",
                     "A shop on Telegram.", "The total is wrong on a cart of one.", "`./gate.sh` exited 1."):
            self.assertIn(part, given)
        self.assertEqual(Path(f"{brief}.tried").read_text(), "its copy is read-only")
        self.assertEqual(sorted(path.name for path in (self.repo / ".ceo" / "night" / date).iterdir()),
                         ["NIGHT.json", "night.log", "result.json"])
        note = self.shown(f"routines/night-{date}.md")
        for said in (*(f"`{order}`, new" for order in DRAFTS), "proposed none of them again.",
                     "`./gate.sh` exited 1: a finding, above.", f"{DECOY['claim']} `{DECOY['command']}`: exits 0",
                     f"Idea 2 in IDEAS.md: {IDEA['idea']}"):
            self.assertIn(said, note)
        self.assertNotIn("started the run", self.tick())  # a draft never starts, nor is it ever eligible
        self.assertEqual(list((self.repo / ".ceo" / "ready").glob("*.json")), [])
        status = (self.repo / "STATUS.md").read_text().splitlines()
        self.assertIn(f"Night: {date}, 3 findings, 3 new for your word: [routines/night-{date}.md]"
                      f"(routines/night-{date}.md)", status)
        self.assertIn(f"- `{DRAFTS[2]}` Night finding: {FLAKY_FOUND['claim'].rstrip('.')}: Agree, decline or "
                      f"comment? `python3 ceo.py answer {slugs[2]} <choice> [--comment \"<text>\"]`", status)
        self.assertEqual(status[status.index(f"- `{DRAFTS[2]}` Night finding: {FLAKY_FOUND['claim'].rstrip('.')}"
                                             ": Agree, decline or comment? `python3 ceo.py answer "
                                             f"{slugs[2]} <choice> [--comment \"<text>\"]`") + 1:][:3],
                         ["  - agree", "  - decline", "  - comment"])
        # Declined, the flaky test is learned: never proposed again. A comment waits on, for the next night.
        # Agreed, the crash is filed: the draft moves into orders/, and it starts as any filed order.
        said = self.ceo("answer", slugs[2], "decline", "--comment", "A set's order is no bug in a test.")
        self.assertEqual(said.returncode, 0, said.stderr)
        refused = self.ceo("answer", slugs[0], "comment")
        self.assertEqual((refused.returncode, refused.stderr),
                         (2, "ceo: refused: comment needs your words: --comment \"<text>\".\n"))
        comment = "settings.local.json stays local: build without it."
        self.assertEqual(self.ceo("answer", slugs[0], "comment", "--comment", comment).returncode, 0)
        self.assertEqual(self.ceo("answer", slugs[1], "agree").returncode, 0)
        took = self.tick("--worker", self.model("wait", str(self.tmp / "began"), str(self.release)))
        self.assertIn(f"ceo: {DRAFTS[2]}: declined: no night proposes it again.\n", took)
        self.assertIn(f"ceo: {DRAFTS[1]}: filed as {FILED}: it starts as a filed order.\n", took)
        self.assertIn(f"ceo: started the run of {FILED}\n", took)
        self.assertEqual(self.origin_git("log", "-1", "--no-renames", "--name-status", "--format=%s",
                                         "--grep=filed on the owner", "main").stdout.splitlines(),
                         [f"Night finding {FILED}: filed on the owner's word", "", f"D\t{DRAFTS[1]}", f"A\t{FILED}"])
        self.assertRegex(self.shown("routines/night-declined.md"), rf"\n## [\d-]+ [\d:]+, {DRAFTS[2]}\n\nClaim: "
                         f"{re.escape(json.dumps(FLAKY_FOUND['claim']))}\nCommand: "
                         rf"{re.escape(json.dumps(FLAKY_FOUND['command']))}\nComment: \"A set's order is no bug "
                         r"in a test\.\"\n\Z")
        self.assertIn(f"Night finding {DRAFTS[2]}: declined, and its draft removed", self.subjects())
        self.assertEqual([path for path in self.files() if Path(path).stem == slugs[2]], [])  # gone, and no cut
        self.assertRegex(self.shown(f"{DRAFTS[0][:-3]}.comment"), rf"\A[\d-]+ [\d:]+: {re.escape(comment)}\n\Z")
        self.assertEqual(self.waiting(), slugs[:1])
        self.assertEqual(self.row(slugs[0])["detail"],
                         f"`./gate.sh` exits 1 in a clean clone of main; your comment: {comment}")
        # The next night, 26 hours east: the same weaknesses, the decline dropped before its replay, the draft
        # and the filed order still open reported again and recorded no second time, the idea no second time.
        date_2, before, brief = self.night_at(14), self.main(), self.tmp / "brief-2"
        self.assertNotEqual(date_2, date)
        record = self.night(date_2, brief)
        self.assertEqual([(one["claim"], one["new"], one["order"]) for one in record["findings"]],
                         [(GATE, False, DRAFTS[0]), (CRASH["claim"], False, FILED)])
        self.assertEqual((record["declined"], record["dropped"], record["said"]),
                         ([FLAKY_FOUND], [{**DECOY, "exit": 0}], "2 findings, 2 still open"))
        self.assertEqual(self.origin_git("diff", "--name-only", before, "main").stdout.split(),
                         [f"routines/night-{date_2}.md"])
        self.assertEqual((self.waiting(), self.told()), (slugs[:1], slugs))
        given = brief.read_text()
        for part in (json.dumps(FLAKY_FOUND["claim"]), comment, f"{DRAFTS[0]}:\nClaim: {json.dumps(GATE)}",
                     f"{FILED}, filed:\nClaim: {json.dumps(CRASH['claim'])}"):
            self.assertIn(part, given)
        note = self.shown(f"routines/night-{date_2}.md")
        for said in (f"1. `{DRAFTS[0]}`, still open", f"2. `{FILED}`, still open", "holds 1 declined finding; "
                     f"tonight's model proposed 1 of them again:\n- {FLAKY_FOUND['claim']}", "None new."):
            self.assertIn(said, note)

    def test_no_night_starts_outside_its_window_nor_twice_in_one_and_a_window_may_cross_midnight(self):
        self.night_at(-12, starts=60, ends=120)
        self.assertNotIn("night", self.tick("--worker", self.model_for(self.tmp / "brief")))
        refused = self.ceo("night", "--model", self.model_for(self.tmp / "brief"))
        self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertRegex(refused.stderr, r"^ceo: refused: it is outside the night's window, \"\d\d:\d\d-\d\d:\d\d\" in "
                                         r"ceo\.toml, in local time\.\n$")
        self.assertFalse((self.repo / ".ceo" / "night").exists())
        # Where it is 22:xx now, a window to two hours on ends tomorrow, the night's date.
        hours = (22 - datetime.now(timezone.utc).hour) % 24
        hours -= 24 if hours > 14 else 0  # time zones run from 12 hours west of UTC to 14 east
        date = self.night_at(hours, ends=120)
        self.assertEqual(date, f"{datetime.now(timezone(timedelta(hours=hours))) + timedelta(days=1):%Y-%m-%d}")
        ran = self.ceo("night", "--model", self.model_for(self.tmp / "brief"))
        self.assertEqual((ran.returncode, ran.stdout), (0, f"ceo: the night of {date}: 3 findings, 3 new for your "
                                                           f"word; the morning note is routines/night-{date}.md.\n"))
        again = self.ceo("night", "--model", self.model_for(self.tmp / "brief"))
        self.assertEqual((again.returncode, again.stderr),
                         (2, f"ceo: refused: the night of {date} has run: .ceo/night/{date} exists.\n"))
        self.assertNotIn("night", self.tick("--worker", self.model_for(self.tmp / "brief")))

    def test_a_night_at_work_is_an_agent_and_does_not_cap_orders(self):
        date = self.night_at(-12)
        model = self.model("wait", str(self.tmp / "began"), str(self.release))
        self.assertIn("ceo: started the night\n", self.tick("--worker", model))
        self.wait_for((self.tmp / "began-repo").exists, "the model began")
        rows = json.loads(self.ceo("status", "--json").stdout)["agents"]
        self.assertEqual([(row["role"], row["slug"], row["phase"]) for row in rows], [("night", None, "model")])
        (self.repo / "orders" / "010-reproduce.md").write_text((KERNEL / "routines" / "bug-reproduce.md").read_text())
        self.commit_all("File 010")
        self.assertEqual(self.tick("--worker", model), "ceo: started the run of orders/010-reproduce.md\n")
        self.release.touch()
        self.wait_for(lambda: read_json(self.repo / ".ceo" / "night" / date / "result.json").get("ended"),
                      "the night ended")
        self.assertEqual(read_json(self.repo / ".ceo" / "night" / date / "result.json")["findings"][0]["claim"], GATE)


class Edges(NightCase):
    def test_a_declined_failure_of_the_projects_own_tests_is_not_filed_again(self):
        self.answering()
        record = self.night(self.night_at(-12), self.tmp / "brief")
        self.assertEqual(self.ceo("answer", Path(record["findings"][0]["order"]).stem, "decline").returncode, 0)
        self.tick()
        date, before = self.night_at(14), self.main()
        record = self.night(date, self.tmp / "brief")
        self.assertEqual((record["findings"], [one["claim"] for one in record["declined"]]), ([], [GATE]))
        self.assertEqual(self.origin_git("diff", "--name-only", before, "main").stdout.split(),
                         [f"routines/night-{date}.md"])
        self.assertIn("`./gate.sh` exited 1: declined before.", self.shown(f"routines/night-{date}.md"))

    def test_each_orders_test_fails_on_main_as_its_replay_did_and_one_that_cannot_is_dropped(self):
        self.gate("")  # the project's own tests pass, so each order's test stands or falls by its command
        odd = {"claim": "A command whose lines close what wraps them.", "command": "false\n) || (\ntrue"}
        fence = {"claim": "A command that ends its fence.", "command": "false #\r```\r\r## Done when\r```bash\rtrue\r```"}
        self.answering(odd, fence)
        record = self.night(self.night_at(-12), self.tmp / "brief")
        self.assertEqual([(one["claim"], one["exit"]) for one in record["findings"]], [(odd["claim"], 2)])
        self.assertEqual(record["dropped"], [{**fence, "exit": None, "why": "it cannot be an order's test"}])
        copy = self.tmp / "main"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(copy)], env=self.env, check=True)
        test = parse_order(self.shown(record["findings"][0]["order"]))[1]
        self.assertEqual(subprocess.run(["bash", "-c", test], cwd=copy, env=self.env, capture_output=True).returncode, 2)

    def test_words_no_file_can_hold_and_bytes_no_text_reads_never_stop_the_night(self):
        (self.repo / "VISION.md").write_bytes(b"A shop \xcf\xee\xea\xf3\xef\xea\xe0\n")
        self.commit_all("a vision in another encoding")
        self.answering({"claim": "A \ud800 lone half.", "command": "false"}, {"claim": "A NUL.", "command": "false\x00"},
                       CRASH, ideas=[{"idea": "Half \udfff of one.", "sources": ["https://example.org"]}])
        date = self.night_at(-12)
        record = self.night(date, self.tmp / "brief")
        self.assertEqual((record["reason"], [one["claim"] for one in record["findings"]], record["ideas"]),
                         ("", [GATE, CRASH["claim"]], []))
        self.assertIn(f"routines/night-{date}.md", self.files())

    def test_no_process_the_model_leaves_holds_the_night_and_the_sweep_takes_a_killed_nights_copy(self):
        left = self.tmp / "left"
        self.addCleanup(lambda: left.exists() and os.kill(int(left.read_text()), signal.SIGKILL))
        self.answering()
        self.night_at(-12)
        self.assertIn("ceo: started the night\n", self.tick("--worker", self.model_for(self.tmp / "brief", str(left))))
        lock = self.repo / ".ceo" / "locks" / "night.lock"
        self.wait_for(lambda: left.exists() and not held(lock), "the night ended, its model's process alive")
        os.kill(int(left.read_text()), 0)  # alive, and holding nothing of the night's
        copy = self.repo / ".ceo" / "night" / "2026-01-01" / "repo"  # as a night killed at once leaves it
        copy.mkdir(parents=True)
        (copy / "app.py").write_text(APP)
        subprocess.run(["chmod", "-R", "a-w", str(copy)], check=True)
        subprocess.run(["touch", "-h", "-t", "202601010000", str(copy / "app.py"), str(copy), str(copy.parent)],
                       check=True)
        swept = self.ceo("sweep")
        self.assertEqual(swept.returncode, 0, swept.stderr)
        self.assertFalse(copy.parent.exists(), swept.stdout)

    def test_an_orders_test_runs_the_projects_test_command_whole_so_it_fails_on_main_as_its_replay_did(self):
        for name in ("lint.sh", "test.sh"):
            (self.repo / name).write_text("exit 0\n")
        self.commit_all("tests in two steps")
        date = self.night_at(-12)
        with open(self.repo / "ceo.toml", "a") as conf:  # the first step ends in `;`, which `&&` binds no further
            conf.write('test_cmd = "sh lint.sh; sh test.sh"\n')
        missing = {"claim": "The file fixed.txt is missing.", "command": "test -f fixed.txt"}
        self.answering(missing)
        record = self.night(date, self.tmp / "brief")
        self.assertEqual([(one["claim"], one["exit"]) for one in record["findings"]], [(missing["claim"], 1)])
        test = parse_order(self.shown(record["findings"][0]["order"]))[1]
        self.assertEqual(test, "bash -c 'test -f fixed.txt' && bash -c 'sh lint.sh; sh test.sh'\n")
        copy = self.clone_main()
        ran = lambda: subprocess.run(["bash", "-c", test], cwd=copy, env=self.env, capture_output=True).returncode
        self.assertEqual(ran(), 1)  # on main: the agreed order has something to prove
        (copy / "fixed.txt").touch()
        self.assertEqual(ran(), 0)
        (copy / "test.sh").write_text("exit 3\n")
        self.assertEqual(ran(), 3)

    def test_tests_that_hang_get_a_quarter_of_the_night_and_are_a_finding_and_the_model_still_runs(self):
        self.gate("sleep 600")
        self.answering(CRASH)
        date = self.night_at(-12)
        record = self.night_now(date, 24)
        bound = record["tests"].get("bound")
        self.assertTrue(1 <= bound <= 6)  # a quarter of the 24 s night, of what is left of it when they start
        self.assertEqual(record["tests"], {"command": "./gate.sh", "exit": None, "bound": bound})
        hang = ("A fresh clone of main does not finish its own tests: `./gate.sh` runs past a quarter of the night.",
                f"perl -e '{BOUND}' {bound} bash -c ./gate.sh")
        self.assertEqual([(one["claim"], one["command"], one["exit"]) for one in record["findings"]],
                         [(*hang, None), (CRASH["claim"], CRASH["command"], 1)])
        self.assertIn(f"`./gate.sh` ran past its {bound} s, a quarter of the night: a finding, above.",
                      self.shown(f"routines/night-{date}.md"))
        # Its order's test is that run under that bound: on main its group is killed once the bound is reached.
        test = parse_order(self.shown(record["findings"][0]["order"]))[1]
        self.assertEqual(test, f"{hang[1]}\n")
        began, proc = time.monotonic(), subprocess.Popen(["bash", "-c", test], cwd=self.clone_main(), env=self.env,
                                                         start_new_session=True)
        try:
            self.assertEqual(proc.wait(timeout=60), 124)
        finally:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)  # anything the bound left
        self.assertGreater(time.monotonic() - began, bound - 1)

    def test_tests_that_ignore_signals_still_fail_their_orders_test_at_its_bound(self):
        self.gate("trap '' ALRM TERM INT\nsleep 10\nexit 0")  # they would pass, ten seconds on
        self.answering()
        record = self.night_now(self.night_at(-12), 8)
        bound = record["tests"]["bound"]
        test = parse_order(self.shown(record["findings"][0]["order"]))[1]
        began, proc = time.monotonic(), subprocess.Popen(["bash", "-c", test], cwd=self.clone_main(), env=self.env,
                                                         start_new_session=True)
        try:
            self.assertEqual(proc.wait(timeout=30), 124)  # its group killed from outside, not asked to stop
        finally:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
        self.assertTrue(bound - 1 < time.monotonic() - began < 10)

    def test_a_replay_that_hangs_is_unproven_and_each_finding_after_it_is_still_replayed(self):
        self.gate("")
        endless = {"claim": "The server never stops.", "command": "sleep 600"}
        self.answering(endless, CRASH)
        date = self.night_at(-12)
        record = self.night_now(date, 30)
        self.assertEqual([(one["claim"], one["exit"]) for one in record["findings"]], [(CRASH["claim"], 1)])
        self.assertEqual([{**one, "why": ""} for one in record["unproven"]], [{**endless, "exit": None, "why": ""}])
        share = int(re.fullmatch(r"its replay ran past its (\d+) s", record["unproven"][0]["why"])[1])
        self.assertTrue(1 <= share <= 15)  # half what was left, for two findings were still to replay
        self.assertIn(f"\n\n- The server never stops. `sleep 600`: its replay ran past its {share} s\n",
                      self.shown(f"routines/night-{date}.md").split("\n## Unproven: ", 1)[1])

    def test_a_replays_share_counts_the_places_left_so_the_most_harmful_finding_gets_its_time(self):
        self.gate("")
        slow = {"claim": "The import takes four seconds, then fails.", "command": "sleep 4; false"}
        self.answering(slow, *({"claim": f"Small thing {n} is off.", "command": f"exit {n}"} for n in range(1, 10)))
        record = self.night_now(self.night_at(-12), 40)  # nine findings past the three asked for share none
        self.assertEqual(([one["claim"] for one in record["findings"]], record["unproven"]),
                         ([slow["claim"], "Small thing 1 is off.", "Small thing 2 is off."], []))

    def test_a_finding_said_twice_is_one_unproven_or_kept_never_both(self):
        self.gate("")
        endless = {"claim": "The server never stops.", "command": "sleep 600"}
        self.answering(endless, {**endless, "command": "false"})
        record = self.night_now(self.night_at(-12), 20)
        self.assertEqual(([one["claim"] for one in record["unproven"]], record["findings"]), ([endless["claim"]], []))


class Origin(NightCase):
    def test_answers_wait_for_origin_then_are_recorded_once(self):
        self.answering(CRASH, {"claim": "Another failure.", "command": "false"})
        record = self.night(self.night_at(-12), self.tmp / "brief")
        answers, waiting = {}, {}
        for choice, finding in zip(("comment", "agree", "decline"), record["findings"]):
            slug = Path(finding["order"]).stem
            sent = self.ceo("answer", slug, choice, "--comment", f"Keep my {choice}.")
            self.assertEqual(sent.returncode, 0, sent.stderr)
            answer = self.repo / ".ceo" / "answers" / f"{slug}.json"
            row = self.repo / ".ceo" / "waiting" / answer.name
            answers[answer], waiting[row] = answer.read_bytes(), row.read_bytes()
        self.assertEqual(len(answers), 3)
        before = self.main()
        self.git("remote", "set-url", "origin", str(self.tmp / "away.git"))
        for _ in range(2):
            self.assertIn("is not saved:", self.idle_tick())
            for path, content in {**answers, **waiting}.items():
                self.assertTrue(path.exists(), f"the offline tick lost {path.name}")
                self.assertEqual(path.read_bytes(), content)
            self.assertEqual(self.git("rev-parse", "main"), before)
        self.git("remote", "set-url", "origin", str(self.origin))
        self.idle_tick()
        self.assertFalse(any(path.exists() for path in answers))
        comment, agree, decline = [finding["order"][:-3] for finding in record["findings"]]
        self.assertIn("Keep my comment.", self.shown(f"{comment}.comment"))
        self.assertEqual((f"{agree}.md" in self.files(), f"{agree[7:]}.md" in self.files()), (False, True))
        self.assertIn('Comment: "Keep my decline."', self.shown("routines/night-declined.md"))
        self.assertEqual([path for path in self.files() if path.startswith(decline)], [])
        self.assertEqual(self.waiting(), [Path(comment).name])
        self.assertIn("your comment: Keep my comment.", self.row(Path(comment).name)["detail"])
        recorded = self.main()
        self.idle_tick()
        self.assertEqual(self.main(), recorded, "an answer was recorded twice")

    def test_a_night_origin_cannot_take_is_recorded_here_first_said_so_and_pushed_by_a_later_tick(self):
        self.answering(CRASH)
        date, before = self.night_at(-12), self.main()
        self.git("remote", "set-url", "origin", str(self.tmp / "away.git"))  # origin is out of reach
        record = self.night(date, self.tmp / "brief")
        orders = [one["order"] for one in record["findings"]]
        self.assertEqual(([one["claim"] for one in record["findings"]], record["filed"], record["pushed"]),
                         ([GATE, CRASH["claim"]], True, False))
        self.assertIn("away.git", record["push"])
        # Recorded here first: main here and the folder hold the drafts and the note, which says so; each finding
        # waits for the owner's word; origin has nothing of it.
        self.assertEqual((self.main(), self.git("rev-parse", "main")), (before, record["recorded"]))
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertTrue(all((self.repo / order).is_file() for order in orders))
        self.assertIn("\n## Recorded here first\n", (self.repo / record["note"]).read_text())
        self.assertEqual(self.waiting(), sorted(Path(order).stem for order in orders))
        # Meanwhile origin's main moves on; no night starts while one is not on origin.
        other = self.tmp / "other"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)], env=self.env, check=True)
        (other / "elsewhere.txt").write_text("pushed from another machine\n")
        for step in (["add", "-A"], ["commit", "-qm", "elsewhere"], ["push", "-q", "origin", "main"]):
            subprocess.run(["git", *step], cwd=other, env=self.env, check=True)
        moved = self.main()
        self.night_at(14)
        refused = self.ceo("night", "--model", self.model_for(self.tmp / "brief"))
        self.assertEqual((refused.returncode, refused.stderr), (2, f"ceo: refused: the night of {date} is not on "
                                                                   "origin yet; the tick pushes it first.\n"))
        said = self.tick("--worker", self.model_for(self.tmp / "brief"))
        self.assertIn(f"ceo: the night of {date}: not on origin yet: ", said)
        self.assertNotIn("started the night", said)
        night = next(line for line in (self.repo / "STATUS.md").read_text().splitlines() if line.startswith("Night: "))
        self.assertIn(f"; not on origin yet: fatal: '{self.tmp / 'away.git'}' does not appear to be a git repository",
                      night)
        # Origin answers again: the next tick joins its main into the night's commit and pushes that.
        self.git("remote", "set-url", "origin", str(self.origin))
        self.night_at(14, starts=60, ends=120)
        self.assertIn(f"ceo: the night of {date}: pushed to origin.\n", self.tick())
        record, recorded = read_json(self.repo / ".ceo" / "night" / date / "result.json"), record["recorded"]
        self.assertEqual((record["pushed"], self.main(), self.git("rev-parse", "main")),
                         (True, record["recorded"], record["recorded"]))
        self.assertEqual(self.origin_git("rev-list", "--parents", "-n", "1", "main").stdout.split()[1:],
                         [moved, recorded])
        self.assertEqual(sorted(set(self.files()) - set(self.files(before))),
                         sorted([*orders, record["note"], "elsewhere.txt"]))
        status = (self.repo / "STATUS.md").read_text().splitlines()
        self.assertIn(f"Night: {date}, 2 findings, 2 new for your word: [{record['note']}]({record['note']})", status)
        self.assertEqual([line for line in status if line.startswith(f"- `{record['note']}` needs attention: the night "
                                                                      "could not push it to origin: ")], [status[-1]])
        self.assertEqual(self.tick().count("the night of"), 0)  # pushed once, told once

    def test_a_night_cut_once_its_commit_is_named_is_filed_whole_by_the_next_tick_before_its_push(self):
        self.gate("")
        self.answering(CRASH)
        date = self.night_at(-12)
        hook = self.repo / ".git" / "hooks" / "reference-transaction"  # a slow disk: main here moves slowly
        hook.write_text(f'#!/bin/sh\nin=$(cat)\nif [ "$1" = prepared ]; then\n'
                        f'  case "$in" in *" refs/heads/main"*) touch "{self.tmp}/in-move"; sleep 60;; esac\nfi\n')
        hook.chmod(0o755)
        night = subprocess.Popen([sys.executable, str(KERNEL / "ceo.py"), "night", "--model",
                                  self.model_for(self.tmp / "brief"), "--timeout", "30"], cwd=self.repo, env=self.env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.wait_for((self.tmp / "in-move").exists, "the night was moving main here")
        record = read_json(self.repo / ".ceo" / "night" / date / "result.json")
        self.assertEqual((bool(record["recorded"]), record["filed"]), (True, False))
        os.killpg(night.pid, signal.SIGTERM)  # cut between its commit and its waiting rows
        night.wait(timeout=30)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(night.pid, signal.SIGKILL)
        self.wait_for(lambda: gone(night.pid), "the stopped night's group is gone")
        hook.unlink()
        self.assertEqual(list((self.repo / ".git").glob("**/*.lock")), [])  # the git it waited for let go
        self.assertEqual((self.waiting(), self.git("rev-parse", "main")), ([], self.main()))
        # The next tick files it whole, then pushes it: main here, its waiting row, told once, and its Night line.
        self.assertIn(f"ceo: the night of {date}: pushed to origin.\n", self.tick())
        record = read_json(self.repo / ".ceo" / "night" / date / "result.json")
        slug = Path(record["findings"][0]["order"]).stem
        self.assertEqual((record["filed"], record["pushed"], self.git("rev-parse", "main")),
                         (True, True, record["recorded"]))
        self.assertEqual((self.main(), self.waiting(), self.told()), (record["recorded"], [slug], [slug]))
        self.assertIn(f"Night: {date}, 1 finding, 1 new for your word: [{record['note']}]({record['note']})",
                      (self.repo / "STATUS.md").read_text().splitlines())

    def test_a_night_cut_once_main_here_holds_it_is_filed_by_the_next_tick_without_a_second_commit(self):
        self.gate("")
        self.answering(CRASH, ideas=[IDEA])
        date, rows = self.night_at(-12), self.repo / ".ceo" / "waiting"
        rows.mkdir(parents=True)
        rows.chmod(0o555)  # no waiting row can be written: the night stops between main here and its rows
        try:
            self.assertNotEqual(self.ceo("night", "--model", self.model_for(self.tmp / "brief")).returncode, 0)
        finally:
            rows.chmod(0o755)
        record = read_json(self.repo / ".ceo" / "night" / date / "result.json")
        self.assertEqual((self.git("rev-parse", "main"), record["filed"]), (record["recorded"], False))
        (self.repo / "local.txt").write_text("the owner's work, on top of the night\n")
        self.git("add", "local.txt")
        self.git("commit", "-qm", "local work")
        self.assertIn(f"ceo: the night of {date}: pushed to origin.\n", self.tick())
        self.assertEqual(self.git("log", "--format=%s", "main").count(f"Night {date}:"), 1)
        self.assertIn(f"- Idea 2 in IDEAS.md: {IDEA['idea']}", (self.repo / record["note"]).read_text())
        self.assertEqual(self.waiting(), [Path(record["findings"][0]["order"]).stem])

    def test_a_night_cut_before_its_night_line_is_not_filed_and_the_next_tick_writes_the_line_before_its_push(self):
        self.gate("")
        self.answering(CRASH)
        date, blocker = self.night_at(-12), self.repo / ".STATUS.md.tmp"
        blocker.mkdir()  # STATUS.md cannot be written: the night stops between its waiting rows and its Night line
        self.assertNotEqual(self.ceo("night", "--model", self.model_for(self.tmp / "brief")).returncode, 0)
        blocker.rmdir()
        record = read_json(self.repo / ".ceo" / "night" / date / "result.json")
        self.assertEqual((bool(record["recorded"]), record["filed"], record["pushed"]), (True, False, False))
        self.assertEqual((len(self.waiting()), (self.repo / "STATUS.md").exists()), (1, False))
        hook, marker = self.repo / ".git" / "hooks" / "pre-push", self.tmp / "pushed-before-its-night-line"
        hook.write_text(f'#!/bin/sh\ngrep -q "^Night: " STATUS.md || touch "{marker}"\n')
        hook.chmod(0o755)
        self.assertIn(f"ceo: the night of {date}: pushed to origin.\n", self.tick())
        record = read_json(self.repo / ".ceo" / "night" / date / "result.json")
        self.assertEqual((record["filed"], record["pushed"], marker.exists()), (True, True, False))
        self.assertIn(f"Night: {date}, 1 finding, 1 new for your word: [{record['note']}]({record['note']})",
                      (self.repo / "STATUS.md").read_text().splitlines())

    def test_a_night_main_here_cannot_take_says_so_once_and_is_filed_once_it_can(self):
        self.gate("")
        self.answering(CRASH, ideas=[IDEA])
        date = self.night_at(-12)
        with open(self.repo / "IDEAS.md", "a") as ideas:
            ideas.write("\nThe owner's words, not committed yet.\n")  # the night's idea would overwrite them
        refused = self.ceo("night", "--model", self.model_for(self.tmp / "brief"))
        self.assertIn("main here cannot move to it: ", refused.stderr)
        note = f"routines/night-{date}.md"
        said = self.tick()
        self.assertIn(f"ceo: the night of {date}: not on origin yet: ", said)
        status = (self.repo / "STATUS.md").read_text().splitlines()
        self.assertEqual(len([line for line in status if line.startswith(
            f"- `{note}` needs attention: the night could not record it on main here: ")]), 1)
        self.assertEqual((self.waiting(), self.main()), ([], self.git("rev-parse", "main")))
        self.git("checkout", "--", "IDEAS.md")  # the owner puts his words elsewhere
        self.assertIn(f"ceo: the night of {date}: pushed to origin.\n", self.tick())
        record = read_json(self.repo / ".ceo" / "night" / date / "result.json")
        self.assertEqual((record["filed"], self.git("rev-parse", "main"), "unfiled" in record),
                         (True, self.main(), False))
        self.assertEqual(self.waiting(), [Path(record["findings"][0]["order"]).stem])

    def test_a_night_with_origin_out_of_reach_and_never_fetched_is_recorded_on_main_here(self):
        self.gate("")
        self.answering(CRASH)
        date = self.night_at(-12)
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        self.git("remote", "set-url", "origin", str(self.tmp / "away.git"))
        record = self.night_now(date, 20)
        self.assertEqual((record["filed"], record["pushed"], self.git("rev-parse", "main")),
                         (True, False, record["recorded"]))
        self.assertEqual(self.waiting(), [Path(record["findings"][0]["order"]).stem])
        self.assertIn("\n## Recorded here first\n", (self.repo / record["note"]).read_text())
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_a_night_with_main_here_ahead_of_origin_is_on_main_here_and_pushes_without_it(self):
        self.gate("")
        self.answering(CRASH)
        date = self.night_at(-12)
        (self.repo / "local.txt").write_text("the owner's work, not pushed\n")
        self.git("add", "local.txt")
        self.git("commit", "-qm", "local work")
        local = self.git("rev-parse", "main")
        self.git("remote", "set-url", "origin", str(self.tmp / "away.git"))
        record = self.night_now(date, 20)
        order = record["findings"][0]["order"]
        self.assertEqual((record["filed"], record["pushed"], self.git("rev-parse", "main")),
                         (True, False, record["recorded"]))
        self.assertTrue(all((self.repo / path).is_file() for path in (order, record["note"], "local.txt")))
        self.assertEqual(self.git("status", "--porcelain"), "")
        # Origin answers again: it gets the night alone, and the owner's commit stays his, here.
        self.git("remote", "set-url", "origin", str(self.origin))
        self.assertIn(f"ceo: the night of {date}: pushed to origin.\n", self.tick())
        self.assertEqual((order in self.files(), "local.txt" in self.files()), (True, False))
        self.git("merge-base", "--is-ancestor", local, "main")  # main here still holds it

    def test_a_record_made_while_a_night_waits_for_origin_pushes_the_night_first(self):
        self.answering(CRASH, ideas=[IDEA])
        date = self.night_at(-12)
        self.git("remote", "set-url", "origin", str(self.tmp / "away.git"))
        self.assertFalse(self.night(date, self.tmp / "brief")["pushed"])
        self.git("remote", "set-url", "origin", str(self.origin))  # back, and the owner's idea comes before a tick
        said = self.ceo("idea", "Ship", "on", "Fridays.")
        self.assertEqual((said.returncode, said.stdout), (0, "3\n"), said.stderr)  # after the night's idea, 2
        self.assertEqual(self.subjects()[:2], ["Idea 3", f"Night {date}: recorded here first"])
        self.assertEqual(re.findall(r"^## (\d+) — ", self.shown("IDEAS.md"), re.M), ["1", "2", "3"])
        self.assertTrue(read_json(self.repo / ".ceo" / "night" / date / "result.json")["pushed"])

    def test_a_tick_stopped_while_it_pushes_a_join_leaves_main_here_on_origins_main(self):
        self.answering(CRASH)
        date = self.night_at(-12)
        self.git("remote", "set-url", "origin", str(self.tmp / "away.git"))
        recorded = self.night(date, self.tmp / "brief")["recorded"]
        self.git("remote", "set-url", "origin", str(self.origin))
        other = self.tmp / "other"  # origin's main moves on meanwhile, so the next push is of a join
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)], env=self.env, check=True)
        (other / "elsewhere.txt").write_text("pushed from another machine\n")
        for step in (["add", "-A"], ["commit", "-qm", "elsewhere"], ["push", "-q", "origin", "main"]):
            subprocess.run(["git", *step], cwd=other, env=self.env, check=True)
        hook = self.origin / "hooks" / "pre-receive"  # a slow network: the push takes a while
        hook.write_text(f'#!/bin/sh\ntouch "{self.tmp}/in-push"\nsleep 60\n')
        hook.chmod(0o755)
        tick = subprocess.Popen([sys.executable, str(KERNEL / "ceo.py"), "tick"], cwd=self.repo,
                                env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        self.wait_for((self.tmp / "in-push").exists, "the tick pushed")
        os.killpg(tick.pid, signal.SIGTERM)  # stopped mid-push, as launchd stops its agent
        tick.wait(timeout=30)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(tick.pid, signal.SIGKILL)
        self.wait_for(lambda: gone(tick.pid), "the stopped tick's group is gone")
        hook.unlink()
        # Nothing here moved to a join origin never got: the next join, of another second, is another commit.
        self.assertEqual(self.git("rev-parse", "main"), recorded)
        self.assertIn(f"ceo: the night of {date}: pushed to origin.\n", self.tick())
        self.assertEqual(self.git("rev-parse", "main"), self.main())  # main here follows origin's, which holds it
        self.assertTrue((self.repo / "elsewhere.txt").is_file())

    def push_elsewhere(self, path, text):
        """Commit on origin's main, from another clone, path with text appended, as the owner does from his
        laptop."""
        other = self.tmp / "other"
        if not other.exists():
            subprocess.run(["git", "clone", "-q", str(self.origin), str(other)], env=self.env, check=True)
        with open(other / path, "a") as file:
            file.write(text)
        for step in (["pull", "-q", "--ff-only"], ["add", "-A"], ["commit", "-qm", f"{path}, elsewhere"],
                     ["push", "-q", "origin", "main"]):
            subprocess.run(["git", *step], cwd=other, env=self.env, check=True)

    def test_a_night_whose_ideas_meet_others_on_origin_is_built_anew_there_and_nights_go_on(self):
        self.answering(CRASH, ideas=[IDEA])
        date = self.night_at(-12)
        self.git("remote", "set-url", "origin", str(self.tmp / "away.git"))
        self.night(date, self.tmp / "brief")
        self.git("remote", "set-url", "origin", str(self.origin))
        self.push_elsewhere("IDEAS.md", "\n## 2 — 2026-10-09\n\nA loyalty card.\n")  # where the night put its own 2
        said = self.ceo("idea", "Ship", "on", "Fridays.")
        self.assertEqual((said.returncode, said.stdout), (0, "4\n"), said.stderr)
        ideas, record = self.shown("IDEAS.md"), read_json(self.repo / ".ceo" / "night" / date / "result.json")
        self.assertEqual(re.findall(r"^## (\d+) — ", ideas, re.M), ["1", "2", "3", "4"])
        self.assertRegex(ideas, rf"\n## 3 — [\d-]{{10}}\n\nFrom the night of {date}, ")
        self.assertEqual((record["pushed"], record["ideas"][0]["filed"]), (True, 3))
        self.assertIn(f"- Idea 3 in IDEAS.md: {IDEA['idea']}", self.shown(record["note"]))
        self.assertEqual(self.git("rev-parse", "main"), self.main())
        self.night(self.night_at(14), self.tmp / "brief-2")  # the next night runs

    def test_a_night_built_on_a_commit_origin_then_took_back_is_built_anew_without_it(self):
        self.answering(CRASH)
        before = self.main()
        (self.repo / "bad.txt").write_text("a commit the owner takes back\n")
        self.commit_all("bad")
        bad, date = self.main(), self.night_at(-12)
        self.git("remote", "set-url", "origin", str(self.tmp / "away.git"))
        note = self.night(date, self.tmp / "brief")["note"]  # built on bad
        subprocess.run(["git", "--git-dir", str(self.origin), "update-ref", "refs/heads/main", before], check=True)
        self.git("remote", "set-url", "origin", str(self.origin))  # back, and the owner took bad back meanwhile
        self.assertIn(f"ceo: the night of {date}: pushed to origin.\n", self.tick())
        self.assertEqual(("bad.txt" in self.files(), note in self.files()), (False, True))
        self.assertEqual(self.origin_git("merge-base", "--is-ancestor", bad, "main").returncode, 1)

    def test_a_tick_stopped_once_origin_has_a_night_built_anew_builds_it_no_second_time(self):
        self.answering(CRASH, ideas=[IDEA])
        before = self.main()
        (self.repo / "bad.txt").write_text("a commit the owner takes back\n")
        self.commit_all("bad")
        date = self.night_at(-12)
        self.git("remote", "set-url", "origin", str(self.tmp / "away.git"))
        note = self.night(date, self.tmp / "brief")["note"]
        subprocess.run(["git", "--git-dir", str(self.origin), "update-ref", "refs/heads/main", before], check=True)
        hook = self.origin / "hooks" / "post-receive"  # origin has the push before the tick hears so
        hook.write_text(f'#!/bin/sh\ncat >/dev/null\n[ -f "{self.tmp}/slow" ] && touch "{self.tmp}/in-push" && sleep 60\n'
                        'exit 0\n')
        hook.chmod(0o755)
        (self.tmp / "slow").touch()
        self.git("remote", "set-url", "origin", str(self.origin))
        tick = subprocess.Popen([sys.executable, str(KERNEL / "ceo.py"), "tick"], cwd=self.repo,
                                env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        self.wait_for((self.tmp / "in-push").exists, "origin took the night built anew")
        os.killpg(tick.pid, signal.SIGTERM)
        tick.wait(timeout=30)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(tick.pid, signal.SIGKILL)
        self.wait_for(lambda: gone(tick.pid), "the stopped tick's group is gone")
        (self.tmp / "slow").unlink()
        self.assertIn(f"ceo: the night of {date}: pushed to origin.\n", self.tick())
        self.assertEqual(sum(subject.startswith(f"Night {date}:") for subject in self.subjects()), 1)
        self.assertIn(f"- Idea 2 in IDEAS.md: {IDEA['idea']}", self.shown(note))

    def test_a_tick_stopped_as_main_here_follows_a_pushed_night_leaves_that_to_the_next_pass(self):
        self.answering(CRASH)
        self.git("checkout", "-q", "-b", "work")  # the owner works on a branch: main here moves by update-ref
        date = self.night_at(-12)
        self.git("remote", "set-url", "origin", str(self.tmp / "away.git"))
        recorded = self.night(date, self.tmp / "brief")["recorded"]
        self.git("remote", "set-url", "origin", str(self.origin))
        self.push_elsewhere("elsewhere.txt", "pushed from another machine\n")
        hook = self.repo / ".git" / "hooks" / "reference-transaction"  # a slow disk: main here moves slowly
        hook.write_text(f'#!/bin/sh\nin=$(cat)\nif [ "$1" = prepared ] && [ -f "{self.tmp}/slow" ]; then\n'
                        f'  case "$in" in *" refs/heads/main"*) touch "{self.tmp}/in-move"; sleep 60;; esac\nfi\n')
        hook.chmod(0o755)
        (self.tmp / "slow").touch()
        tick = subprocess.Popen([sys.executable, str(KERNEL / "ceo.py"), "tick"], cwd=self.repo,
                                env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        self.wait_for((self.tmp / "in-move").exists, "main here was moving to the pushed night")
        os.killpg(tick.pid, signal.SIGTERM)
        tick.wait(timeout=30)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(tick.pid, signal.SIGKILL)
        self.wait_for(lambda: gone(tick.pid), "the stopped tick's group is gone")
        (self.tmp / "slow").unlink()
        self.assertEqual(self.git("rev-parse", "main"), recorded)
        self.assertIn(f"ceo: the night of {date}: pushed to origin.\n", self.tick())
        self.assertEqual(self.git("rev-parse", "main"), self.main())


class Waiting(NightCase):
    def test_waiting_rows_carry_their_time_and_wait_oldest_first_whatever_the_hash_seed(self):
        self.gate("")
        self.answering(*({"claim": f"Finding {n} is real.", "command": f"exit {n}"} for n in (1, 2, 3)))
        record = self.night(self.night_at(-12), self.tmp / "brief")
        slugs = [Path(one["order"]).stem for one in record["findings"]]
        times = [read_json(self.repo / ".ceo" / "waiting" / f"{slug}.json")["time"] for slug in slugs]
        self.assertEqual((sorted(set(times)), len(times)), (times, 3))  # each written after the one before
        # status --json, which the ears read, lists them as written, under any hash seed.
        seen = {tuple(row["slug"] for row in json.loads(self.ceo(
            "status", "--json", env={**self.env, "PYTHONHASHSEED": str(seed)}).stdout)["rows"]) for seed in range(6)}
        self.assertEqual(seen, {tuple(slugs)})
        # The owner files the first by hand and removes the second: the next tick drops their rows; the third waits.
        drafts = [one["order"] for one in record["findings"]]
        self.git("mv", drafts[0], drafts[0][7:])
        self.git("rm", "-q", drafts[1])
        self.commit_all("one filed and one removed by hand")
        self.idle_tick()
        self.assertEqual(self.waiting(), slugs[2:])


if __name__ == "__main__":
    unittest.main()
