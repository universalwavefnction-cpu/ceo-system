"""The night runs the cleaner first: a pass of LESSONS.md when there is work since the last one, a lesson suggested or
an order merged while the page holds notes, its line in result.json and the morning note; else none, and nothing said.
Real git, a local bare repository as origin, real processes; only the model is a stand-in, which researches and finds
nothing in the night's read-only copy, and anywhere else is the cleaner, keeping each brief, or failing when told."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

CEO = Path(__file__).resolve().parent / "ceo.py"
SEEDED = (CEO.parent / "routines" / "lessons.md").read_text()
MODEL = r'''
import json, os, sys
keep, mode = sys.argv[1:]
brief = sys.stdin.read()
if os.path.exists("../night.log"):  # the night's read-only copy: the researcher
    json.dump({"summary": "Looked; found nothing.", "findings": [], "ideas": []}, open("../NIGHT.json", "w"))
    sys.exit(0)
open(os.path.join(keep, "cleaner"), "a").write(brief.replace("\n", " ") + "\n")
sys.exit(mode == "fail")
'''


class NightDream(unittest.TestCase):
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
        (self.repo / ".gitignore").write_text(".ceo/\n")
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

    def night(self, mode="keep"):
        """(its record, its morning note on origin's main) of tonight's night, run again: its folder is scratch."""
        shutil.rmtree(self.repo / ".ceo" / "night", ignore_errors=True)
        model = f"{sys.executable} {self.tmp / 'model.py'} {self.tmp} {mode}"
        done = subprocess.run([sys.executable, str(CEO), "night", "--model", model, "--timeout", "600"], cwd=self.repo,
                              capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        record = json.loads(next((self.repo / ".ceo" / "night").glob("*/result.json")).read_text())
        return record, self.git(self.origin, "show", f"main:{record['note']}")

    def passes(self):
        return [line for line in self.git(self.origin, "log", "--format=%s", "main").splitlines()
                if line.startswith("Lessons pass")]

    def cleaned(self):
        return len((self.tmp / "cleaner").read_text().splitlines()) if (self.tmp / "cleaner").exists() else 0

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


if __name__ == "__main__":
    unittest.main()
