"""Mocks stop at the owner: real git and processes, a local origin, stub workers; no network."""
import json
import signal
import time
import unittest
from unittest.mock import patch

import ceo
from test_ceo import kernel_patch, order_text
from test_door import DoorCase


STUB = r'''
import json, os, re, subprocess, sys, time
from pathlib import Path
mode, audit, missing, brief = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3]), sys.stdin.read()
with audit.open("a") as log:
    log.write(json.dumps({"pid": os.getpid(), "cwd": os.getcwd(), "mode": mode}) + "\n")
if mode == "review":
    raise SystemExit("a mock reached a reviewer")
files = []
for n, line, file in re.findall(r"^(\d+)\. (.*): (.+)$", brief.rsplit("each preview file:\n", 1)[-1], re.M):
    if int(n) != missing:
        path = Path(file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"<p>{line}</p>\n")
        files.append(file)
Path("built").write_text("built\n")
subprocess.run(["git", "add", "built", *files], check=True)
subprocess.run(["git", "commit", "-qm", "Draw all mocks"], check=True)
if mode == "hold":
    audit.with_suffix(".ready").touch()
    time.sleep(60)
'''


def mock_order(*lines, done_when="test -f built"):
    return (order_text(done_when, goal="The owner wants visual mocks to pick from without a review.")
            + "\n## Mocks\n" + "".join(f"- {line}\n" for line in lines)
            + "\n## Preview\n`previews/card.html`\n")


class Mocks(DoorCase):
    def setUp(self):
        super().setUp()
        self.stub.write_text(STUB)
        self.audit = self.tmp / "workers.jsonl"
        with (self.repo / "ceo.toml").open("a") as conf:
            conf.write("recheck_wait = 0\n")

    def worker(self, mode="draw", missing=0):
        return self.model(mode, str(self.audit), str(missing))

    def waiting(self, slug):
        return self.repo / ".ceo/waiting" / f"{slug}.json"

    def prepare_mock(self, slug, count=3, done_when="test -f built"):
        lines = ["red", "blue", "green"][:count]
        self.commit_order(f"{slug}.md", mock_order(*lines, done_when=done_when))
        return lines

    def run_mock(self, slug, count=3, missing=0, done_when="test -f built", code=3):
        lines = self.prepare_mock(slug, count, done_when)
        run = self.ceo("run", f"orders/{slug}.md", "--worker", self.worker(missing=missing))
        self.assertEqual(run.returncode, code, run.stdout + run.stderr)
        return lines

    def assert_no_delivery(self, slug):
        self.assertEqual(self.server(), [], "a mock opened a PR")
        self.assertEqual(self.calls(), [], "a mock called GitHub")
        self.assertFalse((self.repo / ".ceo/door").exists(), "the door ran")
        self.assertFalse((self.run_dir(slug) / "review.json").exists())
        self.assertFalse((self.repo / ".ceo/review" / slug).exists())
        self.assertFalse((self.run_dir(slug) / "vision.json").exists())
        self.assertNotIn("merged", self.read(slug))
        self.assertEqual(self.git("rev-parse", "main"), self.main())
        self.assertEqual(self.git("log", "--format=%s", "main.." + f"order/{slug}"), "Draw all mocks")
        workers = [json.loads(line) for line in self.audit.read_text().splitlines()]
        self.assertEqual(len(workers), 1, workers)
        self.assertIn(workers[0]["mode"], ("draw", "hold"))
        self.assertEqual(workers[0]["cwd"], str(self.repo / ".ceo/wt" / slug))
        self.assertEqual(self.git("branch", "--list", "order/*").strip(), f"+ order/{slug}")

    def three_ticks(self, slug):
        for _ in range(3):
            out = self.tick("--worker", self.worker(), "--reviewer", self.worker("review"))
            self.assertNotIn("started the review", out)
            self.assertNotIn("started the door", out)
        self.assert_no_delivery(slug)

    def assert_waiting(self, slug, lines):
        result = self.read(slug)
        self.assertEqual((result["state"], result["done_when_exit"]), ("waiting", 0))
        self.assertEqual(list((self.repo / ".ceo/waiting").glob("*.json")), [self.waiting(slug)])
        waiting = json.loads(self.waiting(slug).read_text())
        self.assertEqual(waiting["question"], "Which mock do you pick?")
        self.assertEqual([entry["line"] for entry in waiting["spins"]], lines)
        for n, entry in enumerate(waiting["spins"], 1):
            file = self.repo / ".ceo/wt" / slug / "previews" / f"card-{n}.html"
            self.assertEqual(self.repo / entry["preview"], file)
            self.assertEqual(file.read_text(), f"<p>{lines[n - 1]}</p>\n")
            self.assertEqual(entry["branch"], f"order/{slug}")
            self.assertEqual(entry["head"], self.git("rev-parse", entry["branch"]))
        rows = json.loads(self.ceo("status", "--json").stdout)["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "waiting")
        self.assertEqual([choice["preview"] for choice in rows[0]["choices"]],
                         [entry["preview"] for entry in waiting["spins"]])
        self.assertEqual(self.git("worktree", "list", "--porcelain").count("worktree "), 2)

    def test_three_mocks_use_one_worker_and_wait_over_three_ticks(self):
        slug = "046-three"
        lines = self.run_mock(slug)
        self.assert_waiting(slug, lines)
        self.three_ticks(slug)
        status = (self.repo / "STATUS.md").read_text()
        for n in range(1, 4):
            self.assertIn(f"previews/card-{n}.html", status)

    def test_one_mock_uses_one_worker_and_waits_over_three_ticks(self):
        slug = "046-one"
        lines = self.run_mock(slug, count=1)
        self.assert_waiting(slug, lines)
        self.three_ticks(slug)

    def test_pick_records_choice_and_keeps_branch_without_review_or_merge(self):
        slug = "046-pick"
        self.run_mock(slug)
        head = self.git("rev-parse", f"order/{slug}")
        picked = self.ceo("pick", slug, "2")
        self.assertEqual(picked.returncode, 0, picked.stderr)
        result = self.read(slug)
        self.assertEqual((result["state"], result["picked"], result["branch"]), ("done", 2, f"order/{slug}"))
        self.assertEqual(result["mocks"][result["picked"] - 1]["line"], "blue")
        self.assertFalse(self.waiting(slug).exists())
        self.three_ticks(slug)
        self.assertEqual(self.git("rev-parse", f"order/{slug}"), head)
        row = json.loads(self.ceo("status", "--json").stdout)["rows"][0]
        self.assertEqual(row["state"], "finished")
        self.assertIn(f"order/{slug}", row["detail"])
        self.assertNotIn("## Waiting for your word", (self.repo / "STATUS.md").read_text())
        snapshot = ceo.stall_snapshot(self.repo, {slug: time.time()})[slug]  # ready, and a day passes
        self.assertFalse(ceo.stalled(snapshot, time.time() + 86400))

    def test_the_answer_path_of_the_ears_records_a_pick_on_the_next_tick(self):
        slug = "046-answer"
        self.run_mock(slug)
        answer = self.ceo("answer", slug, "3", "--comment", "the green one")
        self.assertEqual(answer.returncode, 0, answer.stderr)
        self.three_ticks(slug)
        self.assertEqual(self.read(slug)["picked"], 3)
        self.assertFalse(self.waiting(slug).exists())
        self.assertFalse((self.repo / ".ceo/answers" / f"{slug}.json").exists())

    def test_interrupted_pick_is_finished_by_the_tick_without_promotion(self):
        slug = "046-pick-cut"
        self.run_mock(slug)
        with kernel_patch("event", side_effect=OSError("interrupted after recording the pick")):
            with self.assertRaises(OSError):
                ceo.pick(self.repo, slug, 2)
        self.assertEqual(self.read(slug)["picked"], 2)
        self.assertTrue(self.waiting(slug).exists())
        self.three_ticks(slug)
        self.assertFalse(self.waiting(slug).exists())

    def test_tick_starts_one_worker_for_a_filed_mock_order(self):
        slug = "046-filed"
        self.prepare_mock(slug)  # committed and pushed into orders/: filed
        self.tick_until((self.run_dir(slug) / "result.json").exists, "the mock run finished",
                        "--worker", self.worker(), "--reviewer", self.worker("review"))
        self.assert_waiting(slug, ["red", "blue", "green"])
        self.three_ticks(slug)

    def test_missing_preview_fails_even_when_done_when_passes(self):
        slug = "046-missing"
        self.run_mock(slug, missing=2, code=1)
        result = self.read(slug)
        self.assertEqual((result["state"], result["done_when_exit"]), ("failed", 0))
        self.assertIn("preview", result["reason"])
        self.assertFalse(self.waiting(slug).exists())
        self.three_ticks(slug)

    def test_failed_done_when_fails_even_with_every_preview(self):
        slug = "046-fails"
        self.run_mock(slug, done_when="test -f never", code=1)
        self.assertEqual(self.read(slug)["state"], "failed")
        self.assertFalse(self.waiting(slug).exists())
        self.three_ticks(slug)

    def test_mocks_and_spins_are_refused_before_anything_starts(self):
        slug = "046-both"
        self.commit_order(f"{slug}.md", mock_order("red") + "\n## Spins\n- blue\n- green\n")
        run = self.ceo("run", f"orders/{slug}.md", "--worker", self.worker())
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn("both '## Mocks' and '## Spins'", run.stderr)
        self.assertFalse(self.audit.exists())
        self.assertFalse((self.repo / ".ceo").exists())
        self.assertEqual(self.git("branch", "--list", "order/*"), "")

    def test_named_mock_worker_needs_no_reviewer_and_manual_review_is_refused(self):
        slug = "046-named"
        with (self.repo / "ceo.toml").open("a") as conf:
            conf.write("[workers]\ndraw = " + json.dumps(self.worker()) + "\n")
        self.commit_order(f"{slug}.md", mock_order("red") + "\n## Worker\ndraw\n")
        run = self.ceo("run", f"orders/{slug}.md")
        self.assertEqual(run.returncode, 3, run.stderr)
        self.assertEqual(self.ceo("pick", slug, "1").returncode, 0)
        refused = self.ceo("review", f"orders/{slug}.md")
        self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertIn("mocks go only to the owner", refused.stderr)
        self.three_ticks(slug)

    def test_recovery_keeps_mock_semantics_for_complete_and_missing_previews(self):
        for missing in (0, 2):
            with self.subTest(missing=missing):
                slug = f"046-recovery-{missing}"
                self.prepare_mock(slug)
                self.audit.with_suffix(".ready").unlink(missing_ok=True)
                run = self.ceo_bg("run", f"orders/{slug}.md", "--worker", self.worker("hold", missing))
                self.wait_for(self.audit.with_suffix(".ready").exists, "the mock worker committed its files")
                run.send_signal(signal.SIGTERM)
                run.communicate(timeout=30)
                self.assertEqual(run.returncode, 2)
                self.assertFalse((self.run_dir(slug) / "result.json").exists())
                self.idle_tick()
                result = self.read(slug)
                self.assertEqual(result["state"], "failed" if missing else "waiting")
                self.assertTrue(result["recovered"])
                self.assertEqual(self.waiting(slug).exists(), not missing)
                self.assertEqual(self.server(), [])

    def test_mock_count_and_preview_validation(self):
        for count in (0, 7):
            with self.assertRaises(ceo.Refused):
                ceo.parse_order(mock_order(*(["red"] * count)))
        self.assertEqual(len(ceo.parse_order(mock_order(*(["red"] * 6)))[2]), 6)
        for path in ("../preview.html", "/preview.html", "preview.txt"):
            with self.assertRaises(ceo.Refused):
                ceo.parse_order(mock_order("red").replace("previews/card.html", path))


if __name__ == "__main__":
    unittest.main()
