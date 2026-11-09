"""A readiness record stays while its order is eligible as the pass last read it, and once a worker of it began, a
retry's too, until the sweep of its finished order. Real git, files and ticks, stub models, no network."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import ceo
from test_ceo import kernel_patch, order_text
from test_tick import TickCase


class Kept(TickCase):
    def test_a_planned_order_whose_child_refused_between_the_passs_two_reads_keeps_its_since_and_reason(self):
        slug, path = "001-between", "orders/001-between.md"
        self.commit_order(f"{slug}.md")
        (self.repo / "plans").mkdir()
        (self.repo / "plans" / "p.md").write_text(f"# P\n- {path} — first\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "a plan of one order")
        self.idle_tick()  # a failed login holds its start: eligible, with the tick's own reason
        record = self.repo / ".ceo" / "ready" / f"{slug}.json"
        kept = {"since": ceo.read_json(record)["since"], "reason": "a floor"}
        ceo.write_json(record, kept)  # as its run child, still being admitted, will leave it when it refuses
        child = ceo.claim(self.repo / ".ceo" / "plan-starts" / slug)  # that child holds its plan's slot
        read, seen = ceo.plan_snapshot, []
        def refused_between(repo, *args, **kwargs):
            if len(seen) == 1:
                os.close(child)  # the child refused and exited after the pass chose its starts, before its second read
            seen.append(read(repo, *args, **kwargs))
            return seen[-1]
        args = SimpleNamespace(worker=self.model("build"), reviewer=None, checker=None, timeout=30, every=None)
        with patch.dict(os.environ, self.env), kernel_patch("plan_snapshot", refused_between):
            ceo.tick_pass(self.repo, args, {}, set())
        self.assertEqual([path in ceo.decide_plans(one)["start"] for one in seen], [False, True])
        self.assertEqual(ceo.read_json(record), kept)  # eligible again as the pass last read it: it stays as it was
        self.assertFalse(self.run_dir(slug).exists())

    def test_a_record_whose_worker_met_an_api_error_stays_while_its_retry_waits_for_the_owners_word(self):
        slug, limit = "001-retried", self.tmp / "limit"
        self.commit_order(f"{slug}.md")
        self.idle_tick()
        record = self.repo / ".ceo" / "ready" / f"{slug}.json"
        since = ceo.read_json(record)["since"]
        limit.touch()  # its worker meets the weekly limit, an api error: the run waits for a retry
        self.assertIn(f"ceo: started the run of orders/{slug}.md", self.tick("--worker", self.model("limit", str(limit))))
        self.wait_for(lambda: ceo.read_json(self.run_dir(slug) / "result.json").get("state") == "retry"
                      and not ceo.held(self.repo / ".ceo" / "locks" / f"{slug}.run"), "the run met an api error")
        self.assertEqual(ceo.read_json(record), {"since": since, "reason": ""})  # its worker began: the reason went
        ceo.wait_on(self.repo, slug, {"order": f"orders/{slug}.md", "title": "Retried", "decision": "retry-question",
                                      "question": "Which action follows the api error?"})
        for _ in range(2):  # waiting for the owner's word, it is not eligible; a worker of it began, so its clock stays
            self.idle_tick()
            self.assertTrue((self.repo / ".ceo" / "waiting" / f"{slug}.json").exists())
            self.assertEqual(ceo.read_json(record), {"since": since, "reason": ""})

    def test_a_round_never_started_that_a_lower_round_cap_holds_loses_its_record_and_raises_no_alarm(self):
        slug = "001-cap-lowered"
        self.commit_order(f"{slug}.md", order_text("test -f built").replace("\n\n## Goal", "\n\nROUND: 3\n\n## Goal"))
        self.idle_tick()  # below the cap of 3, eligible: a failed login holds its start
        record = self.repo / ".ceo" / "ready" / f"{slug}.json"
        self.assertTrue(ceo.read_json(record).get("reason"))
        conf = self.repo / "ceo.toml"
        conf.write_text("round_cap = 2\nstall_minutes = 0\n" + conf.read_text())  # over it now, and the alarm due at once
        for _ in range(2):
            self.idle_tick()
        self.assertIn(f"orders/{slug}.md", ceo.decide_plans(ceo.plan_snapshot(self.repo))["capped"])
        self.assertFalse(record.exists())  # a capped round is not eligible
        self.assertNotIn(f"- `{slug}` is eligible and not started", (self.repo / "STATUS.md").read_text())
        self.assertFalse(self.run_dir(slug).exists())


if __name__ == "__main__":
    unittest.main()
