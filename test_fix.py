"""A failed review below round_cap gets its next round from the kernel, filed and with no CEO call: real ticks,
runs, reviews and git; only the models are stubs."""
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import ceo

KERNEL = Path(__file__).resolve().parent
FENCE = "`" * 3
# A one-shot tick with FIX_SINCE moved to the scene's start, so its reviews are newer than the cut-off whatever the day.
TICK = """import sys
from datetime import datetime
sys.path.insert(0, sys.argv[1])
import ceo
from test_ceo import kernel_patch
with kernel_patch("FIX_SINCE", datetime.fromisoformat(sys.argv[2])):
    sys.exit(ceo.main(sys.argv[3:]))
"""
WORKER = r"""import re, subprocess, sys
asked = re.search(r"git merge --no-edit ([0-9a-f]{40})", sys.stdin.read())
if asked:  # a round: its run's head first, then the fix of the finding
    subprocess.run(["git", "merge", "-q", "--no-edit", asked[1]], check=True)
    open("fixed", "w").write("fixed\n")
    subprocess.run(["git", "add", "fixed"], check=True)
    subprocess.run(["git", "commit", "-qm", "fix"], check=True)
else:
    open("built", "w").write("built\n")
    subprocess.run(["git", "add", "built"], check=True)
    subprocess.run(["git", "commit", "-qm", "build"], check=True)
"""
# Its second gives the tick that started the review time to end first, so no round can be filed in that tick.
REVIEWER = """import json, os, sys, time
sys.stdin.read()
time.sleep(1)
findings = [] if os.path.exists("fixed") else [
    {"claim": "The work leaves  no file\\nnamed fixed.", "command": "test -f fixed"},
    {"claim": "The folder can be listed.", "command": "ls >/dev/null"}]
json.dump({"summary": "checked the work", "findings": findings}, open("REVIEW.json", "w"))
"""
DECIDER = """import sys
sys.stdin.read()
open(sys.argv[1], "w").write("asked\\n")
print('{"decision": "ask", "reason": "the stub", "question": "Which?"}')
"""
FINDINGS = [{"claim": "No file named a.", "command": "test -f a", "exit": 1, "counts": True},
            {"claim": "Fine.", "command": "ls >/dev/null", "exit": 0, "counts": False},
            {"claim": "No file\n  named b.", "command": "test -f b", "exit": 1, "counts": True}]

WORD = "\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430"  # eight Cyrillic letters, spelled as escapes in this source
NODE = "import sys\nsys.stdout.write(ascii((sys.argv[1:], sys.stdin.read())) + '\\n')\n"
COMMANDS = (  # the letters in a heredoc program, in inline program words and in plain shell words
    f"node --input-type=module <<'JS'\nconst word = '{WORD}';\nif (!/{WORD}/.test(`{WORD}`)) throw Error(word);\nJS",
    f"python3 - <<'PY'\nword = '{WORD}'\nprint(ascii(word), len(word))\nPY",
    f"node -e 'console.log(\"{WORD}\")' && node -p \"'{WORD}'\"",
    f"python3 -c 'print(ascii(\"{WORD}\"))' && python3 -c \"print(ascii('{WORD}'))\"",
    f"grep -qF '{WORD}' word.txt && printf '%s\\n' \"{WORD}\" {WORD}",
)


def order_text(title, head="", worker=""):
    return (f"# {title}\n{head}\n## Goal\nBuild it.\n{worker}\n## Done when\n{FENCE}bash\ntest -f built\n{FENCE}\n\n"
            "## Not asked\nAnything else.\n")


class FixCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        # The owner's own git config, hooks and OWNER.md must not reach a scratch repository.
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "Stub", "GIT_AUTHOR_EMAIL": "stub@example.invalid",
                    "GIT_COMMITTER_NAME": "Stub", "GIT_COMMITTER_EMAIL": "stub@example.invalid",
                    "CEO_HOME": str(self.tmp / "home"), "PYTHONDONTWRITEBYTECODE": "1"}
        environ = mock.patch.dict(os.environ, self.env)
        environ.start()
        self.addCleanup(environ.stop)
        self.repo = self.project("repo")

    def project(self, name, config=""):
        repo = self.tmp / name
        (repo / "orders").mkdir(parents=True)
        self.repo = repo
        self.git("init", "-q", "-b", "main")
        (repo / ".gitignore").write_text(".ceo/\nSTATUS.md\n")
        (repo / "ceo.toml").write_text(f'disk_floor_gb = 0\nram_floor_gb = 0\nrecheck_wait = 0\nnight = ""\ncleaner = ""\n{config}')
        return repo

    def git(self, *args, repo=None):
        return subprocess.run(["git", *args], cwd=repo or self.repo, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def on_main(self, path):
        return subprocess.run(["git", "cat-file", "-e", f"main:{path}"], cwd=self.repo, env=self.env,
                              stderr=subprocess.DEVNULL).returncode == 0


class Cases(FixCase):
    """fix_moves() called directly on records written by hand, as a run and a review write them."""
    def order(self, name, title=None, head="", worker="", folder="orders"):
        (self.repo / folder).mkdir(parents=True, exist_ok=True)
        (self.repo / folder / f"{name}.md").write_text(order_text(title or name, head, worker))

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "orders")
        return self.git("rev-parse", "HEAD")

    def records(self, slug, head, result=None, review=None):
        run = self.repo / ".ceo" / "runs" / slug
        run.mkdir(parents=True)
        (run / "result.json").write_text(json.dumps({"order": f"orders/{slug}.md", "title": slug, "state": "done",
                                                     "head": head, **(result or {})}))
        (run / "review.json").write_text(json.dumps({"order": f"orders/{slug}.md", "verdict": "fails", "commit": head,
                                                     "findings": FINDINGS, "ended": ceo.FIX_SINCE.isoformat(),
                                                     **(review or {})}))

    def filed(self, base):
        ceo.fix_moves(self.repo)
        return sorted(self.git("diff", "--name-only", base, "main").split())

    def round_of(self, slug, head, commands):
        """The text of the one round fix_moves() files for a failed review of slug at head finding these commands."""
        self.records(slug, head, review={"findings": [
            {"claim": f"Claim {i}.", "command": command, "exit": 1, "counts": True}
            for i, command in enumerate(commands, 1)]})
        filed = self.filed(head)
        self.assertEqual(len(filed), 1, filed)
        return self.git("show", f"main:{filed[0]}") + "\n"

    def test_a_fresh_failed_review_files_its_round_once(self):
        self.order("002-two", "Two", worker="\n## Worker\nopus\n")
        head = self.commit()
        self.records("002-two", head)
        self.assertEqual(self.filed(head), ["orders/002-round-2.md"])
        text = self.git("show", "main:orders/002-round-2.md") + "\n"
        title, block, worker = ceo.parse_order(text)
        self.assertEqual((title, worker), ("Two: round 2, its review's findings fixed", "opus"))
        self.assertIn("\nORDER: 002\nROUND: 2\nREPLACES: orders/002-two.md\n", text.split("\n## ", 1)[0])
        self.assertEqual(block, f"git merge-base --is-ancestor {head} HEAD || {{ echo \"the run's head {head} is "
                                "not merged into this head\"; exit 1; }\nbash -c 'test -f a' || { echo \"finding 1 of the "
                                "review still holds\"; exit 1; }\nbash -c 'test -f b' || { echo \"finding 2 of the review "
                                "still holds\"; exit 1; }\ntest -f built\n")
        self.assertIn(":\n- No file named a.: `test -f a`\n- No file named b.: `test -f b`\n\nDo this", text)
        self.assertIn(f"1. Run `git merge --no-edit {head}`.", text)
        self.assertNotIn("Fine.", text)
        self.assertNotRegex(text, r"<[a-z]+>", "a name was left unfilled")
        filed = self.git("log", "-1", "--format=%H%n%s", "main").split("\n")
        self.assertEqual(filed[1], "orders/002-two.md: its fix round orders/002-round-2.md")
        self.assertEqual(self.git("rev-list", "--count", "main"), "2", "one commit holds the round")
        self.assertTrue((self.repo / "orders" / "002-round-2.md").is_file(), "the folder on main follows")
        ceo.fix_moves(self.repo)
        self.assertEqual(self.git("rev-list", "--count", "main"), "2", "a second call filed again")

    def test_an_order_its_plan_lists_is_filed_as_any_other_with_no_plan_start_needed(self):
        self.order("008-eight", "Eight")
        (self.repo / "plans").mkdir()
        (self.repo / "plans" / "p.md").write_text("# P\n\n- orders/008-eight.md — eight\n")
        head = self.commit()
        self.records("008-eight", head)  # run by hand: no plan start of it is recorded
        self.assertEqual(self.filed(head), ["orders/008-round-2.md"])
        text = self.git("show", "main:orders/008-round-2.md") + "\n"
        self.assertEqual(ceo.parse_order(text)[2], "", "an order without a worker gives its round none")
        self.assertNotIn("## Worker", text)

    def test_nothing_is_filed_for_a_review_the_kernel_does_not_own(self):
        before = (ceo.FIX_SINCE - timedelta(seconds=1)).isoformat()
        cases = {"a review that survives": ({}, {"verdict": "survives"}),
                 "a review of another commit": ({}, {"commit": "0" * 40}),
                 "a run that failed": ({"state": "failed"}, {}),
                 "a merged run": ({"merged": "1" * 40}, {}),
                 "a CEO merge": ({"ceo_merge": {"decision": "d"}}, {}),
                 "a review that ended before FIX_SINCE": ({}, {"ended": before}),
                 "a review with no end": ({}, {"ended": None}),
                 "a review whose end is no time": ({}, {"ended": "yesterday"}),
                 "the owner's word waiting": ({}, {}), "a stall recorded": ({}, {}),
                 "an order moved to drafts/orders/": ({}, {}), "a cut order": ({}, {})}
        for name, (result, review) in cases.items():
            with self.subTest(name):
                self.project(name.replace(" ", "-").replace("'", "").replace("/", ""))
                self.order("005-five", "Five", folder="drafts/orders" if "drafts" in name else "orders")
                if name == "a cut order":
                    (self.repo / "orders" / "005-five.cut").write_text("")
                head = self.commit()
                self.records("005-five", head, result, review)
                for kind, said in (("waiting", "the owner's word waiting"), ("stalls", "a stall recorded")):
                    if name == said:
                        (self.repo / ".ceo" / kind).mkdir()
                        (self.repo / ".ceo" / kind / "005-five.json").write_text("{}")
                self.assertEqual(self.filed(head), [])

    def test_at_round_cap_nothing_is_filed_and_the_stall_goes_to_the_ceo(self):
        for at, (config, round_, filed) in enumerate((("", 3, False), ("", 2, True), ("round_cap = 2\n", 2, False),
                                                      ("round_cap = 2\n", 1, True), ("round_cap = 1\n", 1, False))):
            with self.subTest(config=config, round=round_):
                self.project(f"cap-{at}", config)
                self.order("003-three", "Three")
                for n in range(2, round_ + 1):
                    replaces = "003-three" if n == 2 else f"003-round-{n - 1}"
                    self.order(f"003-round-{n}", f"Three: round {n}, its review's findings fixed",
                               head=f"\nORDER: 003\nROUND: {n}\nREPLACES: orders/{replaces}.md\n")
                head = self.commit()
                slug = "003-three" if round_ == 1 else f"003-round-{round_}"
                self.records(slug, head)
                new = f"orders/003-round-{round_ + 1}"
                self.assertEqual(self.filed(head), [f"{new}.md"] if filed else [])
                if filed:  # the earlier round's suffix is taken off before the new one is put on
                    self.assertEqual(ceo.parse_order(self.git("show", f"main:{new}.md") + "\n")[0],
                                     f"Three: round {round_ + 1}, its review's findings fixed")
                    continue
                self.assertEqual(self.stalls(slug), [slug], "no stall for the CEO")

    def test_a_round_already_on_main_is_not_recorded_again(self):
        self.order("004-four", "Four")
        head = self.commit()
        self.records("004-four", head)
        (self.repo / "orders" / "004-round-2.md").write_text(order_text("Four, the CEO's round 2"))
        base = self.commit()
        found, review = (json.loads((self.repo / ".ceo" / "runs" / "004-four" / name).read_text())
                         for name in ("result.json", "review.json"))
        row = ceo.order_snapshot(self.repo)["orders/004-four.md"]
        self.assertIsNone(ceo.fixing(self.repo, found, review, row)(base))
        self.assertEqual(self.filed(base), [])

    def test_a_round_carries_each_command_as_its_reviewer_wrote_it(self):
        self.order("004-four", "Four")
        made = self.round_of("004-four", self.commit(), COMMANDS)
        block = ceo.parse_order(made)[1]
        for i, command in enumerate(COMMANDS, 1):
            with self.subTest(command=command):
                self.assertIn(f"\n- Claim {i}.: `{command}`\n", made)
                self.assertEqual(block.count(f"bash -c {shlex.quote(command)} || "), 1)
        self.assertNotIn("\\u", made, "the kernel spelled a letter of a command as an escape")
        review = ceo.read_json(self.repo / ".ceo" / "runs" / "004-four" / "review.json")
        self.assertEqual([one["command"] for one in review["findings"]], list(COMMANDS), "the review's record changed")

    def test_each_command_runs_in_its_round_as_it_runs_alone(self):
        stub = self.tmp / "bin" / "node"
        stub.parent.mkdir()
        stub.write_text(f"#!{sys.executable}\n{NODE}")
        stub.chmod(0o755)
        env = {**self.env, "PATH": f"{stub.parent}:{self.env['PATH']}"}
        bash = lambda script: subprocess.run(["bash", "-c", script], cwd=self.repo, env=env, capture_output=True)
        for at, command in enumerate(COMMANDS):
            self.project(f"alone-{at}")
            self.order("002-two", "Two")
            block = ceo.parse_order(self.round_of("002-two", self.commit(), [command]))[1]
            (self.repo / "built").touch()
            for state in ("without word.txt", "with word.txt"):
                if state == "with word.txt":
                    (self.repo / "word.txt").write_text(WORD + "\n", encoding="utf-8")
                alone, round_ = bash(command), bash(block)
                with self.subTest(command=command, state=state):
                    held = b"" if alone.returncode == 0 else b"finding 1 of the review still holds\n"
                    self.assertEqual((round_.returncode == 0, round_.stdout, round_.stderr),
                                     (alone.returncode == 0, alone.stdout + held, alone.stderr))

    def test_a_later_round_carries_the_command_its_round_carried(self):
        self.order("004-four", "Four")
        block = ceo.parse_order(self.round_of("004-four", self.commit(), COMMANDS))[1]
        carried = [shlex.split(piece)[0] for piece in block.split("\nbash -c ")[1:]]
        self.assertEqual(carried, list(COMMANDS))
        made = self.round_of("004-round-2", self.git("rev-parse", "main"), carried)
        for i, command in enumerate(COMMANDS, 1):
            with self.subTest(command=command):
                self.assertIn(f"\n- Claim {i}.: `{command}`\n", made)

    def test_a_round_refused_is_suppressed_and_the_others_are_filed(self):
        self.order("006-six", "Six")
        self.order("007-seven", "Seven")
        head = self.commit()
        self.records("006-six", head, review={"findings": [{"claim": "A fence.", "command": f"true\n{FENCE}bash",
                                                            "exit": 1, "counts": True}]})
        self.records("007-seven", head)
        self.assertEqual(self.filed(head), ["orders/007-round-2.md"])
        self.assertEqual(self.filed(head), ["orders/007-round-2.md"], "tried again, refused")

    def test_with_an_origin_the_round_is_pushed_to_its_main(self):
        self.order("009-nine", "Nine")
        head = self.commit()
        origin = self.tmp / "origin.git"
        self.git("init", "-q", "--bare", "-b", "main", str(origin))
        self.git("remote", "add", "origin", str(origin))
        self.git("push", "-q", "origin", "main")
        self.git("fetch", "-q", "origin")
        self.records("009-nine", head)
        ceo.fix_moves(self.repo)
        self.assertEqual(sorted(self.git("diff", "--name-only", head, "main", repo=origin).split()),
                         ["orders/009-round-2.md"])

    def lagging(self):
        """001-one's failed review on an origin's main, and the folder on another branch, lagging it."""
        self.order("001-one", "One")
        head = self.commit()
        origin = self.tmp / "origin.git"
        self.git("init", "-q", "--bare", "-b", "main", str(origin))
        self.git("remote", "add", "origin", str(origin))
        self.git("push", "-q", "origin", "main")
        self.git("fetch", "-q", "origin")
        self.records("001-one", head)
        self.git("checkout", "-qb", "owner-work")
        return head, origin

    def stalls(self, slug):
        """The stalls 46 minutes after slug's last progress, slug ready since a pass found it eligible."""
        ceo.readiness(self.repo, {slug: ""})
        snapshot = ceo.stall_snapshot(self.repo, ceo.ready_times(self.repo))
        ceo.record_stalls(self.repo, snapshot, snapshot[slug]["last_progress"] + 46 * 60, 45)
        return sorted(path.stem for path in (self.repo / ".ceo" / "stalls").glob("*.json"))

    def test_a_fix_round_on_origin_ends_the_stall_of_a_folder_on_another_branch(self):
        import ceo_decisions
        marker, decider = self.tmp / "ceo-was-asked", self.tmp / "decide.py"
        decider.write_text(DECIDER)
        with (self.repo / "ceo.toml").open("a") as config:
            config.write(f"ceo = {json.dumps(f'{sys.executable} -B {decider} {marker}')}\n")
        self.lagging()
        ceo.fix_moves(self.repo)
        self.assertTrue(self.git("show", "origin/main:orders/001-round-2.md"))
        self.assertFalse((self.repo / "orders" / "001-round-2.md").exists(), "the folder followed origin")
        self.assertEqual(self.stalls("001-one"), [])
        children = {}
        ceo_decisions.dispatch(self.repo, 15, children)
        for child in children.values():
            child.wait(timeout=30)
        self.assertFalse(marker.exists(), "the CEO was asked")
        # The fix round's own row, once the folder holds it, still stalls; its round 1 does not.
        self.git("checkout", "-q", "origin/main", "--", "orders/001-round-2.md")
        self.assertEqual(self.stalls("001-round-2"), ["001-round-2"])

    def test_a_ceo_round_on_origin_keeps_the_stall_for_its_decisions_receipt(self):
        _, origin = self.lagging()
        other = self.tmp / "other"
        self.git("clone", "-q", str(origin), str(other))
        (other / "orders" / "001-round-2.md").write_text(order_text(
            "One: round 2, as the CEO wrote it", head="\nORDER: 001\nROUND: 2\nREPLACES: orders/001-one.md\n"))
        self.git("add", "orders", repo=other)
        self.git("commit", "-qm", "the CEO round of 001-one", repo=other)
        self.git("push", "-q", "origin", "main", repo=other)
        self.git("fetch", "-q", "origin")
        self.assertEqual(self.stalls("001-one"), ["001-one"], "a CEO's round on origin ended the stall of its receipt")


class Scene(FixCase):
    """Real one-shot ticks on a project with no origin: a failed review's round 2 on main within one tick."""
    def test_a_failed_review_gets_its_round_within_one_tick_and_no_ceo_is_asked(self):
        marker, ticking = self.tmp / "ceo-was-asked", self.tmp / "tick.py"
        for name, text in (("tick.py", TICK), ("worker.py", WORKER), ("reviewer.py", REVIEWER), ("decide.py", DECIDER)):
            (self.tmp / name).write_text(text)
        model = lambda name, *args: " ".join([sys.executable, "-B", str(self.tmp / name), *args])
        with (self.repo / "ceo.toml").open("a") as config:
            config.write(f"ceo = {json.dumps(model('decide.py', str(marker)))}\n")
        (self.repo / "orders" / "001-one.md").write_text(order_text("One"))
        self.git("add", "-A")
        self.git("commit", "-qm", "an order filed")
        since, said = datetime.now(timezone.utc).isoformat(), []
        def tick():
            done = subprocess.run([sys.executable, "-B", str(ticking), str(KERNEL), since, "tick", "--worker",
                                   model("worker.py"), "--reviewer", model("reviewer.py")], cwd=self.repo,
                                  env=self.env, capture_output=True, text=True, timeout=300)
            said.append(done.stdout + done.stderr)
            self.assertEqual(done.returncode, 0, "".join(said))
        def record(slug, name):
            path = self.repo / ".ceo" / "runs" / slug / name
            return json.loads(path.read_text()) if path.exists() else {}
        def wait(check, what, seconds=180):
            deadline = time.monotonic() + seconds
            while not check():
                self.assertLess(time.monotonic(), deadline, f"no {what} in {seconds} s:\n" + "".join(said))
                time.sleep(0.2)
        verdict = lambda slug: record(slug, "review.json").get("verdict") in ("fails", "survives", "blocked")
        tick()
        wait(lambda: record("001-one", "result.json").get("state") in ("done", "failed"), "first run")
        self.assertEqual(record("001-one", "result.json")["state"], "done")
        tick()
        wait(lambda: verdict("001-one"), "first review")
        review = record("001-one", "review.json")
        self.assertEqual((review["verdict"], [one["counts"] for one in review["findings"]]), ("fails", [True, False]))
        self.assertFalse(self.on_main("orders/001-round-2.md"), "round 2 was filed before the review failed")
        tick()  # the next tick after the failed review
        head = record("001-one", "result.json")["head"]
        self.assertTrue(self.on_main("orders/001-round-2.md"), "no round 2 on main within one tick:\n" + "".join(said))
        text = self.git("show", "main:orders/001-round-2.md") + "\n"
        self.assertIn("\nORDER: 001\nROUND: 2\nREPLACES: orders/001-one.md\n", text.split("\n## ", 1)[0])
        self.assertIn("\n- The work leaves no file named fixed.: `test -f fixed`\n", text)
        self.assertNotIn("ls >/dev/null", text)
        self.assertEqual(ceo.parse_order(text)[1].split("\n")[1:2],
                         ["bash -c 'test -f fixed' || { echo \"finding 1 of the review still holds\"; exit 1; }"])
        self.assertRegex(said[-1], r"ceo: started the run of \S*orders/001-round-2\.md", "round 2 did not start then")
        wait(lambda: record("001-round-2", "result.json").get("state") in ("done", "failed"), "round 2 run")
        self.assertEqual(record("001-round-2", "result.json")["state"], "done")
        self.assertTrue(subprocess.run(["git", "merge-base", "--is-ancestor", head, "order/001-round-2"],
                                       cwd=self.repo, env=self.env).returncode == 0, "round 2 holds round 1's head")
        tick()
        wait(lambda: verdict("001-round-2"), "round 2 review")
        self.assertEqual(record("001-round-2", "review.json")["verdict"], "survives")
        tick()
        tick()
        self.assertEqual(list((self.repo / "orders").glob("001-round-3*")), [])
        self.assertFalse(self.on_main("orders/001-round-3.md"), "a round 3 appeared")
        self.assertFalse(marker.exists() or (self.repo / ".ceo" / "decisions").exists(), "the CEO was asked")
        self.assertNotIn("CEO ", self.git("log", "--format=%s", "main"), "a CEO decision was recorded")
        ready = ceo.ready_times(self.repo)  # 46 minutes on, the failed round 1, ready since its pass, never stalls
        self.assertIn("001-one", ready)
        snapshot = ceo.stall_snapshot(self.repo, ready)
        ceo.record_stalls(self.repo, snapshot, snapshot["001-one"]["last_progress"] + 46 * 60, 45)
        self.assertFalse((self.repo / ".ceo" / "stalls" / "001-one.json").exists())


if __name__ == "__main__":
    unittest.main()
