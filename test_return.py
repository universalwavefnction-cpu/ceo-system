"""RETURN.md, the owner's one screen on his return, as real tick passes write it in a scratch repository."""
import json
import unittest

import ceo
from test_ceo import order_text
from test_filed import hold_ceo, stall_clock
from test_tick import TickCase


class Return(TickCase):
    def screen(self):
        return (self.repo / "RETURN.md").read_text().splitlines()

    def merge(self, slug):  # its run's record, merged as main's last commit: the plan counts it merged
        run = self.run_dir(slug)
        run.mkdir(parents=True, exist_ok=True)
        (run / "result.json").write_text(json.dumps({"order": f"orders/{slug}.md", "title": slug, "state": "done",
                                                     "merged": self.git("rev-parse", "main")}))

    def test_each_pass_writes_the_owners_screen_in_40_lines_kept_out_of_git(self):
        for name in ("030-first", "031-second"):
            (self.repo / "orders" / f"{name}.md").write_text(order_text("test -f built", title=f"Order {name}"))
        (self.repo / "plans").mkdir()
        (self.repo / "plans" / "wave.md").write_text(
            "# The wave\n- orders/030-first.md — First.\n- orders/031-second.md — Second.\n")
        with open(self.repo / "ceo.toml", "a") as conf:
            conf.write('dev_url = "http://localhost:5173/"\n')
        board = self.tmp / "ceo" / "board" / "index.html"  # the page in $CEO_HOME/board, where its job writes it
        board.parent.mkdir(parents=True)
        board.write_text("<!doctype html>\n")
        drafts = self.repo / "drafts" / "orders"  # the night's findings, recorded as drafts on main
        drafts.mkdir(parents=True)
        for n in range(33, 57):
            (drafts / f"{n:03}-night.md").write_text(order_text("test -f built", title=f"Night {n}"))
        self.git("add", "-A")
        self.git("commit", "-qm", "two orders filed, their plan, and the night's drafts")
        self.merge("030-first")
        self.idle_tick()  # a failed login holds every start: nothing runs
        screen = self.screen()
        self.assertLessEqual(len(screen), 40)
        self.assertEqual(screen[0], "# repo: idle")
        self.assertIn("- `plans/wave.md` The wave: 1 of 2 orders merged", screen)
        self.assertEqual(screen[-3:], ["## Links", f"- the board: `{board}`", "- dev: http://localhost:5173/"])
        # Out of git as STATUS.md never was: by the repository's own exclude file, .gitignore as init writes it.
        self.assertNotIn("RETURN.md", self.git("status", "--porcelain", "--untracked-files=all"))
        self.assertRegex(self.git("check-ignore", "-v", "RETURN.md"), r"^\.git/info/exclude:\d+:/RETURN\.md\tRETURN\.md$")
        self.assertEqual((self.repo / ".gitignore").read_text(), ".ceo/\n")
        ceo.wait_on(self.repo, "032-asked", {"order": "orders/032-asked.md", "title": "Asked", "bug": 7,
                                             "question": ceo.CANNOT, "choices": [{"pick": 1, "text": "drop"},
                                                                                 {"pick": 2, "text": "add detail"}]})
        for n in range(33, 57):  # as many rows as wait in the kernel today, and more events than fit
            ceo.wait_on(self.repo, f"{n:03}-stopped", {"order": f"orders/{n:03}-stopped.md", "title": f"Stopped {n}",
                                                       "question": "How should this stop proceed?", "decision": f"d{n}",
                                                       "choices": ceo.DECIDE_CHOICES})
            ceo.event(self.repo, "waiting", f"{n:03}-stopped", f"Stopped {n}: how should this stop proceed?")
        ceo.event(self.repo, "waiting", "032-asked", "Asked: cannot reproduce: drop, or add detail.")
        self.idle_tick()
        screen = self.screen()
        self.assertLessEqual(len(screen), 40)
        self.assertEqual(screen[0], "# repo: needs you")
        self.assertIn("- `orders/032-asked.md` Asked: Cannot reproduce: drop, or add detail? `python3 ceo.py answer "
                      "032-asked <n> [--comment \"<detail>\"]` (1. drop; 2. add detail)", screen)
        self.assertIn("- and 17 more", screen)
        self.assertTrue(any(line.endswith("waiting 032-asked: Asked: cannot reproduce: drop, or add detail.")
                            for line in screen), screen)
        for path in (self.repo / ".ceo" / "waiting").glob("*.json"):
            path.unlink()
        self.merge("031-second")
        self.idle_tick()
        screen = self.screen()
        self.assertEqual(screen[0], "# repo: done")
        self.assertNotIn("## Plans", screen)
        self.assertNotIn("RETURN.md", self.git("status", "--porcelain", "--untracked-files=all"))

    def test_an_eligible_order_not_started_past_its_stall_clock_is_named_with_its_clock_and_reason(self):
        self.commit_order("040-held.md", order_text("test -f built", title="Held"))
        self.idle_tick()  # a failed login holds every start: its stall clock, 45 minutes, has not passed
        self.assertEqual(self.screen()[0], "# repo: idle")
        self.assertNotIn("040-held", "\n".join(self.screen()))
        stall_clock(self, 0)
        self.idle_tick()  # its stop recorded, after the screen was written
        self.idle_tick()
        since = ceo.read_json(self.repo / ".ceo" / "ready" / "040-held.json")["since"]
        screen = self.screen()
        self.assertEqual(screen[0], "# repo: needs attention")
        line = next(line for line in screen if "`040-held`" in line)
        self.assertTrue(line.startswith(f"- `040-held` is eligible and not started since {since[:16]}: the login"), line)
        self.assertEqual(screen[screen.index(line) - 1], "## Needs attention")
        hold_ceo(self)
        self.tick("--worker", self.model("build"))  # it starts: its worker begun clears its reason, and the screen drops it
        self.wait_for((self.run_dir("040-held") / "result.json").exists, "its run ended")
        self.idle_tick()
        self.assertNotIn("040-held", "\n".join(line for line in self.screen() if "is eligible" in line))
        self.assertEqual(ceo.read_json(self.repo / ".ceo" / "ready" / "040-held.json"), {"since": since, "reason": ""})


if __name__ == "__main__":
    unittest.main()
