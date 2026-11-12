"""Rejected review handoffs wait once with live heads, without replacing remote work."""
import json
import os
import subprocess
import unittest
from unittest.mock import patch

import ceo_review as review
from test_door import DoorCase


AMENDED = r'''
elif mode == "amended":
    build()
    subprocess.run(["git", "push", "-q", "origin", f"HEAD:refs/heads/order/{os.path.basename(os.getcwd())}"], check=True)
    open("built", "w").write("revised\n")
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-q", "--amend", "--no-edit"], check=True)
'''


class PrHandoff(DoorCase):
    def setUp(self):
        super().setUp()
        with self.stub.open("a") as stub:
            stub.write(AMENDED)
        self.slug = "001-revised"
        self.ref = f"refs/heads/order/{self.slug}"
        self.waiting = self.repo / ".ceo/waiting" / f"{self.slug}.json"

    def amended(self):
        self.commit_order(f"{self.slug}.md")
        run = self.ceo("run", f"orders/{self.slug}.md", "--worker", self.model("amended"))
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return self.read(self.slug), self.on_origin(self.ref)

    def ask(self, earlier, finished):
        checked = self.ceo("review", f"orders/{self.slug}.md", "--reviewer", self.model("clean"))
        self.assertEqual(checked.returncode, 2, checked.stdout + checked.stderr)
        row = json.loads(self.waiting.read_text())
        for head in (earlier, finished):
            self.assertIn(head, row["question"])
        self.assertEqual(row["choices"], review.DECIDE_CHOICES)
        self.assertTrue(row["decision"] and row["pr_handoff"])
        self.assertFalse((self.run_dir(self.slug) / "review.json").exists())
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.on_origin(self.ref), earlier)
        return self.waiting.read_bytes()

    def test_run_then_review_waits_once_and_reconciled_answer_retries(self):
        result, earlier = self.amended()
        self.assertNotEqual(earlier, result["head"])
        self.assertFalse(self.waiting.exists(), "the run's later done event would expire its own ask")
        saved = self.ask(earlier, result["head"])
        # Direct calls, ticks and the owner's status all preserve this one decision.
        with patch.dict(os.environ, self.env), patch.object(review, "git", wraps=review.git) as git:
            for _ in range(3):
                self.assertIsNone(review.ensure_pr(self.repo, self.slug, result))
            git.assert_not_called()
        for _ in range(2):
            self.assertNotIn("started the review", self.tick("--reviewer", self.model("clean")))
            self.assertEqual(self.waiting.read_bytes(), saved)
        row = json.loads(self.ceo("status", "--json").stdout)["rows"][0]
        self.assertEqual((row["state"], row["question"]), ("waiting", json.loads(saved)["question"]))
        # Simulate an explicit reconciliation, with a lease, then use the advertised answer path.
        self.git("push", "-q", f"--force-with-lease={self.ref}:{earlier}", "origin",
                 f"{result['head']}:{self.ref}")
        answered = self.ceo("answer", self.slug, "answer", "--comment", "The remote head is reconciled; retry.")
        self.assertEqual(answered.returncode, 0, answered.stderr)
        self.assertIn("started the review", self.tick("--reviewer", self.model("clean")))
        self.wait_for((self.run_dir(self.slug) / "review.json").exists, "the reconciled PR was reviewed")
        self.assertEqual(self.read(self.slug, "review.json")["verdict"], "survives")
        self.assertEqual(self.read(self.slug)["pr"], 1)
        self.assertFalse(self.waiting.exists())
        self.assertEqual(self.on_origin(self.ref), result["head"])
        self.assertEqual(len([one for one in self.calls() if one["args"][:2] == ["pr", "create"]]), 1)

    def test_unfetched_foreign_remote_head_is_named_and_preserved(self):
        result, earlier = self.amended()
        other = self.tmp / "foreign"
        self.git("clone", "-q", str(self.origin), str(other))
        self.git("-C", str(other), "checkout", "-qb", "foreign")
        (other / "remote-only").write_text("another worker's work\n")
        self.git("-C", str(other), "add", "remote-only")
        self.git("-C", str(other), "commit", "-qm", "foreign worker")
        foreign = self.git("-C", str(other), "rev-parse", "HEAD")
        self.git("-C", str(other), "push", "-q", f"--force-with-lease={self.ref}:{earlier}",
                 "origin", f"{foreign}:{self.ref}")
        unknown = subprocess.run(["git", "cat-file", "-e", foreign], cwd=self.repo,
                                 env=self.env, capture_output=True)
        self.assertNotEqual(unknown.returncode, 0, "fixture must not have the remote object locally")
        self.ask(foreign, result["head"])
        answered = self.ceo("answer", self.slug, "cut", "--comment", "Keep the foreign remote work.")
        self.assertEqual(answered.returncode, 0, answered.stderr)
        self.idle_tick()
        self.assertTrue((self.repo / "orders" / f"{self.slug}.cut").exists())
        self.assertFalse(self.waiting.exists())
        self.assertEqual(self.on_origin(self.ref), foreign)
        self.assertEqual(self.calls(), [])

    def test_other_push_failures_remain_retryable(self):
        result, earlier = self.amended()
        failed = subprocess.CompletedProcess([], 1, "", "origin is temporarily unavailable")
        with patch.dict(os.environ, self.env), patch.object(review, "git", return_value=failed) as git:
            self.assertIsNone(review.ensure_pr(self.repo, self.slug, result))
            self.assertEqual([call.args[0] for call in git.call_args_list], ["push"])
        self.assertIn("temporarily unavailable", result["reason"])
        self.assertFalse(self.waiting.exists())
        self.assertEqual(self.on_origin(self.ref), earlier)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
