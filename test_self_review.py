"""Workers end on one line, not their reviewer's brief: real runs and reviews, with only the models stubbed."""
import unittest

from ceo import ENDING, review_brief
from test_ceo import order_text
from test_door import DoorCase
from test_owner import BLOCK, LINE, STUB


class SelfReview(DoorCase):
    def setUp(self):
        super().setUp()
        self.stub.write_text(STUB)
        home = self.tmp / "home"
        home.mkdir()
        (home / "OWNER.md").write_text(LINE + "\n")
        self.env["CEO_HOME"] = str(home)
        self.given = self.tmp / "given"
        self.given.mkdir()
        self.record = self.model("record", str(self.given))

    def given_to(self, part, slug):
        return (self.given / f"{part}-{slug}").read_bytes()

    def test_the_worker_is_given_its_order_and_one_last_line_and_its_reviewer_the_same_word(self):
        slug = "115-plain"
        raw = order_text(f"test -f {slug}.txt").encode()
        self.commit_order(f"{slug}.md", raw.decode())
        run = self.ceo("run", f"orders/{slug}.md", "--worker", self.record)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        head = self.read(slug)["head"]
        review = self.ceo("review", f"orders/{slug}.md", "--reviewer", self.record)
        self.assertEqual(review.returncode, 0, review.stdout + review.stderr)
        self.assertEqual(self.given_to("review", slug), BLOCK + review_brief(head) + raw)
        self.assertEqual(self.given_to("wt", slug), BLOCK + raw + ENDING)
        self.assertEqual((ENDING[:1], ENDING[1:].count(b"\n"), ENDING[-1:]), (b"\n", 1, b"\n"))  # one line
        for words in (b"Never change LESSONS.md: only the kernel writes it", b"refused before review",
                      b"final answer, one line `Lesson: <one line>` each"):
            self.assertIn(words, ENDING)
        for brief in (ENDING, review_brief(head)):
            self.assertIn(b"the door runs the whole suite once, on the joined commit", b" ".join(brief.split()))
        self.assertNotIn(b"You are a fresh reviewer", self.given_to("wt", slug))

    def test_the_last_line_is_added_once_and_does_not_grow_with_the_order(self):
        for slug, goal in (("116-short", "Build it."), ("117-long", "Build it. " * 5000)):
            with self.subTest(slug):
                raw = order_text(f"test -f {slug}.txt", goal=goal).encode()
                self.commit_order(f"{slug}.md", raw.decode())
                run = self.ceo("run", f"orders/{slug}.md", "--worker", self.record)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                worker = self.given_to("wt", slug)
                self.assertEqual(worker, BLOCK + raw + ENDING)
                self.assertEqual((worker.count(raw), worker.count(ENDING)), (1, 1))

    def test_no_worker_of_mocks_or_spins_is_given_the_review_brief(self):
        for slug, kind in (("118-mocks", "Mocks"), ("119-spins", "Spins")):
            with self.subTest(kind):
                raw = (order_text("test -f built") + f"\n## {kind}\n- red\n- blue\n"
                       "\n## Preview\n`previews/card.html`\n").encode()
                self.commit_order(f"{slug}.md", raw.decode())
                run = self.ceo("run", f"orders/{slug}.md", "--worker", self.record)
                self.assertEqual(run.returncode, 1, run.stdout + run.stderr)  # record draws no previews
                if kind == "Mocks":
                    self.assertEqual(self.given_to("wt", slug), BLOCK + raw +
                                     b"\nDraw every mock in this worktree; each preview file:\n"
                                     b"1. red: previews/card-1.html\n2. blue: previews/card-2.html\n")
                else:
                    for n, line in enumerate(("red", "blue"), 1):
                        self.assertEqual(self.given_to("wt", f"{slug}-spin-{n}"),
                                         BLOCK + raw + f"\nYour spin: {line}\n".encode())
        briefs = list(self.given.iterdir())
        self.assertEqual(len(briefs), 3)
        for brief in briefs:
            self.assertNotIn(b"You are a fresh reviewer", brief.read_bytes())


if __name__ == "__main__":
    unittest.main()
