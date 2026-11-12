"""Cut-run cleanup retains unpublished worktree heads and branch tips."""
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
