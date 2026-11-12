"""Each intent's map in the status file: its stages drawn up front, each where it stands by the plans it names, filed,
drafted or only planned; and each order of the wave its plan and when it merged. Real ticks, git and door."""
import json, time, unittest
from datetime import datetime, timezone
import ceo
from test_ceo import order_text
from test_door import DoorCase
from test_tick import TickCase
BUILD = """# Ship the shop

Outcome: the shop takes payments. The map below is drawn once; the kernel moves its markers.

## Map
- a line before the first stage names nothing

### Foundations
- plans/one.md — The cart and the payment.
  - A plan filed lists its real orders, never this line


### Checkout
Prose under a stage names nothing.
- plans/two.md
- design studio — publishes the launch pieces
  - A department lists no order

### Ahead
- plans/three.md — Drafted only.
  - The receipt is mailed
  - The refund is booked
 - One space is no indent, nor are three
- plans/four.md — Only planned.

### Later

## Limits
- no department lives here
### Nor a stage
"""
def files(repo):
    return {path: path.read_bytes() for path in repo.rglob("*") if path.is_file()}
def blank(plan, title, stands, needs=0):
    return dict(plan=plan, title=title, orders=needs, landed=0, cut=0, building=0, merged_at=None, stands=stands)
def row(slug, state, plan, title=None):
    return dict(number=slug[:3], title=title or f"The {slug}", state=state, plan=plan)
def unfiled(plan, *needs):
    return [dict(number=None, title=need, state="not filed", plan=plan) for need in needs]
def stage(title, plans=(), departments=(), state="ahead", done_at=None, orders=()):
    return dict(title=title, plans=list(plans), departments=list(departments), state=state, done_at=done_at,
                orders=list(orders))
class Board:
    def board(self):
        """The real `ceo.py board --name`, which writes nothing in the project."""
        before, out = files(self.repo), self.tmp / "shop.json"
        done = self.ceo("board", "--name", "shop", "--host", "here", "--out", str(out))
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)
        self.assertEqual(files(self.repo), before, "the board wrote in the project")
        return json.loads(out.read_text())
class Map(DoorCase, Board):
    def merge(self, *slugs):
        """Real ticks until the door merged each of slugs; each merge commit's time, as the status file says it."""
        work = ("--worker", self.model("own"), "--reviewer", self.model("clean"))
        for slug in slugs:
            self.tick_while(lambda: "cleared" in ceo.read_json(self.run_dir(slug) / "result.json"), slug, *work)
        self.wait_for(lambda: not ceo.agents(self.repo), "every agent of the project ended")  # none writes after
        stamps = [int(self.git("show", "-s", "--format=%ct", self.read(slug)["merged"])) for slug in slugs]
        self.assertTrue(all(self.began <= stamp <= time.time() + 1 for stamp in stamps), "no merge time of this run")
        return [datetime.fromtimestamp(stamp, timezone.utc).isoformat() for stamp in stamps]
    def test_a_signed_intents_map_shows_every_stage_where_it_stands_and_each_order_its_plan_and_merge(self):
        self.began = int(time.time())
        self.gate()
        for slug in ("001-cart", "002-pay", "003-checkout"):
            (self.repo / "orders" / f"{slug}.md").write_text(order_text(f"test -f {slug}.txt", title=f"The {slug}"))
        (self.repo / "plans").mkdir()
        (self.repo / "plans" / "one.md").write_text("# Cart and payment\n- orders/001-cart.md — The cart.\n"
                                                    "- orders/002-pay.md — The payment.\n")
        (self.repo / "plans" / "two.md").write_text("# Checkout\n- orders/003-checkout.md — The checkout.\n")
        self.commit_all("three orders and their two plans")
        cart, pay, checkout = self.merge("001-cart", "002-pay", "003-checkout")
        # The intents, signed after: one drawn as four stages, a closed one, one with no map; the next order filed.
        for path, text in (("intents/build.md", BUILD), ("intents/plain.md", "# A plain intent\n\nOutcome: x.\n"),
                           ("intents/old.md", "# The old intent\n\n## Map\n### Long done\n- plans/one.md\n"),
                           ("intents/old.closed", "Its proof is met.\n"), ("drafts/plans/three.md", "# Three\n"),
                           ("orders/004-receipt.md", order_text("test -f 004-receipt.txt", title="The receipt"))):
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text)
        with open(self.repo / "plans" / "two.md", "a") as plan:
            plan.write("- orders/004-receipt.md — The receipt.\n")
        self.commit_all("the intents, a drafted plan and the next order")
        one = dict(plan="plans/one.md", title="Cart and payment", orders=2, landed=2, cut=0, building=0,
                   merged_at=max(cart, pay), stands="filed")
        two = dict(plan="plans/two.md", title="Checkout", orders=2, landed=1, cut=0, building=0, merged_at=checkout,
                   stands="filed")
        later = [stage("Ahead", [blank("plans/three.md", "Drafted only.", "drafted", 2),
                                 blank("plans/four.md", "Only planned.", "planned")],
                       orders=unfiled("plans/three.md", "The receipt is mailed", "The refund is booked")), stage("Later")]
        first = [row("001-cart", "landed", "plans/one.md"), row("002-pay", "landed", "plans/one.md")]
        second = [row("003-checkout", "landed", "plans/two.md"), row("004-receipt", "queued", "plans/two.md", "The receipt")]
        found = self.board()
        self.assertEqual(found["maps"], [
            dict(intent="intents/build.md", title="Ship the shop", open=True, stages=[
                stage("Foundations", [one], state="done", done_at=max(cart, pay), orders=first),
                stage("Checkout", [two], ["design studio"], "running", orders=second), *later]),
            dict(intent="intents/plain.md", title="A plain intent", open=True, stages=[]),
            dict(intent="intents/old.md", title="The old intent", open=False,
                 stages=[stage("Long done", [one], state="done", done_at=max(cart, pay), orders=first)])])
        # The stage that finished stays on the map, though its plan has left the wave.
        self.assertEqual([plan["plan"] for plan in found["wave"]["plans"]], ["plans/two.md"])
        self.assertEqual([(one["number"], one["plan"], one["merged"], one["merged_at"])
                          for one in found["wave"]["orders"]],
                         [("003", "plans/two.md", True, checkout), ("004", "plans/two.md", False, None)])
        self.assertEqual({one["plan"]: one["merged_at"] for one in found["wave"]["every"]},
                         {"plans/one.md": max(cart, pay), "plans/two.md": checkout})
        # Its last order merges: the stage is done, when that order merged. What main lacks is on no map.
        [receipt] = self.merge("004-receipt")
        (self.repo / "intents" / "unsigned.md").write_text("# Unsigned\n\n## Map\n### Never\n")
        (self.repo / "intents" / "local.md").write_text("# Not on origin\n\n## Map\n### Never\n")
        self.git("add", "intents/local.md")
        self.git("commit", "-qm", "an intent origin's main does not hold")
        found = self.board()
        two.update(landed=2, merged_at=receipt)
        second[1]["state"] = "landed"
        self.assertEqual(found["maps"][0]["stages"][1],
                         stage("Checkout", [two], ["design studio"], "done", receipt, second))
        self.assertEqual([one["intent"] for one in found["maps"]],
                         ["intents/build.md", "intents/plain.md", "intents/old.md"])
        self.assertEqual([(one["number"], one["merged_at"]) for one in found["wave"]["orders"]],
                         [("003", checkout), ("004", receipt)])
class Edges(TickCase, Board):
    def commit(self, message, **written):
        for path, text in written.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text)
        self.git("add", "-A")
        self.git("commit", "-qm", message)
    def test_a_cut_plan_settles_a_broken_one_runs_and_a_stage_with_a_plan_unfiled_is_ahead(self):
        self.commit("start")
        self.assertEqual(self.board()["maps"], [], "a project with no intent has a map")
        edge = ("Outcome: no title line.\n\n## Map\n### All cut\n- plans/cut.md\n  - Filed, so not a need\n"
                "### Broken\n- plans/broken.md\n  - Filed, though it lists nothing\n### Stuck\n- plans/stuck.md\n"
                "### Half\n- plans/cut.md\n- plans/next.md — Drafted.\n  - The first need\n  - The second need\n"
                "- plans/none.md\n- writers\n  - A department needs no order\n\n## Mapping\n### No\n  - Nor a map\n")
        self.commit("an order cut, two plans, an intent", **{
            "orders/001-gone.md": order_text("test -f built", title="Gone"), "orders/001-gone.cut": "cut\n",
            "orders/002-stuck.md": order_text("test -f built", title="Stuck"),
            "plans/stuck.md": "# Stuck\n- orders/002-stuck.md\n",
            "plans/cut.md": "# Cut through\n- orders/001-gone.md — Gone.\n", "intents/edge.md": edge,
            "plans/broken.md": "# Broken\n- no order\n", "intents/sub/deep.md": "# In a folder\n\n## Map\n### Never\n"})
        self.idle_tick()  # a real pass over the plans and the intents; with no login, nothing starts
        self.assertEqual(self.board()["maps"][0]["stages"][2]["orders"],
                         [row("002-stuck", "queued", "plans/stuck.md", "Stuck")])
        (self.repo / ".ceo" / "stalls").mkdir(parents=True, exist_ok=True)
        (self.repo / ".ceo" / "stalls" / "002-stuck.json").write_text("{}\n")  # stalled: held, not queued
        (self.repo / "drafts" / "plans").mkdir(parents=True)
        (self.repo / "drafts" / "plans" / "next.md").write_text("# Next\n")
        self.git("checkout", "-qb", "side")
        self.commit("an intent off main", **{"intents/side.md": "# Off main\n\n## Map\n### Never\n"})
        cut = dict(plan="plans/cut.md", title="Cut through", orders=1, landed=0, cut=1, building=0, merged_at=None,
                   stands="filed")
        broken = blank("plans/broken.md", "broken", "filed")  # a plan that cannot be read lists no order
        stuck = dict(blank("plans/stuck.md", "Stuck", "filed"), orders=1)
        gone = [row("001-gone", "cut", "plans/cut.md", "Gone")]
        self.assertEqual(self.board()["maps"], [dict(intent="intents/edge.md", title="edge", open=True, stages=[
            stage("All cut", [cut], state="done", orders=gone), stage("Broken", [broken], state="running"),
            stage("Stuck", [stuck], state="running", orders=[row("002-stuck", "held", "plans/stuck.md", "Stuck")]),
            stage("Half", [cut, blank("plans/next.md", "Drafted.", "drafted", 2), blank("plans/none.md", "", "planned")],
                  ["writers"], orders=gone + unfiled("plans/next.md", "The first need", "The second need"))])])
if __name__ == "__main__":
    unittest.main()
