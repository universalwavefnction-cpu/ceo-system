"""Unreadable reviewer answers recover without hiding the stop or reopening unrelated blocks."""
import unittest

from ceo_base import held
from test_ceo import order_text
from test_tick import TickCase


class ReviewRetry(TickCase):
    def stopped_review(self, slug, mode="garbage", timeout=1800):
        self.commit_order(f"{slug}.md")
        built = self.ceo("run", f"orders/{slug}.md", "--worker", self.model("build"))
        self.assertEqual(built.returncode, 0, built.stderr)
        stopped = self.ceo("review", f"orders/{slug}.md", "--reviewer", self.model(mode),
                           "--timeout", str(timeout))
        self.assertEqual(stopped.returncode, 2, stopped.stderr)
        review = self.read(slug, "review.json")
        self.assertEqual(review["verdict"], "blocked")
        return review

    def ended(self, slug, kind, record):
        """Its record is written and its lock no longer held: no process of it still writes in this test's folder."""
        return (self.run_dir(slug) / record).exists() and not held(self.repo / ".ceo" / "locks" / f"{slug}.{kind}")

    def test_unreadable_answer_is_visible_while_waiting_and_retried_on_the_same_day(self):
        for slug, mode in (("001-json", "garbage"), ("002-shape", "no-command")):
            with self.subTest(mode=mode):
                before = self.stopped_review(slug, mode)
                self.assertIn("REVIEW.json cannot be read", before["reason"])
                # This is also the blocked record older kernels left behind. Even while the login
                # prevents a start, healing keeps its cause visible and does not spend API retries.
                self.idle_tick()
                waiting = self.read(slug, "review.json")
                self.assertEqual((waiting["verdict"], waiting["reason"], waiting["retries"]),
                                 ("retry", before["reason"], before["retries"]))
                line = f"- `orders/{slug}.md` the review waits for a retry: {before['reason']}"
                self.assertIn(line, (self.repo / "STATUS.md").read_text().splitlines())
                self.assertIn(f"started the review of orders/{slug}.md", self.tick("--reviewer", self.model("clean")))
                self.wait_for(lambda: self.read(slug, "review.json")["verdict"] == "survives"
                              and self.ended(slug, "review", "review.json"), "the unreadable review recovered")
                self.assertEqual(self.read(slug, "review.json")["retries"], before["retries"])

    def test_clock_block_is_visible_and_keeps_its_final_verdict(self):
        slug = "003-clock"
        before = self.stopped_review(slug, "endless", timeout=1)
        self.assertIn("wall clock", before["reason"])
        self.assertNotIn("started the review", self.tick("--reviewer", self.model("clean")))
        self.assertEqual(self.read(slug, "review.json")["verdict"], "blocked")
        line = f"- `orders/{slug}.md` the review is blocked: {before['reason']}"
        self.assertIn(line, (self.repo / "STATUS.md").read_text().splitlines())

    def test_cut_and_superseded_unreadable_reviews_stay_blocked(self):
        for slug, stop in (("004-cut", "cut"), ("005-older", "superseded")):
            with self.subTest(stop=stop):
                before = self.stopped_review(slug)
                if stop == "cut":
                    (self.repo / "orders" / f"{slug}.cut").touch()
                else:
                    self.commit_order("005-round-2.md", "ROUND: 2\nREPLACES: orders/005-older.md\n" + order_text("test -f built"))
                said = self.tick("--reviewer", self.model("clean"))
                self.assertNotIn(f"started the review of orders/{slug}.md", said)
                if stop == "cut":
                    self.assertNotIn("ceo: started", said)
                else:  # its later round starts in its place, and runs to its end before the test's folder goes
                    self.assertIn("ceo: started the run of orders/005-round-2.md", said)
                    self.wait_for(lambda: self.ended("005-round-2", "run", "result.json"), "the run of round 2 ended")
                after = self.read(slug, "review.json")
                self.assertEqual((after["verdict"], after["reason"], after["retries"]),
                                 ("blocked", before["reason"], before["retries"]))


if __name__ == "__main__":
    unittest.main()
