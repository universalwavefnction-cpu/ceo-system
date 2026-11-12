"""Cut-run cleanup retains unpublished worktree heads and branch tips; below the disk floor only
finished, clean, saved and idle checkouts go early."""
import contextlib
import io
import json
import os
import shutil
from unittest.mock import patch

import ceo
from ceo import claim, read_json
from test_sweep import SweepCase, age


class CutRetention(SweepCase):
    def test_a_cut_order_keeps_a_commit_no_main_or_origin_ref_holds_until_it_is_pushed(self):
        wt = self.repo / ".ceo" / "wt"
        for name in ("088-later", "089-branch"):
            self.run_done(name)
            (self.repo / "orders" / f"{name}.cut").touch()
        self.commit_all("cut")
        self.git("-C", str(wt / "088-later"), "commit", "-qm", "later", "--allow-empty")  # a clean checkout, ahead of origin
        tip = self.git("commit-tree", "-m", "kept", "-p", "order/089-branch", "order/089-branch^{tree}")
        self.git("branch", "order/089-branch-cut-1", tip)  # a branch no checkout holds, ahead of origin
        mine = [self.run_dir(name) for name in ("088-later", "089-branch")]
        for path in [*mine, wt / "088-later", wt / "089-branch", *(self.repo / "orders").glob("*.cut")]:
            age(path)
        self.assertNotIn("removed", self.sweep())
        self.assertEqual([path for path in mine if not path.is_dir()], [])
        self.assertEqual(self.branches("order/08[89]-*"), ["order/088-later", "order/089-branch", "order/089-branch-cut-1"])
        self.assertTrue((wt / "088-later").is_dir() and (wt / "089-branch").is_dir())
        for branch in ("order/088-later", "order/089-branch-cut-1"):
            self.git("push", "-q", "origin", branch)
        out = self.sweep()
        self.assertIn("removed .ceo/wt/088-later", out)
        self.assertIn("removed the branch order/089-branch-cut-1", out)
        self.assertEqual([path for path in mine if path.exists()], [])
        self.assertEqual(self.branches("order/08[89]-*"), [])
        self.assertEqual(self.on_origin("refs/heads/order/089-branch-cut-1"), tip)


class DiskPressure(SweepCase):
    def merged(self, name):
        """A done run the owner merged into main and pushed with its branch, the door's record of it written,
        its clean checkout left; the checkout's path."""
        self.run_done(name)
        self.git("merge", "-q", "--no-ff", "-m", f"merge {name}", f"order/{name}")
        self.git("push", "-q", "origin", "main", f"order/{name}")
        path = self.run_dir(name) / "result.json"
        path.write_text(json.dumps({**read_json(path), "merged": self.git("rev-parse", "HEAD"),
                                    "cleared": "2026-11-12T00:00:00+00:00"}))
        return self.repo / ".ceo" / "wt" / name

    def squeeze(self):
        """A disk floor above all the disk there is."""
        floor = int(shutil.disk_usage(self.repo).free / 1e9) + 1000
        (self.repo / "ceo.toml").write_text(f'disk_floor_gb = {floor}\nram_floor_gb = 0\nrecheck_wait = 0\n'
                                            'night = ""\ncleaner = ""\n')

    def test_below_the_disk_floor_the_tick_frees_a_young_merged_checkout_and_nothing_else(self):
        wt, record = self.merged("120-young"), self.run_dir("120-young")
        scratch = self.repo / ".ceo" / "interrupted" / "121-x-20261112T000000000000Z"
        scratch.mkdir(parents=True)
        (scratch / "record.json").write_text("{}\n")
        self.assertNotIn("removed", self.idle_tick())  # above the floor a young checkout waits two days
        self.squeeze()
        self.assertIn("ceo: removed .ceo/wt/120-young, ", self.idle_tick())  # the daily sweep has run: still at once
        self.assertFalse(wt.exists())
        self.assertNotIn(str(wt), self.git("worktree", "list", "--porcelain"))
        self.assertTrue((record / "result.json").is_file() and scratch.is_dir(), "more than the checkout went")
        self.assertEqual(self.branches("order/120-*"), ["order/120-young"])
        self.assertFalse((self.repo / ".ceo" / "swept" / "120-young").exists(), "its slug was closed early")
        age(self.repo / ".ceo")
        out = self.sweep()  # the ordinary sweep still ends what the checkout's going left
        self.assertIn("ceo: removed .ceo/runs/120-young, ", out)
        self.assertIn("ceo: removed .ceo/interrupted/121-x-20261112T000000000000Z, ", out)
        self.assertEqual(self.branches("order/120-*"), [])

    def test_below_the_disk_floor_a_checkout_with_changes_unsaved_work_a_hold_or_an_unpulled_merge_stays(self):
        names = ("130-clean", "131-dirty", "132-unsaved", "133-live", "134-attention", "135-behind")
        trees = {name: self.merged(name) for name in names}
        (trees["131-dirty"] / "notes.txt").write_text("uncommitted\n")
        self.git("-C", str(trees["132-unsaved"]), "commit", "-qm", "later", "--allow-empty")  # on no main or origin ref
        record = self.run_dir("134-attention") / "result.json"
        record.write_text(json.dumps({**read_json(record), "attention": "the owner must look"}))  # no line shown yet
        self.git("reset", "-q", "--hard", "HEAD~1")  # 135's merge is on origin, not yet in the owner's main
        lock = claim(self.repo / ".ceo" / "locks" / "133-live.run")
        try:
            with patch.dict(os.environ, self.env), contextlib.redirect_stdout(io.StringIO()) as out:
                ceo.sweep(self.repo, tight=True)
        finally:
            os.close(lock)
        self.assertIn("ceo: removed .ceo/wt/130-clean, ", out.getvalue())
        self.assertEqual([name for name, wt in trees.items() if not wt.exists()], ["130-clean"])

    def test_a_floor_that_is_no_number_or_a_ceo_toml_that_cannot_be_read_is_no_pressure(self):
        above = int(shutil.disk_usage(self.repo).free / 1e9) + 1000
        with patch.dict(os.environ, self.env):
            for text, tight in ((f"disk_floor_gb = {above}\n", True), ("disk_floor_gb = 0\n", False),
                                ('disk_floor_gb = "a lot"\n', False), ("not toml at all\n", False)):
                (self.repo / "ceo.toml").write_text(text)
                self.assertIs(ceo.low_disk(self.repo), tight, text)
