"""The wave board: a CEO's status file from a repository carried by the real tick and its filed plans, and the page
counted from such files by code alone, read as its elements carry it, with no browser."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import board
from ceo_base import claim, sign
from test_ceo import order_text
from test_tick import CEO, TickCase

AT = datetime(2026, 10, 18, 11, 0, tzinfo=timezone(timedelta(hours=2)))  # the page's time, in its own zone
FRESH = (AT - timedelta(minutes=1)).isoformat()


def order(number, round_=1, merged=False, cut=False, stalled=False):
    """A wave order of a status file, as `board --name` writes one."""
    return dict(number=number, order=f"orders/{number}-x.md", latest=f"{number}-x", round=round_,
                title=f"Order {number}", merged=merged, cut=cut, stalled=stalled)


def row(slug, state, **said):
    """A row of `status --json`."""
    return dict(slug=slug, order=f"orders/{slug}.md", sentence=f"{slug}.", since="2026-10-18T07:00:00+00:00",
                state=state, **said)


def status(name, host, orders, rows, tick=FRESH, plans=("A wave",)):
    return dict(ceo=name, host=host, time=FRESH, last_tick=tick, status=dict(rows=rows, agents=[], awake=False),
                wave=dict(plans=[dict(plan=f"plans/{n}.md", title=title, filed=FRESH) for n, title in enumerate(plans)],
                          orders=orders))


def fleet():
    """shop, whose wave holds every class (and the order of their rule where two hold), with a question
    outside its wave; blog, all merged; the kernel, with no filed plan but a question."""
    shop = status("shop", "Mac", [
        order("039"), order("040", merged=True), order("041", merged=True), order("046", 3), order("047", cut=True),
        order("048"), order("049", stalled=True), order("050", 2, stalled=True), order("051", stalled=True)], [
        row("040-x", "finished", detail="merged into main"),
        row("041-x", "needs attention", detail="merged, but it does not fit the vision"),
        row("046-x", "waiting", question="Passed its done-when. Refile?", choices=[]),
        row("047-x", "finished", detail="cut; no more work goes into it"),
        row("048-x", "needs attention", detail="its test does not pass"),
        row("049-x", "in progress", detail="a fresh reviewer checks it"),
        row("050-x", "waiting", question="Round 3 against a cap of 3?", choices=[]),
        row("051-x", "needs attention", detail="the door cannot merge it: it conflicts with main"),
        row("060-night-y", "waiting", question="Agree, decline or comment?", choices=[]),
        row("030-x", "needs attention", detail="an order of no plan")])
    blog = status("blog", "Mac", [order("101", merged=True), order("102", merged=True)], [])
    kernel = status("kernel", "vps", [], [row("130-bug-15-reproduce", "waiting", question="Cannot reproduce?")],
                    plans=())
    return [shop, blog, kernel]


class Seen(HTMLParser):
    """Each view of a page as its elements carry it: hidden or not, its parts in order, the numbers it
    shows, its rows by tab, its hosts and its chips."""
    def __init__(self, text):
        super().__init__()
        self.views, self.view, self.tab, self.want, self.links = {}, None, None, None, []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.links += [value for key, value in attrs if key in ("src", "href")]
        if "data-view" in a:
            self.view = self.views[a["data-view"]] = dict(hidden="hidden" in a, parts=[], counts={}, rows=[],
                                                          hosts={}, chips={})
        if self.view is None:
            return
        if "data-tab" in a:
            self.tab = a["data-tab"]
            self.view["parts"].append((a["data-tab"], "hidden" in a))
        if a.get("class", "").split()[:1] in (["strip"], ["chips"]):
            self.view["parts"].append(a["class"].split()[0])
        if "data-count" in a:
            self.want = ("count", a["data-count"])
        if "data-row" in a:
            self.view["rows"].append((self.tab, a["data-row"], a.get("data-state"), "data-quiet" in a))
        if "data-host" in a:
            self.view["hosts"][a["data-host"]] = "data-quiet" in a
        if "data-chip" in a:
            self.view["chips"][a["data-chip"]] = ["data-flag" in a, a.get("aria-pressed"), ""]
            self.want = ("chip", a["data-chip"])

    def handle_data(self, data):
        if self.want and self.want[0] == "count" and data.strip():
            self.view["counts"].setdefault(self.want[1], set()).add(data.strip())
            self.want = None
        elif self.want and self.want[0] == "chip":
            self.view["chips"][self.want[1]][2] += data

    def handle_endtag(self, tag):
        if tag == "button":
            self.want = None


def rendered(files, at=AT):
    return board.page(board.views(files, at), board.TEMPLATE.read_text(encoding="utf-8"))


class StatusFile(TickCase):
    def file_plans(self, plans):
        """Each plan filed in a commit of its own, at its day of October 2026."""
        (self.repo / "plans").mkdir(exist_ok=True)
        for day, name, text in plans:
            (self.repo / "plans" / f"{name}.md").write_text(text)
            self.git("add", "plans")
            subprocess.run(["git", "commit", "-qm", f"File plans/{name}.md"], cwd=self.repo, check=True,
                           env={**self.env, "GIT_COMMITTER_DATE": f"2026-10-{day}T08:00:00+00:00"})

    def test_the_status_file_holds_status_json_the_last_tick_and_the_wave_by_family(self):
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        rounds = {"001-old": "", "002-new": "", "002-new-round-2": "ROUND: 2\nREPLACES: orders/002-new.md\n",
                  "003-next": ""}
        for slug, header in rounds.items():
            (self.repo / "orders" / f"{slug}.md").write_text(f"# The {slug} order\n{header}"
                                                             + order_text("test -f built", title=""))
        self.git("add", "-A")
        self.git("commit", "-qm", "orders")
        self.file_plans([(16, "old", "# The old wave\n- orders/001-old.md — Old.\n"),  # the newer plainly newer
                         (17, "new", "# The new wave\nat once 2\n- orders/002-new.md — New.\n"
                                     "- orders/003-next.md — Next.\n")])
        self.idle_tick()  # a real pass stamps STATUS.md; with no login, nothing starts
        ceo, main = self.repo / ".ceo", self.git("rev-parse", "HEAD")  # order 001's merge, a commit on main
        for path, record in {
                "runs/001-old/result.json": {"order": "orders/001-old.md", "title": "Old", "state": "done",
                                             "merged": main, "ended": FRESH},
                "runs/002-new/result.json": {"order": "orders/002-new.md", "title": "New", "state": "failed",
                                             "reason": "its test does not pass", "ended": FRESH},
                "waiting/002-new-round-2.json": {"order": "orders/002-new-round-2.md", "title": "New, round 2",
                                                 "question": "Round 3 against a cap of 3?", "time": FRESH,
                                                 "choices": [{"pick": "cut", "text": "cut"}]}}.items():
            (ceo / path).parent.mkdir(parents=True, exist_ok=True)
            (ceo / path).write_text(json.dumps(record))

        def files():
            return {path: path.read_bytes() for path in self.repo.rglob("*") if path.is_file()}
        before, out = files(), self.tmp / "repo.json"
        done = self.ceo("board", "--name", "repo", "--host", "vps", "--out", str(out))
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)
        self.assertEqual(files(), before, "the status file wrote in the project")
        found = json.loads(out.read_text())
        listed = self.ceo("status", "--json")
        self.assertEqual(found["status"], json.loads(listed.stdout), "status is not what status --json prints")
        stamp = re.search(r"(?m)^Last tick: (.*)$", (self.repo / "STATUS.md").read_text())[1].strip()
        self.assertEqual((found["ceo"], found["host"], found["last_tick"]), ("repo", "vps", stamp))
        self.assertLess(abs(datetime.fromisoformat(found["time"]) - datetime.now(timezone.utc)), timedelta(minutes=5))
        # The older plan merged through is no wave; the newer one's order 002 stands as its round 2 does.
        self.assertEqual([(plan["plan"], plan["title"], plan["filed"]) for plan in found["wave"]["plans"]],
                         [("plans/new.md", "The new wave", "2026-10-17T08:00:00+00:00")])
        self.assertEqual(found["wave"]["orders"], [
            dict(number="002", order="orders/002-new.md", latest="002-new-round-2", round=2,
                 title="The 002-new-round-2 order", merged=False, cut=False, stalled=False, picked=False,
                 stage="queued"),
            dict(number="003", order="orders/003-next.md", latest="003-next", round=1, title="The 003-next order",
                 merged=False, cut=False, stalled=False, picked=False, stage="queued")])
        self.assertEqual(found["unreadable"], [])
        rows = {one["slug"]: one["state"] for one in found["status"]["rows"]}
        self.assertEqual((rows["002-new-round-2"], rows["002-new"]), ("waiting", "needs attention"))
        mine = board.views([found], datetime.now(timezone.utc))[1]
        self.assertEqual(mine["lists"]["need-you"], [dict(ceo="repo", number="002", round=2, quiet=False,
                                                          line="Round 3 against a cap of 3?")])
        self.assertEqual([mine["counts"][key] for key in board.COUNTS], [2, 0, 1, 0, 0, 1])
        # Its stall record makes order 003 stuck, and nothing else changes.
        (ceo / "stalls").mkdir(exist_ok=True)
        (ceo / "stalls" / "003-next.json").write_text("{}")
        self.assertEqual(self.ceo("board", "--name", "repo", "--out", str(out)).returncode, 0)
        self.assertEqual([one["stalled"] for one in json.loads(out.read_text())["wave"]["orders"]], [False, True])

    def test_one_order_family_listed_by_two_plans_counts_once(self):
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        for slug, header in {"001-first": "", "001-round-2": "ROUND: 2\nREPLACES: orders/001-first.md\n"}.items():
            (self.repo / "orders" / f"{slug}.md").write_text(f"# The {slug} order\n{header}"
                                                             + order_text("test -f built", title=""))
        self.git("add", "-A")
        self.git("commit", "-qm", "one order family")
        self.file_plans([(16, "old", "# The old wave\n- orders/001-first.md — Its round 1.\n"),
                         (17, "new", "# The new wave\n- orders/001-round-2.md — Its round 2.\n")])
        self.idle_tick()  # a real pass stamps STATUS.md; with no login, nothing starts
        waiting = self.repo / ".ceo" / "waiting" / "001-round-2.json"
        waiting.parent.mkdir(parents=True, exist_ok=True)
        waiting.write_text(json.dumps({"order": "orders/001-round-2.md", "title": "First, round 2",
                                       "question": "Proceed?", "time": FRESH,
                                       "choices": [{"pick": "cut", "text": "cut"}]}))
        out = self.tmp / "repo.json"
        done = self.ceo("board", "--name", "repo", "--host", "Mac", "--out", str(out))
        self.assertEqual(done.returncode, 0, done.stderr)
        found = json.loads(out.read_text())
        # Both plans are the wave, as round 1 is not settled; the family is in it once, where the newer plan lists it.
        self.assertEqual([plan["plan"] for plan in found["wave"]["plans"]], ["plans/new.md", "plans/old.md"])
        self.assertEqual(found["wave"]["orders"], [
            dict(number="001", order="orders/001-round-2.md", latest="001-round-2", round=2,
                 title="The 001-round-2 order", merged=False, cut=False, stalled=False, picked=False,
                 stage="queued")])
        at = datetime.now(timezone.utc)
        for view in board.views([found], at):
            self.assertEqual((view["counts"]["orders"], view["counts"]["need-you"]), (1, 1), view["name"])
            self.assertEqual(view["lists"]["need-you"], [dict(ceo="repo", number="001", round=2, quiet=False,
                                                              line="Proceed?")], view["name"])
        for name, view in Seen(rendered([found], at)).views.items():
            self.assertEqual([one for one in view["rows"] if one[0] == "board"],
                             [("board", "repo/001", "need-you", False)], name)
            self.assertEqual((view["counts"]["orders"], view["counts"]["need-you"]), ({"1"}, {"1"}), name)

    def test_a_cut_order_whose_record_names_a_merge_off_main_is_neither_merged_nor_cut_and_shows_its_stage(self):
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.commit_order("001-built.md", "# The 001-built order\n" + order_text("test -f built", title=""))
        (self.repo / "plans").mkdir()
        (self.repo / "plans" / "wave.md").write_text("# The wave\n- orders/001-built.md — Built.\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "its plan")
        self.git("checkout", "-qb", "order/001-built")
        self.git("commit", "--allow-empty", "-qm", "off main")
        off_main = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        (self.repo / "orders" / "001-built.cut").write_text("cut\n")
        (self.repo / ".ceo" / "runs" / "001-built").mkdir(parents=True)
        (self.repo / ".ceo" / "runs" / "001-built" / "result.json").write_text(
            json.dumps({"order": "orders/001-built.md", "state": "done", "merged": off_main}))
        out = self.tmp / "repo.json"
        self.assertEqual(self.ceo("board", "--name", "repo", "--out", str(out)).returncode, 0)
        # Its record settles the family as before, so it is no cut; nor is a commit off main merged.
        [one] = json.loads(out.read_text())["wave"]["orders"]
        self.assertEqual((one["merged"], one["cut"], one["picked"], one["stage"]), (False, False, False, "done"))

    def test_each_order_names_its_stage_building_in_review_while_its_agent_lives_else_queued_or_its_record(self):
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        slugs = ("001-built", "002-reviewed", "003-broken")
        for slug in slugs:
            (self.repo / "orders" / f"{slug}.md").write_text(f"# The {slug} order\n" + order_text("test -f built",
                                                                                                  title=""))
        (self.repo / "plans").mkdir()
        (self.repo / "plans" / "wave.md").write_text("# The wave\nat once 3\n" + "".join(
            f"- orders/{slug}.md — {slug}.\n" for slug in slugs))
        self.git("add", "-A")
        self.git("commit", "-qm", "three orders and their plan")
        ceo, out = self.repo / ".ceo", self.tmp / "repo.json"
        for slug, text in (("002-reviewed", json.dumps({"order": "orders/002-reviewed.md", "state": "done",
                                                         "head": self.git("rev-parse", "HEAD")})),
                           ("003-broken", '{"order": "orders/003-broken.md", "state": "do')):
            (ceo / "runs" / slug).mkdir(parents=True)
            (ceo / "runs" / slug / "result.json").write_text(text)

        def stages():
            done = self.ceo("board", "--name", "repo", "--out", str(out))
            self.assertEqual(done.returncode, 0, done.stderr)
            found = json.loads(out.read_text())
            self.assertEqual(found["unreadable"], [".ceo/runs/003-broken/result.json"])
            return {one["latest"]: (one["stage"], one["merged"], one["picked"]) for one in found["wave"]["orders"]}
        # A worker holds order 001's run lock, a reviewer order 002's review lock, each signed as an agent signs it.
        locks = [claim(ceo / "locks" / name) for name in ("001-built.run", "002-reviewed.review")]
        try:
            for lock in locks:
                sign(lock)
            self.assertEqual(stages(), {"001-built": ("building", False, False),
                                        "002-reviewed": ("in review", False, False),
                                        "003-broken": ("queued", False, False)})
        finally:
            for lock in locks:
                os.close(lock)
        # Both gone: order 001 has no run record yet, and order 002, built with no reviewer at work, is done.
        self.assertEqual(stages(), {"001-built": ("queued", False, False), "002-reviewed": ("done", False, False),
                                    "003-broken": ("queued", False, False)})

    def test_the_status_file_carries_the_memory_over_what_loads_as_status_json_lists_it(self):
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.git("add", "-A")
        self.git("commit", "-qm", "start")
        (self.repo / "LESSONS.md").write_text("- a lesson\n" * 201)
        out = self.tmp / "repo.json"
        self.assertEqual(self.ceo("board", "--name", "repo", "--out", str(out)).returncode, 0)
        memory = json.loads(out.read_text())["status"]["memory"]
        self.assertEqual(memory, json.loads(self.ceo("status", "--json").stdout)["memory"])
        self.assertEqual(memory, [dict(file=str(self.repo / "LESSONS.md"), lines=201, bytes=2211)])


class Views(unittest.TestCase):
    def test_each_ceo_view_counts_its_own_wave_and_all_sums_them(self):
        files = fleet()
        found = board.views(files, AT)
        self.assertEqual([view["name"] for view in found], ["all", "shop", "blog", "kernel"])
        want = {"shop": [9, 2, 2, 1, 2, 3], "blog": [2, 2, 0, 0, 0, 0], "kernel": [0, 0, 0, 0, 0, 1]}
        want["all"] = [sum(numbers) for numbers in zip(*want.values())]
        for view in found:
            self.assertEqual([view["counts"][key] for key in board.COUNTS], want[view["name"]], view["name"])
        shop = found[1]["lists"]
        self.assertEqual({key: [(one["number"], one["line"]) for one in rows] for key, rows in shop.items()}, {
            "need-you": [("046", "Passed its done-when. Refile?"), ("050", "Round 3 against a cap of 3?")],
            "stuck": [("049", "a fresh reviewer checks it")],
            "parked": [("048", "its test does not pass"), ("051", "the door cannot merge it: it conflicts with main")],
            "merged": [("040", "Order 040"), ("041", "Order 041")],
            "question": [("046", "Passed its done-when. Refile?"), ("050", "Round 3 against a cap of 3?"),
                         ("060", "Agree, decline or comment?")]})
        self.assertEqual([one["round"] for one in shop["need-you"]], [3, 2])
        # All's lists are its CEOs' joined in the files' order; every view has the same chips.
        whole = found[0]["lists"]
        for key, rows in whole.items():
            self.assertEqual(rows, [one for view in found[1:] for one in view["lists"][key]], key)
        self.assertEqual([(one["ceo"], one["number"]) for one in whole["question"]][-1], ("kernel", "130"))
        for view in found:
            self.assertEqual([(chip["chip"], chip["label"], chip["count"], chip["flag"]) for chip in view["chips"]],
                             [("all", "All", 11, False), ("shop", "shop", 9, True), ("blog", "blog", 2, False),
                              ("kernel", "kernel", 0, False)])
        # The page shows each of those numbers, and only those, in its own view.
        seen = Seen(rendered(files)).views
        for view in found:
            for key, shown in seen[view["name"]]["counts"].items():
                self.assertEqual(shown, {str(view["counts"][key])}, (view["name"], key))
            self.assertLessEqual({"orders", "merged", "need-you"}, set(seen[view["name"]]["counts"]))
        self.assertEqual(found[3]["plans"], [dict(ceo="kernel", title=None)])
        self.assertIn("No filed plan, so no wave", rendered([files[2]]))
        # A CEO with no wave and nothing waiting shows zeros, and its chip no dot.
        lone = board.views([status("store", "Mac", [], [], plans=())], AT)[1]
        self.assertEqual(([lone["counts"][key] for key in board.COUNTS], lone["chips"][1]["flag"]), ([0] * 6, False))

    def test_a_quiet_tick_ambers_its_host_greys_its_rows_and_flags_its_chip(self):
        files = fleet()
        files[0]["last_tick"] = (AT - timedelta(minutes=16)).isoformat()  # shop stopped a quarter hour ago
        files[2]["last_tick"] = (AT - timedelta(minutes=14)).isoformat()  # the kernel's pass under load
        files.append(status("store", "Mac", [order("010")], [], tick=None))  # store never ticked
        files.append(status("lab", "vps", [], [], tick="not a time"))
        found = {view["name"]: view for view in board.views(files, AT)}
        hosts = {name: [(host["host"], host["quiet"], host["seen"]) for host in view["hosts"]]
                 for name, view in found.items()}
        self.assertEqual(hosts, {
            "all": [("Mac", True, "never"), ("vps", True, "never")], "shop": [("Mac", True, "never")],
            "blog": [("Mac", True, "never")], "kernel": [("vps", True, "never")], "store": [("Mac", True, "never")],
            "lab": [("vps", True, "never")]})
        self.assertEqual({(one["ceo"], one["quiet"]) for rows in found["all"]["lists"].values() for one in rows},
                         {("shop", True), ("blog", False), ("kernel", False)})
        self.assertEqual({chip["chip"]: chip["flag"] for chip in found["all"]["chips"]},
                         {"all": False, "shop": True, "blog": False, "kernel": False, "store": True, "lab": True})
        # Days old, its day is said too.
        files[0]["last_tick"] = "2026-10-16T07:30:00+00:00"
        self.assertEqual(board.views(files[:1], AT)[1]["hosts"][0]["seen"], "16 Oct 09:30")
        files[0]["last_tick"] = (AT - timedelta(minutes=16)).isoformat()
        page = rendered(files)
        seen = Seen(page).views
        self.assertEqual(seen["shop"]["hosts"], {"Mac": True})
        self.assertEqual(seen["blog"]["hosts"], {"Mac": True})
        self.assertEqual(seen["all"]["hosts"], {"Mac": True, "vps": True})
        self.assertTrue(all(quiet == row.startswith("shop/") for _, row, _, quiet in seen["all"]["rows"]))
        self.assertEqual({key: flag for key, (flag, _, _) in seen["blog"]["chips"].items()},
                         {"all": False, "shop": True, "blog": False, "kernel": False, "store": True, "lab": True})
        strips = re.findall(r'<p class="strip( quiet)?">(.*?)</p>', page)
        self.assertEqual([bool(quiet) for quiet, _ in strips], [True] * 6)
        self.assertIn("last seen never", strips[1][1])
        self.assertTrue(strips[0][1].startswith("Updated 11:00"), strips[0][1])
        # Fresh again, nothing is grey or amber.
        for one in files:
            one["last_tick"] = FRESH
        page = rendered(files)
        quiet = [(name, row) for name, view in Seen(page).views.items() for _, row, _, grey in view["rows"] if grey]
        quiet += [(name, host) for name, view in Seen(page).views.items() for host, amber in view["hosts"].items() if amber]
        self.assertEqual((quiet, page.count('class="strip quiet"')), ([], 0))

    def test_a_fresh_ceos_view_keeps_its_host_quiet_while_another_ceo_on_it_is_quiet(self):
        shop = status("shop", "Mac", [order("040", merged=True), order("048")], [
            row("040-x", "finished", detail="merged into main"),
            row("048-x", "needs attention", detail="its test does not pass")])  # fresh, and nothing needs the owner
        blog = fleet()[1]
        blog["last_tick"] = (AT - timedelta(minutes=40)).isoformat()  # blog stopped forty minutes ago
        found = {view["name"]: view for view in board.views([shop, blog], AT)}
        for name in ("all", "shop", "blog"):
            self.assertEqual([(host["host"], host["quiet"], host["seen"]) for host in found[name]["hosts"]],
                             [("Mac", True, "10:20")], name)
            self.assertEqual({chip["chip"]: chip["flag"] for chip in found[name]["chips"]},
                             {"all": False, "shop": False, "blog": True}, name)
        mine = [one for rows in found["shop"]["lists"].values() for one in rows]
        self.assertEqual(([one["quiet"] for one in mine], found["shop"]["counts"]["parked"]), ([False] * 2, 1))
        # The page says the same: the Mac amber with blog's last tick in every view, shop's rows and chip its own.
        page = rendered([shop, blog])
        seen = Seen(page).views
        for name, view in seen.items():
            self.assertEqual(view["hosts"], {"Mac": True}, name)
            self.assertEqual({key: flag for key, (flag, _, _) in view["chips"].items()},
                             {"all": False, "shop": False, "blog": True}, name)
        self.assertEqual({(row, quiet) for _, row, _, quiet in seen["shop"]["rows"]},
                         {("shop/048", False), ("shop/040", False)})
        self.assertTrue(all(quiet == row.startswith("blog/") for _, row, _, quiet in seen["all"]["rows"]))
        strips = re.findall(r'<p class="strip( quiet)?">(.*?)</p>', page)
        self.assertEqual([(bool(quiet), "last seen 10:20" in text) for quiet, text in strips], [(True, True)] * 3)


class Page(unittest.TestCase):
    def test_the_page_holds_every_view_with_all_shown_and_fetches_nothing(self):
        files = fleet()
        files[0]["status"]["rows"][2]["question"] = "Ship <script>alert(1)</script> & \"go\"?\n\nNow"
        page = rendered(files)
        seen = Seen(page)
        self.assertEqual(list(seen.views), ["all", "shop", "blog", "kernel"])
        self.assertEqual([name for name, view in seen.views.items() if not view["hidden"]], ["all"])
        for name, view in seen.views.items():
            self.assertEqual(view["parts"], ["strip", "chips", ("board", False), ("report", True)], name)
            self.assertEqual([chip[1] for chip in view["chips"].values()],
                             ["true" if chip == name else "false" for chip in view["chips"]])
            self.assertEqual({key: re.sub(r"\D", "", text) for key, (_, _, text) in view["chips"].items()},
                             {"all": "11", "shop": "9", "blog": "2", "kernel": "0"})
            self.assertTrue(view["chips"]["all"][2].strip().startswith("All"))
        rows = {(tab, row, state) for tab, row, state, _ in seen.views["all"]["rows"]}
        self.assertEqual({(row, state) for tab, row, state in rows if tab == "board"}, {
            ("shop/046", "need-you"), ("shop/050", "need-you"), ("shop/049", "stuck"), ("shop/048", "parked"),
            ("shop/051", "parked")})
        self.assertEqual(sorted((row, state) for tab, row, state in rows if tab == "report"), sorted([
            ("shop/046", "question"), ("shop/050", "question"), ("shop/060", "question"), ("kernel/130", "question"),
            ("shop/040", "merged"), ("shop/041", "merged"), ("blog/101", "merged"), ("blog/102", "merged"),
            ("shop/048", "parked"), ("shop/051", "parked")]))
        self.assertEqual(seen.views["blog"]["rows"], [("report", "blog/101", "merged", False),
                                                    ("report", "blog/102", "merged", False)])
        for title in ("Needs you", "Stuck", "Parked", "Questions for you", "Merged", "Parked, with reasons"):
            self.assertIn(f"<h2>{title} <b", page)
        self.assertEqual(page.count("<h2>Stuck "), 2, "All's and shop's; a section with no rows is left out")
        # The owner's words are text, on one line; nothing is fetched; storage is only ever tried.
        self.assertNotIn("<script>alert", page)
        self.assertIn("Ship &lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;go&quot;? Now", page)
        self.assertEqual([link for link in seen.links if link], [])
        self.assertIsNone(re.search(r"@import|url\(|https?:", page))
        script = page.split("<script>")[1]
        self.assertEqual(len(re.findall(r"localStorage", script)), len(re.findall(r"try \{ [^}]*localStorage", script)))
        self.assertEqual(page.count('data-tab="report" hidden'), 4)

    def test_the_verb_refuses_one_ceo_twice_a_ceo_named_all_a_file_not_json_and_no_flag(self):
        with tempfile.TemporaryDirectory() as away:  # outside every repository, as the page is made
            here = Path(away)
            env = {"PATH": "/usr/bin:/bin", "CEO_HOME": str(here / "home"), "PYTHONDONTWRITEBYTECODE": "1"}
            for name, one in zip(("shop", "blog", "all", "kernel"), fleet() + [fleet()[2]]):
                (here / f"{name}.json").write_text(json.dumps({**one, "ceo": "All" if name == "all" else one["ceo"]}))
            (here / "bad.json").write_text("{not json")

            def board_(*args):
                return subprocess.run([sys.executable, "-B", str(CEO), "board", *args], cwd=here, env=env,
                                      capture_output=True, text=True, timeout=60)
            made = board_("--page", "shop.json", "blog.json", "kernel.json", "--out", "page.html")
            self.assertEqual((made.returncode, made.stdout, made.stderr), (0, "", ""))
            self.assertEqual(list(Seen((here / "page.html").read_text()).views), ["all", "shop", "blog", "kernel"])
            for args in (["--page", "shop.json", "blog.json", "shop.json"], ["--page", "all.json"], ["--page", "bad.json"],
                         ["--page", "missing.json"], [], ["--name", "all"], ["--name", "shop", "--page", "shop.json"]):
                done = board_(*args)
                self.assertEqual(done.returncode, 2, (args, done.stdout, done.stderr))
                self.assertRegex(done.stderr, r"^ceo: refused: [^\n.]+(\.json[^\n.]*)*\.\n$", args)
            self.assertFalse((here / "home" / "projects.txt").exists())


class Memory(unittest.TestCase):
    def test_a_memory_file_over_what_loads_is_a_red_row_first_on_the_board_that_flags_its_ceos_chip(self):
        blog = fleet()[1]  # all merged and fresh: a chip with no flag of its own
        over = {**blog, "status": {**blog["status"], "memory": [dict(file="/p/LESSONS.md", lines=201, bytes=2211)]},
                "bells": [dict(key="idle x", kind="idle", line="no start", time=FRESH)]}
        self.assertEqual(rendered([blog]), rendered([{**blog, "status": {**blog["status"], "memory": []}}]))
        for files, flag in (([blog], False), ([{**over, "bells": []}], True)):
            self.assertEqual([view["chips"][1]["flag"] for view in board.views(files, AT)], [flag, flag])
        page = rendered([over])
        for name in ("all", "blog"):
            tab = page.split(f'data-view="{name}"', 1)[1].split('<div data-tab="board">', 1)[1]
            after = tab[re.search(r'<div class="card hero">.*?<p class="need">.*?</p></div>', tab).end():]
            self.assertTrue(after.startswith('<h2>Memory over what loads <b data-count="memory">1</b></h2>'), after)
            self.assertLess(after.index('<span class="state memory">too long</span>'), after.index("<h2>Bell "))
            for shown in (page, rendered([{**over, "last_tick": None}])):  # red when its CEO is quiet too
                self.assertIn(("board", "blog/LESSONS.md", "memory", False), Seen(shown).views[name]["rows"])
        self.assertIn("LESSONS.md is 201 lines and 2,211 bytes, over the 200 lines or 25,000 bytes a session loads: "
                      "only its top loads (/p/LESSONS.md)", page)
        self.assertIn("var(--red)", re.search(r"\.state\.memory\s*\{([^}]*)\}", page)[1])


if __name__ == "__main__":
    unittest.main()
