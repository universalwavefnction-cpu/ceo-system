"""The shared shelf, $CEO_HOME/SHELF.md: a page's pass sees it beside the other listed projects' pages and moves a lesson
they share onto it, held when the shelf shrinks, grows past its cap or moved meanwhile; every worker's brief and every
CEO session's prompt carries it, Gemini's as its first message. Real git, real processes and a CEO_HOME of the test's
own; the model is a stand-in."""
import fcntl
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

KERNEL = Path(__file__).resolve().parent
CEO = KERNEL / "ceo.py"
HEADING = "# What several projects' work has taught ($CEO_HOME/SHELF.md)\n\n"
PAGE = ("Memory is for what the files and the code cannot tell.\n- CSV exports use semicolons,\n"
        "  so parse them with a semicolon separator,\n  as the import test proved.\n- Only here: staging resets on Sunday.\n")
LINK = "- On the shared shelf: CSV exports use semicolons.\n"
MODEL = r'''
import json, os, sys
mode, keep = sys.argv[1:]
sys.stdin.read()
shelf = os.path.join(".shelf", "SHELF.md")
seen = {name: open(os.path.join(".shelf", name)).read() for name in os.listdir(".shelf")} if os.path.isdir(".shelf") else None
json.dump(seen, open(keep, "w"))
if mode == "keep":
    sys.exit(0)
if mode == "race":  # another project's pass writes the shelf while this one works
    open(os.environ["CEO_HOME"] + "/SHELF.md", "a").write("- Written by another pass meanwhile.\n")
if mode == "shrink":  # the shelf loses its lines
    open(shelf, "w").write("- One left.\n")
    sys.exit(0)
page = open("LESSONS.md").readlines()
open("LESSONS.md", "w").writelines(([] if mode == "cut" else [page[0], "- On the shared shelf: CSV exports use semicolons.\n"])
                                   + page[4:])
open(shelf, "a").write("x" * 6000 + "\n" if mode == "flood" else "- CSV exports use semicolons: split on semicolons.\n")
'''


class Shelf(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        (self.tmp / "model.py").write_text(MODEL)
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "CEO_HOME": str(self.home),
                    "CLAUDE_CONFIG_DIR": str(self.tmp / "claude"), "PYTHONDONTWRITEBYTECODE": "1", "TZ": "UTC",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.invalid", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t.invalid"}
        self.p = self.project("p", PAGE)

    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                              env=self.env).stdout.strip()

    def project(self, name, page, origin=True):
        """A scratch project, its page `page`, pushed to an origin of its own when origin."""
        repo = self.tmp / name
        self.git(self.tmp, "init", "-q", "-b", "main", str(repo))
        (repo / ".gitignore").write_text(".ceo/\n")
        (repo / "LESSONS.md").write_text(page)
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "start")
        if origin:
            self.git(self.tmp, "init", "-q", "--bare", str(self.tmp / f"{name}.git"))
            self.git(repo, "remote", "add", "origin", str(self.tmp / f"{name}.git"))
            self.git(repo, "push", "-q", "origin", "main")
        return repo

    def listed(self, *lines):
        (self.home / "projects.txt").write_text("".join(f"{line}\n" for line in lines))

    def dream(self, mode, path="LESSONS.md"):
        """(exit code, all it printed, what the model found in .shelf/, None when there was none) of one pass."""
        model = shlex.join([sys.executable, str(self.tmp / "model.py"), mode, str(self.tmp / "seen.json")])
        done = subprocess.run([sys.executable, str(CEO), "dream", str(path), "--model", model], cwd=self.p,
                              capture_output=True, text=True, env=self.env, timeout=120)
        return done.returncode, done.stdout + done.stderr, json.loads((self.tmp / "seen.json").read_text())

    def page(self):
        return self.git(self.tmp / "p.git", "show", "main:LESSONS.md") + "\n"

    def shelf(self):
        return (self.home / "SHELF.md").read_text() if (self.home / "SHELF.md").exists() else None

    def test_a_pages_pass_sees_the_shelf_and_every_other_listed_page_that_is_not_empty_and_commits_none_of_them(self):
        self.project("q", "- CSV files are semicolon separated.\n")
        here = self.project("r", "- Read on its main: it has no origin.\n", origin=False)
        self.project("s", "  \n")  # an empty page gives no file
        (self.tmp / "plain").mkdir()  # a folder that is no repository gives none
        (self.tmp / "q" / "LESSONS.md").write_text("Committed, never pushed.\n")  # read on origin's main
        self.git(self.tmp / "q", "commit", "-qam", "unpushed")
        (self.tmp / "q" / "LESSONS.md").write_text("Not committed.\n")
        (self.home / "SHELF.md").write_text("- A lesson already shared.\n")
        self.listed(self.p, "", self.tmp / "gone", self.tmp / "q", "  ", here, self.tmp / "s", self.tmp / "plain",
                    f"{self.p}/")
        code, out, seen = self.dream("keep")
        self.assertEqual((code, seen), (0, {"SHELF.md": "- A lesson already shared.\n", "4-q.md": "- CSV files are "
                                            "semicolon separated.\n", "6-r.md": "- Read on its main: it has no origin.\n"}), out)
        self.assertNotIn(".shelf", self.git(self.tmp / "p.git", "ls-tree", "-r", "--name-only", "main"))
        self.assertEqual(self.shelf(), "- A lesson already shared.\n")

    def test_an_absent_shelf_is_given_empty_and_stays_absent_when_the_pass_leaves_it_as_it_was(self):
        code, out, seen = self.dream("keep")
        self.assertEqual((code, seen), (0, {"SHELF.md": ""}), out)
        self.assertIsNone(self.shelf())

    def test_a_moved_lesson_lands_on_the_shelf_before_the_page_and_its_lines_count_as_kept(self):
        self.listed(self.p)
        code, out, _ = self.dream("promote")  # five lines become three on the page: 40 % removed but for the shelf
        self.assertEqual(code, 0, out)
        self.assertIn("applied on main", out)
        self.assertEqual(self.shelf(), "- CSV exports use semicolons: split on semicolons.\n")
        self.assertEqual(self.page(), PAGE.split("\n")[0] + "\n" + LINK + "- Only here: staging resets on Sunday.\n")

    def test_a_shelf_left_over_its_cap_shrunk_or_moved_meanwhile_holds_the_pass_and_is_never_written(self):
        (self.home / "SHELF.md").write_text("".join(f"- Shared lesson {i}.\n" for i in range(5)))
        for mode, why in (("flood", "the shared shelf is over 5,000 bytes"),
                          ("shrink", "the shared shelf loses over 30 % of its lines"),
                          ("race", "the shared shelf moved meanwhile")):
            with self.subTest(mode):
                shelf = self.shelf()
                code, out, _ = self.dream(mode)
                self.assertEqual(code, 3, out)
                self.assertIn(why, out)
                self.assertEqual(self.page(), PAGE)
                self.assertEqual(self.shelf(), shelf + ("- Written by another pass meanwhile.\n" if mode == "race" else ""))

    def test_a_lesson_the_shelf_already_holds_leaves_its_link_and_the_lines_the_link_took_count_as_kept(self):
        (self.home / "SHELF.md").write_text("- CSV exports use semicolons: split on semicolons.\n")
        (self.tmp / "model.py").write_text(MODEL.rsplit('open(shelf, "a").write', 1)[0])  # the page alone changes
        code, out, _ = self.dream("promote")  # five lines become three, the link standing for the three it took
        self.assertEqual(code, 0, out)
        self.assertIn("5 lines, then 5", out)
        self.assertEqual(self.page(), PAGE.split("\n")[0] + "\n" + LINK + "- Only here: staging resets on Sunday.\n")
        self.assertEqual(self.shelf(), "- CSV exports use semicolons: split on semicolons.\n")

    def test_a_link_to_a_shelf_with_no_text_keeps_no_lines_so_the_cut_it_stands_for_holds_the_pass(self):
        (self.tmp / "model.py").write_text(MODEL.rsplit('open(shelf, "a").write', 1)[0])  # the page alone changes
        for shelf in (None, "  \n"):  # absent or blank: no lesson of the three the link took is on any shelf
            with self.subTest(shelf=shelf):
                if shelf:
                    (self.home / "SHELF.md").write_text(shelf)
                code, out, _ = self.dream("promote")
                self.assertEqual(code, 3, out)
                self.assertIn("then 3 (40 % removed)", out)
                self.assertIn("it removes over 30 % of the lines", out)
                self.assertEqual(self.page(), PAGE)
                self.assertEqual(self.shelf(), shelf)

    def test_a_shelf_left_as_it_was_is_still_checked_over_its_cap_or_moved_meanwhile(self):
        for mode, shelf, why in (("keep", "x" * 5001 + "\n", "the shared shelf is over 5,000 bytes"),
                                 ("race", "- A shared lesson.\n", "the shared shelf moved meanwhile")):
            with self.subTest(mode):
                (self.home / "SHELF.md").write_text(shelf)
                (self.tmp / "model.py").write_text(MODEL.replace('if mode == "keep":', 'if mode == "race":\n    open(os.'
                                                                 'environ["CEO_HOME"] + "/SHELF.md", "w").write("- New.\\n")\n'
                                                                 'if mode in ("keep", "race"):'))
                code, out, _ = self.dream(mode)
                self.assertEqual(code, 3, out)
                self.assertIn(why, out)
                self.assertEqual(self.shelf(), shelf if mode == "keep" else "- New.\n")

    def test_a_page_that_moved_meanwhile_holds_the_pass_before_the_shelf_is_written(self):
        moved = (f"\nimport subprocess\nopen({str(self.p / 'LESSONS.md')!r}, 'a').write('- Meanwhile.\\n')\n"
                 f"subprocess.run(['git', '-C', {str(self.p)!r}, 'commit', '-qam', 'moved'], check=True)\n"
                 f"subprocess.run(['git', '-C', {str(self.p)!r}, 'push', '-q', 'origin', 'main'], check=True)\n")
        (self.tmp / "model.py").write_text(MODEL + moved)
        code, out, _ = self.dream("promote")
        self.assertEqual(code, 3, out)
        self.assertIn("the notes moved meanwhile", out)
        self.assertIsNone(self.shelf())

    def test_a_page_another_push_moves_after_the_shelf_is_written_holds_the_pass_and_leaves_the_shelf_as_it_was(self):
        actor = self.tmp / "actor"
        self.git(self.tmp, "clone", "-q", "-b", "main", str(self.tmp / "p.git"), str(actor))
        (actor / "LESSONS.md").write_text(PAGE + "- Meanwhile.\n")
        self.git(actor, "commit", "-qam", "moved")
        marker = self.tmp / "pushed"  # the first push of the pass, its shelf written, loses to the other's
        hook = self.p / ".git" / "hooks" / "pre-push"
        hook.write_text(f"#!/bin/sh\ntest -e {shlex.quote(str(marker))} && exit 0\ntouch {shlex.quote(str(marker))}\n"
                        f"git -C {shlex.quote(str(actor))} push -q origin main\n")
        hook.chmod(0o755)
        for shelf in (None, "- A shared lesson.\r\n"):
            with self.subTest(shelf=shelf):
                marker.unlink(missing_ok=True)
                if shelf:
                    (self.home / "SHELF.md").write_bytes(shelf.encode())
                    self.git(actor, "pull", "-q", "--rebase", "origin", "main")
                    (actor / "LESSONS.md").write_text(self.page() + "- Meanwhile again.\n")
                    self.git(actor, "commit", "-qam", "moved again")
                code, out, _ = self.dream("promote")
                self.assertTrue(marker.exists())
                self.assertEqual(code, 3, out)
                self.assertIn("the notes moved meanwhile", out)
                self.assertNotIn(LINK, self.page())
                self.assertEqual((self.home / "SHELF.md").read_bytes().decode() if shelf else self.shelf(), shelf)

    def test_a_push_that_lands_but_loses_its_reply_keeps_the_shelf_and_one_refused_restores_it(self):
        hooks = self.tmp / "p.git" / "hooks"
        hooks.mkdir(exist_ok=True)
        for name, body, code, page, shelf in (
                ("pre-receive", "exit 1\n", 2, PAGE, "- A shared lesson.\n"),  # refused: nothing landed
                ("post-receive", 'kill -KILL "$PPID"\n', 0, None, None)):  # landed, its reply lost
            with self.subTest(name):
                (self.home / "SHELF.md").write_text("- A shared lesson.\n")
                (hook := hooks / name).write_text(f"#!/bin/sh\ncat >/dev/null\n{body}")
                hook.chmod(0o755)
                got, out, _ = self.dream("promote")
                hook.unlink()
                self.assertEqual(got, code, out)
                self.assertEqual(self.page(), page or PAGE.split("\n")[0] + "\n" + LINK + PAGE.splitlines(True)[-1])
                self.assertEqual(self.shelf(), shelf or "- A shared lesson.\n- CSV exports use semicolons: split on "
                                 "semicolons.\n")

    def test_a_listed_folder_or_worktree_of_this_project_is_never_offered_as_another(self):
        (self.p / "nested").mkdir()
        self.git(self.p, "worktree", "add", "-q", "-b", "other", str(self.tmp / "wt"))
        self.listed(self.p / "nested", self.tmp / "wt", self.p)
        code, out, seen = self.dream("keep")
        self.assertEqual((code, seen), (0, {"SHELF.md": ""}), out)

    def test_a_crlf_shelf_is_counted_by_its_bytes_and_given_to_the_model_as_written(self):
        (self.home / "SHELF.md").write_bytes(b"- A\r\n" * 1001)  # 5,005 bytes, 4,004 were its line ends read as LF
        code, out, seen = self.dream("keep")
        self.assertEqual(code, 3, out)
        self.assertIn("the shared shelf is over 5,000 bytes", out)
        self.assertEqual((self.home / "SHELF.md").stat().st_size, 5005)
        (self.home / "SHELF.md").write_bytes(b"- A\r\n")
        (self.tmp / "model.py").write_text(MODEL.replace("open(os.path.join", "open(newline='', file=os.path.join"))
        code, out, seen = self.dream("keep")
        self.assertEqual((code, seen["SHELF.md"]), (0, "- A\r\n"), out)

    def test_a_pass_looks_at_and_writes_the_shelf_only_while_it_holds_the_shelf_lock(self):
        with open(self.home / "SHELF.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)  # another page's pass, between its look at the shelf and its write
            model = shlex.join([sys.executable, str(self.tmp / "model.py"), "promote", str(self.tmp / "seen.json")])
            waiting = subprocess.Popen([sys.executable, str(CEO), "dream", "LESSONS.md", "--model", model], cwd=self.p,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=self.env)
            self.addCleanup(lambda: waiting.poll() is None and waiting.kill())
            for _ in range(600):
                if (self.tmp / "seen.json").exists() or waiting.poll() is not None:
                    break
                time.sleep(0.05)
            with self.assertRaises(subprocess.TimeoutExpired):
                waiting.wait(timeout=2)  # the model is done, and the pass waits for the lock
            self.assertIsNone(self.shelf())
            (self.home / "SHELF.md").write_text("- Written by the pass that held the lock.\n")
        out = waiting.communicate(timeout=60)[0]
        self.assertEqual(waiting.returncode, 3, out)
        self.assertIn("the shared shelf moved meanwhile", out)
        self.assertEqual(self.page(), PAGE)

    def test_a_page_held_for_its_own_cut_writes_no_shelf(self):
        code, out, _ = self.dream("cut")
        self.assertEqual(code, 3, out)
        self.assertIn("it removes over 30 % of the lines", out)
        self.assertIsNone(self.shelf())

    def test_a_folders_pass_has_no_shelf_and_its_own_shelf_folder_never_reaches_the_shared_one(self):
        notes = self.tmp / "notes"
        (notes / ".shelf").mkdir(parents=True)
        (notes / "MEMORY.md").write_text("- [a](a.md): a note.\n")
        (notes / ".shelf" / "SHELF.md").write_text("A note of its own.\n")
        self.listed(self.tmp / "q")
        self.project("q", "- CSV files are semicolon separated.\n")
        script = MODEL.replace('page = open("LESSONS.md")', 'sys.exit(0)\npage = open("LESSONS.md")')
        (self.tmp / "model.py").write_text(script.replace("json.dump(seen", 'open(shelf, "a").write("x\\n")\njson.dump(seen'))
        code, out, seen = self.dream("write", notes)
        self.assertEqual(code, 0, out)
        self.assertEqual(seen, {"SHELF.md": "A note of its own.\n"})  # its own note, never the shelf or a page
        self.assertEqual((notes / ".shelf" / "SHELF.md").read_text(), "A note of its own.\nx\n")
        self.assertIsNone(self.shelf())

    def run_and_session(self, program):
        """(the worker's brief, the session's prompt) in a fresh scratch project whose session program is named program."""
        repo, bin_ = self.tmp / f"r{len(list(self.tmp.glob('r*')))}", self.tmp / "bin"
        (repo / "orders").mkdir(parents=True)
        (repo / ".gitignore").write_text(".ceo/\n")
        (repo / "LESSONS.md").write_text("A lesson of r alone.\n")
        (repo / "ceo.toml").write_text("disk_floor_gb = 0\nram_floor_gb = 0\nrecheck_wait = 0\n"
                                       f"session = {json.dumps(str(bin_ / program))}\n")
        (repo / "orders" / "001-built.md").write_text("# The shelf order\n\n## Goal\nWrite built.\n\n## Done when\n"
                                                       "```bash\ntest -f built\n```\n\n## Not asked\nAnything else.\n")
        self.git(self.tmp, "init", "-q", "-b", "main", str(repo))
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "start")
        brief = self.tmp / "brief"
        (self.tmp / "worker.py").write_text("import subprocess, sys\n"
                                            f"open({str(brief)!r}, 'wb').write(sys.stdin.buffer.read())\n"
                                            "open('built', 'w').write('built')\n"
                                            "subprocess.run(['git', 'add', 'built'], check=True)\n"
                                            "subprocess.run(['git', 'commit', '-qm', 'built'], check=True)\n")
        subprocess.run([sys.executable, str(CEO), "run", "orders/001-built.md", "--worker",
                        shlex.join([sys.executable, str(self.tmp / "worker.py")])], cwd=repo, check=True,
                       capture_output=True, env=self.env, timeout=120)
        bin_.mkdir(exist_ok=True)
        (bin_ / program).write_text(f"#!{sys.executable}\nimport json, sys\n"
                                    f"json.dump(sys.argv[1:], open({str(self.tmp / 'argv.json')!r}, 'w'))\n")
        (bin_ / program).chmod(0o755)
        subprocess.run([sys.executable, str(CEO), "session"], cwd=repo, check=True, capture_output=True, env=self.env,
                       timeout=120)
        argv = json.loads((self.tmp / "argv.json").read_text())
        prompt = argv[argv.index("--append-system-prompt") + 1] if program == "claude" else json.loads(
            argv[argv.index("-c") + 1].partition("=")[2]) if program == "codex" else argv[argv.index("-i") + 1]
        return brief.read_text(), prompt

    def test_the_shelf_reaches_the_workers_brief_after_the_page_and_every_sessions_prompt_after_session_md(self):
        session = (KERNEL / "SESSION.md").read_text()
        plain = self.run_and_session("claude")
        self.assertEqual(plain[1], session, "absent, the prompt is as it was")
        (self.home / "SHELF.md").write_text("  \n")
        self.assertEqual(self.run_and_session("claude"), plain, "empty, the brief and prompt are as they were")
        (self.home / "SHELF.md").write_text("\n- A shared lesson.\n\n")
        block = HEADING + "- A shared lesson.\n\n"
        for program in ("claude", "codex"):
            with self.subTest(program):
                brief, prompt = self.run_and_session(program)
                self.assertEqual(brief, plain[0].replace("# The shelf order", block + "# The shelf order", 1))
                self.assertLess(brief.index("A lesson of r alone."), brief.index(block))
                self.assertEqual(prompt, session + block)
        brief, prompt = self.run_and_session("gemini")  # no system-prompt flag: its first message, by -i
        self.assertIn(block, brief)
        self.assertEqual(prompt, session + block)


if __name__ == "__main__":
    unittest.main()
