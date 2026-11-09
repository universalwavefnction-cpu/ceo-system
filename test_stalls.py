"""Stalls from files alone: seeded histories and real ticks with local stub workers; no network."""
import json
import os
import random
import select
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

import ceo
from test_ceo import order_text
from test_filed import hold_ceo, stall_clock
from test_inbox import InboxCase
from test_tick import TickCase


def put(path, text, at):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (at, at))


class Histories(unittest.TestCase):
    def test_another_orders_review_is_not_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, old = Path(tmp), time.time() - 7200
            for name in ("001-round-2.md", "001-round-2-judge.md"):
                put(repo / "orders" / name, "", old)
            log = repo / ".ceo" / "review" / "001-round-2-judge" / "review.log"
            put(log, "another order is reviewed now\n", time.time())
            rows = ceo.stall_snapshot(repo, {"001-round-2": old, "001-round-2-judge": old})
            row = rows["001-round-2"]
            self.assertTrue(ceo.stalled(row, time.time()))
            self.assertEqual(row["last_progress"], old)
            self.assertEqual(ceo.stall_snapshot(repo)["001-round-2"]["state"], "filed")  # never found eligible

    def test_only_exact_review_and_replay_folders_count_as_progress(self):
        slug, old, at = "001-round-2", 100, 7300
        for suffix in ("", ".replay", "-judge", "-replay", "0", ".replay-judge", ".replayed"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                put(repo / "orders" / f"{slug}.md", "", old)
                put(repo / ".ceo" / "review" / f"{slug}{suffix}" / "nested" / "review.log", "progress\n", at)
                row = ceo.stall_snapshot(repo, {slug: old})[slug]
                own = suffix in ("", ".replay")
                self.assertEqual(row["last_progress"], at if own else old)
                self.assertEqual(ceo.stalled(row, at), not own)

    def test_320_seeded_histories_obey_the_rule_and_record_each_stop_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            expected, log_events, ready = {}, [], {}
            at = int(time.time()) - 10
            minutes = 3
            actions = ("ready", "start", "log", "event", "wait", "merge", "cut", "waiting", "now")
            covered = set()
            for seed in range(320):
                rng = random.Random(seed)
                slug = f"{seed:03}-history"
                since, merged, cut, waiting = None, False, False, False
                files, times, clock = {}, [], at - 6000
                put(repo / "orders" / f"{slug}.md", "# History\n", clock)
                for _ in range(rng.randrange(12, 40)):
                    action = rng.choice(actions)
                    covered.add(action)
                    clock += rng.randrange(1, 40)
                    if action == "ready":  # a pass found it eligible: the first such pass is its since
                        since = clock if since is None else since
                    elif action in ("start", "log", "wait"):
                        name = rng.choice((f"runs/{slug}/worker.log", f"runs/{slug}/spin/worker.log",
                                           f"review/{slug}/nested/check.log", f"review/{slug}.replay/test.log"))
                        files[name] = (clock, action)
                    elif action == "event":
                        kind = rng.choice(("started", "done", "retry", "blocked", "picked"))
                        times.append(clock)
                        log_events.append({"slug": slug, "event": kind,
                                           "time": datetime.fromtimestamp(clock, timezone.utc).isoformat()})
                    elif action == "merge":
                        merged = True
                        files[f"runs/{slug}/result.json"] = (clock, json.dumps({"state": "done", "merged": "commit"}))
                    elif action == "cut":
                        cut = not cut
                    elif action == "waiting":
                        waiting = not waiting
                    # Check every prefix against an independently accumulated history, including equality.
                    progress = max([since or 0, *times, *(changed for changed, _ in files.values())])
                    row = {"ready": since, "last_progress": progress, "merged": merged, "cut": cut, "waiting": waiting}
                    for current in (clock, progress + minutes * 60, progress + minutes * 60 + 1):
                        want = since is not None and not merged and not cut and not waiting and progress < current - 180
                        self.assertEqual(ceo.stalled(row, current, minutes), want, (seed, action, row))
                if since is not None:
                    ready[slug] = since
                if cut:
                    put(repo / "orders" / f"{slug}.cut", "", clock)
                if waiting:
                    put(repo / ".ceo" / "waiting" / f"{slug}.json", "{}", clock)
                for name, (changed, text) in files.items():
                    put(repo / ".ceo" / name, text, changed)
                expected[slug] = (row, since is not None and not merged and not cut and not waiting and at - progress > 180)
            put(repo / ".ceo" / "events.jsonl", "".join(json.dumps(e) + "\n" for e in log_events), at)
            self.assertEqual(covered, set(actions))
            snapshot = ceo.stall_snapshot(repo, ready)
            for slug, (want_row, want) in expected.items():
                for key, value in want_row.items():
                    self.assertEqual(snapshot[slug][key], value, (slug, key))
                self.assertEqual(ceo.stalled(snapshot[slug], at, minutes), want, slug)
            self.assertTrue(any(want for _, want in expected.values()))
            self.assertTrue(any(not want for _, want in expected.values()))
            ceo.record_stalls(repo, snapshot, at, minutes)
            folder = repo / ".ceo" / "stalls"
            saved = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.glob("*.json")}
            self.assertEqual(set(saved), {f"{slug}.json" for slug, (_, want) in expected.items() if want})
            for _ in range(3):
                ceo.record_stalls(repo, ceo.stall_snapshot(repo, ready), at, minutes)
                self.assertEqual(saved, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.glob("*.json")})
            notices = [e for e in ceo.events(repo)[0] if e["event"] in ("stalled", "moving")]
            self.assertEqual(len(notices), len(saved))
            self.assertTrue(all(e["event"] == "stalled" for e in notices))
            self.assertEqual((repo / "STATUS.md").read_text().count("stalled since"), len(saved))

    def test_default_boundary_and_latest_file_event_and_the_readiness_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, slug = Path(tmp), "001-progress"
            put(repo / "orders" / f"{slug}.md", "# Order", 100)
            record = repo / ".ceo" / "ready" / f"{slug}.json"  # a pass found it eligible at 200
            put(record, json.dumps({"since": datetime.fromtimestamp(200, timezone.utc).isoformat(), "reason": ""}), 200)
            put(repo / ".ceo" / "runs" / slug / "worker.log", "a", 300)
            put(repo / ".ceo" / "review" / f"{slug}.replay" / "nested" / "log", "b", 400)
            put(repo / ".ceo" / "events.jsonl", json.dumps({"slug": slug, "event": "retry",
                "time": datetime.fromtimestamp(500, timezone.utc).isoformat()}) + "\n", 900)
            put(repo / ".ceo" / "tick.log", f"first {slug}\nlast {slug}\n{slug}-other\n", 1000)
            self.assertEqual(ceo.ready_times(repo), {slug: 200})
            row = ceo.stall_snapshot(repo, ceo.ready_times(repo))[slug]
            self.assertEqual((row["ready"], row["last_progress"], row["state"], row["last_log"]),
                             (200, 500, "retry", f"last {slug}"))
            self.assertFalse(ceo.stalled(row, 3200))
            self.assertTrue(ceo.stalled(row, 3200.1))
            self.assertEqual(ceo.stall_snapshot(repo, {slug: 600})[slug]["last_progress"], 600)
            record.unlink()
            self.assertIsNone(ceo.stall_snapshot(repo, ceo.ready_times(repo))[slug]["ready"])
            self.assertFalse(ceo.stalled(ceo.stall_snapshot(repo, ceo.ready_times(repo))[slug], 10000))
            self.assertTrue(ceo.stalled(ceo.stall_snapshot(repo, {slug: 600})[slug], 10000))


class Stalls(TickCase):
    def notices(self, kind):
        return [e for e in ceo.events(self.repo)[0] if e["event"] == kind]

    def test_three_refused_passes_record_one_stop_then_a_changed_run_moves(self):
        slug = "043-refused"
        self.commit_order(f"{slug}.md")  # filed: every pass finds it eligible, and the tick's floor refuses it
        # Hold the new decision consumer while this test measures the stall monitor alone.
        self.stub.write_text(self.stub.read_text() + """
elif mode == "held-ceo":
    while not os.path.exists(args[0]):
        time.sleep(0.02)
    json.dump({"decision": "ask", "reason": "test complete", "question": "Continue?"}, open("DECISION.json", "w"))
""")
        command = self.model("held-ceo", str(self.release))
        (self.repo / "ceo.toml").write_text('disk_floor_gb = "not a number"\nram_floor_gb = 0\nstall_minutes = 0\nnight = ""\n'
                                          + "ceo = " + json.dumps(command) + "\n")
        def finish_ceo():
            self.release.touch()
            for folder in (self.repo / ".ceo" / "decisions").glob("*"):
                self.wait_for((folder / "applied.json").exists, "the held CEO finished")
        self.addCleanup(finish_ceo)
        record = self.repo / ".ceo" / "stalls" / f"{slug}.json"
        said = f"- `{slug}` is eligible and not started: a floor in ceo.toml is not a number."
        saved = None
        for n in range(1, 4):
            out = self.tick("--worker", self.model("build"))
            self.assertIn(f"ceo: not started: orders/{slug}.md: a floor in ceo.toml is not a number.", out)
            self.assertNotIn("started the run", out)
            self.assertFalse(self.run_dir(slug).exists())
            self.assertEqual((self.repo / "STATUS.md").read_text().count(said), 1)  # its alarm rides its stop, once
            self.assertEqual(list(record.parent.glob("*.json")), [record])
            seen = record.read_bytes(), record.stat().st_mtime_ns
            if saved is None:
                saved = seen
            self.assertEqual(seen, saved)
            self.assertEqual(len(self.notices("stalled")), 1)
            self.assertEqual(self.notices("moving"), [])
            self.assertEqual((self.repo / "STATUS.md").read_text().count(f"`{slug}` stalled since"), 1)
        found = json.loads(record.read_text())
        self.assertEqual(found["slug"], slug)
        self.assertEqual(found["state"], "ready")
        self.assertIn("since", found)
        self.assertIn("last_log", found)
        put(self.run_dir(slug) / "result.json", json.dumps({"state": "failed"}), time.time())
        self.idle_tick()
        self.assertFalse(record.exists())
        self.assertEqual(len(self.notices("moving")), 1)
        self.assertNotIn(f"`{slug}` stalled since", (self.repo / "STATUS.md").read_text())
        self.assertNotIn(said, (self.repo / "STATUS.md").read_text())  # it ran: no longer eligible

    def test_a_stalls_ceo_with_a_login_starts_only_once_no_star_entry_holds_every_start(self):
        slug = "045-held"
        self.commit_order(f"{slug}.md")  # filed: every pass finds it eligible, and the tick's floor refuses it
        # No ceo in ceo.toml: claude's, the test's own, an agent CLI with a login.
        (self.repo / "ceo.toml").write_text('disk_floor_gb = "not a number"\nram_floor_gb = 0\nstall_minutes = 0\nnight = ""\n')
        decisions, record = self.repo / ".ceo" / "decisions", self.repo / ".ceo" / "stalls" / f"{slug}.json"
        self.addCleanup(lambda: [self.wait_for((folder / "applied.json").exists, "the CEO finished")
                                 for folder in decisions.glob("*")])
        for _ in range(2):  # a '*' entry holds every start: the stop is recorded, and no CEO is sent on it
            self.idle_tick()
            self.assertEqual(json.loads(record.read_text())["state"], "ready")
            self.assertFalse(decisions.exists() and any(decisions.iterdir()))
        self.assertEqual(len(self.notices("stalled")), 1)
        stall = json.loads(record.read_text())
        self.tick("--worker", self.model("build"))  # the hold gone, the stall's CEO starts
        self.assertEqual([path.parent.name for path in decisions.glob("*/stall.json")],
                         [ceo.ceo_decisions.identity(stall)])

    def test_a_worker_writing_its_log_stays_moving_past_the_stall_interval(self):
        slug = "044-chatty"
        self.commit_order(f"{slug}.md")  # filed, so the first pass starts it
        with (self.repo / "ceo.toml").open("a") as conf:
            conf.write("stall_minutes = 0.05\n")
        self.stub.write_text('''import os, subprocess, sys, time
brief = sys.stdin.buffer.read()
if brief == b"Reply with the single word ok.\\n":
    print("ok")
    sys.exit(0)
while not os.path.exists(sys.argv[1]):
    print("still building", flush=True)
    time.sleep(0.05)
open("built", "w").write("built\\n")
subprocess.run(["git", "add", "built"], check=True)
subprocess.run(["git", "commit", "-qm", "build"], check=True)
''')
        args = ("--worker", self.model(str(self.release)))
        self.tick(*args)
        log = self.run_dir(slug) / "worker.log"
        self.wait_for(lambda: log.exists() and "still building" in log.read_text(), "worker log is live")
        try:
            start, previous = time.monotonic(), log.stat().st_mtime_ns
            while time.monotonic() - start < 4:
                self.wait_for(lambda: log.stat().st_mtime_ns > previous, "another worker log write")
                self.tick(*args)
                previous = log.stat().st_mtime_ns
                self.assertEqual(list((self.repo / ".ceo" / "stalls").glob("*.json")), [])
                self.assertEqual(self.notices("stalled"), [])
        finally:
            self.release.touch()
            self.wait_for((self.run_dir(slug) / "result.json").exists, "the owned worker finished")


class Readiness(TickCase):
    """The readiness record .ceo/ready/<slug>.json, the stall clock, as real passes keep it."""
    def record(self, slug):
        path = self.repo / ".ceo" / "ready" / f"{slug}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def records(self):
        return sorted(path.stem for path in (self.repo / ".ceo" / "ready").glob("*.json"))

    def status(self):
        return (self.repo / "STATUS.md").read_text()

    def rewind(self, slug):
        """As if the first pass that found it eligible were a minute ago, short of a stall: no later pass moves it."""
        since = datetime.fromtimestamp(int(time.time()) - 60, timezone.utc).isoformat()
        ceo.write_json(self.repo / ".ceo" / "ready" / f"{slug}.json", {**self.record(slug), "since": since})
        return since

    def test_since_is_the_first_eligible_pass_and_its_alarm_rides_the_stall_clock_until_it_starts(self):
        slug = "001-one"
        hold_ceo(self)
        with open(self.repo / "ceo.toml", "a") as conf:
            conf.write('[workers]\nsame = "same-cmd"\n')
        self.commit_order(f"{slug}.md", order_text("test -f built") + "\n## Worker\nsame\n")
        before = int(time.time())
        self.assertIn(f"ceo: not started: the run of orders/{slug}.md: the order's worker and its reviewer", self.tick())
        found = self.record(slug)
        self.assertLessEqual(before, datetime.fromisoformat(found["since"]).timestamp())
        self.assertLessEqual(datetime.fromisoformat(found["since"]).timestamp(), time.time())
        self.assertTrue(found["reason"].startswith("the order's worker and its reviewer are both of the vendor claude"))
        self.assertEqual(sorted(found), ["reason", "since"])  # no count
        line = f"- `{slug}` is eligible and not started: {found['reason'].rstrip('.')}."
        self.assertNotIn(line, self.status(), "its stall clock, 45 minutes, has not passed, yet it made its line")
        since = self.rewind(slug)
        stall_clock(self, 0.5)  # its since a minute ago: past a clock of half a minute
        for _ in range(2):
            self.tick()
            self.assertEqual(self.record(slug), {"since": since, "reason": found["reason"]})
            self.assertEqual(self.status().count(line), 1)
            self.assertIn(line, self.status().split("## Needs attention", 1)[1])
        self.commit_order(f"{slug}.md")  # mended: it names no worker of its reviewer's vendor
        self.assertIn(f"ceo: started the run of orders/{slug}.md", self.tick("--worker", self.model("build")))
        self.wait_for(lambda: (self.run_dir(slug) / "result.json").exists() and not ceo.held(
            self.repo / ".ceo" / "locks" / f"{slug}.run"), "the run ended")
        self.assertEqual(self.record(slug), {"since": since, "reason": ""})  # its worker began: its reason went
        self.idle_tick()  # it ran, so it is eligible no more, keeps its record, and its line is gone
        self.assertEqual(self.record(slug), {"since": since, "reason": ""})
        self.assertNotIn(line, self.status())

    def test_orders_held_by_their_plan_get_no_record_and_one_never_run_loses_its_own(self):
        (self.repo / "plans").mkdir()
        (self.repo / "plans" / "held.md").write_text(
            "# Held\n- orders/001-first.md — First.\n- orders/002-after.md after 001 — After the first.\n"
            "- orders/003-slot.md — Third.\n")
        for name in ("001-first", "002-after", "003-slot"):
            (self.repo / "orders" / f"{name}.md").write_text(order_text("test -f built"))
        self.git("add", "-A")
        self.git("commit", "-qm", "a plan of three, one at once")
        stall_clock(self, 0)  # the alarm's clock, due at once
        for _ in range(2):  # a login outage refuses the first; its after holds the second, the slot the third
            self.idle_tick()
            self.assertEqual(self.records(), ["001-first"])
        self.assertIn("  - `orders/002-after.md` — After the first. — waiting for 001\n", self.status())
        self.assertIn("  - `orders/003-slot.md` — Third. — waiting for its plan's slot\n", self.status())
        self.assertIn("- `001-first` is eligible and not started: the login of claude failed", self.status())
        (self.repo / "orders" / "001-first.cut").touch()  # cut, never run: eligible no more
        self.idle_tick()
        self.assertEqual(self.records(), ["003-slot"])  # the slot it held is free; a cut blocks the second
        self.assertTrue(self.record("003-slot")["reason"].startswith("the login of claude failed"))
        self.assertNotIn("`001-first` is eligible", self.status())

    def test_an_order_run_by_hand_gets_its_record_on_the_next_pass_while_its_family_is_unfinished(self):
        for slug in ("001-hand", "002-cut"):
            self.commit_order(f"{slug}.md")
            run = self.ceo("run", f"orders/{slug}.md", "--worker", self.model("build"))
            self.assertEqual(run.returncode, 0, run.stderr)
        (self.repo / "orders" / "002-cut.cut").touch()  # a family finished: no clock to keep
        self.assertEqual(self.records(), [])
        before = int(time.time())
        self.idle_tick()
        self.assertEqual(self.records(), ["001-hand"])
        found = self.record("001-hand")
        self.assertEqual(found, {"since": found["since"], "reason": ""})
        self.assertLessEqual(before, datetime.fromisoformat(found["since"]).timestamp())
        since = self.rewind("001-hand")
        self.idle_tick()
        self.assertEqual(self.record("001-hand"), {"since": since, "reason": ""})


class PickLifetime(InboxCase):
    def test_a_consumed_pick_finishes_its_review_before_the_tick_returns(self):
        slug = self.spun()
        ready, release = self.tmp / "review-ready", self.tmp / "review-release"
        for path in (ready, release):
            os.mkfifo(path)
        ready_fd, release_fd = (os.open(path, os.O_RDWR) for path in (ready, release))
        self.addCleanup(os.close, ready_fd)
        self.addCleanup(os.close, release_fd)
        self.stub.write_text(self.stub.read_text() + '''
elif mode == "held-review":
    if brief != b"Reply with the single word ok.\\n":
        with open(args[0], "wb", buffering=0) as ready:
            ready.write(b"ready")
        with open(args[1], "rb", buffering=0) as release:
            release.read(1)
    answer()
''')
        self.assertEqual(self.ceo("answer", slug, "2").returncode, 0)
        tick = self.ceo_bg("tick", "--worker", self.model("spin"), "--reviewer",
                           self.model("held-review", str(ready), str(release)))
        review = self.run_dir(slug) / "review.json"
        try:
            self.assertTrue(select.select([ready_fd], [], [], 30)[0], "the reviewer never reached its barrier")
            self.assertEqual(os.read(ready_fd, 5), b"ready")
            self.assertFalse(review.exists())
            with self.assertRaises(subprocess.TimeoutExpired, msg="the tick left its picked review writing"):
                tick.communicate(timeout=1)
        finally:
            os.write(release_fd, b"x")
            out, err = tick.communicate(timeout=60)
            self.wait_for(lambda: review.exists() and not ceo.held(self.repo / ".ceo" / "locks" / f"{slug}.review"),
                          "the owned reviewer released its files")
        self.assertEqual(tick.returncode, 0, err)
        self.assertIn(f"started the review of orders/{slug}.md", out)
        self.assertEqual(self.read(slug, "review.json")["verdict"], "survives")


class Loop(InboxCase):
    def test_the_every_loop_keeps_passing_while_a_picked_review_runs(self):
        slug = self.spun()
        ready, release = self.tmp / "ready", self.tmp / "release"
        for path in (ready, release):
            os.mkfifo(path)
        rfd, wfd = (os.open(path, os.O_RDWR) for path in (ready, release))
        self.stub.write_text(self.stub.read_text() + """
elif mode == "held-review":
    if brief != b"Reply with the single word ok.\\n":
        with open(args[0], "wb", buffering=0) as ready:
            ready.write(b"ready")
        with open(args[1], "rb", buffering=0) as release:
            release.read(1)
    answer()
""")
        self.assertEqual(self.ceo("answer", slug, "2").returncode, 0)
        tick = self.ceo_bg("tick", "--every", "1", "--worker", self.model("spin"),
                           "--reviewer", self.model("held-review", str(ready), str(release)))
        stamp = lambda: [line for line in (self.repo / "STATUS.md").read_text().splitlines()
                         if line.startswith("Last tick: ")]
        try:
            self.assertTrue(select.select([rfd], [], [], 60)[0], "the reviewer never started")
            self.wait_for(stamp, "the first pass wrote its Last tick")
            first, deadline = stamp(), time.monotonic() + 20
            while stamp() == first and time.monotonic() < deadline:
                time.sleep(0.2)
            later = stamp()
        finally:
            tick.kill()
            tick.communicate()
            os.write(wfd, b"x")
            self.wait_for(lambda: (self.run_dir(slug) / "review.json").exists() and not ceo.held(
                self.repo / ".ceo" / "locks" / f"{slug}.review"), "the reviewer ended", timeout=60)
        self.assertNotEqual(first, later, "no pass of the --every loop ran while the picked review was held")


if __name__ == "__main__":
    unittest.main()
