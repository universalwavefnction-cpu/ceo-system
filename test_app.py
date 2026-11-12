"""The CEO app's rules (app/ui/model.js), run by Node on the status file `ceo.py board --name` writes from a project
carried by the real tick: each CEO's counts are the board's own, a wave order without `merged` is not
merged, and a file the board cannot read is listed as unreadable, never dropped. No network, no orders/ folder."""
import json
import os
import shutil
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import board
from test_ceo import order_text
from test_tick import TickCase

MODEL = Path(__file__).resolve().parent / "app" / "ui" / "model.js"
NODE = shutil.which("node")
# Node imports model.js as the page does and runs it on the feed it reads from stdin, at the time it is given.
RUN = ("import {model} from %s; let text = ''; process.stdin.on('data', part => { text += part; })"
       ".on('end', () => process.stdout.write(JSON.stringify(model(JSON.parse(text), %d))));")
MOCKS = "\n## Mocks\n- blue\n- red\n\n## Preview\n`previews/card.html`\n"


def feed(folder):
    """folder as app/src-tauri/src/feed.rs gives it: each *.json not starting with a dot, by name, its whole
    seconds and its text, or an error for a file that is not UTF-8."""
    files = []
    for path in sorted(folder.glob("*.json")):
        if path.name.startswith(".") or not path.is_file():
            continue
        one = dict(name=path.name, modified=int(path.stat().st_mtime))
        try:
            one["text"] = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            one["error"] = "not UTF-8"
        files.append(one)
    return dict(dir=str(folder), files=files)


@unittest.skipUnless(NODE, "node is not installed, and the app's rules run in it")
class Model(TickCase):
    def seen(self, folder, at):
        """What model.js makes of folder's feed at the aware time at."""
        done = subprocess.run([NODE, "--input-type=module", "-e", RUN % (json.dumps(MODEL.as_uri()),
                                                                         int(at.timestamp() * 1000))],
                              input=json.dumps(feed(folder)), capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def shop(self):
        """A project's status file, written by `ceo.py board --name` after a real tick: one order merged on
        main, one whose mock was picked, one waiting for the owner, one queued, one stalled, one failed, one cut,
        and a run record that is no JSON."""
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        titles = {"001-merged": "Merged", "002-picked-mocks": "Picked mocks", "003-waiting": "Waiting",
                  "004-queued": "Queued", "005-stalled": "Stalled", "006-failed": "Failed", "007-cut": "Cut",
                  "008-broken": "Broken"}
        for slug, title in titles.items():
            (self.repo / "orders" / f"{slug}.md").write_text(
                order_text("test -f built", title=title) + (MOCKS if "mocks" in slug else ""))
        (self.repo / "plans").mkdir()
        (self.repo / "plans" / "wave.md").write_text("# The wave\nat once 8\n" + "".join(
            f"- orders/{slug}.md — {title}.\n" for slug, title in titles.items()))
        (self.repo / "intents").mkdir()  # two open intents, one drawing the wave, and a closed one
        (self.repo / "intents" / "shop.md").write_text(
            "# Ship the shop\n\n## Map\n### The wave\n- plans/wave.md — The eight orders\n### The window\n"
            "- plans/window.md — The next plan\n  - The card shows a number\n  - A click opens the stages\n")
        (self.repo / "intents" / "more.md").write_text(
            "# More\n\n## Map\n### Next\n- plans/more.md — Not filed\n  - One\n  - Two\n  - Three\n")
        (self.repo / "intents" / "old.md").write_text("# Old\n\n## Map\n### Past\n- plans/wave.md — Counted once\n")
        (self.repo / "intents" / "old.closed").write_text("closed\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "orders and their plan")
        self.idle_tick()  # a real pass stamps STATUS.md; with no login, nothing starts
        (self.repo / "merged.txt").write_text("merged\n")
        self.git("add", "merged.txt")
        self.git("commit", "-qm", "the merge of order 001")
        merge, ceo = self.git("rev-parse", "HEAD"), self.repo / ".ceo"
        mocks = [dict(spin=n, line=line, preview=f"previews/card-{n}.html", finished=True, head=merge)
                 for n, line in ((1, "blue"), (2, "red"))]
        for path, record in {
                "runs/001-merged/result.json": dict(state="done", done_when_exit=0, head=merge, merged=merge),
                "runs/002-picked-mocks/result.json": dict(state="done", head=merge, mocks=mocks, picked=2,
                                                          branch="order/002-picked-mocks", done_when_exit=0),
                "runs/006-failed/result.json": dict(state="failed", done_when_exit=1, reason="its test fails"),
                "waiting/003-waiting.json": dict(title="Waiting", question="Which way?", time=board.now(),
                                                 choices=[{"pick": "cut", "text": "cut"}]),
                "stalls/005-stalled.json": {}}.items():
            (ceo / path).parent.mkdir(parents=True, exist_ok=True)
            slug = Path(path).parent.name if path.startswith("runs/") else Path(path).stem
            (ceo / path).write_text(json.dumps(dict(order=f"orders/{slug}.md", **record)))
        (ceo / "runs" / "008-broken").mkdir(parents=True)
        (ceo / "runs" / "008-broken" / "result.json").write_text('{"order": "orders/008-broken.md", "state": "do')
        (self.repo / "orders" / "007-cut.cut").write_text("cut\n")
        out = self.tmp / "board" / "shop.json"
        out.parent.mkdir()
        done = self.ceo("board", "--name", "shop", "--host", "lab", "--out", str(out))
        self.assertEqual(done.returncode, 0, done.stderr)
        return out

    def test_each_ceos_counts_are_the_boards_own_and_every_order_shows_its_stage(self):
        shop = self.shop()
        folder, found = shop.parent, board.read(shop)
        # A second CEO, the same project quiet since 20 minutes before the page's time.
        at = datetime.now(timezone.utc)
        quiet = dict(found, ceo="quiet", last_tick=(at - timedelta(minutes=20)).isoformat())
        (folder / "quiet.json").write_text(json.dumps(quiet, ensure_ascii=False))
        files = [found, quiet]
        views = board.views(files, at)
        got = self.seen(folder, at)
        self.assertEqual([one["name"] for one in got["ceos"]], ["shop", "quiet"])  # offline sinks to the bottom
        for view, one in zip(views[1:], files):
            mine = next(each for each in got["ceos"] if each["name"] == view["name"])
            quiet_ = board.ceo_of(one, at)["quiet"]
            self.assertEqual(
                (mine["counts"]["merged"], mine["counts"]["orders"], mine["counts"]["needs"], mine["online"]),
                (view["counts"]["merged"], view["counts"]["orders"], view["counts"]["need-you"], not quiet_),
                view["name"])
            rows = {row.get("slug"): row for row in one["status"]["rows"]}
            self.assertEqual({order["number"]: order["kind"] for order in mine["orders"]},
                             {order["number"]: board.placed(order, rows.get(order["latest"]) or {})
                              for order in one["wave"]["orders"]}, view["name"])
        whole = views[0]["counts"]
        self.assertEqual({key: got["totals"][key] for key in ("online", "of", "merged", "orders", "needs")},
                         dict(online=1, of=2, merged=whole["merged"], orders=whole["orders"], needs=whole["need-you"]))
        self.assertEqual((got["unreadable"], got["feed"]["stale"]), ([], False))
        # Each order's lights and Status word, by the file's own keys: the failed run's row needs attention, so parked.
        mine = {order["number"]: (order["lights"], order["status"]) for order in got["ceos"][0]["orders"]}
        self.assertEqual(mine, {"001": (["done"] * 3, "Merged"), "002": (["done", "off", "off"], "Picked"),
                                "003": (["off"] * 3, "Needs you"), "004": (["off"] * 3, "Queued"),
                                "005": (["off"] * 3, "Stuck"), "006": (["failed", "off", "off"], "Parked"),
                                "007": (["off"] * 3, "Cut"), "008": (["off"] * 3, "Queued")})
        self.assertEqual((got["ceos"][0]["bad"], got["ceos"][0]["counts"]["cut"], got["ceos"][0]["counts"]["stuck"]),
                         (len(found["unreadable"]), 1, 1))
        self.assertEqual(found["unreadable"], [".ceo/runs/008-broken/result.json"])
        # The quiet CEO has no live light and no working lamp, whatever its file says.
        self.assertEqual((got["ceos"][1]["online"], got["ceos"][1]["working"]), (False, False))

    def test_an_order_without_merged_is_not_merged_and_a_bad_file_is_unreadable_and_counted(self):
        shop = self.shop()
        folder, found = shop.parent, board.read(shop)
        older = json.loads(json.dumps(found))  # a kernel's file before order 201: no stage, picked or unreadable
        older["ceo"] = "older"
        for key in ("bells", "unreadable"):
            older.pop(key, None)
        for order in older["wave"]["orders"]:
            order.pop("stage")
            order.pop("picked")
        merged = next(order for order in older["wave"]["orders"] if order["merged"])
        del merged["merged"]
        (folder / "older.json").write_text(json.dumps(older))
        (folder / "cutoff.json").write_bytes(shop.read_bytes()[:200])
        (folder / "latin.json").write_bytes(b'{"ceo": "latin \xff"}')
        (folder / "other.json").write_text('{"ceo": "other", "status": {}}')
        (folder / ".hidden.json").write_text("{")
        (folder / "notes.txt").write_text("{")
        at = datetime.now(timezone.utc)
        got = self.seen(folder, at)
        self.assertEqual(sorted((one["name"], one["file"]) for one in got["unreadable"]),
                         [("cutoff", "cutoff.json"), ("latin", "latin.json"), ("other", "other.json")])
        self.assertEqual((got["totals"]["online"], got["totals"]["of"]), (2, 5))
        mine = next(one for one in got["ceos"] if one["name"] == "older")
        expected = board.ceo_of(found, at)["counts"]
        self.assertEqual((mine["counts"]["merged"], mine["counts"]["orders"], mine["bad"]),
                         (expected["merged"] - 1, expected["orders"], 0))
        gone = next(order for order in mine["orders"] if order["number"] == merged["number"])
        self.assertNotEqual(gone["status"], "Merged")
        self.assertNotEqual(gone["lights"][2], "done")
        # With no stage, Build and Judge are unknown, never green; a picked order is no longer told apart.
        queued = next(order for order in mine["orders"] if order["number"] == "004")
        self.assertEqual((queued["lights"], queued["status"]), (["unknown", "unknown", "off"], "Unknown"))

    def test_each_wave_plan_shows_the_time_it_was_filed_on_main(self):
        shop = self.shop()
        plans = board.read(shop)["wave"]["plans"]
        got = self.seen(shop.parent, datetime.now(timezone.utc))["ceos"][0]["plans"]
        filed = [int(datetime.fromisoformat(plan["filed"]).timestamp()) * 1000 for plan in plans]  # whole seconds
        self.assertEqual(got, [dict(title=plan["title"], filed=at) for plan, at in zip(plans, filed)])
        self.assertEqual([plan["title"] for plan in got], ["The wave"])  # a plan GO'd no more: filing starts it

    def test_each_ceos_map_is_the_percentage_of_its_open_intents_orders_landed_and_every_order_by_stage(self):
        shop = self.shop()
        found = board.read(shop)
        (shop.parent / "older.json").write_text(json.dumps({k: v for k, v in dict(found, ceo="older").items()
                                                            if k != "maps"}))
        got = {one["name"]: one["map"] for one in self.seen(shop.parent, datetime.now(timezone.utc))["ceos"]}
        self.assertIsNone(got["older"])  # an older kernel's file has no maps, so no number
        opened = [one for one in found["maps"] if one["open"]]
        self.assertEqual([one["intent"] for one in found["maps"]], ["intents/more.md", "intents/shop.md", "intents/old.md"])
        orders = [row for one in opened for stage in one["stages"] for row in stage["orders"]]
        need = [row for row in orders if row["state"] != "cut"]
        landed = sum(row["state"] == "landed" for row in need)
        # 2 of 12, 16 and not 17: the merged order and the picked one (the kernel lands both), with the cut one out, the
        # stalled, failed and unmerged ones not landed, and the five needs of plans not yet filed counted, over both
        # open intents; the closed intent, which names the wave too, counts none.
        self.assertEqual((len(orders), len(need), landed), (13, 12, 2))
        mine = got["shop"]
        self.assertEqual((mine["percent"], mine["landed"], mine["all"]), (16, 2, 12))
        self.assertEqual([(one["title"], [(stage["title"], stage["state"], len(stage["orders"]))
                                         for stage in one["stages"]]) for one in mine["intents"]],
                         [("More", [("Next", "ahead", 3)]),
                          ("Ship the shop", [("The wave", "running", 8), ("The window", "ahead", 2)])])
        shown = [order for one in mine["intents"] for stage in one["stages"] for order in stage["orders"]]
        self.assertEqual(shown, [dict(number=row["number"] or "", title=row["title"], state=row["state"],
                                      plan=row["plan"]) for row in orders])
        self.assertEqual(sorted({order["state"] for order in shown}),
                         ["cut", "held", "landed", "not filed", "queued"])
        self.assertEqual([(order["number"], order["title"], order["state"]) for order in shown[:1] + shown[-1:]],
                         [("", "One", "not filed"), ("", "A click opens the stages", "not filed")])

    def test_a_map_field_that_is_no_text_is_empty_and_never_aborts_the_whole_page(self):
        shop = self.shop()
        found = board.read(shop)
        order = found["maps"][1]["stages"][0]["orders"][0]
        # {"toString": null} makes JavaScript's String() throw; a number is still its digits, an array or object empty.
        order["title"], order["number"], order["plan"] = {"toString": None}, 7, ["a", {"toString": None}]
        found["maps"][1]["title"] = {"toString": None}
        shop.write_text(json.dumps(found))
        mine = self.seen(shop.parent, datetime.now(timezone.utc))["ceos"][0]["map"]
        shown = mine["intents"][1]
        self.assertEqual(shown["title"], "intents/shop.md")  # the intent's own path stands in for a title that is no text
        self.assertEqual(shown["stages"][0]["orders"][0], dict(number="7", title="7", state=order["state"], plan=""))
        self.assertEqual((mine["percent"], mine["all"]), (16, 12))

    def test_the_feed_is_stale_after_a_quarter_hour_without_a_pull_and_when_there_is_no_file(self):
        folder = self.tmp / "board"
        folder.mkdir()
        at = datetime(2026, 11, 4, 8, 31, tzinfo=timezone.utc)
        self.assertEqual(self.seen(folder, at)["feed"], dict(at=None, stale=True))
        path = folder / "lone.json"
        path.write_text("{")
        for minutes, stale in ((15, False), (16, True)):
            stamp = (at - timedelta(minutes=minutes)).timestamp()
            os.utime(path, (stamp, stamp))
            self.assertEqual(self.seen(folder, at)["feed"], dict(at=int(stamp) * 1000, stale=stale), minutes)


if __name__ == "__main__":
    unittest.main()
