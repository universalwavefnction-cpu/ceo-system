"""The inbox end to end: `ceo.py idea` and `ceo.py answer` with real git (a local bare repository as
origin), real processes and files; `ears/hermes.sh` from the shell. No model is called: the waiting
row is a CEO's stop put to the owner, with its choices, as the kernel writes it."""
import json
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path

import ceo
from test_door import STUB, DoorCase
from test_tick import CEO

EARS = Path(__file__).resolve().parent / "ears"
# Words as the owner types them: another script, a line break, a leading dash, spaces kept.
WORDS = "-- магазин: a shop that sells   one thing\nand ships it the same day  "


class InboxCase(DoorCase):
    def setUp(self):
        super().setUp()
        self.stub.write_text(STUB)
        self.home = Path(self.env["CEO_HOME"])
        self.today = datetime.now().astimezone().strftime("%Y-%m-%d")

    def idea(self, *words, repo=None):
        return subprocess.run([sys.executable, str(CEO), "idea", "--", *words], cwd=repo or self.repo,
                              env=self.env, capture_output=True, encoding="utf-8", timeout=120)

    def on_main(self, path, ref="main"):
        """The file at path in ref of the owner's folder, byte for byte."""
        return subprocess.run(["git", "show", f"{ref}:{path}"], cwd=self.repo, env=self.env, check=True,
                              capture_output=True, encoding="utf-8").stdout

    def asked(self, slug="061-shop"):
        """A stop the CEO put to the owner, waiting for his word with its choices; the project listed as a run lists it."""
        (self.repo / ".ceo").mkdir(exist_ok=True)
        ceo.wait_on(self.repo, slug, {"order": f"orders/{slug}.md", "title": "The page of the shop", "decision": "d1",
                                      "question": "Ship the page as it is?", "detail": "its CEO stopped",
                                      "choices": ceo.DECIDE_CHOICES})
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "projects.txt").write_text(f"{self.repo}\n")
        self.assertEqual([(row["slug"], row["state"]) for row in self.rows()], [(slug, "waiting")])
        return slug

    def rows(self):
        """The rows of `status --json`, alone or beside the agents at work."""
        found = json.loads(self.ceo("status", "--json").stdout)
        return found["rows"] if isinstance(found, dict) else found

    def answered(self, slug):
        return json.loads((self.repo / ".ceo" / "answers" / f"{slug}.json").read_text())

    def assert_refused(self, done):
        self.assertEqual(done.returncode, 2, done.stdout)
        self.assertRegex(done.stderr, r"^ceo: refused: [^\n]+\.\n$")


class Idea(InboxCase):
    def test_an_idea_is_kept_word_for_word_numbered_on_main_and_pushed_with_nothing_else_read(self):
        self.assertFalse(self.home.exists())
        first, second = self.idea(WORDS), self.idea("a second", "idea in two words")
        self.assertEqual((first.returncode, first.stdout, second.stdout), (0, "1\n", "2\n"), first.stderr)
        self.assertEqual((self.repo / "IDEAS.md").read_text(encoding="utf-8"),
                         f"# Ideas\n\n## 1 — {self.today}\n\n{WORDS}\n\n## 2 — {self.today}\n\n"
                         "a second idea in two words\n")
        self.assertEqual(self.git("log", "--format=%s", "-3").splitlines(), ["Idea 2", "Idea 1", "start"])
        for commit in ("main", "main~1"):  # each records the idea alone
            self.assertEqual(self.git("show", "--name-only", "--format=", commit), "IDEAS.md")
        self.assertEqual(self.main(), self.git("rev-parse", "main"))  # pushed: origin has both
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertFalse(self.home.exists(), "the idea read or made the owner's home")

    def test_an_idea_lands_on_a_merge_made_meanwhile_and_the_owners_open_work_stays(self):
        self.assertEqual(self.idea("one").stdout, "1\n")
        other = self.tmp / "other"  # the door merges on the server while the folder is not pulled yet
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)], env=self.env, check=True)
        (other / "merged.txt").write_text("merged by the door\n")
        for step in (["add", "-A"], ["commit", "-qm", "A merged order (#1)"], ["push", "-q"]):
            subprocess.run(["git", *step], cwd=other, env=self.env, check=True)
        merged, here = self.main(), self.git("rev-parse", "main")
        (self.repo / ".gitignore").write_text(".ceo/\nopen work\n")  # the owner's, uncommitted
        self.assertEqual(self.idea("two").stdout, "2\n")
        self.assertEqual(self.git("log", "--format=%s", "-2", "origin/main").splitlines(),
                         ["Idea 2", "A merged order (#1)"])
        self.assertEqual(self.git("rev-parse", "origin/main~1"), merged)
        self.assertEqual(self.on_main("IDEAS.md", "origin/main"),
                         f"# Ideas\n\n## 1 — {self.today}\n\none\n\n## 2 — {self.today}\n\ntwo\n")
        # main here stood behind origin: it waits for the tick's pull, and the owner's work is untouched.
        self.assertEqual(self.git("rev-parse", "main"), here)
        self.assertEqual(self.git("status", "--porcelain"), "M .gitignore")
        self.assertNotIn("two", (self.repo / "IDEAS.md").read_text())

    def test_without_origin_main_takes_it_and_the_owners_staged_work_and_branch_stay(self):
        self.git("remote", "remove", "origin")
        (self.repo / "staged.txt").write_text("staged by the owner\n")
        self.git("add", "staged.txt")
        self.assertEqual(self.idea("on main").stdout, "1\n")
        self.assertEqual(self.git("log", "--format=%s", "-1"), "Idea 1")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "staged.txt")
        self.assertIn("on main", (self.repo / "IDEAS.md").read_text())
        self.git("checkout", "-q", "-b", "elsewhere")
        before = self.folder()
        self.assertEqual(self.idea("from elsewhere").stdout, "2\n")
        after = self.folder()  # main moves; the branch the folder is on, its index and files stay
        self.assertEqual((after[:2], after[3:]), (before[:2], before[3:]))
        self.assertEqual(self.git("log", "--format=%s", "-1", "main"), "Idea 2")
        self.assertEqual(self.on_main("IDEAS.md").count("## "), 2)

    def test_ideas_sent_at_once_get_a_number_each(self):
        procs = [subprocess.Popen([sys.executable, str(CEO), "idea", f"idea {n}"], cwd=self.repo,
                                  env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for n in range(4)]
        numbers = sorted(int(proc.communicate(timeout=120)[0]) for proc in procs)
        self.assertEqual(numbers, [1, 2, 3, 4])
        ideas = self.on_main("IDEAS.md", "origin/main")
        self.assertEqual([ideas.count(f"## {n} — ") for n in numbers], [1, 1, 1, 1])
        self.assertEqual(sorted(line for line in ideas.splitlines() if line.startswith("idea ")),
                         [f"idea {n}" for n in range(4)])

    def test_two_folders_on_one_origin_send_ideas_at_once_and_none_is_numbered_twice(self):
        other = self.tmp / "other"  # a second folder of the project, as on another machine
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)], env=self.env, check=True)
        # Two ideas a folder: a push the other folder beat is refused at most twice, and built again.
        procs = [subprocess.Popen([sys.executable, str(CEO), "idea", f"from {folder.name} {n}"], cwd=folder,
                                  env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for folder in (self.repo, other) for n in range(2)]
        said = [proc.communicate(timeout=120) for proc in procs]
        self.assertEqual(sorted(int(out) for out, _ in said), [1, 2, 3, 4], said)
        self.git("fetch", "-q", "origin")
        ideas = self.on_main("IDEAS.md", "origin/main")
        self.assertEqual([ideas.count(f"## {n} — ") for n in range(1, 5)], [1, 1, 1, 1])
        self.assertEqual(len([line for line in ideas.splitlines() if line.startswith("from ")]), 4)

    def test_a_refused_idea_says_why_in_one_line_and_saves_nothing(self):
        self.assert_refused(self.idea("  \n "))
        hook = self.origin / "hooks" / "pre-receive"  # origin refuses every push, as a protected main does
        hook.write_text("#!/bin/sh\necho 'main is protected' >&2\nexit 1\n")
        hook.chmod(0o755)
        before, server = self.folder(), self.main()
        refused = self.idea("lost?")
        self.assert_refused(refused)
        self.assertIn("the idea is not saved", refused.stderr)
        self.assertIn("main is protected", refused.stderr)
        self.assertEqual((self.folder(), self.main()), (before, server))


class Answer(InboxCase):
    def test_an_answer_lands_where_the_tick_reads_it_and_any_other_row_or_choice_is_refused(self):
        slug = self.asked()
        for wrong in (("061-nothing", "cut"), (slug, "3"), (slug, "one"), (slug, "answer")):
            self.assert_refused(self.ceo("answer", *wrong))
        self.assertFalse((self.repo / ".ceo" / "answers").exists())
        sent = self.ceo("answer", slug, "cut", "--comment", "the light one reads better")
        self.assertEqual((sent.returncode, sent.stdout), (0, f"ceo: sent cut (cut) for {slug}; the "
                                                             "next tick takes it.\n"), sent.stderr)
        answer = self.answered(slug)
        self.assertEqual({key: answer[key] for key in ("pick", "choice", "comment")},
                         {"pick": "cut", "choice": "cut", "comment": "the light one reads better"})
        self.assertEqual(self.rows()[0]["answered"], "cut")
        self.tick("--worker", self.model("clean"), "--reviewer", self.model("clean"))  # read on the next tick
        self.assertEqual(self.on_main(f"orders/{slug}.cut", "origin/main"),
                         "Cut on the owner's word: the light one reads better.\n")
        self.assertFalse((self.repo / ".ceo" / "answers" / f"{slug}.json").exists())
        self.assertFalse((self.repo / ".ceo" / "waiting" / f"{slug}.json").exists())


class Ears(InboxCase):
    def test_hermes_sh_carries_an_idea_and_an_answer_by_the_projects_name(self):
        slug = self.asked()  # the repository listed in projects.txt; a gone one of its name first
        listed = self.home / "projects.txt"
        listed.write_text(f"{self.tmp / 'gone' / 'repo'}\n{listed.read_text()}")
        hermes = lambda *args: subprocess.run([str(EARS / "hermes.sh"), *args], env=self.env,
                                              capture_output=True, text=True, timeout=120)
        said = hermes("idea", "repo", WORDS)
        self.assertEqual((said.returncode, said.stdout), (0, "1\n"), said.stderr)
        self.assertIn(f"## 1 — {self.today}\n\n{WORDS}\n", self.on_main("IDEAS.md", "origin/main"))
        said = hermes("answer", "repo", slug, "answer", "the", "dark", "one")
        self.assertEqual(said.returncode, 0, said.stderr)
        self.assertEqual({key: self.answered(slug)[key] for key in ("pick", "choice", "comment")},
                         {"pick": "answer", "choice": "answer", "comment": "the dark one"})
        for wrong in (("idea", "nowhere", "words"), ("answer", "repo", slug, "9"), ("idea", "repo")):
            self.assertEqual(hermes(*wrong).returncode, 2, wrong)


if __name__ == "__main__":
    unittest.main()
