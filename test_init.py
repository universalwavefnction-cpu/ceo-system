"""ceo.py init end to end: real git, real files and a real run, in scratch repositories under a scratch
CEO_HOME. Only the worker is replaced, by a stand-in."""
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ceo import settings

KERNEL = Path(__file__).resolve().parent
CEO = KERNEL / "ceo.py"
FILES = [".gitignore", "CEO.md", "LESSONS.md", "VISION.md", "ceo.toml", "orders/README.md"]  # as git lists a commit's paths
# The stand-in worker builds what the first order asks for, and commits it.
WORKER = '''import subprocess, sys
sys.stdin.buffer.read()
open("built", "w").write("built\\n")
subprocess.run(["git", "add", "built"], check=True)
subprocess.run(["git", "commit", "-qm", "build"], check=True)
'''
FIRST = """# The first order

## Goal
Build it.

## Done when
```bash
test -f built
```

## Not asked
Anything else.
"""


class Init(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        # The owner's own git config, hooks and CEO_HOME must not reach a scratch repository, and no
        # repository above the scratch folder may be found from it.
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "Stub", "GIT_AUTHOR_EMAIL": "stub@example.invalid",
                    "GIT_COMMITTER_NAME": "Stub", "GIT_COMMITTER_EMAIL": "stub@example.invalid",
                    "GIT_CEILING_DIRECTORIES": str(self.tmp), "CEO_HOME": str(self.tmp / "ceo"),
                    "CLAUDE_CONFIG_DIR": str(self.tmp / "claude")}  # nor the owner's Claude Code folder
        self.repo = self.project("portal", "node_modules/")  # the owner's, with no last newline

    def project(self, name, gitignore=None):
        """A repository on the branch trunk with one commit of the owner's work."""
        repo = self.tmp / name
        repo.mkdir()
        self.git("init", "-q", "-b", "trunk", cwd=repo)  # the current branch, whatever its name
        if gitignore is not None:
            (repo / ".gitignore").write_text(gitignore)
        (repo / "app.txt").write_text("the owner's work\n")
        self.git("add", "-A", cwd=repo)
        self.git("commit", "-qm", "the owner's work", cwd=repo)
        return repo

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def status(self):
        return subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=self.repo,
                              env=self.env, check=True, capture_output=True, text=True).stdout.splitlines()

    def ceo(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(CEO), *args], cwd=cwd or self.repo, env=self.env,
                              capture_output=True, encoding="utf-8", timeout=120)

    def state(self):
        """HEAD, the files at the top and git status: what a refused init must leave as it was."""
        return self.git("rev-parse", "HEAD"), sorted(os.listdir(self.repo)), self.status()

    def committed(self):
        return self.git("show", "--name-only", "--format=", "HEAD").splitlines()

    def listed(self):
        path = self.tmp / "ceo" / "projects.txt"
        return path.read_text().splitlines() if path.exists() else []

    def assert_refused(self, done):
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertRegex(done.stderr, r"^ceo: refused: [^\n]+\.\n$")
        self.assertEqual(done.stdout, "")

    def test_a_repository_gets_the_files_in_one_commit_on_its_branch_and_is_listed(self):
        before = self.git("rev-parse", "HEAD")
        # The owner's work in progress, which the commit leaves as it is: a change staged, a file
        # untracked, a line of their own in .gitignore.
        (self.repo / "app.txt").write_text("the owner's next work\n")
        self.git("add", "app.txt")
        (self.repo / "notes.txt").write_text("not for git yet\n")
        (self.repo / ".gitignore").write_text("node_modules/\nsecret.env")
        done = self.ceo("init", str(self.repo), cwd=self.tmp)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr, "")  # no CLAUDE.md, no warning
        vision = [line for line in (self.repo / "VISION.md").read_text().splitlines() if line]
        self.assertEqual(vision, ["# Vision", "## What this project is for", "## What done looks like",
                                  "## What it must never do"])
        guide = (self.repo / "orders" / "README.md").read_text()
        self.assertEqual(guide, (KERNEL / "orders" / "README.md").read_text())
        self.assertEqual(len(guide.splitlines()), 10)
        self.assertEqual((self.repo / "LESSONS.md").read_text(), (KERNEL / "routines" / "lessons.md").read_text())
        self.assertEqual((self.repo / "CEO.md").read_text(), (KERNEL / "CEO.md").read_text())
        self.assertEqual(settings(self.repo), {"name": "portal"})
        self.assertEqual((self.repo / ".gitignore").read_text(), "node_modules/\nsecret.env\n.ceo/\n")
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD"), "trunk")
        self.assertEqual(self.git("rev-parse", "HEAD^"), before)
        self.assertEqual(self.committed(), FILES)
        self.assertEqual(self.git("show", "HEAD:.gitignore"), "node_modules/\n.ceo/")
        self.assertEqual(self.status(), [" M .gitignore", "M  app.txt", "?? notes.txt"])
        self.assertEqual(self.listed(), [str(self.repo)])
        steps = [line for line in done.stdout.splitlines() if line[:2] in ("1.", "2.", "3.")]
        self.assertEqual(len(steps), 3, done.stdout)
        for step, words in ((steps[0], ["VISION.md"]), (steps[2], ["tick", "keep"])):
            for word in words:
                self.assertIn(word, step)
        # The first order is the owner's to write as a draft and to file by where it lives: nothing else.
        self.assertEqual(steps[1], "2. Write the first order, as orders/README.md says, a draft in "
                                   "drafts/orders/001-<name>.md, and file it: move it into orders/ and commit.")
        self.assertEqual(sorted(path.name for path in (self.repo / "orders").iterdir()), ["README.md"])
        self.assertFalse((self.repo / "drafts").exists())

    def test_a_second_init_refuses_and_changes_nothing(self):
        (self.repo / "src").mkdir()
        self.assertEqual(self.ceo("init", cwd=self.repo / "src").returncode, 0)  # the repository here
        self.assertEqual(self.committed(), FILES)
        head, listed = self.git("rev-parse", "HEAD"), self.listed()
        (self.repo / "VISION.md").write_text("# Vision\n\nThe owner's words.\n")
        self.assert_refused(self.ceo("init"))
        self.assertEqual((self.repo / "VISION.md").read_text(), "# Vision\n\nThe owner's words.\n")
        self.assertEqual((self.git("rev-parse", "HEAD"), self.listed()), (head, listed))

    def test_no_repository_top_and_no_branch_refuse_and_nothing_is_written(self):
        plain, inside = self.tmp / "plain", self.repo / "src"
        plain.mkdir()
        inside.mkdir()
        for name, args, cwd in (("a plain folder", [str(plain)], self.tmp),
                                ("the plain folder here", [], plain),
                                ("a folder inside a repository", [str(inside)], self.tmp),
                                ("no folder at all", [str(self.tmp / "nowhere")], self.tmp)):
            with self.subTest(name):
                self.assert_refused(self.ceo("init", *args, cwd=cwd))
                self.assertEqual((os.listdir(plain), os.listdir(inside)), ([], []))
                self.assertFalse((self.repo / "VISION.md").exists())
                self.assertFalse((self.tmp / "nowhere").exists())
                self.assertEqual(self.listed(), [])
        with self.subTest("a detached HEAD, no branch to commit on"):
            self.git("checkout", "-q", "--detach")
            self.assert_refused(self.ceo("init"))
            self.assertEqual(self.status(), [])
            self.assertFalse((self.repo / "VISION.md").exists())

    def test_a_commit_git_refuses_keeps_nothing_and_init_runs_again(self):
        hook = self.repo / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text("#!/bin/sh\necho no commits today >&2\nexit 1\n")
        hook.chmod(0o755)
        before = (self.git("rev-parse", "HEAD"), sorted(os.listdir(self.repo)),
                  (self.repo / ".gitignore").read_bytes(), self.status())
        done = self.ceo("init")
        self.assert_refused(done)
        self.assertIn("no commits today", done.stderr)
        self.assertEqual((self.git("rev-parse", "HEAD"), sorted(os.listdir(self.repo)),
                          (self.repo / ".gitignore").read_bytes(), self.status()), before)
        self.assertEqual(self.listed(), [])
        hook.unlink()  # the owner deals with what git said
        self.assertEqual(self.ceo("init").returncode, 0)
        self.assertEqual(self.committed(), FILES)

    def test_git_midway_through_something_refuses_and_its_state_stays_the_owners(self):
        # A merge or a pick stopped at a conflict would fold into init's commit (a second parent, the
        # pick's author), and an index another git process holds would never learn of the commit.
        self.git("checkout", "-q", "-b", "feature")
        (self.repo / "app.txt").write_text("the feature\n")
        self.git("commit", "-qam", "the feature")
        self.git("checkout", "-q", "trunk")
        (self.repo / "app.txt").write_text("the trunk\n")
        self.git("commit", "-qam", "the trunk")
        lock = self.repo / ".git" / "index.lock"
        for name, start, stop in (("a merge", ["merge", "feature"], ["merge", "--abort"]),
                                  ("a cherry-pick", ["cherry-pick", "feature"], ["cherry-pick", "--abort"]),
                                  ("another git process's index", None, None)):
            with self.subTest(name):
                if start:  # stops at the conflict in app.txt
                    subprocess.run(["git", *start], cwd=self.repo, env=self.env, capture_output=True)
                else:
                    lock.touch()
                before = self.state()
                self.assert_refused(self.ceo("init"))
                self.assertEqual(self.state(), before)
                self.assertEqual(self.listed(), [])
                if stop:
                    self.git(*stop)  # git's own state, still there to finish or abort
                else:
                    lock.unlink()
        self.assertEqual(self.ceo("init").returncode, 0)
        self.assertEqual(self.committed(), FILES)
        self.assertEqual(self.status(), [])

    def test_a_message_git_prepared_for_the_owners_commit_refuses_and_stays(self):
        # A squash merge or a pick not committed leaves no _HEAD, only the message git prepared for the
        # owner's own commit, which any commit, init's too, makes git delete.
        self.git("checkout", "-q", "-b", "feature")
        (self.repo / "feature.txt").write_text("the feature\n")
        self.git("add", "feature.txt")
        self.git("commit", "-qm", "the feature")
        self.git("checkout", "-q", "trunk")
        for start, message in ((["merge", "--squash", "feature"], "SQUASH_MSG"),
                               (["cherry-pick", "-n", "feature"], "MERGE_MSG")):
            with self.subTest(message):
                self.git(*start)  # applied cleanly, waiting to be committed
                prepared = (self.repo / ".git" / message).read_text()
                before = self.state()
                self.assert_refused(self.ceo("init"))
                self.assertEqual(self.state(), before)
                self.assertEqual((self.repo / ".git" / message).read_text(), prepared)
                self.assertEqual(self.listed(), [])
                self.git("reset", "-q", "--hard")  # the owner drops it, message and all
        self.assertEqual(self.ceo("init").returncode, 0)
        self.assertEqual(self.committed(), FILES)
        self.assertEqual(self.status(), [])

    def test_an_index_held_past_the_commit_is_said_with_the_command_that_mends_it(self):
        hook = self.repo / ".git" / "hooks" / "post-commit"  # another process takes the index meanwhile
        hook.parent.mkdir(exist_ok=True)
        hook.write_text("#!/bin/sh\ntouch .git/index.lock\n")
        hook.chmod(0o755)
        done = self.ceo("init")
        self.assertEqual(done.returncode, 0, done.stderr)
        mend = r"`git reset -q -- VISION\.md orders/README\.md CEO\.md LESSONS\.md ceo\.toml \.gitignore` here"
        self.assertRegex(done.stderr,
                         rf"^ceo: warning: committed, but [^\n]*index\.lock[^\n]*{mend}[^\n]*\n$")
        self.assertEqual(self.committed(), FILES)
        (self.repo / ".git" / "index.lock").unlink()
        self.git("reset", "-q", "--", "VISION.md", "orders/README.md", "CEO.md", "LESSONS.md", "ceo.toml", ".gitignore")
        self.assertEqual(self.status(), [])

    def test_what_cannot_be_written_keeps_nothing_and_init_runs_again(self):
        (self.repo / "orders").write_text("the owner's file, no folder\n")
        before = self.state()
        self.assert_refused(self.ceo("init"))
        self.assertEqual(self.state(), before)
        self.assertEqual(self.listed(), [])
        (self.repo / "orders").rename(self.repo / "orders.txt")
        self.assertEqual(self.ceo("init").returncode, 0)
        self.assertEqual(self.committed(), FILES)

    def test_a_name_holding_a_line_separator_reads_back_whole(self):
        repo = self.project("north\u2028portal")  # Python's splitlines breaks there; JSON does not escape it
        self.assertEqual(self.ceo("init", str(repo), cwd=self.tmp).returncode, 0)
        self.assertEqual(settings(repo), {"name": "north\u2028portal"})

    def test_ceo_is_ignored_whatever_gitignore_held(self):
        for name, held, after in (("no .gitignore", None, ".ceo/\n"), ("the line", "/.ceo\n", "/.ceo\n"),
                                  ("a line with a space before it", " .ceo/\n", " .ceo/\n.ceo/\n")):
            with self.subTest(name):
                repo = self.project(name.replace(" ", "-"), held)
                self.assertEqual(self.ceo("init", str(repo), cwd=self.tmp).returncode, 0)
                self.assertEqual((repo / ".gitignore").read_text(), after)
                (repo / ".ceo").mkdir()
                (repo / ".ceo" / "run").write_text("")
                self.assertEqual(self.git("status", "--porcelain", cwd=repo), "")

    def test_the_owners_own_files_stay_as_they_are(self):
        (self.repo / "ceo.toml").write_text('name = "the owner\'s"\ndisk_floor_gb = 0\n')
        (self.repo / ".gitignore").write_text("/.ceo\n")
        (self.repo / "LESSONS.md").write_text("The owner's own lessons.\n")
        (self.repo / "CEO.md").write_text("# The owner's own CEO\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "the owner's settings")
        done = self.ceo("init")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(settings(self.repo), {"name": "the owner's", "disk_floor_gb": 0})
        self.assertEqual((self.repo / ".gitignore").read_text(), "/.ceo\n")
        self.assertEqual((self.repo / "LESSONS.md").read_text(), "The owner's own lessons.\n")
        self.assertEqual((self.repo / "CEO.md").read_text(), "# The owner's own CEO\n")
        self.assertEqual(self.committed(), ["VISION.md", "orders/README.md"])

    def test_every_file_binding_a_worker_is_read_and_only_whole_words_count(self):
        (self.repo / "CLAUDE.md").write_text("This is chiefly a portal: screw nothing up.\n")
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "CLAUDE.md").write_text("The crews build; post with desk-post.\n")
        (self.repo / "AGENTS.md").write_text("Codex works under the herdr Chief.\n")
        done = self.ceo("init")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr, "ceo: warning: every worker here is bound by .claude/CLAUDE.md and "
                                      "AGENTS.md, written for another agent system (crew, desk-post, herdr, "
                                      "chief); rewrite before the first order.\n")

    def test_a_claude_md_of_another_system_is_warned_of_and_a_first_order_runs(self):
        claude = "Every change goes through the herdr chief; its crew builds it.\n"
        (self.repo / "CLAUDE.md").write_text(claude)
        self.git("add", "CLAUDE.md")
        self.git("commit", "-qm", "the other system's file")
        done = self.ceo("init")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(done.stderr, r"^ceo: warning: [^\n]*CLAUDE\.md[^\n]*herdr[^\n]*"
                                      r"rewrite before the first order\.\n$")
        self.assertEqual((self.repo / "CLAUDE.md").read_text(), claude)  # rewriting it is the owner's
        self.assertEqual(self.committed(), FILES)
        # The first order, with the floors at zero: the machine's free space is not under test here.
        with open(self.repo / "ceo.toml", "a") as conf:
            conf.write("disk_floor_gb = 0\nram_floor_gb = 0\n")
        (self.repo / "orders" / "001-first.md").write_text(FIRST)
        self.git("add", "-A")
        self.git("commit", "-qm", "the first order")
        (self.tmp / "worker.py").write_text(WORKER)
        worker = shlex.join([sys.executable, str(self.tmp / "worker.py")])
        run = self.ceo("run", "orders/001-first.md", "--worker", worker)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads((self.repo / ".ceo" / "runs" / "001-first" / "result.json").read_text())
        self.assertEqual((result["state"], result["worker"], result["worker_exit"], result["done_when_exit"]),
                         ("done", worker, 0, 0))
        events = (self.repo / ".ceo" / "events.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(line)["event"] for line in events], ["started", "done"])
        self.assertEqual(self.listed(), [str(self.repo)])
        # What the run made stays out of git: init's line in .gitignore.
        self.assertEqual(self.status(), [])

    def test_init_suggests_keeping_claude_codes_transcripts_90_days_and_never_writes_its_settings(self):
        home, config = self.tmp / "home", self.tmp / "claude"
        settings_file, theirs = home / ".claude" / "settings.json", b'{\n  "theme": "dark"\n}\n'
        settings_file.parent.mkdir(parents=True)
        settings_file.write_bytes(theirs)
        config.mkdir()
        # CLAUDE_CONFIG_DIR empty is unset: Claude Code's folder is ~/.claude, under HOME.
        for repo, folder, more in ((self.repo, home / ".claude", {"CLAUDE_CONFIG_DIR": ""}),
                                   (self.project("blog"), config, {"CLAUDE_CONFIG_DIR": str(config)})):
            self.env.update(HOME=str(home), **more)
            done = self.ceo("init", cwd=repo)
            self.assertEqual((done.returncode, done.stderr), (0, ""))
            lines = done.stdout.splitlines()
            self.assertEqual([line[:2] for line in lines if line[:2] in ("1.", "2.", "3.")], ["1.", "2.", "3."])
            self.assertTrue(lines[-2].startswith("3. Start the tick: "), lines)  # after the three steps
            self.assertEqual(lines[-1], "ceo: Claude Code keeps its transcripts 30 days; to keep them 90, for this "
                                        f'project\'s memory, add "cleanupPeriodDays": 90 to {folder / "settings.json"}: '
                                        "the kernel never sets it.")
            self.assertEqual(settings_file.read_bytes(), theirs)
            self.assertEqual(sorted(home.rglob("*")), [home / ".claude", settings_file])
            self.assertEqual(list(config.iterdir()), [], "init wrote in Claude Code's folder")


if __name__ == "__main__":
    unittest.main()
