"""Clean copies compare against fetched main without changing the requested head or the source."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import ceo_workspace as workspace


class Clone(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.repo, self.origin = self.tmp / "folder", self.tmp / "origin.git"
        self.git("init", "-q", "-b", "main", str(self.repo), cwd=self.tmp)
        self.stale = self.commit(self.repo, "base.txt")
        self.lock = os.open(self.tmp / "lock", os.O_CREAT | os.O_RDWR)
        self.addCleanup(os.close, self.lock)

    def raw(self, *args, cwd=None):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              cwd=cwd or self.repo, capture_output=True, text=True)

    def git(self, *args, cwd=None):
        done = self.raw(*args, cwd=cwd)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done.stdout.strip()

    def commit(self, repo, name):
        (repo / name).write_text(name + "\n")
        self.git("add", name, cwd=repo)
        self.git("commit", "-qm", name, cwd=repo)
        return self.git("rev-parse", "HEAD", cwd=repo)

    def fetched_main(self):
        self.git("init", "-q", "--bare", "-b", "main", str(self.origin))
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "-q", "-u", "origin", "main")
        self.other = self.tmp / "other"
        self.git("clone", "-q", str(self.origin), str(self.other))
        fetched = self.commit(self.other, "merged-by-another-order.txt")
        self.git("push", "-q", "origin", "main", cwd=self.other)
        self.git("fetch", "-q", "origin", "+refs/heads/main:refs/remotes/origin/main")
        self.assertNotEqual(fetched, self.stale)
        self.assertEqual(self.git("rev-parse", "main"), self.stale)
        self.assertEqual(self.git("rev-parse", "origin/main"), fetched)
        return fetched

    def order_head(self, base):
        self.git("checkout", "-q", "-b", "order/1-x", base)
        head = self.commit(self.repo, "order.txt")
        self.git("checkout", "-q", "main")
        return head

    def assert_detached(self, copy, head):
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=copy), head)
        self.assertEqual(self.raw("symbolic-ref", "-q", "HEAD", cwd=copy).returncode, 1)
        self.assertEqual(self.git("status", "--porcelain", cwd=copy), "")

    def test_fetched_main_sets_both_bases_from_a_folder_or_linked_worktree(self):
        fetched = self.fetched_main()
        head = self.order_head(fetched)
        worktree = self.tmp / "worktree"
        self.git("worktree", "add", "-q", str(worktree), "order/1-x")
        for source in (self.repo, worktree):
            with self.subTest(source=source.name):
                before = self.git("show-ref", cwd=source)
                copy = self.tmp / (source.name + "-copy")
                self.assertEqual(workspace.clone(source, head, copy, self.lock), "")
                self.assert_detached(copy, head)
                self.assertEqual(self.git("remote", cwd=copy), "")
                for ref in ("refs/heads/main", "refs/remotes/origin/main"):
                    self.assertEqual(self.git("rev-parse", ref, cwd=copy), fetched)
                for base in ("main", "origin/main"):
                    since = self.git("merge-base", "HEAD", base, cwd=copy)
                    self.assertEqual(since, fetched)
                    self.assertEqual(self.git("diff", "--name-only", since, "HEAD", cwd=copy),
                                     "order.txt")
                    self.assertEqual(self.git("diff", "--numstat", since, "HEAD", cwd=copy),
                                     "1\t0\torder.txt")
                self.assertEqual(self.git("show-ref", cwd=source), before)
                self.assertEqual(self.git("status", "--porcelain", cwd=source), "")

    def test_without_fetched_main_keeps_local_main_and_no_tracking_ref(self):
        head = self.order_head(self.stale)
        for has_origin in (False, True):
            with self.subTest(has_origin=has_origin):
                if has_origin:
                    self.git("remote", "add", "origin", str(self.origin))
                copy = self.tmp / "copy"
                self.assertEqual(workspace.clone(self.repo, head, copy, self.lock), "")
                self.assert_detached(copy, head)
                self.assertEqual(self.git("rev-parse", "refs/heads/main", cwd=copy), self.stale)
                self.assertEqual(self.raw("rev-parse", "-q", "--verify",
                                          "refs/remotes/origin/main", cwd=copy).returncode, 1)
                self.assertEqual(self.git("remote", cwd=copy), "")

    def test_door_keeps_the_origin_refs_even_when_the_folder_fetched_an_older_main(self):
        fetched = self.fetched_main()
        head = self.order_head(fetched)
        latest = self.commit(self.other, "new-on-origin.txt")
        self.git("push", "-q", "origin", "main", cwd=self.other)
        self.assertEqual(self.git("rev-parse", "origin/main"), fetched)
        copy = self.tmp / "door"
        self.assertEqual(workspace.clone(self.repo, head, copy, self.lock,
                                          url=str(self.origin), door=True), "")
        self.assert_detached(copy, head)
        self.assertEqual(self.git("remote", "get-url", "origin", cwd=copy), str(self.origin))
        for ref in ("refs/heads/main", "refs/remotes/origin/main"):
            self.assertEqual(self.git("rev-parse", ref, cwd=copy), latest)
        self.assertEqual(self.git("rev-parse", "main"), self.stale)
        self.assertEqual(self.git("rev-parse", "origin/main"), fetched)


if __name__ == "__main__":
    unittest.main()
