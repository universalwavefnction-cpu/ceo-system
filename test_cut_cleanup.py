"""Fresh cuts reclaim only clean, idle Git worktrees whose current commits remain retained."""
import os
import subprocess
import sys
import unittest

from ceo import boot, claim, read_json, write_json
from test_ceo import order_text
from test_door import DoorCase


class CutCleanup(DoorCase):
    def finished(self, slug="036-cut", *, pushed=True, cut=True):
        self.commit_order(f"{slug}.md", order_text("test -f built.txt"))
        wt, branch = self.repo / ".ceo" / "wt" / slug, f"order/{slug}"
        self.git("worktree", "add", "-q", "--no-track", "-b", branch, str(wt), "main")
        (wt / "built.txt").write_text(f"Work for {slug}.\n")
        self.git("-C", str(wt), "add", "built.txt")
        self.git("-C", str(wt), "commit", "-qm", "Finish work")
        head = self.git("-C", str(wt), "rev-parse", "HEAD")
        if pushed:
            self.git("push", "-q", "origin", branch)
        self.run_dir(slug).mkdir(parents=True)
        write_json(self.run_dir(slug) / "result.json", dict(
            order=f"orders/{slug}.md", title=f"Finish {slug}", state="done", reason="",
            head=head, worktree=f".ceo/wt/{slug}", started="2026-11-07T12:00:00+00:00",
            ended="2026-11-07T12:01:00+00:00", done_when_exit=0, source=None))
        locks = self.repo / ".ceo" / "locks"
        locks.mkdir(exist_ok=True)
        write_json(locks / f"{slug}.json", dict(order=f"orders/{slug}.md", groups=[], boot=boot()))
        (locks / f"{slug}.run").touch()
        if cut:
            (self.repo / "orders" / f"{slug}.cut").touch()
            self.commit_all("Cut order")
        # The daily sweep must not run, and all scratch is fresh.
        last = self.repo / ".ceo" / "last-sweep"
        last.touch()
        return wt

    def retained(self, slug, wt):
        self.assertTrue((wt / ".git").is_file(), slug)
        self.assertIn(str(wt), self.git("worktree", "list", "--porcelain"))

    def removed(self, slug, wt):
        self.assertFalse(wt.exists(), slug)
        self.assertNotIn(str(wt), self.git("worktree", "list", "--porcelain"))

    def test_fresh_pushed_cut_checkout_goes_without_sweeping_records_or_branches(self):
        slug = "036-cut"
        wt = self.finished(slug)
        record = self.run_dir(slug) / "result.json"
        before = record.read_bytes()
        head = self.git("-C", str(wt), "rev-parse", "HEAD")
        review = self.repo / ".ceo" / "review" / slug
        review.mkdir(parents=True)
        (review / "evidence.txt").write_text("Review evidence\n")
        stamp = (self.repo / ".ceo" / "last-sweep").stat().st_mtime_ns
        self.assertNotIn("started the", self.tick())
        self.removed(slug, wt)
        self.assertEqual(record.read_bytes(), before)
        self.assertTrue((review / "evidence.txt").is_file())
        self.assertEqual(self.git("rev-parse", f"order/{slug}"), head)
        self.assertEqual(self.on_origin(f"refs/heads/order/{slug}"), head)
        self.assertEqual((self.repo / ".ceo" / "last-sweep").stat().st_mtime_ns, stamp)
        self.assertFalse((self.repo / ".ceo" / "swept" / slug).exists())
        events = (self.repo / ".ceo" / "events.jsonl").read_bytes()
        self.tick()  # no checkout left, and no duplicate cut event
        self.assertEqual((self.repo / ".ceo" / "events.jsonl").read_bytes(), events)

    def test_a_current_head_on_main_needs_no_pushed_order_branch(self):
        slug = "037-main"
        wt = self.finished(slug, pushed=False)
        self.git("merge", "-qm", "Keep finished work on main", f"order/{slug}")
        self.git("push", "-q", "origin", "main")
        self.assertFalse(self.on_origin(f"refs/heads/order/{slug}"))
        self.tick()
        self.removed(slug, wt)

    def test_dirty_and_new_unpushed_heads_stay_until_the_current_head_is_saved(self):
        slug = "038-local"
        wt = self.finished(slug)
        recorded = read_json(self.run_dir(slug) / "result.json")["head"]
        notes = wt / "notes.txt"
        notes.write_text("Uncommitted work\n")
        self.git("config", "status.showUntrackedFiles", "no")
        self.tick()
        self.retained(slug, wt)
        self.assertEqual(notes.read_text(), "Uncommitted work\n")
        self.git("-C", str(wt), "add", "notes.txt")
        self.git("-C", str(wt), "commit", "-qm", "Later local work")
        head = self.git("-C", str(wt), "rev-parse", "HEAD")
        self.assertNotEqual(head, recorded)
        self.tick()
        self.retained(slug, wt)
        self.git("push", "-q", "origin", f"order/{slug}:refs/heads/backup")
        self.tick()
        self.retained(slug, wt)  # another remote branch is not this order's saved head
        self.git("push", "-q", "origin", f"order/{slug}")
        self.tick()
        self.removed(slug, wt)

    def test_run_review_and_door_locks_keep_checkout_until_a_later_tick(self):
        for number, kind in enumerate(("run", "review", "door"), 39):
            with self.subTest(kind=kind):
                slug = f"{number:03}-held"
                wt = self.finished(slug)
                name = "door.lock" if kind == "door" else f"{slug}.{kind}"
                lock = claim(self.repo / ".ceo" / "locks" / name)
                self.assertIsNotNone(lock)
                try:
                    self.tick()
                    self.retained(slug, wt)
                finally:
                    os.close(lock)
                self.tick()  # the cut was already announced while its lock was held
                self.removed(slug, wt)

    def test_live_recorded_group_keeps_checkout_even_without_a_held_lock(self):
        slug = "042-group"
        wt = self.finished(slug)
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                cwd=wt, env=self.env, start_new_session=True)
        meta = self.repo / ".ceo" / "locks" / f"{slug}.json"
        write_json(meta, {**read_json(meta), "groups": [proc.pid]})
        try:
            self.tick()
            self.retained(slug, wt)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        self.tick()
        self.removed(slug, wt)

    def test_an_active_order_keeps_its_clean_pushed_checkout(self):
        slug = "043-active"
        wt = self.finished(slug, cut=False)
        record = self.run_dir(slug) / "result.json"
        write_json(record, {**read_json(record), "state": "failed", "done_when_exit": 1})
        self.tick()
        self.retained(slug, wt)


if __name__ == "__main__":
    unittest.main()
