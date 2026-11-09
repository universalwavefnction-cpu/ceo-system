"""Independent orders filed under one number: STATUS.md names each one held back, and why, while it is."""
import unittest

import ceo
from test_ceo import order_text
from test_tick import TickCase


class Collisions(TickCase):
    def file(self, slug, round_=None, replaces=""):
        head = f"# {slug}\n" + (f"ROUND: {round_}\n" if round_ else "") + (f"REPLACES: orders/{replaces}.md\n"
                                                                           if replaces else "")
        self.commit_order(f"{slug}.md", head + order_text("test -f built", title=""))

    def result(self, slug, **fields):
        self.run_dir(slug).mkdir(parents=True, exist_ok=True)
        ceo.write_json(self.run_dir(slug) / "result.json", dict(order=f"orders/{slug}.md", **fields))

    def shared(self):
        """STATUS.md's shared-number lines after a real pass; a login outage starts no worker."""
        self.idle_tick("--worker", self.model("build"))
        return [line for line in (self.repo / "STATUS.md").read_text().splitlines() if "shares number" in line]

    def test_status_names_the_order_its_namesake_holds_back_until_it_is_renumbered(self):
        self.file("005-alpha")
        self.file("005-beta")
        self.assertEqual(self.shared(), ["- `orders/005-alpha.md` is filed and cannot start: it shares number 005 "
                                         "with `orders/005-beta.md`, so they are read as rounds of one order whose "
                                         "latest is `orders/005-beta.md`: renumber it"])
        self.git("mv", "orders/005-beta.md", "orders/006-beta.md")
        self.git("commit", "-qm", "renumber beta")
        self.assertEqual(self.shared(), [])

    def test_a_merged_namesake_still_names_the_order_it_holds_back(self):
        self.file("005-alpha")
        self.result("005-alpha", state="done", merged=self.git("rev-parse", "main"))
        self.file("005-beta")
        self.assertEqual(self.shared(), ["- `orders/005-beta.md` is filed and cannot start: it shares number 005 "
                                         "with `orders/005-alpha.md`, so they are read as rounds of one order: "
                                         "renumber it"])

    def test_a_cut_latest_namesake_still_names_the_order_it_holds_back(self):
        self.file("005-alpha")
        self.file("005-beta")
        (self.repo / "orders" / "005-beta.cut").touch()
        shared = self.shared()
        self.assertEqual(len(shared), 1)
        self.assertTrue(shared[0].startswith("- `orders/005-alpha.md` is filed and cannot start: it shares number "
                                             "005 with `orders/005-beta.md`"), shared)

    def test_a_round_of_one_namesake_leaves_the_other_named(self):
        self.file("005-alpha")
        self.file("005-beta")
        self.result("005-beta", state="failed")
        self.file("005-round-2", 2, "005-beta")
        shared = self.shared()
        self.assertEqual(len(shared), 1)
        self.assertIn("`orders/005-alpha.md` is filed and cannot start", shared[0])
        self.assertIn("whose latest is `orders/005-round-2.md`", shared[0])

    def test_rounds_of_one_order_are_no_collision(self):
        self.file("015-first", 1)
        self.file("015-round-2", 2)  # an old round names no REPLACES line
        self.file("016-first")
        self.file("016-round-2", 2, "016-first")
        self.assertEqual(self.shared(), [])

    def old_family(self, merged=False):
        """005-alpha ran and failed; its old round 2, which names no REPLACES line, is the family's latest."""
        self.file("005-alpha", 1)
        self.result("005-alpha", state="failed")
        self.file("005-round-2", 2)
        if merged:
            self.result("005-round-2", state="done", merged=self.git("rev-parse", "main"))

    def test_a_new_order_under_an_old_familys_number_is_named(self):
        self.old_family()
        self.file("005-beta", 1)
        self.assertEqual(self.shared(), ["- `orders/005-beta.md` is filed and cannot start: it shares number 005 "
                                         "with `orders/005-alpha.md`, so they are read as rounds of one order whose "
                                         "latest is `orders/005-round-2.md`: renumber it"])

    def test_a_new_order_under_a_merged_old_familys_number_is_named(self):
        self.old_family(merged=True)
        self.file("005-beta", 1)
        self.assertEqual(self.shared(), ["- `orders/005-beta.md` is filed and cannot start: it shares number 005 "
                                         "with `orders/005-alpha.md`, so they are read as rounds of one order: "
                                         "renumber it"])


if __name__ == "__main__":
    unittest.main()
