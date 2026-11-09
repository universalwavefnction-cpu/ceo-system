"""Real Git diffs distinguish JS/TS tests from production additions at the review limit, and find
a run that wrote LESSONS.md, the kernel's page, from where its branch left main."""
import os
import shlex
import sys
import unittest
from unittest.mock import patch

from ceo_review import SPLIT_IT, oversized, wrote_lessons
from test_tick import TickCase


class ReviewSize(TickCase):
    def test_root_and_nested_test_and_spec_files_do_not_count_toward_the_limit(self):
        self.commit_order("001-size.md")
        raw = (self.repo / "orders" / "001-size.md").read_bytes()
        self.git("checkout", "-qb", "work")
        (self.repo / "goals.mjs").write_text("export {};\n" * 166)
        for name in ("goals.test.js", "goals.spec.ts", "tools/desk/batch.test.tsx",
                     "tools/desk/batch.spec.mjs"):
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("// test case\n" * 177)
        self.git("add", "-A")
        self.git("commit", "-qm", "166 code lines and JS/TS tests")
        with patch.dict(os.environ, self.env):
            self.assertEqual(oversized({"commit": self.git("rev-parse", "HEAD")}, raw, self.repo), "")
            (self.repo / "tools" / "desk" / "batch_checks.mjs").write_text("export {};\n" * 135)
            self.git("add", "-A")
            self.git("commit", "-qm", "301 code lines and JS/TS tests")
            self.assertEqual(oversized({"commit": self.git("rev-parse", "HEAD")}, raw, self.repo),
                             "adds 301 lines: split it")

    def test_a_run_that_changes_lessons_md_is_refused_and_one_that_only_joined_the_kernels_page_is_not(self):
        (self.repo / "LESSONS.md").write_text("A lesson the kernel wrote.\n")
        self.commit_order("002-page.md")
        before = self.git("rev-parse", "HEAD")
        self.git("checkout", "-qb", "work")
        (self.repo / "built").write_text("built\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "build")
        head = lambda: {"commit": self.git("rev-parse", "HEAD")}
        with patch.dict(os.environ, self.env):
            self.assertEqual(wrote_lessons(head(), self.repo), "")
            self.git("checkout", "-q", "main")  # the kernel's lessons pass on main, which the branch joins
            (self.repo / "LESSONS.md").write_text("A lesson the kernel wrote.\nA second one.\n")
            self.git("commit", "-qam", "Lessons pass")
            self.git("checkout", "-q", "work")
            self.git("merge", "-q", "--no-edit", "main")
            self.assertEqual(wrote_lessons(head(), self.repo), "")
            # Origin's main, when there is one, is where the branch left: a stale local main does not count.
            self.git("update-ref", "refs/remotes/origin/main", "main")
            self.git("branch", "-f", "main", before)
            self.assertEqual(wrote_lessons(head(), self.repo), "")
            self.git("update-ref", "-d", "refs/remotes/origin/main")
            self.assertIn("LESSONS.md", wrote_lessons(head(), self.repo))
            self.git("branch", "-f", "main", "work")
            with (self.repo / "LESSONS.md").open("a") as page:
                page.write("A lesson the worker wrote itself.\n")
            self.git("commit", "-qam", "build more")
            self.assertEqual(wrote_lessons(head(), self.repo),
                             "it changes LESSONS.md, which only the kernel writes; a lesson goes in the final answer")

    def test_a_first_run_too_big_that_also_changes_lessons_md_is_refused_for_both_size_last(self):
        (self.repo / "LESSONS.md").write_text("A lesson the kernel wrote.\n")
        self.commit_order("003-both.md")
        worker = self.tmp / "worker.py"
        worker.write_text("import subprocess, sys\nsys.stdin.buffer.read()\n"
                          "open('LESSONS.md', 'a').write('A lesson the worker wrote.\\n')\n"
                          "open('built', 'w').write('built\\n')\nopen('code.py', 'w').write('x = 1\\n' * 301)\n"
                          "subprocess.run(['git', 'add', '-A'], check=True)\n"
                          "subprocess.run(['git', 'commit', '-qm', 'build'], check=True)\n")
        run = self.ceo("run", "orders/003-both.md", "--worker", shlex.join([sys.executable, str(worker)]))
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.ceo("review", "orders/003-both.md", "--reviewer", self.model("clean"))
        review = self.read("003-both", "review.json")
        self.assertEqual((review["verdict"], review["reason"], review["reviewer_exit"]),
                         ("blocked", "it changes LESSONS.md, which only the kernel writes; a lesson goes in the "
                                     "final answer; adds 302 lines: split it", None))  # no reviewer started
        self.assertTrue(review["reason"].endswith(SPLIT_IT))  # so no round follows a size refusal


if __name__ == "__main__":
    unittest.main()
