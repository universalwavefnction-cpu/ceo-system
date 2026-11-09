"""The bell: ring() records once in .ceo/bells what the owner should hear, sends nothing, and his board shows it."""
import json
import os
import re
import socket
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import board
import ceo
import ceo_status
from test_board import AT, Seen, order, rendered, row, status
from test_ceo import order_text
from test_filed import hold_ceo, stall_clock
from test_tick import TickCase

T = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc).timestamp()  # a stop after BELL_SINCE
HOUR = 3600


class BellCase(TickCase):
    def setUp(self):
        super().setUp()
        (self.repo / ".ceo").mkdir()
        self.bells = self.repo / ".ceo" / "bells"

    def commit(self, message, at=None):
        env = {**self.env, **({"GIT_AUTHOR_DATE": at, "GIT_COMMITTER_DATE": at} if at else {})}
        for args in (["add", "-A"], ["commit", "-qm", message]):
            subprocess.run(["git", *args], cwd=self.repo, env=env, check=True, capture_output=True)
        return self.git("rev-parse", "HEAD")

    def order(self, slug, ready=True, header=""):
        """An order filed in orders/; ready, with the readiness record of a pass that found it eligible at T."""
        (self.repo / "orders" / f"{slug}.md").write_text(f"# {slug}\n{header}" + order_text("false", title=""))
        if ready:
            self.record(slug, since=datetime.fromtimestamp(T, timezone.utc).isoformat(), reason="")

    def record(self, slug, **found):
        (self.repo / ".ceo" / "ready").mkdir(exist_ok=True)
        (self.repo / ".ceo" / "ready" / f"{slug}.json").write_text(json.dumps(found))

    def stop(self, slug, state, at, record="result.json", **more):
        path = self.repo / ".ceo" / "runs" / slug / record
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"order": f"orders/{slug}.md", "title": slug,
                                    "verdict" if record == "review.json" else "state": state, **more}))
        os.utime(path, (at, at))

    def started(self, at):
        with open(self.repo / ".ceo" / "events.jsonl", "a") as out:
            out.write(json.dumps({"time": datetime.fromtimestamp(at, timezone.utc).isoformat(), "event": "started",
                                  "slug": "000-x", "sentence": "s."}) + "\n")

    def watch(self, at, minutes=45):
        """The tick's stall watch at the clock at: each stop recorded once, or released by progress."""
        ceo.record_stalls(self.repo, ceo.stall_snapshot(self.repo, ceo.ready_times(self.repo)), at, minutes)

    def ring(self, at):
        """Ring once at the clock at; every bell rung so far, by key."""
        ceo.ring(self.repo, ceo.stall_snapshot(self.repo, ceo.ready_times(self.repo)), at)
        return {one["key"]: one for one in (json.loads(path.read_text()) for path in self.bells.glob("*.json"))}

    def plan(self, name, *orders):
        (self.repo / "plans").mkdir(exist_ok=True)
        (self.repo / "plans" / f"{name}.md").write_text(f"# The {name} plan\n"
                                                        + "".join(f"- orders/{one}.md — It.\n" for one in orders))


class Attention(BellCase):
    def test_a_stopped_ready_order_rings_after_30_minutes_and_once(self):
        self.order("003-failed")
        self.commit("orders")
        self.stop("003-failed", "failed", T)
        self.assertEqual(self.ring(T + 29 * 60), {})
        self.assertEqual(self.ring(T + 30 * 60), {}, "30 minutes is not over 30")
        found = self.ring(T + 31 * 60)
        key = f"attention 003-failed {int(T)}"
        self.assertEqual(list(found), [key])
        self.assertEqual({name: found[key][name] for name in ("key", "kind", "line")},
                         dict(key=key, kind="attention",
                              line="`003-failed` needs attention since 2026-10-26T09:00+00:00: failed"))
        self.assertLess(abs(datetime.fromisoformat(found[key]["time"]) - datetime.now(timezone.utc)),
                        timedelta(minutes=5))
        path = self.bells / f"attention-003-failed-{int(T)}.json"
        written = path.read_bytes()
        self.assertEqual(self.ring(T + 5 * HOUR), found)
        self.assertEqual(path.read_bytes(), written, "a bell was written twice")
        self.stop("003-failed", "failed", T + 6 * HOUR)  # a new stop is a new key: it rings once more
        self.assertEqual(len(self.ring(T + 7 * HOUR)), 2)

    def test_each_stop_state_rings_and_no_other(self):
        for slug in ("001-a", "002-b", "003-c", "004-d", "005-e", "006-f", "007-g"):
            self.order(slug)
        self.commit("orders")
        self.stop("001-a", "failed", T)
        self.stop("002-b", "fails", T, "review.json")
        self.stop("003-c", "blocked", T, "review.json")
        self.stop("004-d", "diverges", T)
        self.stop("005-e", "done", T)
        self.stop("006-f", "survives", T, "review.json")
        self.stop("007-g", "retry", T)
        self.assertEqual(sorted(one["line"].split()[0] for one in self.ring(T + HOUR).values()),
                         ["`001-a`", "`002-b`", "`003-c`", "`004-d`"])

    def test_no_bell_for_an_order_never_eligible_waiting_cut_merged_or_inactive(self):
        self.order("001-draft", ready=False)
        for slug in ("002-waits", "003-cut", "004-merged", "005-old"):
            self.order(slug)
        self.order("005-round-2", header="ROUND: 2\nREPLACES: orders/005-old.md\n")
        (self.repo / "orders" / "003-cut.cut").write_text("no\n")
        self.commit("orders")
        for slug in ("001-draft", "002-waits", "003-cut", "005-old"):
            self.stop(slug, "failed", T)
        self.stop("004-merged", "failed", T, merged="1" * 40)
        (self.repo / ".ceo" / "waiting").mkdir()
        (self.repo / ".ceo" / "waiting" / "002-waits.json").write_text(json.dumps({"order": "orders/002-waits.md"}))
        self.assertEqual(self.ring(T + HOUR), {})

    def test_no_stop_before_BELL_SINCE_rings_and_the_cutoff_is_read_when_ring_runs(self):
        self.assertEqual(ceo.BELL_SINCE, datetime(2026, 10, 25, tzinfo=timezone.utc).timestamp())
        self.order("011-old")
        self.order("012-edge")
        self.commit("orders")
        self.stop("011-old", "failed", ceo.BELL_SINCE - 1)
        self.stop("012-edge", "failed", ceo.BELL_SINCE)  # at the cut-off rings
        self.assertEqual([one["line"].split()[0] for one in self.ring(T).values()], ["`012-edge`"])
        with mock.patch.object(ceo_status, "BELL_SINCE", 0):
            self.assertEqual(len(self.ring(T)), 2)


class Idle(BellCase):
    def setUp(self):
        super().setUp()
        self.order("001-x", ready=False)
        self.commit("an order", "2026-10-25T10:00:00Z")
        (self.repo / "IDEAS.md").write_text("# Ideas\n\n## 1 — 2026-10-25\n\nAn idea.\n")
        self.idea = self.commit("Idea 1", "2026-10-25T11:00:00Z")
        self.key = f"idle {self.idea[:12]}"

    def test_no_start_for_12_hours_with_an_open_idea_rings_once(self):
        self.started(T)
        self.assertEqual(self.ring(T + 12 * HOUR), {})
        found = self.ring(T + 12 * HOUR + 1)
        self.assertEqual({key: (one["kind"], one["line"]) for key, one in found.items()}, {self.key: (
            "idle", "no start for 12 hours, while IDEAS.md changed after the newest order and nothing waits for you")})
        self.assertEqual(self.ring(T + 40 * HOUR), found)

    def test_no_start_ever_counts_as_never(self):
        self.assertEqual(list(self.ring(T)), [self.key])

    def test_a_later_start_or_a_waiting_row_keeps_it_silent(self):
        self.started(T - 30 * HOUR)
        self.started(T - 11 * HOUR)
        self.assertEqual(self.ring(T), {})
        (self.repo / ".ceo" / "waiting").mkdir()
        (self.repo / ".ceo" / "waiting" / "002-y.json").write_text("{}")
        self.assertEqual(self.ring(T + 13 * HOUR), {})
        (self.repo / ".ceo" / "waiting" / "002-y.json").unlink()
        self.assertEqual(list(self.ring(T + 13 * HOUR)), [self.key])

    def test_an_idea_no_newer_than_the_newest_order_keeps_it_silent(self):
        self.order("002-after", ready=False)
        self.commit("an order after the idea", "2026-10-25T12:00:00Z")
        self.assertEqual(self.ring(T), {})
        (self.repo / "IDEAS.md").write_text("# Ideas\n\n## 1 — 2026-10-25\n\nAn idea.\n\n## 2 — night\n\nMore.\n")
        self.order("003-night", ready=False)
        self.commit("a night: its ideas and its orders in one commit", "2026-10-25T13:00:00Z")
        self.assertEqual(self.ring(T), {}, "a night alone opened an idea")

    def test_origins_main_decides_when_the_folder_has_one(self):
        self.git("update-ref", "refs/remotes/origin/main", "HEAD~1")  # origin's main is before the idea
        self.assertEqual(self.ring(T), {})
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.assertEqual(list(self.ring(T)), [self.key])


class Unstarted(BellCase):
    def stall(self, slug):
        return json.loads((self.repo / ".ceo" / "stalls" / f"{slug}.json").read_text())

    def test_an_order_not_started_rings_once_its_stall_clock_passes_and_once_for_each_stall(self):
        self.order("001-one", ready=False)
        self.commit("an order")
        since = datetime.fromtimestamp(T, timezone.utc).isoformat()
        self.record("001-one", since=since, reason="below the disk floor")
        self.watch(T + 45 * 60)
        self.assertEqual(self.ring(T + 45 * 60), {}, "it rang before its stall clock, 45 minutes, passed")
        self.watch(T + 46 * 60)
        found = self.ring(T + 46 * 60)
        key = f"unstarted 001-one {self.stall('001-one')['since']}"
        self.assertEqual({key: (one["kind"], one["line"]) for key, one in found.items()},
                         {key: ("unstarted", "`001-one` is eligible and not started: below the disk floor")})
        self.record("001-one", since=since, reason="below the RAM floor")
        self.watch(T + HOUR)
        self.assertEqual(self.ring(T + HOUR), found, "it rang twice for one stall")
        self.record("001-one", since=since, reason="")  # its run began
        self.assertEqual(self.ring(T + HOUR), found)
        # Its worker met an api error: the run's progress restarts the clock, and a refusal after it rings once more.
        self.stop("001-one", "retry", T + 2 * HOUR)
        self.record("001-one", since=since, reason="below the disk floor")
        self.watch(T + 2 * HOUR + 45 * 60)
        self.assertEqual(self.ring(T + 2 * HOUR + 45 * 60), found, "it rang before the clock from the run passed")
        self.watch(T + 2 * HOUR + 46 * 60)
        self.assertEqual(sorted(self.ring(T + 2 * HOUR + 46 * 60)),
                         sorted([key, f"unstarted 001-one {self.stall('001-one')['since']}"]))

    def test_a_record_that_cannot_be_read_rings_nothing(self):
        self.record("001-one", since="x", reason="r")
        (self.repo / ".ceo" / "ready" / "002-two.json").write_text("{")
        self.watch(T + HOUR)
        self.assertEqual(self.ring(T + HOUR), {})


class SendsNothing(BellCase):
    def test_a_ring_writes_only_its_bells_and_opens_no_socket(self):
        self.order("003-failed")
        self.order("004-refused", ready=False)
        self.commit("two orders", "2026-10-25T10:00:00Z")
        (self.repo / "IDEAS.md").write_text("# Ideas\n")
        self.commit("an idea", "2026-10-25T11:00:00Z")
        self.record("004-refused", since="2026-10-25T10:00:00+00:00", reason="below the disk floor")
        self.stop("003-failed", "failed", T)
        self.watch(T + HOUR)  # both stopped: the one refused at every start past its stall clock
        (self.repo / "STATUS.md").write_text("# Status\n")

        def files():
            return {path: path.read_bytes() for path in self.repo.rglob("*") if path.is_file() and ".git" not in
                    path.parts and path.parent != self.bells}
        before = files()
        with mock.patch.object(socket, "socket", side_effect=AssertionError("a ring opened a socket")):
            self.ring(T + HOUR)
            found = self.ring(T + HOUR)
        self.assertEqual(sorted(one["kind"] for one in found.values()), ["attention", "idle", "unstarted"])
        self.assertEqual(files(), before, "a ring wrote outside its bells")
        self.assertFalse((self.repo / ".ceo" / "waiting").exists())


class Tick(BellCase):
    def refused(self):
        """An order filed under a plan whose worker would share its reviewer's vendor: refused at every start."""
        (self.repo / "orders" / "001-one.md").write_text(order_text("false") + "\n## Worker\nsame\n")
        with open(self.repo / "ceo.toml", "a") as conf:
            conf.write('[workers]\nsame = "same-cmd"\n')
        self.plan("stuck", "001-one")

    def test_the_tick_rings_once_a_pass_and_a_bell_cannot_stop_it(self):
        self.refused()
        self.commit("a plan")
        hold_ceo(self)
        self.tick()
        self.assertFalse(any(self.bells.glob("*.json")), "a bell before its stall clock, 45 minutes, passed")
        stall_clock(self, 0)
        self.tick()
        self.assertEqual([json.loads(path.read_text())["kind"] for path in self.bells.glob("*.json")], ["unstarted"])
        self.tick()
        self.assertEqual(len(list(self.bells.glob("*.json"))), 1, "the bell rang twice")
        for path in self.bells.glob("*.json"):
            path.unlink()
        self.bells.rmdir()
        self.bells.write_text("")  # no folder can be made for the bells
        done = self.ceo("tick")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("ceo: stalls could not be read:", done.stderr)

    def test_a_failed_fetch_with_a_cached_origin_reads_the_folders_orders_as_the_tick_does(self):
        self.commit("initial main")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")  # a cached origin's main, without the order
        self.refused()
        self.commit("a plan and its order in the folder")
        self.git("remote", "add", "origin", str(self.tmp / "missing-origin.git"))  # every fetch fails
        hold_ceo(self)
        stall_clock(self, 0)  # the alarm's clock, due at once
        for _ in range(2):
            self.tick()
            self.assertIn("Orders: orders run from your folder; origin fetch failed",
                          (self.repo / "STATUS.md").read_text())
        self.assertTrue(json.loads((self.repo / ".ceo" / "ready" / "001-one.json").read_text())["reason"])
        stall = json.loads((self.repo / ".ceo" / "stalls" / "001-one.json").read_text())["since"]
        self.assertEqual([json.loads(path.read_text())["key"] for path in self.bells.glob("*.json")],
                         [f"unstarted 001-one {stall}"])


class Board(unittest.TestCase):
    def bell(self, kind, line, ago):
        return dict(key=f"{kind} x", kind=kind, line=line, time=(AT - ago).astimezone(timezone.utc).isoformat())

    def fleet(self):
        shop = status("shop", "Mac", [order("039")], [row("039-x", "in progress", detail="building")])
        shop["bells"] = [self.bell("plan", "`plans/a.md` was sent", timedelta(minutes=5)),
                         self.bell("idle", "no start for 12 hours", timedelta(hours=2))]
        blog = status("blog", "Mac", [order("101", merged=True)], [])  # a kernel's from before bells: no key
        return shop, blog

    def test_a_bell_is_a_row_apart_from_counts_and_lists_that_flags_its_ceos_chip(self):
        shop, blog = self.fleet()
        found = {view["name"]: view for view in board.views([shop, blog], AT)}
        plain = {view["name"]: view for view in board.views([{**shop, "bells": []}, blog], AT)}
        rows = [dict(ceo="shop", number="plan", round=None, line="`plans/a.md` was sent", quiet=False),
                dict(ceo="shop", number="idle", round=None, line="no start for 12 hours", quiet=False)]
        for name in ("all", "shop", "blog"):
            self.assertEqual([found[name]["counts"][key] for key in board.COUNTS],
                             [plain[name]["counts"][key] for key in board.COUNTS], name)
            self.assertEqual(found[name]["lists"], plain[name]["lists"], name)
            self.assertEqual(set(found[name]["lists"]), set(board.LISTS))
            want = [] if name == "blog" else rows
            self.assertEqual((found[name]["bells"], found[name]["counts"]["bells"]), (want, len(want)), name)
            self.assertEqual({chip["chip"]: chip["flag"] for chip in found[name]["chips"]},
                             dict(all=False, shop=True, blog=False))
        self.assertEqual({chip["chip"]: chip["flag"] for chip in plain["all"]["chips"]},
                         dict(all=False, shop=False, blog=False))

    def test_the_board_tab_shows_the_bells_first_under_the_hero_card_and_nothing_without(self):
        shop, blog = self.fleet()
        page = rendered([shop, blog])
        seen = Seen(page)
        for name, want in (("all", 2), ("shop", 2), ("blog", 0)):
            view = page.split(f'data-view="{name}"', 1)[1].split("</section>", 1)[0]
            tab = view.split('<div data-tab="board">', 1)[1].split('<div data-tab="report"', 1)[0]
            hero = re.search(r'<div class="card hero">.*?<p class="need">.*?</p></div>', tab)
            after = tab[hero.end():]
            bells = [one for one in seen.views[name]["rows"] if one[2] == "bell"]
            if want:
                self.assertTrue(after.startswith(f'<h2>Bell <b data-count="bells">{want}</b></h2><ul class="rows">'))
                self.assertEqual(bells, [("board", "shop/plan", "bell", False), ("board", "shop/idle", "bell", False)])
                self.assertIn('<span class="state bell">bell</span>', after)
                self.assertEqual(seen.views[name]["counts"]["bells"], {str(want)})
            else:
                self.assertNotIn("<h2>Bell ", view)
                self.assertEqual(bells, [])
        report = page.split('data-view="shop"', 1)[1].split('<div data-tab="report"', 1)[1].split("</section>")[0]
        self.assertNotIn("Bell", report)

    def test_a_status_file_without_bells_renders_as_one_with_none(self):
        _, blog = self.fleet()
        self.assertNotIn("bells", blog)
        self.assertEqual(rendered([blog]), rendered([{**blog, "bells": []}]))
        self.assertNotIn("<h2>Bell ", rendered([blog]))


class StatusFile(BellCase):
    def test_the_status_file_carries_the_bells_of_the_last_day_newest_first(self):
        self.commit("start")
        self.bells.mkdir()
        nowish = datetime.now(timezone.utc)
        for name, ago in (("old", timedelta(days=1, minutes=1)), ("older", timedelta(days=3)),
                          ("hour", timedelta(hours=1)), ("minute", timedelta(minutes=1))):
            (self.bells / f"{name}.json").write_text(json.dumps(dict(key=f"plan {name}", kind="plan", line=name,
                                                                     time=(nowish - ago).isoformat())))
        (self.bells / "broken.json").write_text("{")
        out = self.tmp / "repo.json"
        done = self.ceo("board", "--name", "repo", "--out", str(out))
        self.assertEqual(done.returncode, 0, done.stderr)
        found = json.loads(out.read_text())
        self.assertEqual([one["line"] for one in found["bells"]], ["minute", "hour"])
        self.assertEqual(set(found["bells"][0]), {"key", "kind", "line", "time"})
        self.assertEqual(found["status"], json.loads(self.ceo("status", "--json").stdout))
        self.assertNotIn("bell", json.dumps(found["status"]), "a bell is a row of status --json")


if __name__ == "__main__":
    unittest.main()
