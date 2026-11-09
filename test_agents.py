"""What each agent at work is doing, end to end: real git, real processes, real files.
Only the model is replaced, by a stand-in worker that writes three lines a second apart
as claude's stream-json writes them, a tool call or other JSON after each, which no row ever shows, and
by one that writes the steps a test gives it; GitHub is the fake gh of test_door."""
import json
import os
import shlex
import signal
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone

from ceo import claim
from test_ceo import alive, order_text
from test_desk import DeskCase
from test_pick import STUB as SPIN_STUB, spin_order

# Each line waits a second, then until the test has seen the one before: none is missed on a slow machine.
STANDIN = r'''
import json, os, subprocess, sys, time
folder = sys.argv[1]
sys.stdin.read()

def say(event):
    print(json.dumps(event), flush=True)

def text(words):
    say({"type": "assistant", "message": {"content": [{"type": "text", "text": words}]}})

def until(name):  # the test saw the line, or lets the worker go
    while not os.path.exists(os.path.join(folder, name)):
        if not os.path.isdir(folder):
            sys.exit(1)  # its test is over
        time.sleep(0.05)

text("Reading the order.\nFirst line.")
say({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "Bash", "input": {"command": "echo a tool call"}}]}})
time.sleep(1)
until("seen-1")
print("Second line.", flush=True)  # plain, as a model without stream-json writes it
print('["plain JSON"]', flush=True)
say({"type": "user", "message": {"content": [{"type": "tool_result", "content": "a tool's result"}]}})
time.sleep(1)
until("seen-2")
text("Third line.\n{\"text\": \"JSON\"}")
say({"type": "result", "result": "Its answer, as JSON."})
until("release")
open("built", "w").write("built\n")
subprocess.run(["git", "add", "built"], check=True)
subprocess.run(["git", "commit", "-qm", "build"], check=True)
'''
LINES = ("First line.", "Second line.", "Third line.")
# Each step is lines of claude's stream-json, or plain as a model without it writes them; after each, the
# stand-in says so and waits until the test has seen it. It ends without an answer.
SAYING = r'''
import os, sys, time
folder, steps = sys.argv[1], sys.argv[2:]
sys.stdin.read()
for number, step in enumerate(steps, 1):
    print(step, flush=True)
    open(os.path.join(folder, f"said-{number}"), "w").close()
    while not os.path.exists(os.path.join(folder, f"seen-{number}")):
        if not os.path.isdir(folder):
            sys.exit(1)  # its test is over
        time.sleep(0.05)
'''
# A model that has called a tool and written no text yet.
TOOLS = "\n".join(json.dumps(event) for event in (
    {"type": "system", "subtype": "init"},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}}))


def text(words):
    """words as claude's stream-json writes the model's text."""
    return json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": words}]}})


class AgentsCase(DeskCase):
    def setUp(self):
        super().setUp()
        (self.tmp / "standin.py").write_text(STANDIN)
        (self.tmp / "saying.py").write_text(SAYING)

    def worker(self):
        return shlex.join([sys.executable, str(self.tmp / "standin.py"), str(self.tmp)])

    def saying(self, *steps):
        return shlex.join([sys.executable, str(self.tmp / "saying.py"), str(self.tmp), *steps])

    def said(self, number):
        """The agents of `status --json` once the stand-in has written its step number, which it then leaves."""
        self.wait_for((self.tmp / f"said-{number}").exists, f"the model wrote its step {number}")
        rows = self.agents()
        (self.tmp / f"seen-{number}").touch()
        return rows

    def agents(self):
        return json.loads(self.ceo_in(self.repo, "status", "--json"))["agents"]

    def until(self, check, what, timeout=60):
        """The agents of `status --json` once check holds for them."""
        deadline = time.monotonic() + timeout
        while not check(rows := self.agents()):
            self.assertLess(time.monotonic(), deadline, f"this never happened: {what}; the rows: {rows}")
            time.sleep(0.1)
        return rows

    def walk(self):
        """Each row the stand-in's run shows as its newest line changes, while it runs."""
        seen = []
        for number, line in enumerate(LINES, 1):
            seen += self.until(lambda rows: rows and rows[0]["line"] == line, f"the row shows {line!r}")
            (self.tmp / f"seen-{number}").touch()
        return seen

    def running(self, name):
        """The stand-in's run of a new order, started by hand, and the rows it showed to its third line."""
        self.commit_order(f"{name}.md", order_text("test -f built", title="Visible work"))
        return self.ceo_bg("run", f"orders/{name}.md", "--worker", self.worker()), self.walk()


class Agents(AgentsCase):
    def test_a_worker_is_one_row_with_its_newest_line_while_it_runs_and_none_once_it_ends(self):
        run, seen = self.running("091-visible")
        self.assertEqual([row["line"] for row in seen], list(LINES))  # never a tool call or other JSON
        row = seen[-1]
        self.assertEqual({key: row[key] for key in ("role", "order", "slug", "spin", "phase", "pid")},
                         {"role": "worker", "order": "orders/091-visible.md", "slug": "091-visible",
                          "spin": None, "phase": "model", "pid": run.pid})
        self.assertGreaterEqual(row["elapsed"], 2)  # three lines, a second apart at least
        self.assertEqual([one["elapsed"] for one in seen], sorted(one["elapsed"] for one in seen))
        started = datetime.fromisoformat(row["started"])
        lag = (datetime.now(timezone.utc) - started).total_seconds() - row["elapsed"]
        self.assertLess(abs(lag), 5)
        self.assertEqual(row["sentence"],
                         f"worker on order 91 visible, {row['elapsed']} s, model: Third line.")
        status = json.loads(self.ceo_in(self.repo, "status", "--json"))
        self.assertEqual([(one["slug"], one["state"]) for one in status["rows"]],
                         [("091-visible", "in progress")])
        self.assertEqual([(one["role"], one["line"]) for one in status["agents"]],
                         [("worker", "Third line.")])
        self.release.touch()
        run.communicate(timeout=120)
        self.assertEqual(run.returncode, 0)
        self.assertEqual(self.agents(), [])
        self.assertEqual(json.loads(self.ceo_in(self.repo, "status", "--json"))["agents"], [])

    def test_a_lock_whose_processes_are_gone_is_no_row(self):
        run, _ = self.running("092-stale")
        group = json.loads((self.repo / ".ceo" / "locks" / "092-stale.json").read_text())["groups"][-1]
        self.kill_later([group])
        run.send_signal(signal.SIGKILL)
        run.communicate(timeout=30)
        # Its worker still holds the lock: the row stays, and names the worker's group.
        self.assertEqual([(row["role"], row["pid"]) for row in self.agents()], [("worker", group)])
        os.killpg(group, signal.SIGKILL)
        self.wait_for(lambda: not alive(group), "the worker died")
        lock = self.repo / ".ceo" / "locks" / "092-stale.run"
        self.assertEqual(lock.read_text(), f"{run.pid}\n")  # a stale lock, naming a process that is gone
        self.assertFalse((self.run_dir("092-stale") / "result.json").exists())
        self.assertEqual(self.agents(), [])
        held = claim(lock)  # as the tick holds it a moment, to see whether the run was cut off
        try:
            self.assertEqual(self.agents(), [])
        finally:
            os.close(held)

    def test_a_run_of_spins_is_a_row_for_each_spin(self):
        self.stub.write_text(SPIN_STUB)
        self.commit_order("096-colors.md", spin_order("red", "blue"))
        run = self.ceo_bg("run", "orders/096-colors.md", "--worker",
                          self.model("spin", str(self.tmp / "began"), str(self.release)))
        for number in (1, 2):
            self.wait_for((self.tmp / f"began-096-colors-spin-{number}").exists, f"spin {number} started")
        rows = self.until(lambda rows: [row["phase"] for row in rows] == ["model", "model"], "a row a spin")
        self.assertEqual([(row["role"], row["order"], row["spin"], row["pid"]) for row in rows],
                         [("worker", "orders/096-colors.md", spin, run.pid) for spin in (1, 2)])
        self.assertRegex(rows[1]["sentence"], r"^worker on order 96 colors spin 2, \d+ s, model$")
        self.release.touch()
        run.communicate(timeout=120)
        self.assertEqual((run.returncode, self.agents()), (3, []))  # waiting for the pick, nothing runs

    def test_each_pass_writes_running_now_under_the_free_line_and_removes_it_when_nothing_runs(self):
        status = self.repo / "STATUS.md"
        status.write_text("# Status\n\n## In progress\n- something\n")
        self.commit_order("093-ticked.md", order_text("test -f built"))  # filed: the tick starts it
        self.tick("--worker", self.worker())
        self.walk()
        self.idle_tick()
        lines = status.read_text().splitlines()
        at = lines.index("## Running now")
        self.assertEqual([line.split(":")[0] for line in lines[1:at - 1]],
                         ["Last tick", "Health", "Free", "Orders", "Sweep"])
        self.assertEqual((lines[0], lines[at - 1]), ("# Status", ""))
        self.assertRegex(lines[at + 1], r"^- worker on order 93 ticked, \d+ s, model: Third line\.$")
        self.assertEqual(lines[at + 2:], ["", "## In progress", "- something"])
        self.release.touch()
        self.wait_for((self.run_dir("093-ticked") / "result.json").exists, "the run ended")
        self.wait_for(lambda: not self.agents(), "its processes ended")
        self.idle_tick()
        lines = status.read_text().splitlines()
        self.assertNotIn("## Running now", lines)
        self.assertEqual(lines[6:], ["", "## In progress", "- something"])

    def test_the_reviewer_and_the_door_are_rows_in_their_phases(self):
        self.commit_order("094-merge.md", order_text("test -f 094-merge.txt", title="A merge"))
        built = self.ceo("run", "orders/094-merge.md", "--worker", self.model("own"))
        self.assertEqual(built.returncode, 0, built.stderr)
        review = self.ceo_bg("review", "orders/094-merge.md", "--reviewer",
                             self.model("wait", str(self.tmp / "pid"), str(self.release)))
        self.wait_for((self.tmp / "pid-094-merge").exists, "the reviewer started")
        rows = self.until(lambda rows: rows and rows[0]["phase"] == "model", "the reviewer's model runs")
        self.assertEqual([(row["role"], row["order"], row["pid"]) for row in rows],
                         [("reviewer", "orders/094-merge.md", review.pid)])
        self.assertRegex(rows[0]["sentence"], r"^reviewer on order 94 merge, \d+ s, model$")  # no line yet
        self.release.touch()  # it answers nothing, so its review is made again, by one that finds nothing
        review.communicate(timeout=120)
        clean = self.ceo("review", "orders/094-merge.md", "--reviewer", self.model("clean"))
        self.assertEqual(clean.returncode, 0, clean.stderr)
        gating, let_go = self.tmp / "gating", self.tmp / "let-go"
        self.gate(f'touch "{gating}"\nwhile [ ! -e "{let_go}" ]; do sleep 0.05; done')
        door = self.ceo_bg("door")
        self.wait_for(gating.exists, "the door runs the gate")
        self.assertEqual([(row["role"], row["order"], row["phase"], row["pid"]) for row in self.agents()],
                         [("door", "orders/094-merge.md", "gate", door.pid)])
        let_go.touch()
        door.communicate(timeout=120)
        self.assertEqual((door.returncode, self.agents()), (0, []))
        self.assertTrue(self.settled("094-merge"))

    def test_until_the_model_writes_text_its_line_is_empty_never_what_the_done_when_printed(self):
        # The order's test fails before the worker with a line of its own; the model begins with a tool.
        self.commit_order("097-tools.md", order_text('echo "FAILED (errors=1)"; test -f built'))
        run = self.ceo_bg("run", "orders/097-tools.md", "--worker", self.saying(TOOLS))
        rows = self.said(1)
        self.assertIn(b"\nFAILED (errors=1)\n", (self.run_dir("097-tools") / "worker.log").read_bytes())
        self.assertEqual([(row["phase"], row["line"]) for row in rows], [("model", "")])
        self.assertRegex(rows[0]["sentence"], r"^worker on order 97 tools, \d+ s, model$")
        run.communicate(timeout=120)

    def test_text_that_ends_in_json_printed_pretty_shows_the_last_line_of_prose_before_it(self):
        self.commit_order("098-answers.md", order_text("test -f built"))
        run = self.ceo_bg("run", "orders/098-answers.md", "--worker", self.saying(
            text('Wrote REVIEW.json:\n{\n  "summary": "checked",\n  "findings": []\n}'),
            'Wrote VISION.json:\n```json\n{\n  "fits": false,\n  "suggestion": "Say what it is for."\n}\n```',
            text("Its last record:\n" + json.dumps({"type": "result", "result": "done",
                                                    "findings": ["a claim", 2, True]}, indent=2))))
        self.assertEqual([[row["line"] for row in self.said(number)] for number in (1, 2, 3)],
                         [["Wrote REVIEW.json:"], ["Wrote VISION.json:"], ["Its last record:"]])
        run.communicate(timeout=120)

    def test_a_review_made_again_after_a_retry_shows_nothing_of_the_one_before(self):
        self.commit_order("099-again.md", order_text("test -f 099-again.txt", title="Again"))
        self.assertEqual(self.ceo("run", "orders/099-again.md", "--worker", self.model("own")).returncode, 0)
        # The reviewer before says a line and ends at once without an answer, as at an api error.
        script = f"import sys; sys.stdin.read(); print({text('Old answer.')!r})"
        self.ceo("review", "orders/099-again.md", "--reviewer", shlex.join([sys.executable, "-c", script]))
        self.assertEqual(self.read("099-again", "review.json")["verdict"], "retry")
        # A hook of git's holds the review made again in its clone, before its reviewer starts.
        cloning, let_go, hooks = self.tmp / "cloning", self.tmp / "let-go", self.tmp / "hooks"
        hooks.mkdir()
        (hooks / "post-checkout").write_text(
            f'#!/bin/sh\ntouch "{cloning}"\nwhile [ ! -e "{let_go}" ]; do sleep 0.05; done\n')
        (hooks / "post-checkout").chmod(0o755)
        again = self.ceo_bg("review", "orders/099-again.md", "--reviewer", self.saying(TOOLS), env={
            **self.env, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": str(hooks)})
        self.wait_for(cloning.exists, "the review made again clones its copy")
        self.assertIn(b"Old answer.", (self.run_dir("099-again") / "review.log").read_bytes())
        rows = self.agents()
        let_go.touch()  # then its own reviewer starts, and calls a tool before it writes any text
        rows += self.said(1)
        self.assertEqual([(row["role"], row["phase"], row["line"], row["pid"]) for row in rows],
                         [("reviewer", phase, "", again.pid) for phase in ("setup", "model")])
        again.communicate(timeout=120)

    def test_a_program_with_no_login_probe_is_said_once_in_each_projects_tick_log(self):
        models = self.tmp / "models"
        models.mkdir()
        for name in ("codex", "gemini"):  # each would fail a probe, so one that ran would say so
            (models / name).write_text(f"#!{sys.executable}\nimport sys\nsys.exit(1)\n")
            (models / name).chmod(0o755)
        codex = shlex.join([str(models / "codex"), "exec", "--dangerously-bypass-approvals-and-sandbox", "-"])
        beta = self.tmp / "beta"  # a second project, with no order to start
        (beta / "orders").mkdir(parents=True)
        (beta / ".gitignore").write_text(".ceo/\n")
        (beta / "ceo.toml").write_text('disk_floor_gb = 0\nram_floor_gb = 0\nnight = ""\n')
        for step in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "a project"]):
            subprocess.run(["git", *step], cwd=beta, env=self.env, check=True, capture_output=True)
        for repo in (self.repo, beta):
            with (repo / "ceo.toml").open("a") as conf:
                conf.write(f"worker = {json.dumps(codex)}\n")
        for args in ((), (), ("--worker", f"{models / 'gemini'} --yolo"), ("--worker", str(models / "gemini"))):
            self.assertNotIn("LOGIN FAILED", self.ceo_in(self.repo, "tick", *args))
        self.ceo_in(beta, "tick")
        said = "has no login probe, so a failed login shows only as its runs failing"
        for repo, programs in ((self.repo, ["codex", "gemini"]), (beta, ["codex"])):
            log = (repo / ".ceo" / "tick.log").read_text()
            self.assertEqual([line.split(": ", 1)[1] for line in log.splitlines() if said in line],
                             [f"{program} {said}" for program in programs])
            self.assertEqual((repo / ".ceo" / "unprobed").read_text().splitlines(), programs)
            self.assertFalse((repo / ".ceo" / "login.json").exists(), "a program with no probe was recorded")


if __name__ == "__main__":
    unittest.main()
