"""The lesson chute: a worker's `Lesson:` line on the kernel's machinery lands once in the owner's inbox, and the
second run naming its mechanism files a kernel bug through the bug door. Real git, real files, a CEO_HOME of the
test's own and a scratch kernel folder with no origin: no test files into the folder it runs from."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ceo import KERNEL, chute, fingerprint
from test_tick import TickCase

DONE_WHEN = "\n== ceo 2026-11-08T09:30:00+00:00: done-when: attempt 1\nLesson: the merge door restarts on every push\n"
PLAIN = ("Lesson: the hero image needs alt text for the screen reader.", "Lesson: review the copy before publishing.",
         "Lesson: round the card corners to 8 px.", "Lesson: pull the fonts before the build.",
         "Lesson: tick the consent box only on submit.")


def claude_log(*lines):
    """A Claude worker's stream-json, between the kernel's marks: only its result's text is its answer."""
    said = {"type": "assistant", "message": {"content": [{"type": "text", "text": "Lesson: the tick is slow"}]}}
    return ("\n== ceo 2026-11-08T09:00:00+00:00: done-when, before the worker: it must fail\nLesson: the door printed this\n"
            "\n== ceo 2026-11-08T09:00:01+00:00: worker starts: claude -p\n" + json.dumps(said) + "\n"
            + json.dumps({"type": "result", "result": "Built it.\n\n" + "\n".join(lines)}) + "\n" + DONE_WHEN)


def codex_log(*lines):
    """A Codex worker's plain text: every line of it is its own."""
    return "\n== ceo 2026-11-08T09:00:01+00:00: worker starts: codex exec\nWorking.\n" + "\n".join(lines) + "\n" + DONE_WHEN


class Chute(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.inbox = self.home / "kernel-lessons.jsonl"
        # The owner's git config, hooks and $CEO_HOME never reach the scratch folders.
        env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "CEO_HOME": str(self.home),
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.invalid", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t.invalid"}
        held = patch.dict(os.environ, env)
        held.start()
        self.addCleanup(held.stop)

    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def repository(self, name):
        repo = self.tmp / name
        repo.mkdir()
        self.git(repo, "init", "-q", "-b", "main")
        (repo / ".gitignore").write_text(".ceo/\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "start")
        return repo

    def kernel(self, name="kernel"):
        """A scratch kernel folder, a CEO project's own checkout with no origin: what it files stays on its main."""
        kernel = self.tmp / name
        (kernel / "routines").mkdir(parents=True)
        shutil.copy(KERNEL.parent / "routines" / "bug-reproduce.md", kernel / "routines")
        self.git(kernel, "init", "-q", "-b", "main")
        (kernel / ".gitignore").write_text(".ceo/\nceo.toml\n")
        self.git(kernel, "add", "-A")
        self.git(kernel, "commit", "-qm", "the kernel")
        (kernel / "ceo.toml").write_text('name = "ceo-kernel"\n')
        return kernel

    def finished(self, repo, slug, log, state="failed", ended="2026-11-08T10:00:00+00:00"):
        """A finished run's record and worker log, as ceo.py run leaves them."""
        run = repo / ".ceo" / "runs" / slug
        run.mkdir(parents=True, exist_ok=True)
        (run / "worker.log").write_text(log)
        (run / "result.json").write_text(json.dumps({
            "order": f"orders/{slug}.md", "title": slug, "state": state, "reason": "its test does not pass",
            "ended": ended, "log": f".ceo/runs/{slug}/worker.log"}))

    def chute(self, repo, kernel):
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            chute(repo, kernel)
        return said.getvalue()

    def rows(self):
        return [json.loads(line) for line in self.inbox.read_text().splitlines()] if self.inbox.exists() else []

    def bugs(self, kernel):
        return [name for name in self.git(kernel, "ls-tree", "-r", "--name-only", "main").split() if name.startswith("bugs/")]

    def read(self, repo):
        return json.loads((repo / ".ceo" / "lessons.json").read_text())

    def test_the_chute_takes_of_a_claude_worker_its_result_text_alone(self):
        shop, kernel = self.repository("shop"), self.kernel()
        self.finished(shop, "001-cart", claude_log(
            "Lesson: The merge door restarts its join when the CEO pushes a record.", "  Lesson:  the tick never starts an "
            "order whose plan was edited.", "Lesson: The merge door restarts its join when the CEO pushes a record.", *PLAIN))
        self.assertEqual(self.chute(shop, kernel), "")
        rows = self.rows()
        self.assertEqual([(row["project"], row["slug"], row["mechanism"], row["lesson"]) for row in rows], [
            (str(shop), "001-cart", "door", "The merge door restarts its join when the CEO pushes a record."),
            (str(shop), "001-cart", "tick", "the tick never starts an order whose plan was edited.")])
        self.assertTrue(all(row["time"].startswith("20") for row in rows), rows)
        self.assertEqual(self.read(shop), {"001-cart": "2026-11-08T10:00:00+00:00"})
        self.assertEqual(self.bugs(kernel), [], "one run naming a mechanism files no bug")

    def test_the_chute_takes_of_a_codex_worker_its_plain_lines_and_the_first_mechanism_named(self):
        blog, kernel = self.repository("blog"), self.kernel()
        self.finished(blog, "007-feed", codex_log(
            "Lesson: the kernel's sweep leaves a cut order's branch behind.", "Lesson: the tick's clock skips a worktree.",
            "Lesson: Round 2 of a fix carried bare Cyrillic.", "Lesson: a fresh reviewer read a stale main.",
            "Lesson: each sweep\u2019s tombstone outlives its order.", "Lesson: python3 ceo.py tick hung.", *PLAIN))
        self.chute(blog, kernel)
        self.assertEqual([(row["mechanism"], row["lesson"]) for row in self.rows()], [
            ("sweep", "the kernel's sweep leaves a cut order's branch behind."),
            ("tick", "the tick's clock skips a worktree."), ("round", "Round 2 of a fix carried bare Cyrillic."),
            ("review", "a fresh reviewer read a stale main."), ("sweep", "each sweep\u2019s tombstone outlives its order."),
            ("ceo.py", "python3 ceo.py tick hung.")])

    def test_the_chute_leaves_plain_english_out_of_the_inbox(self):
        shop, kernel = self.repository("shop"), self.kernel()
        self.finished(shop, "001-cart", claude_log(*PLAIN, "Lesson: the doormat sweeps the hall.", "Nothing to learn."))
        self.finished(shop, "002-cart", codex_log(*PLAIN))
        self.chute(shop, kernel)
        self.assertFalse(self.inbox.exists(), "a lesson naming no mechanism reached the inbox")
        self.assertEqual(sorted(self.read(shop)), ["001-cart", "002-cart"], "runs with no kernel lesson are read too")

    def test_the_chute_reads_a_run_once_each_time_it_ended_and_never_while_it_retries(self):
        shop, kernel = self.repository("shop"), self.kernel()
        self.finished(shop, "001-cart", codex_log("Lesson: the door restarts."), state="retry")
        self.finished(shop, "002-cart", codex_log("Lesson: a stall."), ended=None)
        self.chute(shop, kernel)
        self.assertEqual(self.rows(), [], "a run to be retried, or not ended, was read")
        self.finished(shop, "001-cart", codex_log("Lesson: the door restarts."))
        self.chute(shop, kernel)
        self.chute(shop, kernel)
        self.assertEqual([row["slug"] for row in self.rows()], ["001-cart"], "a run was read twice")
        self.finished(shop, "001-cart", codex_log("Lesson: the door restarts."), ended="2026-11-08T12:00:00+00:00")
        self.chute(shop, kernel)
        self.assertEqual([row["slug"] for row in self.rows()], ["001-cart", "001-cart"], "a run ended again was not read")
        self.assertEqual(self.bugs(kernel), [], "the same run, read again, is no second run")

    def test_the_chute_files_one_kernel_bug_on_the_second_run_and_adds_each_later_one_to_it(self):
        shop, blog, kernel = self.repository("shop"), self.repository("blog"), self.kernel()
        self.finished(shop, "001-cart", claude_log("Lesson: The merge door restarts its join when the CEO pushes a record.",
                                                   "Lesson: the tick never starts an order whose plan was edited."))
        self.chute(shop, kernel)
        start = self.git(kernel, "rev-parse", "main")
        self.finished(blog, "007-feed", codex_log("Lesson: a push while the door joins main restarts the door run."))
        said = self.chute(blog, kernel)
        self.assertEqual(self.bugs(kernel), ["bugs/1.md"], said)
        self.assertIn("ceo: the lessons on the kernel's door: bug 1 is filed", said)
        report = self.git(kernel, "show", "main:bugs/1.md")
        self.assertIn(f"Fingerprint: {fingerprint('kernel lesson: door')}\n", report)
        self.assertIn("from the lesson chute", report)
        for words in ("The merge door restarts its join", "a push while the door joins main"):
            self.assertIn(words, report)
        for words in (str(self.tmp), "shop", "blog", "001-cart", "007-feed", "tick never starts"):
            self.assertNotIn(words, report)
        self.assertIn("orders/001-bug-1-reproduce.md", self.git(kernel, "ls-tree", "-r", "--name-only", "main", "orders"))
        self.assertEqual(self.git(kernel, "log", "--format=%s", f"{start}..main"), "Bug 1: orders/001-bug-1-reproduce.md filed")
        # The kernel's own project counts as any other: its run on the door adds its report to the open bug.
        self.finished(kernel, "230-door-again", codex_log("Lesson: the door started over after a record was pushed."))
        self.chute(kernel, kernel)
        self.assertEqual(self.bugs(kernel), ["bugs/1.md"])
        report = self.git(kernel, "show", "main:bugs/1.md")
        self.assertEqual(report.count("\n### "), 2, report)
        self.assertIn("the door started over", report.split("\n### ")[2])
        # Once the bug is closed, the next lesson on the door files a new one.
        (kernel / "bugs" / "1.md").write_text(report + "\n## Closed\n\nfixed.\n")
        self.git(kernel, "commit", "-qam", "Bug 1: closed")
        self.finished(shop, "002-cart-round-2", claude_log("Lesson: the door's join restarts when anything is pushed."))
        self.chute(shop, kernel)
        self.assertEqual(self.bugs(kernel), ["bugs/1.md", "bugs/2.md"])
        self.assertIn(f"Fingerprint: {fingerprint('kernel lesson: door')}\n", self.git(kernel, "show", "main:bugs/2.md"))

    def test_the_chute_files_nothing_into_a_folder_that_is_no_projects_own_checkout(self):
        shop, blog, kernel = self.repository("shop"), self.repository("blog"), self.kernel()
        linked = self.tmp / "linked"
        self.git(kernel, "worktree", "add", "-q", "--detach", str(linked))
        (linked / "ceo.toml").write_text('name = "ceo-kernel"\n')
        bare = self.kernel("bare")
        (bare / "ceo.toml").unlink()
        self.finished(shop, "001-cart", codex_log("Lesson: the door restarts its join."))
        self.chute(shop, kernel)
        for n, folder in enumerate((linked, bare), 1):
            heads = {one: self.git(one, "rev-parse", "main") for one in (kernel, bare)}
            self.finished(blog, f"00{n}-feed", codex_log("Lesson: a push restarts the door run."))
            said = self.chute(blog, folder)
            self.assertEqual(said.count(str(self.inbox)), 1, said)
            self.assertIn(f"{folder} is no CEO project's own checkout, so no kernel bug is filed", said)
            self.assertEqual({one: self.git(one, "rev-parse", "main") for one in (kernel, bare)}, heads, "filed")
            self.assertIn(f"00{n}-feed", self.read(blog), "the run is not counted as read")
            self.assertEqual(self.chute(blog, folder), "", "a lesson left in the inbox is said again")
        self.assertEqual([row["slug"] for row in self.rows()], ["001-cart", "001-feed", "002-feed"])

    def test_the_chute_reads_a_run_again_when_the_inbox_cannot_be_written(self):
        shop, kernel = self.repository("shop"), self.kernel()
        self.inbox.write_text('{"time": "2026-11-08T10:00:00+00:00", "proj')  # a line a crash cut short
        kept = self.inbox.read_bytes()
        self.inbox.rename(self.home / "kept.jsonl")
        self.inbox.mkdir()
        self.finished(shop, "001-cart", codex_log("Lesson: the kernel's sweep leaves a branch behind."))
        said = self.chute(shop, kernel)
        self.assertRegex(said, r"^ceo: the lessons of 001-cart are read again next pass: .*kernel-lessons\.jsonl.*\n$")
        self.assertNotIn("001-cart", json.loads((shop / ".ceo" / "lessons.json").read_text() if (
            shop / ".ceo" / "lessons.json").exists() else "{}"))
        self.inbox.rmdir()
        (self.home / "kept.jsonl").rename(self.inbox)
        self.assertEqual(self.chute(shop, kernel), "")
        self.assertTrue(self.inbox.read_bytes().startswith(kept), "the inbox is append-only")
        lines = self.inbox.read_text().splitlines()
        self.assertEqual(len(lines), 2, "a line cut short is ended before the next is added")
        self.assertEqual(json.loads(lines[1])["mechanism"], "sweep")

    def test_the_chute_skips_alone_a_line_a_crash_cut_inside_a_letter(self):
        shop, blog, kernel = self.repository("shop"), self.repository("blog"), self.kernel()
        self.inbox.write_bytes(b'{"lesson": "cut \xd0')  # a crash cut a two-byte letter in half
        kept = self.inbox.read_bytes()
        self.finished(shop, "001-cart", codex_log("Lesson: the door restarts its join."))
        self.assertEqual(self.chute(shop, kernel), "")
        self.finished(blog, "007-feed", codex_log("Lesson: a push restarts the door run."))
        said = self.chute(blog, kernel)
        lines = self.inbox.read_bytes().splitlines()
        self.assertEqual(lines[0], kept, "the inbox is append-only, and a line cut short is ended before the next")
        self.assertEqual([json.loads(line)["slug"] for line in lines[1:]], ["001-cart", "007-feed"])
        self.assertEqual(self.bugs(kernel), ["bugs/1.md"], "the rows past a cut letter were not read: " + said)

    def test_the_chute_counts_no_run_of_a_whole_line_that_is_not_utf8(self):
        shop, blog, kernel = self.repository("shop"), self.repository("blog"), self.kernel()
        row = {"time": "2026-11-08T09:00:00+00:00", "project": str(blog), "slug": "000-damaged", "mechanism": "door",
               "lesson": "the door lost a byte: BAD"}
        damaged = json.dumps(row).encode().replace(b"BAD", b"\xff") + b"\n"  # whole JSON, one byte no letter of UTF-8
        self.inbox.write_bytes(damaged)
        self.finished(shop, "001-cart", codex_log("Lesson: the door restarts its join."))
        self.assertEqual(self.chute(shop, kernel), "")
        self.assertEqual(self.bugs(kernel), [], "a line that cannot be read counted as another run")
        self.finished(blog, "007-feed", codex_log("Lesson: a push restarts the door run."))
        said = self.chute(blog, kernel)
        lines = self.inbox.read_bytes().splitlines(keepends=True)
        self.assertEqual(lines[0], damaged, "the inbox is append-only")
        self.assertEqual([json.loads(line)["slug"] for line in lines[1:]], ["001-cart", "007-feed"])
        self.assertEqual(self.bugs(kernel), ["bugs/1.md"], "the second readable run filed no bug: " + said)
        self.assertNotIn("lost a byte", self.git(kernel, "show", "main:bugs/1.md"), "an unreadable line reached the kernel")

    def test_the_chute_adds_a_run_once_whose_read_record_could_not_be_saved(self):
        shop, blog, kernel = self.repository("shop"), self.repository("blog"), self.kernel()
        self.finished(shop, "001-cart", codex_log("Lesson: the door restarts its join."))
        (shop / ".ceo" / "lessons.json").mkdir()  # a read record that cannot be saved
        self.assertIn("ceo: the lessons of 001-cart are read again next pass: ", self.chute(shop, kernel))
        self.assertEqual([row["slug"] for row in self.rows()], ["001-cart"])
        (shop / ".ceo" / "lessons.json").rmdir()
        self.assertEqual(self.chute(shop, kernel), "")
        self.assertEqual([row["slug"] for row in self.rows()], ["001-cart"], "a run was added again once its record saved")
        self.assertEqual(self.read(shop), {"001-cart": "2026-11-08T10:00:00+00:00"})
        self.assertEqual(self.bugs(kernel), [], "the run added again counted as a second run")
        # The second run, its record not saved at first: once it is, its bug is filed, its lesson added and quoted once.
        self.finished(blog, "007-feed", codex_log("Lesson: a push restarts the door run."))
        (blog / ".ceo" / "lessons.json").mkdir()
        self.assertIn("ceo: the lessons of 007-feed are read again next pass: ", self.chute(blog, kernel))
        self.assertEqual(self.bugs(kernel), [])
        (blog / ".ceo" / "lessons.json").rmdir()
        said = self.chute(blog, kernel)
        self.assertEqual([row["slug"] for row in self.rows()], ["001-cart", "007-feed"])
        self.assertEqual(self.bugs(kernel), ["bugs/1.md"], said)
        self.assertEqual(self.git(kernel, "show", "main:bugs/1.md").count("a push restarts the door run"), 1)
        self.assertEqual(self.chute(blog, kernel), "")
        self.assertEqual(len(self.rows()), 2, "a run was read twice")

    def test_the_chute_files_no_projects_path_or_folder_name_into_the_kernel(self):
        shop, blog, kernel = self.repository("private-shop"), self.repository("blog"), self.kernel()
        self.finished(shop, "001-cart", codex_log(f"Lesson: the door restarts after Private-Shop pushes from {shop}/orders."))
        self.chute(shop, kernel)
        self.finished(blog, "007-feed", codex_log(f"Lesson: the door joins private-shop's main while {blog} pushes."))
        said = self.chute(blog, kernel)
        self.assertEqual(self.bugs(kernel), ["bugs/1.md"], said)
        report, filed = self.git(kernel, "show", "main:bugs/1.md"), self.git(kernel, "show", "main")  # bug, order, subject
        self.assertIn("- the door restarts after the project pushes from the project/orders.\n", report)
        self.assertTrue(report.endswith("\n    - the door joins the project's main while the project pushes."), report)
        for words in (str(self.tmp), "private-shop", "blog"):
            self.assertNotIn(words.lower(), filed.lower(), "a project's path or folder name reached the kernel")
        # The owner's inbox keeps each lesson as said, and the project the tick finds its run by.
        self.assertEqual([(row["project"], row["lesson"]) for row in self.rows()], [
            (str(shop), f"the door restarts after Private-Shop pushes from {shop}/orders."),
            (str(blog), f"the door joins private-shop's main while {blog} pushes.")])

    def test_the_chute_files_no_projects_path_or_folder_name_set_off_by_underscores(self):
        shop, blog, kernel = self.repository("private-shop"), self.repository("blog"), self.kernel()
        self.finished(shop, "001-cart", codex_log(f"Lesson: the door restarts for _{shop.name}_ after pushing from _{shop}_."))
        self.chute(shop, kernel)
        self.finished(blog, "007-feed", codex_log(f"Lesson: the door joins __Blog__'s main while {blog}_ci pushes blog_ci."))
        said = self.chute(blog, kernel)
        self.assertEqual(self.bugs(kernel), ["bugs/1.md"], said)
        report = self.git(kernel, "show", "main:bugs/1.md")
        self.assertIn("- the door restarts for _the project_ after pushing from _the project_.\n", report)
        self.assertTrue(report.endswith(
            "\n    - the door joins __the project__'s main while the project_ci pushes the project_ci."), report)
        for words in (str(self.tmp), "private-shop", "blog"):
            self.assertNotIn(words.lower(), report.lower(), "a project's path or folder name reached the kernel")


class ChuteInTheTick(TickCase):
    def test_a_tick_pass_goes_on_past_the_chute_whose_inbox_cannot_be_written(self):
        # One run, in a CEO_HOME of the test's own: no second run, so nothing is filed anywhere.
        run = self.repo / ".ceo" / "runs" / "001-cart"
        run.mkdir(parents=True)
        (run / "worker.log").write_text(claude_log("Lesson: the merge door restarts its join on a push."))
        (run / "result.json").write_text(json.dumps({"order": "orders/001-cart.md", "title": "Cart", "state": "failed",
                                                     "ended": "2026-11-08T10:00:00+00:00", "log": ".ceo/runs/001-cart/worker.log"}))
        inbox = Path(self.env["CEO_HOME"]) / "kernel-lessons.jsonl"
        inbox.mkdir(parents=True)
        said = self.tick()
        self.assertIn("ceo: the lessons of 001-cart are read again next pass", said)
        self.assertIn("Last tick:", (self.repo / "STATUS.md").read_text())
        inbox.rmdir()
        self.tick()
        self.assertEqual([(row["slug"], row["mechanism"]) for row in map(json.loads, inbox.read_text().splitlines())],
                         [("001-cart", "door")])


if __name__ == "__main__":
    unittest.main()
