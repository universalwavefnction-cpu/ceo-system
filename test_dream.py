"""ceo.py dream, the cleaner: a page a project tracks, or a folder of notes, snapshotted in git, judged in a clean copy,
then applied or held as a branch. Real git, real processes and a CEO_HOME of the test's own; only the model is a
stand-in, which keeps its brief and where it ran, then cleans as its first argument says."""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import ceo

CEO = Path(__file__).resolve().parent / "ceo.py"
ROUTINE = Path(__file__).resolve().parent / "routines" / "dream.md"
PARAGRAPH = ("Memory is for what the files and the code cannot tell: rules that were tested,\neach with how it was proven;\n"
             "the traps met here.\n")
PAGE = PARAGRAPH + "A wrong note: an untracked file blocks the pull.\n" + "".join(
    f"A tested rule, {i}, and how it was proven.\n" for i in range(1, 10))  # 13 lines
MODEL = r'''
import os, re, subprocess, sys, time
mode, keep, *more = sys.argv[1:]
brief = sys.stdin.read()
n = len([name for name in os.listdir(keep) if name.endswith(".brief")])
open(os.path.join(keep, f"{n:02d}.brief"), "w").write(brief)
open(os.path.join(keep, f"{n:02d}.cwd"), "w").write(os.getcwd())
name = more[1] if mode == "cut" and len(more) > 1 else "LESSONS.md" if os.path.exists("LESSONS.md") else "MEMORY.md"
lines = open(name).readlines()
if mode == "fail":
    sys.exit(1)
if mode == "hang":
    time.sleep(60)
if mode == "fold" and name == "LESSONS.md":
    kept = [line for line in lines if "A wrong note" not in line]
    open(name, "w").writelines(kept + [found + "\n" for found in re.findall(r"Lesson: ([^<\n].*)", brief)])
elif mode == "fold":
    os.remove("old.md")
    open(name, "w").writelines(line for line in lines if "old.md" not in line)
    open("a.md", "a").write("Fixed: proven by a test of its own.\n")
elif mode == "cut":  # as many of its last lines go as it is told
    open(name, "w").writelines(lines[:len(lines) - int(more[0])])
elif mode == "wipe":
    open(name, "w").writelines(lines[:1])
    for other in os.listdir("."):
        if other.endswith(".md") and other != name:
            os.remove(other)
elif mode == "long":
    open(name, "a").write("".join(f"A line of its own, {i}.\n" for i in range(200)))
elif mode == "wide":
    open(name, "a").write("A long line, " + "x" * 25_000 + ".\n")
elif mode == "moved":  # meanwhile, the page itself (or another record) moves on origin's main, or the folder changes
    folder, path = more
    open(os.path.join(folder, path), "a").write("Written meanwhile.\n")
    if os.path.exists(os.path.join(folder, ".gitignore")):
        subprocess.run(["git", "commit", "-qam", "meanwhile"], cwd=folder, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=folder, check=True)
    open(name, "a").write("A note of this pass.\n")
'''


class Dream(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.briefs = self.tmp / "briefs"
        self.briefs.mkdir()
        (self.tmp / "model.py").write_text(MODEL)
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "CEO_HOME": str(self.tmp / "home"), "CLAUDE_CONFIG_DIR": str(self.tmp / "claude"),
                    "PYTHONDONTWRITEBYTECODE": "1", "GIT_AUTHOR_NAME": "t",
                    "GIT_AUTHOR_EMAIL": "t@t.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.invalid"}
        self.origin, self.repo = self.tmp / "origin.git", self.tmp / "p"
        self.git(self.tmp, "init", "-q", "--bare", str(self.origin))
        self.git(self.tmp, "init", "-q", "-b", "main", str(self.repo))
        (self.repo / ".gitignore").write_text(".ceo/\n")
        (self.repo / "LESSONS.md").write_text(PAGE)
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-qm", "start")
        self.git(self.repo, "remote", "add", "origin", str(self.origin))
        self.git(self.repo, "push", "-q", "origin", "main")

    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                              env=self.env).stdout.strip()

    def dream(self, path, mode, *more, timeout=None):
        """(exit code, all it printed) of one pass, its model the stand-in in mode."""
        model = shlex.join([sys.executable, str(self.tmp / "model.py"), mode, str(self.briefs), *map(str, more)])
        done = subprocess.run([sys.executable, str(CEO), "dream", str(path), "--model", model,
                               *(["--timeout", str(timeout)] if timeout else [])], cwd=self.repo, capture_output=True,
                              text=True, env=self.env, timeout=120)
        return done.returncode, done.stdout + done.stderr

    def brief(self, n):
        return (self.briefs / f"{n:02d}.brief").read_text()

    def main(self, path="LESSONS.md"):
        return self.git(self.origin, "show", f"main:{path}") + "\n"

    def passes(self):
        return [line for line in self.git(self.origin, "log", "--format=%s", "main").splitlines()
                if line.startswith("Lessons pass")]

    def finished(self, slug, ended, lesson, state="merged"):
        """A finished run as ceo.py run leaves it, its log a worker's plain text between the kernel's marks."""
        run = self.repo / ".ceo" / "runs" / slug
        run.mkdir(parents=True)
        (run / "worker.log").write_text(
            f"== ceo 2026-01-02T03:00:00+00:00: worker starts: worker\nBuilt.\nLesson: {lesson}\n"
            f"== ceo 2026-01-02T03:30:00+00:00: done-when: attempt 1\nLesson: the done-when said this, {slug}\n")
        (run / "result.json").write_text(json.dumps({"state": state, "ended": ended, "log": f".ceo/runs/{slug}/worker.log"}))

    def held(self, repo):
        return self.git(repo, "for-each-ref", "--format=%(refname:lstrip=2)", "refs/heads/dream/*").split()

    def test_a_pass_folds_the_suggested_lesson_drops_a_wrong_note_and_lands_on_main_as_one_commit(self):
        self.finished("001-work", "2026-01-02T03:30:00+00:00", "a scratch disk swings by a gigabyte, so read it twice.")
        code, out = self.dream("LESSONS.md", "fold")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(out.splitlines()), 1, out)
        self.assertRegex(out, r"^ceo: dream: LESSONS\.md: 13 lines, then 13 \(0 % removed\); .*lessons suggested: 1; "
                              r"applied on main\.$")
        self.assertNotIn(str(self.tmp), out)  # the page is named in the project, never by a path of this machine
        self.assertIn("a scratch disk swings by a gigabyte, so read it twice.\n", self.main())
        self.assertNotIn("A wrong note", self.main())
        self.assertTrue(self.main().startswith(PARAGRAPH))
        said = out[len("ceo: dream: "):].rsplit("; applied", 1)[0]
        self.assertEqual(self.passes(), [f"Lessons pass: {said}"])  # the report line, applied or not
        trailer = self.git(self.origin, "log", "-1", "--format=%(trailers:key=Lessons-read,valueonly)", "main")
        self.assertRegex(trailer, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")
        self.assertEqual(self.git(self.repo, "rev-parse", "main"), self.git(self.origin, "rev-parse", "main"))
        brief = self.brief(0)
        self.assertTrue(brief.startswith(ROUTINE.read_text()), "the brief opens with routines/dream.md")
        self.assertIn("Lesson: a scratch disk swings by a gigabyte, so read it twice.\n", brief)
        self.assertNotIn("the done-when said this", brief)
        self.assertIn("The notes: LESSONS.md. The index: LESSONS.md, at most 200 lines and 25,000 bytes.", brief)
        self.assertNotEqual((self.briefs / "00.cwd").read_text(), str(self.repo))
        self.assertFalse((self.repo / ".ceo" / "dream" / "copy").exists())
        self.assertIn("dreamer starts: ", (self.repo / ".ceo" / "dream" / "dream.log").read_text())

    def test_the_next_pass_reads_the_runs_ended_since_the_last_one_began_and_a_pass_changing_nothing_marks_main(self):
        self.finished("001-work", "2026-01-02T03:30:00+00:00", "folded by the first pass.")
        self.assertEqual(self.dream("LESSONS.md", "fold")[0], 0)
        read = self.git(self.origin, "log", "-1", "--format=%(trailers:key=Lessons-read,valueonly)", "main")
        self.finished("002-same-second", read, "a run that ended as the first pass read.")
        self.finished("003-later", datetime.now(timezone.utc).isoformat(timespec="seconds"), "a run ended since.")
        self.finished("004-retry", datetime.now(timezone.utc).isoformat(timespec="seconds"), "not finished.", "retry")
        page = self.main()
        code, out = self.dream("LESSONS.md", "keep")
        self.assertEqual(code, 0, out)
        self.assertIn("lessons suggested: 2; applied on main.", out)
        brief = self.brief(1)
        self.assertNotIn("folded by the first pass", brief)
        self.assertIn("Lesson: a run that ended as the first pass read.\n", brief)
        self.assertIn("Lesson: a run ended since.\n", brief)
        self.assertNotIn("not finished", brief)
        self.assertEqual((len(self.passes()), self.main()), (2, page))
        self.assertEqual(self.git(self.origin, "diff", "main^", "main"), "")  # a pass that changed nothing is a commit

    def test_the_last_pass_is_the_newest_commit_titled_so_never_one_whose_body_starts_a_line_so(self):
        cutoff = "2026-01-03T00:00:00+00:00"
        self.git(self.repo, "commit", "--allow-empty", "-qm", f"Lessons pass: a pass\n\nLessons-read: {cutoff}")
        last = self.git(self.repo, "rev-parse", "HEAD")
        self.finished("001-old", "2026-01-02T00:00:00+00:00", "read by that pass.")
        self.finished("002-new", "2026-01-04T00:00:00+00:00", "a run ended since.")
        self.git(self.repo, "commit", "--allow-empty", "-qm", "Document the cleaner\n\nLessons pass is the title of a pass.")
        self.assertEqual(ceo.suggested(self.repo, self.git(self.repo, "rev-parse", "HEAD")),
                         (cutoff, ["a run ended since."], last))
        self.assertEqual(ceo.suggested(self.repo, self.git(self.repo, "rev-parse", "HEAD~2")),  # before any pass, each run
                         ("", ["read by that pass.", "a run ended since."], ""))

    def test_a_project_whose_main_has_its_own_routine_briefs_with_it(self):
        (self.repo / "routines").mkdir()
        (self.repo / "routines" / "dream.md").write_text("# This project's own way of cleaning\n")
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-qm", "routine")
        self.git(self.repo, "push", "-q", "origin", "main")
        self.assertEqual(self.dream("LESSONS.md", "keep")[0], 0)
        self.assertTrue(self.brief(0).startswith("# This project's own way of cleaning\n"))
        self.assertNotIn("Lesson: ", self.brief(0))

    def test_a_pass_removing_over_30_percent_or_leaving_the_index_over_its_cap_is_held_as_a_branch(self):
        page = self.main()
        for mode, why in (("wipe", "it removes over 30 % of the lines"), ("long", "its index is over its cap"),
                          ("wide", "its index is over its cap")):
            with self.subTest(mode=mode):
                code, out = self.dream("LESSONS.md", mode)
                self.assertEqual(code, 3, out)
                self.assertRegex(out, rf"held for the owner's word as the branch dream/lessons-[\d-]+, for {why}\.$")
                self.assertEqual((self.main(), self.passes()), (page, []))
        branches = self.held(self.repo)
        self.assertEqual(len(branches), 3)
        self.assertEqual(self.git(self.repo, "show", f"{branches[0]}:LESSONS.md") + "\n", PARAGRAPH.splitlines()[0] + "\n")
        self.assertTrue(self.git(self.repo, "log", "-1", "--format=%B", branches[0]).startswith("Lessons pass: "))

    def test_a_pass_removing_30_percent_exactly_is_applied_and_one_removing_more_is_held(self):
        (self.repo / "LESSONS.md").write_text("".join(f"A rule, {i}.\n" for i in range(20)))
        self.git(self.repo, "commit", "-qam", "twenty rules")
        self.git(self.repo, "push", "-q", "origin", "main")
        for cut, code, said in ((6, 0, "20 lines, then 14 (30 % removed)"), (5, 3, "14 lines, then 9 (36 % removed)"),
                                (4, 0, "14 lines, then 10 (29 % removed)")):
            with self.subTest(cut=cut):
                done, out = self.dream("LESSONS.md", "cut", cut)
                self.assertEqual(done, code, out)
                self.assertIn(said, out)
        self.assertEqual(len(self.main().splitlines()), 10)

    def test_a_model_that_fails_or_runs_past_its_clock_changes_nothing(self):
        page = self.main()
        for mode, timeout, said in (("fail", None, "the model failed (1), so nothing changed: .ceo/dream/dream.log"),
                                    ("hang", 2, "the model ran past its 2 s")):
            with self.subTest(mode=mode):
                code, out = self.dream("LESSONS.md", mode, timeout=timeout)
                self.assertEqual(code, 2, out)
                self.assertIn(f"ceo: refused: {said}", out)
                self.assertEqual((self.main(), self.passes(), self.held(self.repo)), (page, [], []))
                self.assertFalse((self.repo / ".ceo" / "dream" / "copy").exists())

    def test_a_page_that_moved_on_main_meanwhile_is_held_but_another_record_landing_is_not(self):
        code, out = self.dream("LESSONS.md", "moved", self.repo, "LESSONS.md")
        self.assertEqual(code, 3, out)
        self.assertIn("for the notes moved meanwhile.", out)
        self.assertEqual(self.main(), PAGE + "Written meanwhile.\n")
        self.assertIn("A note of this pass.", self.git(self.repo, "show", f"{self.held(self.repo)[0]}:LESSONS.md"))
        (self.repo / "IDEAS.md").write_text("# Ideas\n")
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-qm", "ideas")
        code, out = self.dream("LESSONS.md", "moved", self.repo, "IDEAS.md")
        self.assertEqual(code, 0, out)
        self.assertEqual((self.main("IDEAS.md"), len(self.passes())), ("# Ideas\nWritten meanwhile.\n", 1))
        self.assertTrue(self.main().endswith("Written meanwhile.\nA note of this pass.\n"))

    def test_a_page_with_no_origin_is_read_and_applied_on_main_here(self):
        self.git(self.repo, "remote", "remove", "origin")
        code, out = self.dream("LESSONS.md", "long")
        self.assertEqual(code, 3, out)
        code, out = self.dream("LESSONS.md", "fold")
        self.assertEqual(code, 0, out)
        self.assertEqual((self.repo / "LESSONS.md").read_text(), PAGE.replace("A wrong note: an untracked file blocks the pull.\n", ""))
        self.assertEqual(self.git(self.repo, "log", "-1", "--format=%s"), "Lessons pass: " + out[len("ceo: dream: "):-len("; applied on main.\n")])
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), "")

    def notebook(self):
        """A folder of notes inside a repository that ignores it: an index and one file a note."""
        outer = self.tmp / "home2"
        self.git(self.tmp, "init", "-q", "-b", "main", str(outer))
        (outer / ".gitignore").write_text("notes/\n")
        self.git(outer, "add", "-A")
        self.git(outer, "commit", "-qm", "start")
        notes = outer / "notes"
        notes.mkdir()
        for name in ("a", "b", "c", "old"):
            with open(notes / "MEMORY.md", "a") as index:
                index.write(f"- [{name}]({name}.md): what {name} holds\n")
            (notes / f"{name}.md").write_text(f"# {name}\nOne.\nTwo.\nThree.\n")
        return outer, notes

    def test_a_folder_of_notes_gets_a_repository_of_its_own_a_snapshot_and_its_pass(self):
        outer, notes = self.notebook()
        code, out = self.dream(notes, "fold")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, f"ceo: dream: {notes}: 20 lines, then 16 (20 % removed); its index MEMORY.md: 3 of 200 "
                              f"lines, 78 of 25,000 bytes; lessons suggested: 0; applied in {notes}.\n")
        self.assertEqual(self.git(notes, "log", "--format=%s").splitlines()[1:], ["dream: snapshot"])
        self.assertFalse((notes / "old.md").exists())
        self.assertIn("Fixed:", (notes / "a.md").read_text())
        self.assertEqual(self.git(notes, "status", "--porcelain"), "")
        self.assertEqual(self.git(outer, "status", "--porcelain"), "")
        self.assertIn("The notes: MEMORY.md, a.md, b.md, c.md, old.md. The index: MEMORY.md", self.brief(0))
        self.assertNotIn("Lesson", self.brief(0)[len(ROUTINE.read_text()):])
        self.assertFalse(list((self.tmp / "home" / "dream").glob("*/copy")))
        with open(notes / "b.md", "a") as note:
            note.write("An edit made between passes.\n")
        code, out = self.dream(notes, "keep")
        self.assertEqual(code, 0, out)
        self.assertIn("+An edit made between passes.", self.git(notes, "show", "HEAD^"))
        self.assertEqual(self.git(notes, "log", "-1", "--format=%s", "HEAD^"), "dream: snapshot")

    def test_session_logs_are_no_notes_to_the_guard_so_they_neither_dilute_a_large_cut_nor_hide_a_small_one(self):
        _, notes = self.notebook()
        (notes / "sessions").mkdir()
        (notes / "sessions" / "log.md").write_text("Session transcript line.\n" * 1000)
        code, out = self.dream(notes, "wipe")
        self.assertEqual(code, 3, out)
        self.assertIn("20 lines, then 1 (95 % removed)", out)
        self.assertIn("for it removes over 30 % of the lines.", out)
        self.assertEqual(len((notes / "sessions" / "log.md").read_text().splitlines()), 1000)
        code, out = self.dream(notes, "fold")
        self.assertEqual(code, 0, out)
        self.assertIn("20 lines, then 16 (20 % removed)", out)
        self.assertEqual(len((notes / "sessions" / "log.md").read_text().splitlines()), 1000)

    def test_a_page_asked_for_under_sessions_is_counted_as_any_page_is(self):
        (self.repo / "sessions").mkdir()
        (self.repo / "sessions" / "log.md").write_text("".join(f"A line, {i}.\n" for i in range(20)))
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-qm", "a page under sessions")
        self.git(self.repo, "push", "-q", "origin", "main")
        code, out = self.dream("sessions/log.md", "cut", 8, "sessions/log.md")
        self.assertEqual(code, 3, out)
        self.assertIn("sessions/log.md: 20 lines, then 12 (40 % removed)", out)
        self.assertIn("for it removes over 30 % of the lines.", out)

    def test_a_folder_pass_held_or_moved_meanwhile_leaves_the_folder_and_is_a_branch_of_its_repository(self):
        _, notes = self.notebook()
        for mode, more, why in (("wipe", (), "it removes over 30 % of the lines"),
                                ("moved", (notes, "a.md"), "the notes moved meanwhile")):
            with self.subTest(mode=mode):
                code, out = self.dream(notes, mode, *more)
                self.assertEqual(code, 3, out)
                self.assertRegex(out, rf"held for the owner's word as the branch dream/notes-[\d-]+, for {why}\.$")
                self.assertTrue((notes / "old.md").exists())
                self.assertNotIn("A note of this pass.", (notes / "MEMORY.md").read_text())
        self.assertEqual(len(self.held(notes)), 2)
        self.assertEqual(self.git(notes, "rev-parse", f"{self.held(notes)[1]}^"), self.git(notes, "rev-parse", "HEAD"))

    def test_a_folders_ignored_notes_are_snapshotted_judged_and_one_written_meanwhile_holds_the_pass(self):
        _, notes = self.notebook()
        (notes / ".gitignore").write_text("a.md\n")
        code, out = self.dream(notes, "fold")
        self.assertEqual(code, 0, out)
        self.assertIn("a.md", self.git(notes, "ls-tree", "-r", "--name-only", "HEAD^").splitlines())  # the snapshot
        self.assertIn("The notes: .gitignore, MEMORY.md, a.md, b.md, c.md, old.md.", self.brief(0))
        self.assertIn("Fixed:", (notes / "a.md").read_text())
        (notes / ".gitignore").unlink()  # the stand-in reads a folder with one as the project, which pushes
        (notes / ".git" / "info").mkdir(exist_ok=True)
        (notes / ".git" / "info" / "exclude").write_text("a.md\nnew.md\n")  # new.md: a note written while the model runs
        code, out = self.dream(notes, "moved", notes, "new.md")
        self.assertEqual(code, 3, out)
        self.assertIn("for the notes moved meanwhile.", out)
        self.assertNotIn("A note of this pass.", (notes / "MEMORY.md").read_text())
        self.assertEqual(self.dream(notes, "keep")[0], 0)
        self.assertIn("new.md", self.git(notes, "ls-tree", "-r", "--name-only", "HEAD^").splitlines())

    def test_a_folder_whose_status_hides_untracked_files_still_holds_a_pass_when_a_note_is_written_meanwhile(self):
        _, notes = self.notebook()
        self.git(notes, "init", "-q", "-b", "main")
        self.git(notes, "config", "status.showUntrackedFiles", "no")
        code, out = self.dream(notes, "moved", notes, "new.md")
        self.assertTrue((notes / "new.md").is_file())
        self.assertEqual(code, 3, out)
        self.assertIn("for the notes moved meanwhile.", out)
        self.assertNotIn("A note of this pass.", (notes / "MEMORY.md").read_text())

    def test_notes_are_read_and_written_as_their_own_bytes_so_a_crlf_index_counts_every_byte(self):
        _, notes = self.notebook()
        index = (b"x" * 124 + b"\r\n") * 200  # 25,200 bytes; 25,000 if its newlines were read as one byte each
        (notes / "MEMORY.md").write_bytes(index)
        code, out = self.dream(notes, "keep")
        self.assertEqual(code, 3, out)
        self.assertIn("its index MEMORY.md: 200 of 200 lines, 25,200 of 25,000 bytes", out)
        self.assertIn("for its index is over its cap.", out)
        self.assertEqual((notes / "MEMORY.md").read_bytes(), index)
        (notes / "MEMORY.md").write_bytes(b"- [a](a.md): what a holds\r\n- [old](old.md): what old holds\r\n")
        (notes / "a.md").write_bytes(b"# a\r\nOne.\r\n")
        code, out = self.dream(notes, "fold")  # it appends a line to a.md, so the rest of a.md must stay as written
        self.assertEqual(code, 0, out)
        self.assertEqual((notes / "a.md").read_bytes(), b"# a\r\nOne.\r\nFixed: proven by a test of its own.\n")

    def test_a_folder_holding_a_symlink_is_refused_before_the_model_runs_and_what_it_links_stays_as_it_was(self):
        outer, notes = self.notebook()
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "a.md").write_text("# a\nOutside the notes.\n")
        before = {name: (notes / name).read_bytes() for name in ("MEMORY.md", "a.md", "b.md", "c.md", "old.md")}
        for mode in ("fail", "wipe"):  # the controls: ordinary files, a failed model changes nothing, a wiping one is held
            with self.subTest(control=mode):
                code, out = self.dream(notes, mode)
                self.assertEqual(code, 2 if mode == "fail" else 3, out)
                self.assertIn("the model failed (1)" if mode == "fail" else "it removes over 30 % of the lines", out)
                self.assertEqual({name: (notes / name).read_bytes() for name in before}, before)
        self.assertEqual(len(list(self.briefs.glob("*.brief"))), 2)
        held = self.held(notes)
        (notes / "a.md").unlink()
        (notes / "a.md").symlink_to(outside / "a.md")  # absolute, to a file the model would write through
        (notes / "shelf").symlink_to(outside)  # to a folder
        (notes / "gone.md").symlink_to(outside / "gone.md")  # dangling: writing it would make the file
        (notes / "b.md").unlink()
        (notes / "b.md").symlink_to("c.md")  # relative, inside the notes: the pass would flatten it into a file
        for mode in ("fail", "fold", "wipe"):
            with self.subTest(mode=mode):
                code, out = self.dream(notes, mode)
                self.assertEqual(code, 2, out)
                self.assertIn(f"ceo: refused: {notes} holds symlinks (", out)
                self.assertIn("), which the model would write through, so no pass was made", out)
                self.assertEqual(len(list(self.briefs.glob("*.brief"))), 2)  # the model never ran
                self.assertEqual((outside / "a.md").read_bytes(), b"# a\nOutside the notes.\n")
                self.assertEqual(sorted(one.name for one in outside.iterdir()), ["a.md"])
                self.assertEqual([os.readlink(notes / name) for name in ("a.md", "shelf", "gone.md", "b.md")],
                                 [str(outside / "a.md"), str(outside), str(outside / "gone.md"), "c.md"])
                self.assertEqual((notes / "old.md").read_bytes(), before["old.md"])
                self.assertEqual(self.held(notes), held)
        for name in ("a.md", "shelf", "gone.md", "b.md"):
            (notes / name).unlink()
        (notes / "a.md").write_bytes(before["a.md"])
        (notes / "b.md").write_bytes(before["b.md"])
        code, out = self.dream(notes, "fold")  # the notes themselves in their place: a pass is made
        self.assertEqual(code, 0, out)
        self.assertIn("Fixed:", (notes / "a.md").read_text())
        self.assertEqual(sorted(one.name for one in outside.iterdir()), ["a.md"])

    def test_a_folder_another_repository_tracks_is_refused_and_given_no_repository(self):
        outer, _ = self.notebook()
        (outer / "docs").mkdir()
        (outer / "docs" / "a.md").write_text("# Docs\n")
        self.git(outer, "add", "docs")
        self.git(outer, "commit", "-qm", "docs")
        code, out = self.dream(outer / "docs", "keep")
        self.assertEqual(code, 2, out)
        self.assertIn("is tracked by the repository above it", out)
        self.assertFalse((outer / "docs" / ".git").exists())
        self.assertEqual(list(self.briefs.iterdir()), [])
        code, out = self.dream(self.tmp / "nowhere" / "LESSONS.md", "keep")
        self.assertEqual((code, out), (2, f"ceo: refused: {self.tmp / 'nowhere' / 'LESSONS.md'} is no folder of notes, "
                                          "nor a page in a git repository.\n"))

    def claude(self, flag=None, setting=None, raw=None):
        """Claude Code's two files as it leaves them, in the folder CLAUDE_CONFIG_DIR names: the server's flag as cached
        in .claude.json (raw, its text as written, when given) and the setting in settings.json; None writes no file."""
        home = Path(self.env.get("CLAUDE_CONFIG_DIR") or self.env["HOME"])
        home.mkdir(parents=True, exist_ok=True)
        if flag is not None:
            (home / ".claude.json").write_text(raw or json.dumps({"cachedGrowthBookFeatures": {"tengu_onyx_plover": flag}}))
        if setting is not None:
            (home / "settings.json").write_text(json.dumps({"autoDreamEnabled": setting}))

    def asides(self, notes):
        """(exit code, whether the pass stepped aside, whether it wrote anything) of a pass of the notes."""
        code, out = self.dream(notes, "keep")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(out.splitlines()), 1, out)
        aside = out == "ceo: dream: Claude Code's own dreaming is on, so this pass steps aside.\n"
        wrote = (notes / ".git").exists() or bool(list(self.briefs.iterdir())) or any(  # not even the owner's file
            (self.tmp / "home" / name).exists() for name in ("dream", "OWNER.md"))
        return aside, wrote

    def test_a_folder_pass_steps_aside_before_any_write_while_claude_codes_own_dreaming_is_on(self):
        _, notes = self.notebook()
        before = {one.name: one.read_bytes() for one in notes.iterdir()}
        for flag, setting in (({"enabled": True, "minHours": 24}, None), ({"enabled": True}, True),
                              ({"enabled": False, "available": True}, True), ({"available": True, "enabled": True}, True)):
            self.claude(flag, setting)
            self.assertEqual(self.asides(notes), (True, False), (flag, setting))
            self.assertEqual({one.name: one.read_bytes() for one in notes.iterdir()}, before)
        self.claude({"enabled": True}, False)  # the owner's setting, off, wins over the flag
        self.assertEqual(self.asides(notes), (False, True))
        self.assertEqual(self.git(notes, "log", "--format=%s").splitlines()[1:], ["dream: snapshot"])

    def test_a_folder_pass_runs_while_claude_codes_flag_or_setting_is_off_or_its_files_are_missing_or_unreadable(self):
        _, notes = self.notebook()
        cases = (({"enabled": False, "minHours": 24, "remoteEnabled": False}, True, None),  # as on the owner's accounts
                 ({"enabled": False, "available": False}, True, None), ({"available": True}, None, None),
                 ({"enabled": "true"}, True, None), (None, True, None), (None, None, None), ({}, None, "not json"),
                 ({}, None, "[]"), ({}, None, '{"cachedGrowthBookFeatures": []}'),
                 ({}, None, '{"cachedGrowthBookFeatures": {"tengu_onyx_plover": [true]}}'),
                 ({}, None, '{"cachedGrowthBookFeatures": {"tengu_onyx_plover": "enabled"}}'))
        for n, (flag, setting, raw) in enumerate(cases, 1):
            shutil.rmtree(self.tmp / "claude", ignore_errors=True)
            self.claude(flag, setting, raw)
            self.assertEqual(self.asides(notes), (False, True), (flag, setting, raw))
            self.assertEqual(len([one for one in self.briefs.iterdir() if one.suffix == ".brief"]), n)  # the model ran

    def test_a_settings_file_that_cannot_be_read_counts_as_off_while_a_missing_one_leaves_the_setting_unset(self):
        _, notes = self.notebook()
        self.claude({"enabled": True})
        self.assertEqual(self.asides(notes), (True, False))  # no settings.json: the setting is unset, the flag's enabled decides
        settings = self.tmp / "claude" / "settings.json"
        for n, raw in enumerate((b"\xff", b"not json", b"[]", None), 1):
            shutil.rmtree(settings, ignore_errors=True)
            settings.unlink(missing_ok=True)
            settings.mkdir() if raw is None else settings.write_bytes(raw)  # None: a folder in its place, which cannot be read
            self.assertEqual(self.asides(notes), (False, True), raw)
            self.assertEqual(len([one for one in self.briefs.iterdir() if one.suffix == ".brief"]), n)  # the model ran

    def test_claude_codes_files_are_read_in_the_home_folder_when_no_config_folder_is_named(self):
        _, notes = self.notebook()
        self.env.pop("CLAUDE_CONFIG_DIR")
        self.env["HOME"] = str(self.tmp / "user")
        self.claude({"enabled": True}, True)
        self.assertEqual(self.asides(notes), (True, False))  # ~/.claude.json, the flag's cache, and the setting beside it
        (self.tmp / "user" / ".claude.json").unlink()
        self.assertEqual(self.asides(notes), (False, True))

    def test_a_page_pass_never_steps_aside_for_claude_codes_dreaming(self):
        self.claude({"enabled": True}, True)
        code, out = self.dream("LESSONS.md", "fold")
        self.assertEqual(code, 0, out)
        self.assertNotIn("steps aside", out)
        self.assertEqual(len(self.passes()), 1)

    def test_a_lessons_pass_landing_on_main_while_the_door_pushes_is_a_record(self):
        self.assertTrue(ceo.is_record("LESSONS.md"))
        self.assertFalse(ceo.is_record("docs/LESSONS.md"))


if __name__ == "__main__":
    unittest.main()
