"""Work cut off is kept, end to end: real git, real runs started by the real tick, real processes
and files. Only the model is replaced, by a worker script that commits once, changes more, then
waits to be stopped."""
import json
import os
import shutil
import signal
import unittest
from pathlib import Path

from ceo import ENDING, held
from test_ceo import alive, order_text
from test_tick import TickCase

# The owner's words, as the worker of a run that starts from kept work must read them.
RESUMED = ("Earlier work on this order was cut off; your branch starts from it. "
           "Read git log and git status first.")
ORDER = order_text("test -f built")
REVIEW = ENDING.decode()

# One stub worker; its first argument says how to behave. Each start leaves one line of report,
# once its work is done: where its branch began, what it read and the ids to stop it by.
STUB = r'''
import json, os, subprocess, sys, time
mode, tmp, release = sys.argv[1], sys.argv[2], sys.argv[3]
order, slug = sys.stdin.buffer.read().decode(), os.path.basename(os.getcwd())

def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()

report = os.path.join(tmp, slug + ".jsonl")
n = len(open(report).readlines()) + 1 if os.path.exists(report) else 1
seen = {"head": git("rev-parse", "HEAD"), "order": order, "ceo": os.getppid(), "pid": os.getpid()}
if mode == "build":
    open("built", "w").write("built\n")
    git("add", "built")
    git("commit", "-qm", "build")
elif mode == "keep":  # one commit, then the committed file changed and one more file never added
    open(f"kept-{n}.txt", "w").write(f"committed by worker {n}\n")
    git("add", f"kept-{n}.txt")
    git("commit", "-qm", f"worker {n}")
    open(f"kept-{n}.txt", "a").write(f"changed by worker {n}\n")
    open(f"loose-{n}.txt", "w").write(f"never added by worker {n}\n")
elif mode in ("detach", "stray"):  # one commit, then HEAD detached at the base, as to watch a test fail
    open(f"kept-{n}.txt", "w").write(f"committed by worker {n}\n")
    git("add", f"kept-{n}.txt")
    git("commit", "-qm", f"worker {n}")
    git("checkout", "-q", "--detach", "HEAD~1")
    if mode == "stray":  # and a file made there, never added
        open(f"stray-{n}.txt", "w").write(f"made at the base by worker {n}\n")
open(report, "a").write(json.dumps(seen) + "\n")
while mode != "build" and not os.path.exists(release) and os.path.isdir(tmp):
    time.sleep(0.05)  # "keep" and "idle" wait to be stopped
'''


class Keep(TickCase):
    def setUp(self):
        super().setUp()
        self.stub.write_text(STUB)

    def worker(self, mode):
        return self.model(mode, str(self.tmp), str(self.release))

    def file(self, slug):
        """Commit the order into orders/: filed, the next tick starts it."""
        self.commit_order(f"{slug}.md", ORDER)

    def started(self, slug, count):
        """The report of the count-th worker of the order, once its work is done."""
        report = self.tmp / f"{slug}.jsonl"
        self.wait_for(lambda: report.exists() and len(report.read_text().splitlines()) >= count,
                      f"worker {count} of {slug} did its work")
        return json.loads(report.read_text().splitlines()[count - 1])

    def cut(self, slug, seen, how):
        if how == signal.SIGTERM:  # to the run's own process group, by id: ceo.py stops its worker
            self.assertEqual(os.getpgid(seen["ceo"]), seen["ceo"])
            os.killpg(seen["ceo"], signal.SIGTERM)
        else:  # a crash: ceo.py dies first, so it records nothing, then its worker
            os.kill(seen["ceo"], signal.SIGKILL)
            self.wait_for(lambda: not alive(seen["ceo"]), "ceo.py died")
            os.kill(seen["pid"], signal.SIGKILL)
        for pid in (seen["ceo"], seen["pid"]):
            self.wait_for(lambda: not alive(pid), f"process {pid} ended")
        self.wait_for(lambda: not held(self.repo / ".ceo" / "locks" / f"{slug}.run"), "the lock went")
        self.assertFalse((self.run_dir(slug) / "result.json").exists(), "a cut-off run left a result")

    def ended(self, slug):
        self.wait_for((self.run_dir(slug) / "result.json").exists, f"the run of {slug} ended")
        return self.read(slug)

    def assert_kept(self, slug, n, base):
        """order/<slug>-cut-<n> holds worker n's commit on base and, on it, one wip commit with all
        that worker changed after; the newest record names it and its head."""
        kept = f"order/{slug}-cut-{n}"
        self.assertRegex(self.git("log", "-1", "--format=%s", kept),
                         r"^wip: cut off \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")
        self.assertEqual(self.git("log", "-1", "--format=%s", f"{kept}^"), f"worker {n}")
        self.assertEqual(self.git("rev-parse", f"{kept}^^"), base)
        self.assertEqual(self.git("diff", "--name-only", f"{kept}^", kept).split(),
                         [f"kept-{n}.txt", f"loose-{n}.txt"])
        self.assert_holds(kept, n)
        record = json.loads(self.records(slug)[-1].read_text())
        self.assertEqual((record["kept"], record["head"]), (kept, self.git("rev-parse", kept)))
        return record["head"]

    def assert_holds(self, ref, *workers):
        for n in workers:
            self.assertEqual(self.git("show", f"{ref}:kept-{n}.txt"),
                             f"committed by worker {n}\nchanged by worker {n}")
            self.assertEqual(self.git("show", f"{ref}:loose-{n}.txt"), f"never added by worker {n}")

    def dying(self, trigger):
        """A git that, called for trigger, takes the worktree's .git file and is cut off there with the
        tick that called it: what a crash leaves in the middle of git worktree remove."""
        bin_dir, real = self.tmp / "bin", shutil.which("git")
        bin_dir.mkdir()
        (bin_dir / "git").write_text(f'#!/bin/sh\nif [ "$1 $2" = "{trigger}" ]; then\n'
                                     f'for last; do :; done; rm -f "$last/.git"; kill -9 $PPID; exit 137\n'
                                     f'fi\nexec "{real}" "$@"\n')
        (bin_dir / "git").chmod(0o755)
        return {**self.env, "PATH": f"{bin_dir}{os.pathsep}{self.env['PATH']}"}

    def test_a_run_cut_twice_by_sigterm_keeps_both_cuts_and_each_rerun_starts_from_the_newest(self):
        self.file("070-term")
        main = self.git("rev-parse", "main")
        self.tick("--worker", self.worker("keep"))
        self.cut("070-term", self.started("070-term", 1), signal.SIGTERM)
        # The next tick keeps the work and starts the order again from it.
        self.assertIn("recovered 070-term: its work is kept on order/070-term-cut-1",
                      self.tick("--worker", self.worker("keep")))
        first = self.assert_kept("070-term", 1, main)
        second = self.started("070-term", 2)
        self.assertEqual((second["head"], second["order"]), (first, f"{ORDER}\n{RESUMED}\n" + REVIEW))
        self.cut("070-term", second, signal.SIGTERM)
        self.assertIn("recovered 070-term: its work is kept on order/070-term-cut-2",
                      self.tick("--worker", self.worker("build")))
        latest = self.assert_kept("070-term", 2, first)
        self.assertEqual(self.git("rev-parse", "order/070-term-cut-1"), first, "the first cut moved")
        third = self.started("070-term", 3)
        self.assertEqual((third["head"], third["order"]), (latest, f"{ORDER}\n{RESUMED}\n" + REVIEW))
        result = self.ended("070-term")
        self.assertEqual((result["state"], result["resumed_from"]), ("done", "order/070-term-cut-2"))
        self.assert_holds(result["head"], 1, 2)

    def test_a_run_killed_with_its_worker_keeps_its_work_and_runs_again_from_it(self):
        self.file("071-kill")
        main = self.git("rev-parse", "main")
        self.tick("--worker", self.worker("keep"))
        self.cut("071-kill", self.started("071-kill", 1), signal.SIGKILL)
        self.assertIn("recovered 071-kill: its work is kept on order/071-kill-cut-1",
                      self.tick("--worker", self.worker("build")))
        kept = self.assert_kept("071-kill", 1, main)
        rerun = self.started("071-kill", 2)
        self.assertEqual((rerun["head"], rerun["order"]), (kept, f"{ORDER}\n{RESUMED}\n" + REVIEW))
        result = self.ended("071-kill")
        self.assertEqual((result["state"], result["resumed_from"]), ("done", "order/071-kill-cut-1"))
        self.assertEqual(self.git("rev-parse", f"{result['head']}^"), kept)
        self.assert_holds(result["head"], 1)

    def test_a_run_whose_worker_did_nothing_is_cleared_as_before_and_runs_again_from_main(self):
        self.file("072-idle")
        main = self.git("rev-parse", "main")
        self.tick("--worker", self.worker("idle"))
        self.cut("072-idle", self.started("072-idle", 1), signal.SIGTERM)
        self.assertIn("recovered 072-idle: cleared, so it can run again",
                      self.tick("--worker", self.worker("build")))
        self.assertEqual(self.git("branch", "--list", "order/072-idle-cut-*"), "")
        self.assertIsNone(json.loads(self.records("072-idle")[0].read_text())["kept"])
        rerun = self.started("072-idle", 2)
        self.assertEqual((rerun["head"], rerun["order"]), (main, ORDER + REVIEW))
        result = self.ended("072-idle")
        self.assertEqual((result["state"], result["resumed_from"]), ("done", None))

    def test_a_change_that_cannot_be_committed_leaves_the_run_whole_until_it_can(self):
        self.file("073-locked")
        main = self.git("rev-parse", "main")
        self.tick("--worker", self.worker("keep"))
        self.cut("073-locked", self.started("073-locked", 1), signal.SIGKILL)
        wt = self.repo / ".ceo" / "wt" / "073-locked"
        # What a git killed in the middle of its work leaves behind: its index locked.
        lock = Path(self.git("-C", str(wt), "rev-parse", "--absolute-git-dir")) / "index.lock"
        lock.touch()
        self.assertIn("recovered 073-locked: left alone: its changes could not be committed",
                      self.idle_tick())
        self.assertEqual((wt / "loose-1.txt").read_text(), "never added by worker 1\n")
        self.assertTrue(self.has_branch("073-locked"))
        self.assertEqual(self.git("branch", "--list", "order/073-locked-cut-*"), "")
        lock.unlink()
        self.assertIn("recovered 073-locked: its work is kept on order/073-locked-cut-1",
                      self.idle_tick())
        self.assert_kept("073-locked", 1, main)
        self.assertFalse(wt.exists())
        self.assertFalse(self.has_branch("073-locked"))

    def test_a_number_once_given_is_never_given_again_though_its_branch_was_deleted(self):
        self.file("074-again")
        self.tick("--worker", self.worker("keep"))
        self.cut("074-again", self.started("074-again", 1), signal.SIGKILL)
        self.idle_tick()
        self.git("branch", "-D", "order/074-again-cut-1")  # the owner throws it away
        self.tick("--worker", self.worker("keep"))
        rerun = self.started("074-again", 2)
        self.assertEqual(rerun["order"], ORDER + REVIEW, "a run started from a branch that is gone")
        self.cut("074-again", rerun, signal.SIGKILL)
        self.assertIn("recovered 074-again: its work is kept on order/074-again-cut-2",
                      self.idle_tick())
        self.assertEqual(self.git("branch", "--list", "order/074-again-cut-*"),
                         "order/074-again-cut-2")

    def test_a_recovery_cut_off_while_git_removes_the_worktree_has_kept_the_work_before(self):
        self.file("075-remove")
        main = self.git("rev-parse", "main")
        self.tick("--worker", self.worker("keep"))
        self.cut("075-remove", self.started("075-remove", 1), signal.SIGKILL)
        wt = self.repo / ".ceo" / "wt" / "075-remove"
        dying = self.idle_ceo("tick", env=self.dying("worktree remove"))
        self.assertEqual(dying.returncode, -signal.SIGKILL, dying.stderr)
        self.assertFalse((wt / ".git").exists(), "git never began to remove the worktree")
        # Before anything was removed, the work was on its kept branch.
        self.assertEqual(self.git("branch", "--list", "order/075-remove-cut-1"), "order/075-remove-cut-1")
        self.assertEqual(self.git("rev-parse", "order/075-remove-cut-1"),
                         self.git("rev-parse", "order/075-remove"))
        self.assertIn("recovered 075-remove: its work is kept on order/075-remove-cut-1",
                      self.idle_tick())
        self.assert_kept("075-remove", 1, main)
        self.assertFalse(wt.exists())
        self.assertFalse(self.has_branch("075-remove"))

    def test_a_worker_cut_while_its_head_is_detached_at_the_base_keeps_its_branch(self):
        self.file("076-detached")
        main = self.git("rev-parse", "main")
        self.tick("--worker", self.worker("detach"))
        self.cut("076-detached", self.started("076-detached", 1), signal.SIGTERM)
        self.assertIn("recovered 076-detached: its work is kept on order/076-detached-cut-1,",
                      self.tick("--worker", self.worker("build")))
        kept = "order/076-detached-cut-1"
        self.assertEqual(self.git("log", "--format=%s", f"{main}..{kept}"), "worker 1")
        self.assertEqual(self.git("show", f"{kept}:kept-1.txt"), "committed by worker 1")
        record = json.loads(self.records("076-detached")[-1].read_text())
        self.assertEqual((record["kept"], record["head"]), (kept, self.git("rev-parse", kept)))
        rerun = self.started("076-detached", 2)
        self.assertEqual((rerun["head"], rerun["order"]), (record["head"], f"{ORDER}\n{RESUMED}\n" + REVIEW))
        result = self.ended("076-detached")
        self.assertEqual((result["state"], result["resumed_from"]), ("done", kept))
        self.assertEqual(self.git("rev-parse", f"{result['head']}^"), record["head"])

    def test_a_worker_cut_while_detached_with_a_change_there_keeps_both_and_reruns_on_its_branch(self):
        self.file("077-stray")
        main = self.git("rev-parse", "main")
        self.tick("--worker", self.worker("stray"))
        self.cut("077-stray", self.started("077-stray", 1), signal.SIGKILL)
        self.assertIn("recovered 077-stray: its work is kept on order/077-stray-cut-2, which its next "
                      "run starts from, and on order/077-stray-cut-1: ",
                      self.tick("--worker", self.worker("build")))
        # The change made at the detached HEAD, on the base; the branch's commit, where the rerun starts.
        at_base, branch = "order/077-stray-cut-1", "order/077-stray-cut-2"
        self.assertRegex(self.git("log", "--format=%s", f"{main}..{at_base}"), r"^wip: cut off \S+$")
        self.assertEqual(self.git("show", f"{at_base}:stray-1.txt"), "made at the base by worker 1")
        self.assertEqual(self.git("log", "--format=%s", f"{main}..{branch}"), "worker 1")
        record = json.loads(self.records("077-stray")[-1].read_text())
        self.assertEqual((record["kept"], record["head"]), (branch, self.git("rev-parse", branch)))
        rerun = self.started("077-stray", 2)
        self.assertEqual((rerun["head"], rerun["order"]), (record["head"], f"{ORDER}\n{RESUMED}\n" + REVIEW))
        self.assertEqual(self.ended("077-stray")["state"], "done")


if __name__ == "__main__":
    unittest.main()
