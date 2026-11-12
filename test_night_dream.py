"""The night runs the cleaner first: a pass of LESSONS.md when there is work since the last one, a lesson suggested or
an order merged while the page holds notes, its line in result.json and the morning note; else none, and nothing said.
Where ceo.toml's night is "", the cleaner runs alone, once a night, in the window of its `cleaner` (the night's own when
it names none): its pass, and no research. Real git, a local bare repository as origin, real processes; only the model is
a stand-in, which researches and finds nothing in the night's read-only copy, and anywhere else is the cleaner, keeping
each brief and writing each lesson it is handed into the page, or failing when told."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ceo import held

CEO = Path(__file__).resolve().parent / "ceo.py"
SEEDED = (CEO.parent / "routines" / "lessons.md").read_text()
MODEL = r'''
import json, os, re, sys
keep, mode = sys.argv[1:]
brief = sys.stdin.read()
if os.path.exists("../night.log"):  # the night's read-only copy: the researcher
    open(os.path.join(keep, "researched"), "a").write("x\n")
    json.dump({"summary": "Looked; found nothing.", "findings": [], "ideas": []}, open("../NIGHT.json", "w"))
    sys.exit(0)
open(os.path.join(keep, "cleaner"), "a").write(brief.replace("\n", " ") + "\n")
with open("LESSONS.md", "a") as page:  # each lesson it is handed
    page.writelines(found + "\n" for found in re.findall(r"^Lesson: (.+)$", brief, re.M))
sys.exit(mode == "fail")
'''
LESSON = "a scratch disk swings by a gigabyte, so read it twice"


class Scratch(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        (self.tmp / "model.py").write_text(MODEL)
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "CEO_HOME": str(self.tmp / "home"), "PYTHONDONTWRITEBYTECODE": "1", "GIT_AUTHOR_NAME": "t",
                    "GIT_AUTHOR_EMAIL": "t@t.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.invalid"}
        self.origin, self.repo = self.tmp / "origin.git", self.tmp / "p"
        self.git(self.tmp, "init", "-q", "--bare", str(self.origin))
        self.git(self.tmp, "init", "-q", "-b", "main", str(self.repo))
        at = datetime.now()
        (self.repo / ".gitignore").write_text(".ceo/\nSTATUS.md\n")
        (self.repo / "ceo.toml").write_text(f'disk_floor_gb = 0\nram_floor_gb = 0\nnight = "{at - timedelta(minutes=1):%H:%M}'
                                            f'-{at + timedelta(minutes=59):%H:%M}"\n')
        self.commit("start")
        self.git(self.repo, "remote", "add", "origin", str(self.origin))
        self.git(self.repo, "push", "-q", "origin", "main")

    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                              env=self.env).stdout.strip()

    def commit(self, message):
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-qm", message)

    def page(self, text):
        (self.repo / "LESSONS.md").write_text(text)
        self.commit("the page")
        self.git(self.repo, "push", "-q", "origin", "main")

    def merged(self, slug):
        """An order's work merged into main as the door merges it, a commit of two parents, its run suggesting nothing."""
        self.git(self.repo, "checkout", "-q", "-b", f"order/{slug}")
        (self.repo / f"{slug}.txt").write_text(f"{slug}\n")
        self.commit(slug)
        self.git(self.repo, "checkout", "-q", "main")
        self.git(self.repo, "merge", "-q", "--no-ff", "-m", f"{slug} (#1)", f"order/{slug}")
        self.git(self.repo, "push", "-q", "origin", "main")

    def passes(self):
        return [line for line in self.git(self.origin, "log", "--format=%s", "main").splitlines()
                if line.startswith("Lessons pass")]

    def cleaned(self):
        return len((self.tmp / "cleaner").read_text().splitlines()) if (self.tmp / "cleaner").exists() else 0


class NightDream(Scratch):
    def night(self, mode="keep"):
        """(its record, its morning note on origin's main) of tonight's night, run again: its folder is scratch."""
        shutil.rmtree(self.repo / ".ceo" / "night", ignore_errors=True)
        model = f"{sys.executable} {self.tmp / 'model.py'} {self.tmp} {mode}"
        done = subprocess.run([sys.executable, str(CEO), "night", "--model", model, "--timeout", "600"], cwd=self.repo,
                              capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        record = json.loads(next((self.repo / ".ceo" / "night").glob("*/result.json")).read_text())
        return record, self.git(self.origin, "show", f"main:{record['note']}")

    def test_an_order_merged_onto_a_page_of_notes_is_work_once_and_its_line_is_in_the_morning_note(self):
        self.page(SEEDED + "A tested rule, and how it was proven.\n")
        self.merged("001-work")
        record, note = self.night()
        self.assertEqual((self.cleaned(), len(self.passes()), record["reason"]), (1, 1, ""))
        self.assertRegex(record["dream"], r"^LESSONS\.md: 4 lines, then 4 \(0 % removed\); .*lessons suggested: 0; "
                                          r"applied on main$")
        self.assertIn(f"\nThe cleaner: {record['dream']}.\n", note)
        self.assertEqual(record["summary"], "Looked; found nothing.")  # the research ran after it
        record, note = self.night()  # nothing merged since the pass: no pass, and nothing said of one
        self.assertEqual((self.cleaned(), len(self.passes()), record["dream"]), (1, 1, ""))
        self.assertNotIn("The cleaner", note)

    def test_a_page_only_seeded_is_no_work_and_one_seeded_from_an_older_paragraph_holds_notes(self):
        self.merged("001-work")  # and no page at all
        self.assertEqual((self.night()[0]["dream"], self.cleaned()), ("", 0))
        self.page(SEEDED)
        self.merged("002-work")
        self.assertEqual((self.night()[0]["dream"], self.cleaned(), self.passes()), ("", 0, []))
        self.page(SEEDED.replace("the goals of now", "the goals of today"))  # as an older kernel seeded it
        self.assertEqual((self.night()[0]["dream"] != "", self.cleaned(), len(self.passes())), (True, 1, 1))

    def test_a_pass_refused_says_why_and_the_research_goes_on(self):
        self.page(SEEDED + "A tested rule, and how it was proven.\n")
        self.merged("001-work")
        record, note = self.night("fail")
        self.assertEqual(record["dream"], "no pass of LESSONS.md: the model failed (1), so nothing changed: "
                                          ".ceo/dream/dream.log")
        self.assertIn(f"\nThe cleaner: {record['dream']}.\n", note)
        self.assertEqual((self.passes(), record["reason"], record["summary"]), ([], "", "Looked; found nothing."))
        self.assertEqual(self.night()[0]["dream"][-len("applied on main"):], "applied on main")  # still work: no pass since


class CleanerAlone(Scratch):
    """Where `night` is "" the tick starts the cleaner's pass once a night, in the window of `cleaner`, and no research."""
    def at(self, hour, night, cleaner=None):
        """It is `hour` o'clock in the project's time zone, by the zone alone, and ceo.toml (committed) says night and,
        unless None, cleaner."""
        east = (hour - datetime.now(timezone.utc).hour + 12) % 24 - 12  # hours east of UTC, within half a day either way
        self.env["TZ"] = f"CLEANER{-east:+d}"  # POSIX counts the hours west of UTC
        (self.repo / "ceo.toml").write_text(f'disk_floor_gb = 0\nram_floor_gb = 0\nnight = "{night}"\n'
                                            + ("" if cleaner is None else f'cleaner = "{cleaner}"\n'))
        self.page(SEEDED)

    def suggest(self, slug, lesson):
        """A finished run whose worker suggested lesson, in its own log, where the cleaner reads it."""
        run = self.repo / ".ceo" / "runs" / slug
        run.mkdir(parents=True)
        (run / "worker.log").write_text(f"== ceo 2026-01-02T03:00:00+00:00: worker starts: worker\nLesson: {lesson}\n")
        (run / "result.json").write_text(json.dumps({"order": f"orders/{slug}.md", "state": "failed",
                                                     "log": f".ceo/runs/{slug}/worker.log",
                                                     "ended": datetime.now(timezone.utc).isoformat()}))

    def command(self, *args):
        model = f"{sys.executable} {self.tmp / 'model.py'} {self.tmp} keep"
        return subprocess.run([sys.executable, str(CEO), *args[:1], "--" + ("worker" if args[0] == "tick" else "model"), model,
                               *args[1:]], cwd=self.repo, capture_output=True, text=True, env=self.env, timeout=120)

    def tick(self):
        """One pass of the real tick: what it printed, once the night it started has ended and let go of its lock."""
        done = self.command("tick")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        until = time.monotonic() + 120
        while "ceo: started the night\n" in done.stdout and (held(self.repo / ".ceo" / "locks" / "night.lock") or not [
                one for one in (self.repo / ".ceo" / "night").glob("*/result.json") if json.loads(one.read_text())["ended"]]):
            self.assertLess(time.monotonic(), until, "the night the tick started never let go")
            time.sleep(0.2)
        return done.stdout

    def nothing_starts(self):
        self.assertNotIn("started the night", self.tick())
        self.assertEqual((self.cleaned(), self.passes()), (0, []))
        self.assertFalse((self.repo / ".ceo" / "night").exists())

    def test_a_night_that_is_off_gets_one_pass_at_three_o_clock_the_lesson_on_origins_page_and_no_research(self):
        self.at(3, "")
        self.suggest("001-work", LESSON)
        self.assertIn("ceo: started the night\n", self.tick())
        self.assertEqual((self.cleaned(), len(self.passes())), (1, 1))
        self.assertIn(f"Lesson: {LESSON}", (self.tmp / "cleaner").read_text())
        self.assertIn(LESSON, self.git(self.origin, "show", "main:LESSONS.md"))
        self.assertFalse((self.tmp / "researched").exists())
        self.assertEqual([one for one in self.git(self.origin, "ls-tree", "-r", "--name-only", "main").splitlines()
                          if one.startswith("routines/night-")], [])  # no morning note
        (folder,) = (self.repo / ".ceo" / "night").iterdir()
        record = json.loads((folder / "result.json").read_text())
        self.assertRegex(record["dream"], r"applied on main$")
        self.assertEqual((record["summary"], record["recorded"], record["filed"], record["reason"]), ("", None, False, ""))
        self.assertEqual([one.name for one in folder.iterdir()], ["result.json"])  # no night.log, no clone, no tests
        self.suggest("002-more", "a second one")  # once a night: the next ticks, and the night itself, start no second
        for _ in range(2):
            self.assertNotIn("started the night", self.tick())
        self.assertEqual((self.cleaned(), len(self.passes())), (1, 1))
        refused = self.command("night")
        self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertRegex(refused.stderr, r"the night of \d{4}-\d\d-\d\d has run: \.ceo/night/")
        status = (self.repo / "STATUS.md").read_text()  # the status line knows only a night that filed its note
        self.assertNotIn("Night:", status)
        self.assertNotIn("needs attention", status)

    def test_its_one_line_says_there_is_no_night_and_what_the_pass_said_or_that_there_was_no_work(self):
        self.at(3, "")
        done = self.command("night")
        self.assertEqual((done.returncode, done.stdout), (0, "ceo: no night in ceo.toml, only the cleaner: "
                                                             "no work since its last pass.\n"), done.stderr)
        self.assertEqual(self.cleaned(), 0)
        shutil.rmtree(self.repo / ".ceo" / "night")
        self.suggest("001-work", LESSON)
        done = self.command("night")
        self.assertRegex(done.stdout, r"^ceo: no night in ceo\.toml, only the cleaner: LESSONS\.md: .*applied on main\.\n$")
        self.assertEqual((done.returncode, self.cleaned(), len(self.passes())), (0, 1, 1), done.stderr)
        self.assertFalse((self.tmp / "researched").exists())

    def test_nothing_starts_at_noon_and_the_night_itself_refuses_naming_the_cleaners_window(self):
        self.at(12, "")
        self.suggest("001-work", LESSON)
        self.nothing_starts()
        refused = self.command("night")
        self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertIn("it is outside the cleaner's window, \"02:00-06:00\" in ceo.toml, in local time", refused.stderr)

    def test_a_project_with_its_own_night_window_gets_nothing_outside_it_and_never_reads_the_cleaner(self):
        self.at(3, "14:00-15:00", "02:00-06:00")  # the cleaner's window holds the moment, which a night that is on ignores
        self.suggest("001-work", LESSON)
        self.nothing_starts()
        self.at(3, "14:00-15:00", "later")  # nor does a value of it that is no window refuse anything
        self.assertNotIn("no night", self.tick())

    def test_a_night_that_is_on_keeps_its_pass_in_the_night_whatever_the_cleaner_says(self):
        self.at(3, "02:00-06:00", "")
        self.suggest("001-work", LESSON)
        done = self.command("night")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual((self.cleaned(), len(self.passes())), (1, 1))
        self.assertTrue((self.tmp / "researched").exists())  # the research ran, after the pass

    def test_both_off_nothing_starts_at_three_o_clock(self):
        self.at(3, "", "")
        self.suggest("001-work", LESSON)
        self.nothing_starts()
        refused = self.command("night")
        self.assertEqual(refused.returncode, 2, refused.stderr)

    def test_the_cleaner_runs_in_its_own_hour_alone_and_not_outside_it(self):
        self.at(12, "", "11:00-13:00")
        self.suggest("001-work", LESSON)
        self.assertIn("ceo: started the night\n", self.tick())
        self.assertEqual((self.cleaned(), len(self.passes())), (1, 1))
        self.assertFalse((self.tmp / "researched").exists())
        self.at(3, "", "11:00-13:00")
        shutil.rmtree(self.repo / ".ceo" / "night")
        self.suggest("002-more", "a second one")
        self.assertNotIn("started the night", self.tick())
        self.assertFalse((self.repo / ".ceo" / "night").exists())
        self.assertEqual((self.cleaned(), len(self.passes())), (1, 1))

    def test_a_cleaner_that_is_no_window_starts_nothing_and_says_so_naming_the_key(self):
        self.at(3, "", "later")
        out = self.tick()
        self.assertIn("ceo: no night: ceo.toml's cleaner must be two different local times", out)
        self.assertNotIn("started the night", out)
        refused = self.command("night")
        self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertIn("ceo.toml's cleaner must be two different local times", refused.stderr)
        self.assertFalse((self.repo / ".ceo" / "night").exists())


if __name__ == "__main__":
    unittest.main()
