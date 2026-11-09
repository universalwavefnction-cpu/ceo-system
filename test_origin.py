"""Orders filed on origin's main, with a dirty owner checkout: real git and local clones, no network or model."""
import json
import shlex
import shutil
import subprocess
import sys
import unittest

import ceo
from ceo import ENDING
from test_ceo import order_text
from test_door import FAKE_GH
from test_tick import CEO, STUB, TickCase


class Origin(TickCase):
    def setUp(self):
        super().setUp()
        self.slug = "041-origin"
        self.order = f"orders/{self.slug}.md"
        (self.repo / "code.txt").write_text("owner base\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "initial code")
        self.initial = self.git("rev-parse", "HEAD")
        self.origin = self.tmp / "origin.git"
        self.other = self.tmp / "other"
        self.git("init", "-q", "--bare", "-b", "main", str(self.origin))
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "-q", "-u", "origin", "main")
        self.git("clone", "-q", str(self.origin), str(self.other))
        agent = self.tmp / "agent"
        (agent / "gh").write_text(f"#!{sys.executable}\n{FAKE_GH}")
        (agent / "gh").chmod(0o755)
        self.env.update(FAKE_ORIGIN=str(self.origin), FAKE_GITHUB=str(self.tmp / "github.json"))
        self.stub.write_text(STUB + '\nelif mode == "record":\n    open("received.md", "wb").write(brief)\n'
                             '    subprocess.run(["git", "add", "received.md"], check=True)\n    build()\n')

    def publish(self, *slugs):
        (self.other / "orders").mkdir(exist_ok=True)
        (self.other / "code.txt").write_text("origin code\n")
        for slug in slugs or (self.slug,):
            (self.other / "orders" / f"{slug}.md").write_text(order_text(
                f'test -f built && head -c "$(wc -c < orders/{slug}.md)" received.md | '
                f"cmp -s - orders/{slug}.md && grep -qx 'origin code' code.txt",
                title="Order from origin"))
        self.git("-C", str(self.other), "add", "-A")
        self.git("-C", str(self.other), "commit", "-qm", "filed from another clone")
        self.git("-C", str(self.other), "push", "-q", "origin", "main")
        return self.git("-C", str(self.other), "rev-parse", "HEAD")

    def finished(self, slug=None):
        slug = slug or self.slug
        self.wait_for((self.run_dir(slug) / "result.json").exists, f"{slug} finished")
        result = self.read(slug)
        self.assertEqual(result["state"], "done", result)
        return result

    def status(self):
        return (self.repo / "STATUS.md").read_text()

    def test_an_order_filed_on_origin_alone_starts_with_dirty_files_untouched_and_origin_code(self):
        (self.repo / "code.txt").write_bytes(b"owner's changed code\r\n\x00")
        loose = self.repo / "loose.txt"
        loose.write_bytes(b"untracked\x00\xff\r\n")
        before = [(self.repo / "code.txt").read_bytes(), loose.read_bytes()]
        index = (self.repo / ".git" / "index").read_bytes()
        source = self.publish()
        self.assertFalse((self.repo / self.order).exists())
        self.assertEqual(self.git("rev-parse", "origin/main"), self.initial)
        self.assertIn(f"started the run of {self.order}", self.tick("--worker", self.model("record")))
        result = self.finished()
        wt = self.repo / result["worktree"]
        self.assertEqual(self.git("-C", str(wt), "rev-parse", "HEAD^"), source)
        self.assertEqual(result["source"], source)
        self.assertEqual((wt / "received.md").read_bytes(), (self.other / self.order).read_bytes() + ENDING)
        self.assertEqual([(self.repo / "code.txt").read_bytes(), loose.read_bytes()], before)
        self.assertEqual((self.repo / ".git" / "index").read_bytes(), index)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.initial)
        self.assertFalse((self.repo / self.order).exists())
        self.assertIn("your folder has local changes; orders run from origin", self.status().splitlines())
        # The review must still have the order when it exists only in the fetched tree.
        self.tick("--reviewer", self.model("clean"))
        self.wait_for((self.run_dir(self.slug) / "review.json").exists, "the origin order was reviewed")
        self.assertEqual(self.read(self.slug, "review.json")["verdict"], "survives")

    def test_local_order_text_and_an_order_in_the_folder_alone_do_not_override_origin(self):
        self.publish()
        (self.repo / self.order).write_text("not an order\n")
        (self.repo / "orders" / "042-local.md").write_text(order_text("test -f built"))  # filed here, not on origin
        self.tick("--worker", self.model("record"))
        self.finished()
        self.assertEqual((self.repo / self.order).read_text(), "not an order\n")
        self.assertFalse(self.run_dir("042-local").exists())

    def test_fetch_failure_reads_folder_even_with_a_stale_origin_main(self):
        self.publish()
        self.git("fetch", "-q", "origin", "main")
        self.git("remote", "set-url", "origin", str(self.tmp / "missing-origin.git"))
        (self.repo / "orders" / "042-local.md").write_text(order_text("test -f built"))
        self.tick("--worker", self.model("build"))
        self.finished("042-local")
        self.assertFalse(self.run_dir(self.slug).exists())
        self.assertIn("Orders: orders run from your folder; origin fetch failed", self.status())

    def test_without_origin_an_order_in_the_folder_starts_as_before(self):
        self.git("remote", "remove", "origin")
        self.commit_order(f"{self.slug}.md")
        base = self.git("rev-parse", "HEAD")
        self.tick("--worker", self.model("build"))
        result = self.finished()
        self.assertEqual(self.git("-C", str(self.repo / result["worktree"]), "rev-parse", "HEAD^"), base)
        self.assertIn("Orders: orders run from your folder; no origin", self.status())

    def test_remote_and_local_cuts_still_prevent_runs(self):
        self.publish(self.slug, "042-local-cut")
        (self.other / "orders" / f"{self.slug}.cut").touch()
        self.git("-C", str(self.other), "add", "orders")
        self.git("-C", str(self.other), "commit", "-qm", "cut order")
        self.git("-C", str(self.other), "push", "-q", "origin", "main")
        (self.repo / "orders" / "042-local-cut.cut").touch()
        self.assertNotIn("started the run", self.tick("--worker", self.model("record")))
        self.assertFalse(self.run_dir(self.slug).exists())
        self.assertFalse(self.run_dir("042-local-cut").exists())

    def test_one_fetch_per_pass_even_after_merge_and_status_source_is_replaced(self):
        source = self.publish()
        record = self.run_dir("040-merged")
        record.mkdir(parents=True)
        (record / "result.json").write_text(json.dumps({"merged": source}))
        (self.repo / "loose.txt").touch()
        real = shlex.quote(shutil.which("git"))
        log = self.tmp / "fetches"
        shim = self.tmp / "agent" / "git"
        shim.write_text(f'#!/bin/sh\nif [ "$1" = fetch ]; then echo fetch >> {shlex.quote(str(log))}; fi\n'
                        f'exec {real} "$@"\n')
        shim.chmod(0o755)
        self.idle_tick()
        self.assertEqual(log.read_text().splitlines(), ["fetch"])
        self.idle_tick()
        self.assertEqual(log.read_text().splitlines(), ["fetch", "fetch"])
        self.git("remote", "remove", "origin")
        self.idle_tick()
        self.assertEqual([line for line in self.status().splitlines() if line.startswith("Orders:")],
                         ["Orders: orders run from your folder; no origin"])
        self.assertNotIn("orders run from origin", self.status())

    def test_selected_commit_stays_pinned_when_origin_tracking_ref_moves(self):
        source = self.publish()
        real = shlex.quote(shutil.which("git"))
        shim = self.tmp / "agent" / "git"
        shim.write_text(f'#!/bin/sh\nif [ "$1" = ls-tree ]; then\n'
                        f'  {real} update-ref refs/remotes/origin/main {self.initial}\nfi\n'
                        f'exec {real} "$@"\n')
        shim.chmod(0o755)
        self.tick("--worker", self.model("record"))
        result = self.finished()
        self.assertEqual(self.git("rev-parse", "origin/main"), self.initial)
        self.assertEqual(self.git("-C", str(self.repo / result["worktree"]), "rev-parse", "HEAD^"), source)

    def test_a_plan_filed_from_another_checkout_starts_in_one_pass_while_the_folder_lags_origin(self):
        first, second, drafted = "orders/301-first.md", "orders/302-second.md", "drafts/orders/303-drafted.md"
        for path in (first, second, drafted):
            (self.other / path).parent.mkdir(parents=True, exist_ok=True)
            (self.other / path).write_text(order_text("test -f built", title=f"Order {path.rsplit('/', 1)[1][:-3]}"))
        (self.other / "plans").mkdir()
        (self.other / "drafts" / "plans").mkdir()
        (self.other / "plans/sent.md").write_text(
            f"# Sent from another checkout\nat once 2\n- {first} — Build the first.\n"
            f"- {second} after 301 — Build the second.\n")
        (self.other / "drafts/plans/drafted.md").write_text(f"# A draft, never read\n- {drafted} — Build the draft.\n")
        self.git("-C", str(self.other), "add", "-A")
        self.git("-C", str(self.other), "commit", "-qm", "a plan, its orders and a draft, filed from another checkout")
        self.git("-C", str(self.other), "push", "-q", "origin", "main")
        pushed = self.git("-C", str(self.other), "rev-parse", "HEAD")
        self.assertFalse((self.repo / "plans").exists())  # nothing has merged, so the folder lags origin
        work = ("--worker", self.model("build"), "--reviewer", self.model("clean"))
        started = [line for line in self.tick(*work).splitlines() if "started the run" in line]
        self.assertEqual(started, [f"ceo: started the run of {first}"])
        self.wait_for((self.run_dir("301-first") / "result.json").exists, "the plan's first order ended", 120)
        result = self.finished("301-first")
        self.assertEqual(result["source"], pushed)
        self.assertEqual(self.git("-C", str(self.repo / result["worktree"]), "rev-parse", "HEAD^"), pushed)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.initial)
        self.assertFalse((self.repo / "plans").exists())
        status = self.status().splitlines()
        self.assertIn(f"  - `{second}` — Build the second. — waiting for 301", status)
        self.assertNotIn("303-drafted", self.status())
        self.assertNotIn("started the run", self.tick(*work))
        self.wait_for((self.run_dir("301-first") / "review.json").exists, "the plan's order was reviewed", 120)
        self.assertEqual(self.read("301-first", "review.json")["verdict"], "survives")
        self.assertFalse(self.run_dir("303-drafted").exists())
        self.assertEqual(self.git("rev-parse", "origin/main"), pushed)
    def test_an_order_only_origin_holds_has_its_readiness_clock_and_stalls_like_one_here(self):
        (self.repo / "code.txt").write_text("owner changes\n")  # the folder lags origin: the order is not here
        with (self.repo / "ceo.toml").open("a") as conf:
            conf.write("stall_minutes = 0\n")
        self.publish()
        self.idle_tick()  # a failed login holds every start
        self.assertFalse((self.repo / self.order).exists())
        path = self.repo / ".ceo" / "ready" / f"{self.slug}.json"
        record = json.loads(path.read_text())
        record["since"] = ceo.datetime.fromtimestamp(ceo.time.time() - 120, ceo.timezone.utc).isoformat()
        path.write_text(json.dumps(record))
        decisions = self.repo / ".ceo" / "decisions"  # the stall's CEO the tick sends ends before the test's folder goes
        self.addCleanup(self.wait_for, lambda: not any(ceo.held(lock) for name in ("launch", "lock")
                                                       for lock in decisions.glob(f"*/{name}")), "the stall's CEO ended", 120)
        self.idle_tick()
        self.assertEqual(json.loads((self.repo / ".ceo" / "stalls" / f"{self.slug}.json").read_text())["state"], "ready")
        self.assertIn(f"- `{self.slug}` stalled since ", self.status())

    def test_a_night_draft_removed_on_origin_loses_its_row_while_the_folder_lags_and_one_not_pushed_keeps_its(self):
        (self.repo / ".ceo").mkdir(exist_ok=True)
        for slug, where in (("001-night-removed", self.other), ("002-night-unpushed", self.repo)):
            draft = f"drafts/orders/{slug}.md"
            (where / draft).parent.mkdir(parents=True, exist_ok=True)
            (where / draft).write_text(order_text("test -f built", title="Night finding"))
            self.git("-C", str(where), "add", draft)
            self.git("-C", str(where), "commit", "-qm", f"night draft {slug}")
            if where == self.other:  # recorded on origin's main, and main here follows it
                self.git("-C", str(where), "push", "-q", "origin", "main")
                self.git("pull", "-q", "--ff-only")
            ceo.wait_on(self.repo, slug, {"order": draft, "title": "Night finding", "night": "2026-11-04",
                                          "question": "Agree?", "claim": "A finding", "command": "test -f built",
                                          "choices": ceo.NIGHT_CHOICES})
        (self.repo / "code.txt").write_text("owner changes\n")  # the folder has changes: main here lags origin's
        self.git("-C", str(self.other), "rm", "-q", "drafts/orders/001-night-removed.md")
        self.git("-C", str(self.other), "commit", "-qm", "the night draft removed on origin")
        self.git("-C", str(self.other), "push", "-q", "origin", "main")
        for _ in range(3):  # the first pass fetches the removal, the next drops its row
            self.idle_tick()
        self.assertTrue((self.repo / "drafts/orders/001-night-removed.md").exists())
        self.assertEqual(sorted(path.stem for path in (self.repo / ".ceo" / "waiting").glob("*.json")),
                         ["002-night-unpushed"])  # only main here holds it, from a commit origin has not seen

if __name__ == "__main__":
    unittest.main()
