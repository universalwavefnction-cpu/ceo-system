"""The pull request door and the pull end to end: real git with a local bare
repository as origin, real processes, real files. Only the model is replaced, by the stub of
test_tick with a few more parts, and GitHub, by a fake gh first on PATH that keeps pull requests on
that bare repository as GitHub keeps them. One test runs the real gh on GitHub when it is logged in."""
import errno
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import ceo
import ceo_decisions as decisions
from ceo import held, wip, worktree
from test_ceo import order_text
from test_tick import CEO, STUB, TickCase

KERNEL = Path(__file__).resolve().parent

STUB += r'''
elif mode == "own":  # a worker that commits a file named after its order
    name = os.path.basename(os.getcwd()) + ".txt"
    open(name, "w").write(name + "\n")
    subprocess.run(["git", "add", name], check=True)
    subprocess.run(["git", "commit", "-qm", "own"], check=True)
elif mode == "same":  # a worker that writes the one file every such worker writes
    open("shared.txt", "w").write(os.path.basename(os.getcwd()) + "\n")
    subprocess.run(["git", "add", "shared.txt"], check=True)
    subprocess.run(["git", "commit", "-qm", "same"], check=True)
elif mode == "many":  # a worker that commits its order's folder: 20000 uncounted Markdown files
    name = os.path.basename(os.getcwd())
    os.mkdir(name)
    for i in range(20000):
        open(f"{name}/{i}.md", "w").write(f"{i}\n")
    subprocess.run(["git", "add", name], check=True)
    subprocess.run(["git", "commit", "-qm", "many"], check=True)
elif mode == "looks":  # a reviewer that finds nothing, and writes to args[0] what orders/ holds on its disk
    open(args[0], "w").write(" ".join(sorted(os.listdir("orders"))))
    answer()
elif mode == "silent":  # a model that writes no answer
    pass
'''

# GitHub, as far as the kernel uses it, on a bare repository. It refuses what GitHub refused when
# measured on 2026-10-07: approving or requesting changes on a pull request of one's own account,
# and a merge whose head is not the commit named; a second merge of a merged one exits 0.
FAKE_GH = r'''
import fcntl, json, os, signal, subprocess, sys
ORIGIN, STATE, args = os.environ["FAKE_ORIGIN"], os.environ["FAKE_GITHUB"], sys.argv[1:]

def opt(name):
    return args[args.index(name) + 1] if name in args else None

def git(*more):
    return subprocess.run(["git", "--git-dir", ORIGIN, *more], capture_output=True, text=True)

def head(branch):
    return git("rev-parse", "-q", "--verify", "refs/heads/" + branch).stdout.strip()

def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)

def die_here(moment):  # FAKE_GH_KILL=<moment>:<mark>: the caller is killed there, once
    when, _, mark = os.environ.get("FAKE_GH_KILL", "").partition(":")
    if when == moment and not os.path.exists(mark):
        open(mark, "w").close()
        os.kill(os.getppid(), signal.SIGKILL)
        sys.exit(1)  # and nothing more happens on the server

lock = open(STATE + ".lock", "w")
fcntl.flock(lock, fcntl.LOCK_EX)  # one call at a time, as on one server
inside = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
with open(STATE + ".calls", "a") as calls:  # and the checkout gh was started in, if any
    calls.write(json.dumps({"args": args, "checkout": inside.stdout.strip()}) + "\n")
state = json.load(open(STATE)) if os.path.exists(STATE) else {"prs": []}
prs, repo, command = state["prs"], opt("-R"), args[:2]
if not repo:
    fail("no repository named with -R")
pr = prs[int(args[2]) - 1] if command[1:] and command[1] in ("view", "review", "merge") else None
if command == ["pr", "create"]:
    branch = opt("--head")
    for other in prs:
        if other["head"] == branch and other["state"] == "OPEN":
            fail('a pull request for branch "%s" into branch "main" already exists:\n'
                 "https://github.com/%s/pull/%d" % (branch, repo, other["number"]))
    if not head(branch):
        fail("no branch " + branch)
    prs.append({"number": len(prs) + 1, "head": branch, "title": opt("--title"),
                "body": opt("--body"), "state": "OPEN", "mergeCommit": None, "reviews": []})
    print("https://github.com/%s/pull/%d" % (repo, len(prs)))
elif command == ["pr", "view"]:
    known = {"state": pr["state"], "mergeCommit": pr["mergeCommit"], "number": pr["number"],
             "headRefOid": head(pr["head"]) or pr.get("merged_head"), "title": pr["title"]}
    late = os.environ.get("FAKE_GH_LATE")  # a merge the server shows late, once
    if late and pr["state"] == "MERGED" and not os.path.exists(late):
        open(late, "w").close()
        known.update(state="OPEN", mergeCommit=None)
    print(json.dumps({field: known[field] for field in opt("--json").split(",")}))
elif command == ["pr", "review"]:
    for flag, what in (("--approve", "approve"), ("--request-changes", "request changes on")):
        if flag in args:
            fail("failed to create review: GraphQL: Review Can not %s your own pull request "
                 "(addPullRequestReview)" % what)
    pr["reviews"].append(opt("--body"))
elif command == ["pr", "merge"] and pr["state"] == "MERGED":
    print("! Pull request %s#%d was already merged" % (repo, pr["number"]))
elif command == ["pr", "merge"]:
    if os.environ.get("FAKE_GH_BEFORE_MERGE"):
        subprocess.run(os.environ["FAKE_GH_BEFORE_MERGE"], shell=True, check=True)
    die_here("before")
    slow = os.environ.get("FAKE_GH_SLOW")  # GitHub refuses until it has judged the last push, once
    if slow and not os.path.exists(slow):
        open(slow, "w").close()
        fail("GraphQL: Pull Request is not mergeable (mergePullRequest)")
    if "--squash" not in args or head(pr["head"]) != opt("--match-head-commit"):
        fail("GraphQL: Head branch was modified. Review and try the merge again. (mergePullRequest)")
    main = head("main")
    tree = git("merge-tree", "--write-tree", main, head(pr["head"]))
    if tree.returncode:
        fail("GraphQL: Pull Request is not mergeable (mergePullRequest)")
    title = "%s (#%d)" % (pr["title"], pr["number"])
    new = git("commit-tree", tree.stdout.split()[0], "-p", main, "-m", title).stdout.strip()
    git("update-ref", "refs/heads/main", new, main)
    pr.update(state="MERGED", mergeCommit={"oid": new}, merged_head=head(pr["head"]))
    json.dump(state, open(STATE, "w"), indent=2)
    die_here("after")
else:
    fail("the fake gh does not know: " + " ".join(args))
json.dump(state, open(STATE, "w"), indent=2)
'''

# Keep Git's real atomic transaction and lease checks. This wrapper injects races or interruption
# at publication and models only GitHub noticing that a pushed PR head is reachable from main.
PUSH_EVENTS = r'''
import fcntl, json, os, signal, subprocess, sys
args = sys.argv[1:]

def die_here(moment):
    when, _, mark = os.environ.get("FAKE_GH_KILL", "").partition(":")
    if when == moment and not os.path.exists(mark):
        open(mark, "w").close()
        os.kill(os.getppid(), signal.SIGKILL)
        sys.exit(1)

if os.environ.get("FAKE_GH_BEFORE_MERGE"):
    subprocess.run(os.environ["FAKE_GH_BEFORE_MERGE"], shell=True, check=True)
die_here("before")
slow = os.environ.get("FAKE_GH_SLOW")
if slow and not os.path.exists(slow):
    open(slow, "w").close()
    sys.exit("server temporarily refuses the push")
done = subprocess.run([REAL_GIT, *args], close_fds=False)
if done.returncode:
    sys.exit(done.returncode)
origin, state_path = os.environ["FAKE_ORIGIN"], os.environ["FAKE_GITHUB"]
with open(state_path + ".lock", "w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    state = json.load(open(state_path))
    for pr in state["prs"]:
        if pr["state"] != "OPEN":
            continue
        head = subprocess.check_output([REAL_GIT, "--git-dir", origin, "rev-parse",
                                        "refs/heads/" + pr["head"]], text=True).strip()
        reached = subprocess.run([REAL_GIT, "--git-dir", origin, "merge-base", "--is-ancestor",
                                  head, "refs/heads/main"])
        if pr["state"] == "OPEN" and reached.returncode == 0:
            pr.update(state="MERGED", mergeCommit={"oid": head}, merged_head=head)
    json.dump(state, open(state_path, "w"), indent=2)
die_here("after")
'''


class DoorCase(TickCase):
    def setUp(self):
        super().setUp()
        self.stub.write_text(STUB)
        self.origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)],
                       env=self.env, check=True)
        self.git("-C", str(self.origin), "config", "maintenance.auto", "false")
        fake = self.tmp / "fakebin"
        fake.mkdir()
        (fake / "gh").write_text(f"#!{sys.executable}\n{FAKE_GH}")
        (fake / "gh").chmod(0o755)
        events, real_git = fake / "push-events.py", shutil.which("git")
        events.write_text(f"REAL_GIT = {real_git!r}\n{PUSH_EVENTS}")
        # Ordinary git calls stay cheap; race injection must not depend on --atomic being present.
        (fake / "git").write_text("#!/bin/sh\nmain= order=\nfor arg do\n"
                                  "  case $arg in *:refs/heads/main) main=1;; "
                                  "*:refs/heads/order/*) order=1;; esac\ndone\n"
                                  'if [ "$main$order" = 11 ]; then exec ' +
                                  shlex.join([sys.executable, str(events)]) + ' "$@"; fi\nexec ' +
                                  shlex.quote(real_git) + ' "$@"\n')
        (fake / "git").chmod(0o755)
        self.github = self.tmp / "github.json"
        self.env.update(PATH=f"{fake}{os.pathsep}{self.env['PATH']}", FAKE_ORIGIN=str(self.origin),
                        FAKE_GITHUB=str(self.github))
        self.marks = self.tmp / "gated"  # the commit each run of gate.sh tested, one per line
        self.git("remote", "add", "origin", str(self.origin))
        self.commit_all("start")

    def commit_all(self, message):
        """Commit everything in the owner's folder and push it, as the owner does with an order,
        after pulling what the door merged."""
        if self.main():
            self.git("pull", "-q", "--ff-only", "origin", "main")
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        self.git("push", "-q", "-u", "origin", "main")

    def commit_order(self, name, text=None):
        (self.repo / "orders" / name).write_text(text or order_text("test -f built"))
        self.commit_all(f"order {name}")

    def gate(self, body=""):
        gate = self.repo / "gate.sh"
        gate.write_text(f'#!/bin/sh\ngit rev-parse HEAD >> "{self.marks}"\n{body}\n')
        gate.chmod(0o755)
        self.commit_all("gate")

    def reviewed(self, name, worker="own", reviewer="clean", done_when=None, timeout=1800):
        """Build an order and review its PR."""
        self.commit_order(f"{name}.md", order_text(done_when or f"test -f {name}.txt"))
        run = self.ceo("run", f"orders/{name}.md", "--worker", self.model(worker))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.ceo("review", f"orders/{name}.md", "--reviewer", self.model(reviewer), f"--timeout={timeout}")
        return self.read(name)

    def door(self, env=None):
        done = self.ceo("door", env=env)
        if done.returncode < 0:  # a killed door's gh holds the door's lock until it exits too
            self.wait_for(lambda: not held(self.repo / ".ceo" / "locks" / "door.lock"), "gh ended")
        return done

    def server(self):
        return json.loads(self.github.read_text())["prs"] if self.github.exists() else []

    def calls(self):
        log = Path(f"{self.github}.calls")
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def on_origin(self, ref):
        found = subprocess.run(["git", "--git-dir", str(self.origin), "rev-parse", "-q", "--verify",
                                ref], capture_output=True, text=True)
        return found.stdout.strip()

    def main(self):
        return self.on_origin("refs/heads/main")

    def moved(self, since):
        """The files origin's main changed since the commit since."""
        return self.git("--git-dir", str(self.origin), "diff", "--name-only", since, "main").split()

    def one_round(self):
        """round_cap = 1: a failed review files no fix round on main."""
        with (self.repo / "ceo.toml").open("a") as config:
            config.write("round_cap = 1\n")

    def settled(self, name):
        """Merged on the server, recorded, and cleared away."""
        return "cleared" in self.read(name)

    def tick_while(self, check, what, *args, timeout=120):
        deadline = time.monotonic() + timeout
        while not check():
            self.assertLess(time.monotonic(), deadline, f"this never happened: {what}")
            self.tick(*args)
            time.sleep(0.2)

    def folder(self):
        """What the owner sees in the folder: HEAD, branches, index, and every file outside .ceo."""
        files = {str(path.relative_to(self.repo)): path.read_bytes()
                 for path in self.repo.rglob("*") if path.is_file()
                 and not {".git", ".ceo"} & set(path.relative_to(self.repo).parts)}
        return (self.git("rev-parse", "HEAD"), self.git("symbolic-ref", "HEAD"),
                self.git("for-each-ref", "refs/heads/main"), self.git("ls-files", "-s"),
                self.git("status", "--porcelain", "-uall"), files)


MERGE_CEO = r'''
import json, sys
from pathlib import Path
here, brief = Path(sys.argv[1]), sys.stdin.read()
n = len(list(here.glob("brief-*"))) + 1
(here / f"brief-{n}").write_text(brief)
modes = (here / "modes").read_text().split()
review = json.loads(brief.split("Quoted records:\n", 1)[1]).get("review.json") or {}
accepts = [{"claim": one["claim"], "why": "The owner accepts it: it does not hold this order."}
           for one in review.get("findings", []) if one.get("counts")]
print(json.dumps({"merge": {"decision": "merge", "reason": "Its done-when passed; the owner said merge it.",
                            "accepts": accepts},
                  "ask": {"decision": "ask", "reason": "The door could not merge it.", "question": "What now?"}}
                 [modes[min(n, len(modes)) - 1]]))
'''


class CeoMerge(DoorCase):
    def scene(self, slug, done_when, modes=("merge",), moved="moved.txt", gate_body="", reviewer="doubt", timeout=1800):
        self.gate(gate_body)
        self.slug, self.ran = slug, self.tmp / "done-when-ran"
        self.commit_order(f"{slug}.md", order_text(f"{done_when} && git rev-parse HEAD >> {shlex.quote(str(self.ran))}"))
        self.ceo("run", f"orders/{slug}.md", "--worker", self.model("own"))
        if self.read(slug)["state"] == "done":
            self.ceo("review", f"orders/{slug}.md", "--reviewer", self.model(reviewer), f"--timeout={timeout}")
        self.briefs = self.tmp / "ceo-model"
        self.briefs.mkdir()
        (self.briefs / "modes").write_text(" ".join(modes))
        script = self.briefs / "ceo.py"
        script.write_text(MERGE_CEO)
        command = shlex.join([sys.executable, str(script), str(self.briefs)])
        conf = self.repo / "ceo.toml"
        conf.write_text(f"{conf.read_text()}round_cap=1\nstall_minutes=0\nceo={json.dumps(command)}\n")
        (self.repo / moved).write_text("main moved after the run\n")
        self.commit_all("the CEO, and main moves")
        self.waiting = self.repo / ".ceo" / "waiting" / f"{slug}.json"

    def asked(self):
        return sorted(self.briefs.glob("brief-*"))

    def decided(self):
        text = subprocess.run(["git", "--git-dir", str(self.origin), "show", "main:decisions.md"],
                              capture_output=True, text=True, env=self.env).stdout
        return decisions.entries(text)

    def on_main(self, commit):
        return subprocess.run(["git", "--git-dir", str(self.origin), "merge-base", "--is-ancestor", commit,
                               "refs/heads/main"], capture_output=True, env=self.env).returncode == 0

    def door_tests(self):
        log = self.run_dir(self.slug) / "door.log"
        return log.read_text().count("door: its done-when block on the joined commit") if log.exists() else 0

    def merge_events(self):
        return [one for one in ceo.events(self.repo)[0] if one["event"] == "merge" and one["slug"] == self.slug]

    def test_the_ceos_merge_lands_a_run_whose_review_failed_after_the_door_tests_the_joined_commit(self):
        entered = self.tmp / "gate-entered"
        self.scene("901-merged", "test -f 901-merged.txt", gate_body=
                   f"touch {shlex.quote(str(entered))}\nwhile test ! -e {shlex.quote(str(self.release))}; do sleep .05; done")
        review = self.read(self.slug, "review.json")
        self.assertEqual(review["verdict"], "fails")
        self.assertTrue(any(one["counts"] for one in review["findings"]))
        ceo.note(self.repo, self.slug, attention="An earlier door block")
        self.tick_while(entered.exists, "the door reached its gate")
        self.assertNotIn("attention", self.read(self.slug))
        with mock.patch.dict(os.environ, self.env):
            self.assertTrue(ceo.stall_snapshot(self.repo)[self.slug]["waiting"])
        for _ in range(3):
            self.tick()
        self.assertEqual(len(self.asked()), 1, "the working door caused another CEO decision")
        self.release.touch()
        self.tick_while(lambda: self.settled(self.slug), "the CEO's merge landed the run")
        for _ in range(3):
            self.tick()
        result, entries = self.read(self.slug), self.decided()
        joined = self.on_origin(f"{result['merged']}^2")
        self.assertNotEqual(joined, result["head"])
        self.assertEqual(self.ran.read_text().split()[-1], joined)
        self.assertEqual(self.marks.read_text().split(), [joined])
        self.assertEqual(self.server()[0]["state"], "MERGED")
        self.assertTrue(self.on_main(result["merged"]) and self.on_main(result["head"]))
        self.assertEqual([one["decision"] for one in entries], ["merge"])
        self.assertEqual(entries[0]["head"], result["head"])
        self.assertEqual({one["claim"] for one in entries[0]["accepts"]},
                         {one["claim"] for one in review["findings"] if one["counts"]})
        self.assertEqual(result["ceo_merge"], dict(decision=entries[0]["id"], head=result["head"], time=entries[0]["time"]))
        self.assertEqual((len(self.asked()), self.door_tests(), len(self.merge_events())), (1, 1, 1))
        self.assertIn(entries[0]["reason"], self.merge_events()[0]["sentence"])
        self.assertEqual(self.read(self.slug, "review.json"), review, "the CEO rewrote the review")
        self.assertEqual(len(self.server()[0]["reviews"]), 1)

    def test_the_ceos_merge_of_a_run_whose_done_when_failed_is_refused_and_nothing_merges(self):
        self.scene("902-refused", "test -f other.txt")
        self.assertEqual(self.read(self.slug)["state"], "failed")
        self.tick_while(self.waiting.exists, "the refused merge became an ask")
        for _ in range(3):
            self.tick()
        entries, briefs = self.decided(), self.asked()
        self.assertEqual([one["decision"] for one in entries], ["ask"])
        self.assertTrue(entries[0]["reason"].startswith(decisions.REFUSED))
        self.assertEqual(len(briefs), 2)
        self.assertIn("The kernel refused your last answer", briefs[1].read_text())
        self.assertFalse(self.on_main(self.read(self.slug)["head"]))
        self.assertEqual((self.server(), self.door_tests(), self.merge_events()), ([], 0, []))
        self.assertNotIn("ceo_merge", self.read(self.slug))

    def test_a_ceo_merge_posts_a_blocked_review_and_still_tests_the_join(self):
        self.scene("905-blocked", "test -f 905-blocked.txt", reviewer="endless", timeout=1)
        review = self.read(self.slug, "review.json")
        self.assertEqual((review["verdict"], review["posted"]), ("blocked", None))
        self.tick_while(lambda: self.settled(self.slug), "the blocked review was overridden", timeout=15)
        self.tick()
        self.wait_for(lambda: not held(self.repo / ".ceo" / "locks" / "door.lock"), "the door ended")
        self.assertFalse((self.run_dir(self.slug) / "vision.json").exists())
        self.assertEqual(self.decided()[0]["accepts"], [])
        self.assertEqual(self.read(self.slug, "review.json")["verdict"], "blocked")
        posted = self.server()[0]["reviews"]
        self.assertEqual(len(posted), 1)
        self.assertIn("review: blocked", posted[0])
        self.assertEqual((len(self.asked()), self.door_tests(), len(self.marks.read_text().split())), (1, 1, 1))

    def test_a_joined_done_when_that_fails_goes_back_to_the_ceo_and_is_never_tried_again(self):
        self.scene("903-blocked", "test -f 903-blocked.txt && test ! -e blocker", ("merge", "ask"), "blocker")
        self.tick_while(self.waiting.exists, "the door failure went back to the CEO")
        for _ in range(3):
            self.tick()
        result, entries, briefs = self.read(self.slug), self.decided(), self.asked()
        failure = "its done-when block exited 1 on the joined commit"
        self.assertIn(failure, result["attention"])
        self.assertEqual(result["ceo_merge"]["outcome"], result["attention"])
        self.assertTrue(result["ceo_merge"]["tried"])
        self.assertFalse(ceo.ceo_merge_pending(result))
        self.assertEqual([one["decision"] for one in entries], ["merge", "ask"])
        self.assertEqual(len(briefs), 2)
        self.assertIn(failure, briefs[1].read_text())
        self.assertEqual(self.server()[0]["state"], "OPEN")
        self.assertFalse(self.on_main(result["head"]))
        self.assertFalse(self.marks.exists(), "the gate ran after the done-when failed")
        self.assertEqual(self.door_tests(), 1)
        receipt = self.repo / ".ceo" / "decisions" / entries[0]["id"] / "applied.json"
        receipt.unlink()
        self.tick_while(receipt.exists, "the old merge decision replayed")
        for _ in range(3):
            self.tick()
        self.assertEqual(self.read(self.slug)["ceo_merge"], result["ceo_merge"])
        self.assertEqual(self.read(self.slug)["attention"], result["attention"])
        self.assertEqual((len(self.asked()), len(self.decided()), self.door_tests(), len(self.merge_events())), (2, 2, 1, 1))
        self.assertTrue(self.waiting.exists())
        self.assertEqual(self.server()[0]["state"], "OPEN")

    def test_a_merge_recorded_on_main_whose_decider_died_is_honoured_once_after_a_restart(self):
        for before_follow in (False, True):
            with self.subTest(before_local_main_follows=before_follow):
                case = CeoMerge()
                case.setUp()
                try:
                    case.restart(before_follow)
                finally:
                    case.doCleanups()

    def restart(self, before_follow):
        self.scene("904-restart", "test -f 904-restart.txt")
        with mock.patch.dict(os.environ, self.env):
            progress = ceo.stall_snapshot(self.repo)[self.slug]["last_progress"]
        stall = dict(slug=self.slug, since=ceo.now("microseconds"), last_progress=progress, state="fails", last_log="")
        folder = self.repo / ".ceo" / "decisions" / decisions.identity(stall)
        folder.mkdir(parents=True)
        ceo.write_json(folder / "stall.json", stall)
        # The real decider dies after publishing, before local follow or before applying the override.
        crash = '''import os, sys
from pathlib import Path
import ceo, ceo_decisions
from test_ceo import kernel_patch
record = ceo.record_on_main
def recorded(*args, **kw):
    result = record(*args, **kw)
    os._exit(23)
if sys.argv[3] == "before-follow":
    kernel_patch("follow", lambda *args, **kw: os._exit(23)).start()
else:
    kernel_patch("record_on_main", recorded).start()
ceo_decisions.run(Path(sys.argv[1]), sys.argv[2], 30)
'''
        died = subprocess.run([sys.executable, "-c", crash, str(self.repo), folder.name,
                               "before-follow" if before_follow else "after-record"], cwd=KERNEL,
                              env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(died.returncode, 23, died.stdout + died.stderr)
        self.assertEqual([one["decision"] for one in self.decided()], ["merge"])
        self.assertEqual(len(self.asked()), 1)
        self.assertEqual(bool(ceo.blob(self.repo, "main", "decisions.md")), not before_follow)
        self.assertFalse((folder / "applied.json").exists())
        self.assertNotIn("ceo_merge", self.read(self.slug))
        self.assertEqual(self.door_tests(), 0)
        # No old stall file survives either: watch_stalls sees a fresh stop before dispatch replays.
        self.assertFalse((self.repo / ".ceo" / "stalls" / f"{self.slug}.json").exists())
        self.tick_while(lambda: self.settled(self.slug), "the recovered decision landed the run")
        for _ in range(3):
            self.tick()
        result, entries = self.read(self.slug), self.decided()
        self.assertTrue((folder / "applied.json").exists())
        self.assertEqual(result["ceo_merge"]["decision"], entries[0]["id"])
        self.assertEqual((len(entries), len(self.asked()), self.door_tests(), len(self.merge_events())), (1, 1, 1, 1))
        self.assertTrue(self.on_main(result["head"]) and self.on_main(result["merged"]))
        self.assertEqual(self.server()[0]["state"], "MERGED")
        self.assertEqual(len(self.marks.read_text().split()), 1)
        self.assertFalse(self.waiting.exists())


class Door(DoorCase):
    def test_a_run_starts_while_another_holds_the_repository_config(self):
        # Found by the real proof: two runs started at once raced for .git/config's lock, as
        # a branch made from origin/main wrote its upstream there.
        self.commit_order("039-config.md", order_text("test -f 039-config.txt"))
        lock = self.repo / ".git" / "config.lock"
        lock.touch()  # held, as by a second run starting at the same moment
        run = self.ceo("run", "orders/039-config.md", "--worker", self.model("own"))
        lock.unlink()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.read("039-config")["state"], "done")

    def test_two_filed_orders_become_two_merged_pull_requests_and_leave_nothing_behind(self):
        (self.repo / "VISION.md").write_text("A kernel that keeps a project moving.\n")
        self.gate()
        for name in ("040-a", "041-b"):
            (self.repo / "orders" / f"{name}.md").write_text(order_text(f"test -f {name}.txt"))
        self.commit_all("two orders filed")  # in orders/ at the same time
        began, checked = self.main(), self.tmp / "checked"
        # A live tick's --checker is accepted and ignored: no check of a merge against the vision starts.
        work = ("--worker", self.model("own"), "--reviewer", self.model("clean"),
                "--checker", shlex.join(["touch", str(checked)]))
        self.tick_while(lambda: all("cleared" in ceo.read_json(self.run_dir(name) / "result.json")
                                    for name in ("040-a", "041-b")), "both merged", *work)
        self.wait_for(lambda: not held(self.repo / ".ceo" / "locks" / "door.lock"), "the door ended")
        self.tick(*work)  # the pass that pulls after the last merge
        # In the order they landed: the oldest review merges first, whichever pull request it is.
        results = sorted((self.read(name) for name in ("040-a", "041-b")),
                         key=lambda result: self.on_origin(f"{result['merged']}^") != began)
        prs = self.server()
        self.assertEqual([pr["state"] for pr in prs], ["MERGED", "MERGED"])
        self.assertEqual(sorted((result["pr"], result["merged"]) for result in results),
                         sorted((pr["number"], pr["mergeCommit"]["oid"]) for pr in prs))
        first, second = (result["merged"] for result in results)
        self.assertEqual(self.main(), second)
        self.assertEqual([self.on_origin(f"{second}^"), self.on_origin(f"{first}^")], [first, began])
        # The first joined nothing, main being as it began; the second was joined with the first.
        tested = self.marks.read_text().split()
        self.assertEqual(tested[0], results[0]["head"])
        self.assertEqual([self.on_origin(f"{tested[1]}^1"), self.on_origin(f"{tested[1]}^2")],
                         [results[1]["head"], first])
        self.assertEqual(len(tested), 2, "gate.sh ran more than once a merge")
        for result in results:
            self.assertEqual(result["title"], "An order")
            self.assertFalse((self.repo / result["worktree"]).exists())
        self.assertEqual(self.git("branch", "--list", "order/*"), "")
        self.assertEqual(self.on_origin("refs/heads/order/040-a") + self.on_origin(
            "refs/heads/order/041-b"), "", "a branch on origin was kept")
        for pr in prs:  # the kernel's review on each, as a comment: GitHub refuses an approval
            self.assertEqual(len(pr["reviews"]), 1)
            self.assertTrue(pr["reviews"][0].startswith("The CEO kernel's review: survives."))
            self.assertIn("Order: `orders/04", pr["body"])
        for name in ("040-a", "041-b"):
            self.assertEqual(self.read(name, "review.json")["posted"], "comment")
            self.assertFalse((self.run_dir(name) / "vision.json").exists())
        self.assertFalse(checked.exists(), "a checker was started")
        # The clean folder on main was pulled, and only that changed in it.
        self.assertEqual(self.git("rev-parse", "main"), second)
        self.assertTrue((self.repo / "040-a.txt").is_file() and (self.repo / "041-b.txt").is_file())
        self.assertEqual(self.git("status", "--porcelain"), "?? STATUS.md")
        self.assertNotIn("behind origin", (self.repo / "STATUS.md").read_text())
        for call in self.calls():  # every gh names its repository and runs in no checkout
            self.assertIn("-R", call["args"])
            self.assertEqual(call["checkout"], "", call)

    def test_a_review_that_fails_or_is_blocked_never_merges(self):
        self.one_round()  # the door's refusal alone is under test: test_fix files the round of a failed review
        self.reviewed("042-fails", reviewer="doubt")
        self.reviewed("043-blocked", reviewer="endless", timeout=1)
        main = self.main()
        door = self.door()
        self.assertEqual((door.returncode, door.stdout), (0, ""))
        self.assertNotIn("door", self.tick())
        self.assertEqual(self.main(), main)
        fails, blocked = self.server()
        self.assertEqual((fails["state"], blocked["state"]), ("OPEN", "OPEN"))
        # GitHub refuses one's own change requests; a comment carries them.
        self.assertEqual(len(fails["reviews"]), 1)
        self.assertIn("fails", fails["reviews"][0])
        self.assertIn("`test -f proof` exits 1", fails["reviews"][0])
        self.assertEqual(blocked["reviews"], [])
        for name in ("042-fails", "043-blocked"):
            self.assertNotIn("merged", self.read(name))
            self.assertTrue((self.repo / ".ceo" / "wt" / name).is_dir())

    def test_a_committed_answer_cannot_replace_a_fresh_review(self):
        # The fresh reviewer may write exactly the same bytes as an earlier committed answer.
        answer = json.dumps({"summary": "checked the work", "findings": []})
        path = self.repo / "REVIEW.json"
        for number, (seeded, reviewer, verdict, state) in enumerate((
                (False, "silent", "retry", "OPEN"), (True, "clean", "survives", "MERGED"),
                (True, "silent", "retry", "OPEN"))):
            with self.subTest(seeded=seeded, reviewer=reviewer):
                if seeded and not path.exists():
                    path.write_text(answer)
                    self.commit_all("retain an earlier review answer")
                name = f"{907 + number}-review-provenance"
                result = self.reviewed(name, reviewer=reviewer)
                review = self.read(name, "review.json")
                main = self.main()
                door = self.door()
                self.assertEqual(door.returncode, 0, door.stdout + door.stderr)
                self.assertEqual((review["verdict"], review["reviewer_exit"], self.server()[-1]["state"]),
                                 (verdict, 0, state))
                if reviewer == "silent":
                    self.assertIn("without an answer", review["reason"])
                    self.assertEqual((self.main(), self.server()[-1]["reviews"]), (main, []))
                if seeded:
                    self.assertEqual(path.read_text(), answer)
                    self.assertEqual(self.git("show", f"{result['head']}:REVIEW.json"), answer)

    def test_a_conflict_needs_attention_with_its_files_and_its_pull_request_stays_open(self):
        (self.repo / "STATUS.md").write_text("# Status\n\n## Needs attention\n\n## Finished\n")
        self.commit_all("status")
        for name in ("044-x", "045-y"):
            self.reviewed(name, worker="same", done_when="test -f shared.txt")
        self.assertEqual(self.door().returncode, 0)
        main = self.main()
        door = self.door()
        self.assertEqual(door.returncode, 1, door.stdout)
        result = self.read("045-y")
        self.assertEqual(result["conflicts"], ["shared.txt"])
        self.assertIn("conflicts with main in shared.txt", result["attention"])
        self.assertNotIn("merged", result)
        self.assertEqual((self.main(), self.server()[1]["state"]), (main, "OPEN"))
        self.assertTrue((self.repo / ".ceo" / "wt" / "045-y").is_dir())
        self.assertEqual(self.door().stdout, "", "a run that needs attention was taken again")
        # Only status is under test; the login gate keeps any detached model from outliving the scratch repo.
        (conf := self.repo / "ceo.toml").write_text("stall_minutes = 0\n" + conf.read_text())  # the alarm's clock
        self.idle_tick()  # it files 045-y's join round, as for any filed order, and the outage keeps it unstarted:
        self.idle_tick()  # past its stall clock, a line of its own above, then its stop's
        join = f"045-join-{result['head'][:7]}"
        self.assertEqual(self.moved(main), [f"orders/{join}.md"])
        status = (self.repo / "STATUS.md").read_text().splitlines()
        at = status.index("## Needs attention")
        self.assertRegex(status[at + 1], rf"^- `{join}` is eligible and not started: the login of ")
        self.assertTrue(status[at + 2].startswith(f"- `{join}` stalled since "), status)
        self.assertEqual(status[at + 3], f"- `orders/045-y.md` needs attention: {result['attention']}")
        self.assertEqual(sum("045-y" in line for line in status), 1)

    def test_a_joined_commit_that_fails_its_tests_twice_fails_its_review_and_nothing_merges(self):
        self.gate("test ! -f 046-a.txt || test ! -f 047-b.txt")  # each alone passes; both, never
        for name in ("046-a", "047-b"):
            self.reviewed(name)
        self.assertEqual(self.door().returncode, 0)
        main = self.main()
        self.assertEqual(self.door().returncode, 2)
        self.assertNotIn("attention", self.read("047-b"))
        self.assertEqual(self.door().returncode, 1)
        self.assertNotIn("attention", self.read("047-b"))
        self.assertRegex(self.read("047-b", "review.json")["door"],
                         r"^\./gate\.sh exited 1 on the joined commit [0-9a-f]{40}; on its retry, "
                         r"\./gate\.sh exited 1 on the joined commit [0-9a-f]{40}\.\n\n    ")
        self.assertEqual((self.main(), self.server()[1]["state"]), (main, "OPEN"))
        self.assertTrue((self.repo / ".ceo" / "wt" / "047-b").is_dir())

    def test_the_server_refuses_a_head_other_than_the_tested_commit_and_nothing_unreviewed_lands(self):
        result = self.reviewed("048-push")
        main, git_dir = self.main(), f"--git-dir={self.origin}"
        ref = "refs/heads/order/048-push"
        # Someone pushes to the branch after the door's tests, just before the server merges.
        late = (f'h=$(git {git_dir} rev-parse {ref}) && c=$(git {git_dir} commit-tree "$h^{{tree}}" '
                f'-p "$h" -m unreviewed) && git {git_dir} update-ref {ref} "$c"')
        env = {**self.env, "FAKE_GH_BEFORE_MERGE": late}
        door = self.door(env=env)
        self.assertEqual(door.returncode, 2, door.stdout)
        self.assertEqual((self.main(), self.server()[0]["state"]), (main, "OPEN"))
        self.assertEqual(self.door().returncode, 1)
        self.assertEqual(self.read("048-push")["attention"], "its branch on origin moved after its review")
        self.assertEqual((self.main(), self.server()[0]["state"]), (main, "OPEN"))
        self.assertTrue((self.repo / result["worktree"]).is_dir())

    def test_moving_main_refuses_the_whole_push_then_tests_the_new_combination(self):
        for moment in ("before", "during"):
            with self.subTest(moment=moment):
                c = DoorCase()
                c.setUp()
                try:
                    (c.repo / "palette").write_text("blue\n")
                    c.commit_all("initial API")
                    c.gate("test ! -e widget || cmp -s palette widget")
                    slug = "903-moving-main"
                    c.commit_order(slug + ".md", order_text("test -f widget"))
                    worker = shlex.join(["sh", "-c", "cat >/dev/null; cp palette widget; "
                                         "git add widget; git commit -qm widget"])
                    self.assertEqual(c.ceo("run", "orders/" + slug + ".md", "--worker", worker).returncode, 0)
                    self.assertEqual(c.ceo("review", "orders/" + slug + ".md",
                                           "--reviewer", c.model("clean")).returncode, 0)
                    head = c.on_origin("refs/heads/order/" + slug)
                    advance = c.tmp / "advance.sh"
                    advance.write_text("set -e\ncd " + shlex.quote(str(c.repo)) + "\n"
                                       "printf 'red\\n' > palette\ngit add palette\n"
                                       "git commit -qm concurrent-change\n./gate.sh\n"
                                       "git push -q origin main\n")
                    env = dict(c.env)
                    if moment == "before":
                        env["FAKE_GH_BEFORE_MERGE"] = shlex.join(["sh", str(advance)])
                    else:
                        # Advance main after receive-pack advertised its old tip: the real server
                        # must reject the transaction, not just a client-side stale lease check.
                        hook = c.origin / "hooks" / "pre-receive"
                        hook.write_text(f"#!{sys.executable}\nimport os, subprocess, sys\n"
                                        "updates = sys.stdin.read()\n"
                                        "if 'refs/heads/order/' not in updates or 'refs/heads/main' not in updates:\n"
                                        "    sys.exit(0)\n"
                                        "env = dict(os.environ)\n"
                                        "for key in ('GIT_DIR', 'GIT_OBJECT_DIRECTORY', "
                                        "'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_QUARANTINE_PATH'):\n"
                                        "    env.pop(key, None)\n"
                                        f"subprocess.run(['sh', {str(advance)!r}], env=env, check=True)\n")
                        hook.chmod(0o755)
                    door = c.door(env=env)
                    self.assertEqual(door.returncode, 2, door.stdout + door.stderr)
                    self.assertEqual(c.server()[0]["state"], "OPEN")
                    self.assertEqual(c.on_origin("refs/heads/order/" + slug), head)
                    self.assertEqual(c.main(), c.git("rev-parse", "main"))
                    self.assertEqual(c.git("show", "main:palette"), "red")
                    self.assertFalse((c.repo / "widget").exists())
                    self.assertEqual(c.ceo("door").returncode, 2)
                    self.assertEqual(c.ceo("door").returncode, 1)
                    self.assertNotIn("attention", c.read(slug))
                    review = c.read(slug, "review.json")
                    self.assertEqual(review["verdict"], "fails")
                    self.assertIn("./gate.sh exited 1", review["door"])
                finally:
                    c.doCleanups()

    def test_a_merge_the_server_judges_or_shows_late_is_tried_again_and_lands(self):
        # A refused push is retried on the next pass; GitHub can also show a merge late.
        self.reviewed("054-slow")
        env = {**self.env, "FAKE_GH_SLOW": str(self.tmp / "judged"),
               "FAKE_GH_LATE": str(self.tmp / "shown")}
        self.assertEqual(self.door(env=env).returncode, 2)
        self.assertEqual(self.server()[0]["state"], "OPEN")
        door = self.door(env=env)
        self.assertEqual(door.returncode, 0, door.stdout)
        self.assertEqual(self.server()[0]["state"], "MERGED")
        self.assertTrue(self.settled("054-slow"))
        self.assertTrue((self.tmp / "shown").exists())
        self.assertFalse(any(call["args"][:2] == ["pr", "merge"] for call in self.calls()))

    def test_a_commit_pushed_on_the_reviewed_commit_is_never_replaced_and_needs_attention(self):
        result = self.reviewed("055-fix")
        side, ref = self.tmp / "side", "refs/heads/order/055-fix"
        # The owner fixes the pull request after its review, from a clone of their own.
        subprocess.run(["git", "clone", "-q", "-b", "order/055-fix", str(self.origin), str(side)],
                       env=self.env, check=True)
        (side / "fix.txt").write_text("the owner's fix\n")
        for step in (("config", "maintenance.auto", "false"),
                     ("add", "fix.txt"), ("commit", "-qm", "the owner's fix"),
                     ("push", "-q", "origin", f"HEAD:{ref}")):
            subprocess.run(["git", "-C", str(side), *step], env=self.env, check=True,
                           capture_output=True)
        fixed, main = self.on_origin(ref), self.main()
        door = self.door()
        self.assertEqual(door.returncode, 1, door.stdout)
        self.assertEqual(self.read("055-fix")["attention"], "its branch on origin moved after its review")
        self.assertEqual((self.server()[0]["state"], self.on_origin(ref), self.main()),
                         ("OPEN", fixed, main), "the owner's commit was pushed away")
        self.assertTrue((self.repo / result["worktree"]).is_dir())

    def test_a_door_killed_before_or_after_the_merge_is_settled_by_the_next_pass(self):
        self.reviewed("049-first")
        self.git("commit", "-q", "--allow-empty", "-m", "main moves")  # so the door pushes a join
        self.git("push", "-q", "origin", "main")
        for moment, name in (("before", "049-first"), ("after", "049-first")):
            with self.subTest(moment):
                env = {**self.env, "FAKE_GH_KILL": f"{moment}:{self.tmp / moment}"}
                killed = self.door(env=env)
                self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stdout)
                self.assertEqual(self.server()[0]["state"], "OPEN" if moment == "before" else "MERGED")
                self.assertNotIn("merged", self.read(name))
        # Merged on the server, unrecorded here: the next pass reads the server and clears it away.
        self.assertIn("started the door", self.tick())
        self.wait_for(lambda: self.settled("049-first"), "the merged run was settled")
        self.assertEqual(self.read("049-first")["merged"], self.server()[0]["mergeCommit"]["oid"])
        self.assertFalse((self.repo / ".ceo" / "wt" / "049-first").exists())
        self.assertEqual(self.on_origin("refs/heads/order/049-first"), "")

    def test_a_join_pushed_by_a_killed_door_is_tested_and_merged_by_the_next(self):
        self.reviewed("050-again")
        self.git("commit", "-q", "--allow-empty", "-m", "main moves")
        self.git("push", "-q", "origin", "main")
        env = {**self.env, "FAKE_GH_KILL": f"before:{self.tmp / 'once'}"}
        self.assertEqual(self.door(env=env).returncode, -signal.SIGKILL)
        pushed = self.on_origin("refs/heads/order/050-again")
        self.assertEqual(self.on_origin(f"{pushed}^1"), self.read("050-again")["head"])
        self.git("commit", "-q", "--allow-empty", "-m", "main moves again")
        self.git("push", "-q", "origin", "main")
        door = self.door()
        self.assertEqual(door.returncode, 0, door.stdout)
        self.assertEqual(self.server()[0]["state"], "MERGED")
        self.assertTrue(self.settled("050-again"))

    def test_a_published_merge_is_not_repeated_while_github_shows_its_merge_late(self):
        self.reviewed("056-late")
        self.assertEqual(self.door(env={**self.env, "FAKE_GH_KILL": f"after:{self.tmp / 'killed'}"})
                         .returncode, -signal.SIGKILL)
        merged = self.main()
        self.assertEqual(self.door(env={**self.env, "FAKE_GH_LATE": str(self.tmp / "late")})
                         .returncode, 0)
        self.assertEqual(self.main(), merged)
        self.assertNotIn("attention", self.read("056-late"))
        self.assertEqual(self.door().returncode, 0)
        self.assertEqual(self.read("056-late")["merged"], merged)
        self.assertTrue(self.settled("056-late"))

    def test_the_door_with_the_owners_folder_dirty_changes_nothing_in_it(self):
        (self.repo / "notes.txt").write_text("the owner's notes\n")
        self.commit_all("notes")
        self.reviewed("051-dirty")
        (self.repo / "notes.txt").write_text("the owner's notes, changed and not committed\n")
        (self.repo / "staged.txt").write_text("staged by the owner\n")
        self.git("add", "staged.txt")
        (self.repo / "loose.txt").write_text("untracked\n")
        before = self.folder()
        door = self.door()
        self.assertEqual(door.returncode, 0, door.stdout)
        self.assertEqual(self.server()[0]["state"], "MERGED")
        self.assertEqual(self.folder(), before, "the door changed the owner's folder")
        # The tick pulls nothing into a dirty folder and says how far main is behind.
        # Hold off new model work through the login gate so cleanup cannot race a detached model.
        self.idle_tick()
        *after, status, files = self.folder()
        self.assertEqual(after, list(before[:4]))
        self.assertEqual(status.replace("?? STATUS.md\n", ""), before[4])
        self.assertEqual({p: t for p, t in files.items() if p not in ("STATUS.md", "RETURN.md")}, before[5])
        self.assertIn("main is 2 behind origin\n", (self.repo / "STATUS.md").read_text())

    def test_a_clean_folder_is_pulled_only_on_main_and_the_line_goes_once_it_caught_up(self):
        self.reviewed("052-pull")
        self.git("checkout", "-q", "-b", "elsewhere")
        here = self.git("rev-parse", "HEAD")
        self.assertEqual(self.door().returncode, 0)
        self.idle_tick()  # exercise the pull without starting a detached model
        self.assertIn("main is 2 behind origin", (self.repo / "STATUS.md").read_text())
        self.assertEqual([self.git("rev-parse", "main"), self.git("rev-parse", "elsewhere")],
                         [here, here], "a branch of the owner's moved")
        self.assertFalse((self.repo / "052-pull.txt").exists())
        self.git("checkout", "-q", "main")
        self.idle_tick()
        self.assertEqual(self.git("rev-parse", "main"), self.main())
        self.assertTrue((self.repo / "052-pull.txt").is_file())
        self.assertNotIn("behind origin", (self.repo / "STATUS.md").read_text())

    def test_an_untracked_folder_is_kept_when_main_is_pulled_and_the_behind_line_goes(self):
        self.reviewed("059-untracked")
        kept = self.repo / "kept" / "notes.txt"
        kept.parent.mkdir()
        kept.write_text("the owner's untracked notes\n")
        self.git("checkout", "-q", "-b", "elsewhere")
        self.assertEqual(self.door().returncode, 0)
        self.idle_tick()
        self.assertIn("main is 2 behind origin", (self.repo / "STATUS.md").read_text())
        self.git("checkout", "-q", "main")
        self.idle_tick()
        self.assertEqual(self.git("rev-parse", "main"), self.main())
        self.assertEqual(self.git("rev-parse", "HEAD"), self.main())
        self.assertEqual((self.repo / "059-untracked.txt").read_text(), "059-untracked.txt\n")
        self.assertEqual(kept.read_text(), "the owner's untracked notes\n")
        self.assertIn("?? kept/notes.txt", self.git("status", "--porcelain", "-uall").splitlines())
        self.assertNotIn("behind origin", (self.repo / "STATUS.md").read_text())

    def test_an_untracked_file_the_pull_would_overwrite_leaves_the_folder_and_main_behind(self):
        self.reviewed("060-untracked")
        clash = self.repo / "060-untracked.txt"
        clash.write_text("the owner's own file\n")
        before = self.folder()
        self.assertEqual(self.door().returncode, 0)
        self.assertEqual(self.server()[0]["state"], "MERGED")
        self.assertEqual(self.folder(), before)
        self.idle_tick()
        *after, status, files = self.folder()
        self.assertEqual(after, list(before[:4]), "the refused pull moved HEAD, main or the index")
        self.assertEqual([line for line in status.splitlines() if line != "?? STATUS.md"],
                         before[4].splitlines())
        self.assertEqual({p: t for p, t in files.items() if p not in ("STATUS.md", "RETURN.md")}, before[5])
        self.assertEqual(clash.read_text(), "the owner's own file\n")
        self.assertIn("main is 2 behind origin\n", (self.repo / "STATUS.md").read_text())

    def test_a_main_with_commits_of_the_owners_own_is_never_merged_into(self):
        self.reviewed("056-ahead")
        (self.repo / "mine.txt").write_text("the owner's own work, not pushed\n")
        self.git("add", "mine.txt")
        self.git("commit", "-qm", "the owner's own commit")
        own = self.git("rev-parse", "HEAD")
        self.assertEqual(self.door().returncode, 0)
        self.idle_tick()  # exercise the pull without starting a detached model
        self.assertEqual(self.git("rev-parse", "main"), own, "the tick merged into the owner's main")
        self.assertIn("main is 2 behind origin", (self.repo / "STATUS.md").read_text())

    def test_a_tick_stopped_while_it_pulls_leaves_the_folder_pulled_whole(self):
        self.git("config", "gc.auto", "0")  # no git of the test's own at work in the background
        for sig, name, code in ((signal.SIGINT, "057-int", 130),
                                (signal.SIGTERM, "058-term", -signal.SIGTERM)):
            with self.subTest(sig.name):
                self.reviewed(name, worker="many", done_when=f"test -f {name}/19999.md")
                self.assertEqual(self.door().returncode, 0)
                # This pull-only test holds off model work through the real login gate.
                (self.repo / ".ceo" / "login.json").write_text(json.dumps(
                    {"*": [time.time(), "test login unavailable"]}))
                tick = subprocess.Popen([sys.executable, str(CEO), "tick"],
                                        cwd=self.repo, env=self.env, start_new_session=True,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                while not (self.repo / name).exists() and tick.poll() is None:
                    time.sleep(0.002)
                # git holds the index while it writes the merged files into the folder.
                pulling = (self.repo / ".git" / "index.lock").exists() and tick.poll() is None
                self.assertTrue(pulling, "the tick was not caught while it pulled")
                os.killpg(tick.pid, sig)  # Ctrl-C reaches the tick's whole group; a stop, SIGTERM
                self.assertEqual(tick.wait(timeout=120), code)
                self.assertFalse((self.repo / ".git" / "index.lock").exists(), "git outlived it")
                self.assertEqual(self.git("rev-parse", "main"), self.main())
                self.assertEqual(self.git("status", "--porcelain", "--", ":!STATUS.md"), "")
                self.assertEqual(len(list((self.repo / name).iterdir())), 20000)
                self.tick()
                self.assertNotIn("behind origin", (self.repo / "STATUS.md").read_text())

    def test_a_second_door_is_refused_while_one_is_at_work(self):
        self.gate(f'touch "{self.tmp}/paused"; while [ ! -e "{self.release}" ]; do sleep 0.05; done')
        self.reviewed("053-slow")
        door = self.ceo_bg("door")
        self.wait_for((self.tmp / "paused").exists, "the door runs gate.sh")
        second = self.door()
        self.assertEqual(second.returncode, 2)
        self.assertRegex(second.stderr, r"^ceo: refused: [^\n]+\.\n$")
        self.assertNotIn("started the door", self.tick())
        self.release.touch()
        door.communicate(timeout=60)
        self.assertEqual(door.returncode, 0)


class RecordsMoved(DoorCase):
    records = ["decisions.md", "IDEAS.md", "LESSONS.md", "STATUS.md", "orders/980-other.md", "orders/980-other.cut",
               "plans/980-wave.md", "bugs/980.md", "results/980-other.md"]

    def scene(self, knocked, *, conflict=False, at_push=False, worker=None):
        """A reviewed run, a code change requiring a join, and records arriving during its gate."""
        self.slug = "970-records"
        self.knock, self.knocks = self.tmp / "knock.sh", self.tmp / "knocks"
        paths = shlex.join(knocked)
        self.knock.write_text("set -e\ncd " + shlex.quote(str(self.repo)) + "\n"
                              f'for p in {paths}; do mkdir -p "$(dirname "$p")"; '
                              'echo knocked >> "$p"; done\n'
                              f"git add -- {paths}\ngit commit -qm 'CEO 980-other: wait'\n"
                              "git push -q origin main\n"
                              "git rev-parse HEAD >> " + shlex.quote(str(self.knocks)) + "\n",
                              errors="surrogateescape")
        knock = shlex.join(["sh", str(self.knock)])
        once = shlex.quote(str(self.tmp / "knocked"))
        self.gate("" if at_push else f"if [ ! -e {once} ]; then touch {once}; {knock} || exit 1; fi")
        self.done_marks = self.tmp / "done-when"
        self.commit_order(self.slug + ".md", order_text(
            f"test -f {self.slug}.txt && git rev-parse HEAD >> {shlex.quote(str(self.done_marks))}"))
        worker = worker or self.model("own")
        if conflict:
            worker = shlex.join(["sh", "-c", "cat >/dev/null; mkdir -p results; "
                                 "echo branch > results/980-other.md; "
                                 f"touch {self.slug}.txt; git add results/980-other.md {self.slug}.txt; "
                                 "git commit -qm build"])
        run = self.ceo("run", f"orders/{self.slug}.md", "--worker", worker)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        review = self.ceo("review", f"orders/{self.slug}.md", "--reviewer", self.model("clean"))
        self.assertEqual(review.returncode, 0, review.stdout + review.stderr)
        (self.repo / "palette").write_text("blue\n")
        self.commit_all("code moves main after the review")
        self.done_marks.write_text("")  # count just the door's done-when executions
        return self.main()

    def log(self):
        return (self.run_dir(self.slug) / "door.log").read_text()

    def refused(self, door, repushes=0):
        self.assertEqual(door.returncode, 2, door.stdout + door.stderr)
        self.assertIn("the tested merge was not pushed", door.stdout)
        self.assertEqual(self.server()[0]["state"], "OPEN")
        self.assertNotIn("attention", self.read(self.slug))
        self.assertNotIn("cleared", self.read(self.slug))
        self.assertEqual(self.log().count(": door: push again"), repushes)
        self.assertEqual(self.main(), self.knocks.read_text().split()[-1])

    def test_records_landing_on_main_during_the_gate_merge_once_without_a_second_test(self):
        base = self.scene(self.records)
        door = self.door()
        self.assertEqual(door.returncode, 0, door.stdout + door.stderr)
        self.assertEqual(self.server()[0]["state"], "MERGED")
        self.assertTrue(self.settled(self.slug))
        tested, knocked = self.marks.read_text().split(), self.knocks.read_text().strip()
        self.assertEqual(len(tested), 1)
        self.assertEqual(self.done_marks.read_text().split(), tested)
        self.assertEqual(self.log().count(": door: its done-when block"), 1)
        self.assertEqual(self.on_origin(f"{tested[0]}^2"), base)
        self.assertEqual(self.on_origin(f"{knocked}^"), base)
        self.assertEqual(self.on_origin(f"{self.main()}^1"), knocked)
        self.assertEqual(self.on_origin(f"{self.main()}^2"), tested[0])
        marks = [line for line in self.log().splitlines() if ": door: push again" in line]
        self.assertEqual(len(marks), 1)
        self.assertIn(f"door: push again onto main {knocked}", marks[0])
        for path in self.records:
            self.assertIn(path, marks[0])

    def test_code_or_any_other_path_landing_during_the_gate_defers_the_door_as_today(self):
        for path in ("palette", "README.md", "board/pick.png", "test_other.py"):
            with self.subTest(path=path):
                c = RecordsMoved()
                c.setUp()
                self.addCleanup(c.doCleanups)
                base = c.scene(["decisions.md", path])
                c.refused(c.door())
                knocked = c.main()
                self.assertEqual(c.on_origin(f"{knocked}^"), base)
                self.assertEqual(c.on_origin(f"refs/heads/order/{c.slug}"), c.marks.read_text().strip())
                again = c.door()
                self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
                tested = c.marks.read_text().split()
                self.assertEqual(len(tested), 2)
                self.assertEqual(c.done_marks.read_text().split(), tested)
                self.assertEqual(c.on_origin(f"{tested[1]}^2"), knocked)
                self.assertEqual(c.server()[0]["state"], "MERGED")
                self.assertTrue(c.settled(c.slug))

    def test_the_merged_tree_outside_the_record_paths_is_the_tested_joined_tree(self):
        self.scene(self.records)
        door = self.door()
        self.assertEqual(door.returncode, 0, door.stdout + door.stderr)
        joined, knocked = self.marks.read_text().strip(), self.knocks.read_text().strip()

        def blobs(commit):
            entries = self.git("--git-dir", str(self.origin), "ls-tree", "-rz", commit).split("\0")
            return {path: blob for entry in entries if entry for blob, path in [entry.split("\t", 1)]}

        tested, merged, records = blobs(joined), blobs(self.main()), blobs(knocked)
        self.assertEqual({p: b for p, b in tested.items() if p not in self.records},
                         {p: b for p, b in merged.items() if p not in self.records})
        for path in self.records:
            self.assertEqual(merged[path], records[path])
        changed = self.git("--git-dir", str(self.origin), "diff-tree", "-r", "--name-only",
                           "--no-renames", joined, self.main()).splitlines()
        self.assertEqual(sorted(changed), sorted(self.records))

    def test_a_record_of_this_very_order_or_one_its_branch_also_wrote_defers_the_door(self):
        for path, conflict in (("orders/970-records.cut", False), ("results/980-other.md", True)):
            with self.subTest(path=path):
                c = RecordsMoved()
                c.setUp()
                self.addCleanup(c.doCleanups)
                base = c.scene(["decisions.md", path], conflict=conflict)
                door = c.door()
                c.refused(door)
                self.assertIn("conflict" if conflict else "this order's number", door.stdout)
                self.assertEqual(c.on_origin(f"{c.main()}^"), base)
                self.assertEqual(c.on_origin(f"refs/heads/order/{c.slug}"), c.marks.read_text().strip())
                self.assertEqual(len(c.marks.read_text().split()), 1)
                self.assertEqual(c.done_marks.read_text(), c.marks.read_text())

    def test_records_landing_before_every_push_defer_the_door_after_three_repushes(self):
        base = self.scene(["decisions.md"], at_push=True)
        env = {**self.env, "FAKE_GH_BEFORE_MERGE": shlex.join(["sh", str(self.knock)])}
        door = self.door(env=env)
        self.refused(door, repushes=3)
        knocks = self.knocks.read_text().split()
        self.assertEqual(len(knocks), 4)
        self.assertEqual(self.on_origin(f"{knocks[-1]}~4"), base)
        self.assertEqual(self.git("--git-dir", str(self.origin), "show", "main:decisions.md").splitlines(),
                         ["knocked"] * 4)
        self.assertEqual(len(self.marks.read_text().split()), 1)
        self.assertEqual(self.done_marks.read_text(), self.marks.read_text())
        self.assertEqual(self.on_origin(f"refs/heads/order/{self.slug}"), self.marks.read_text().strip())
        again = self.door()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(len(self.marks.read_text().split()), 2)
        self.assertEqual(self.server()[0]["state"], "MERGED")
        self.assertTrue(self.settled(self.slug))

    def test_a_record_path_with_non_utf8_bytes_merges_and_is_logged_safely(self):
        try:
            with open(os.path.join(os.fsencode(self.tmp), b"\xff"), "wb"):
                pass
        except OSError as why:
            if why.errno != errno.EILSEQ:
                raise
            self.skipTest("the file system refuses non-UTF-8 filenames with EILSEQ")
        path = os.fsdecode(b"results/980-\xff.md")
        self.scene([path])
        door = self.door()
        self.assertEqual(door.returncode, 0, door.stdout + door.stderr)
        self.assertTrue(self.settled(self.slug))
        self.assertIn(ascii(path), self.log())
        changed = subprocess.check_output(["git", "--git-dir", str(self.origin), "diff-tree", "-r",
                                           "--name-only", "--no-renames", "-z",
                                           self.marks.read_text().strip(), self.main()])
        self.assertEqual(changed, os.fsencode(path) + b"\0")

    def test_cr_and_lf_in_record_paths_do_not_alias_in_the_tree_guard(self):
        source, destination = "results/980-x\r.md", "results/980-x\n.md"
        (self.repo / "results").mkdir()
        (self.repo / source).write_text("".join(f"line {i}\n" for i in range(50)))
        self.commit_all("a record whose path contains CR")
        worker = shlex.join(["sh", "-c", "cat >/dev/null; " + shlex.join(["mv", source, destination]) +
                             "; touch 970-records.txt; git add -- results 970-records.txt; "
                             "git commit -qm rename"])
        self.scene([source], worker=worker)
        door = self.door()
        self.refused(door)
        self.assertIn("the merged tree is not the tested tree outside main's moved records", door.stdout)
        self.assertEqual(self.done_marks.read_text(), self.marks.read_text())

    def test_a_conflicting_record_with_non_utf8_bytes_defers_the_door(self):
        try:
            with open(os.path.join(os.fsencode(self.tmp), b"\xff"), "wb"):
                pass
        except OSError as why:
            if why.errno != errno.EILSEQ:
                raise
            self.skipTest("the file system refuses non-UTF-8 filenames with EILSEQ")
        path = b"results/980-\xff.md"
        worker = shlex.join([sys.executable, "-c",
                             "import os, subprocess, sys; sys.stdin.read(); os.mkdir('results'); "
                             f"open({path!r}, 'wb').write(b'branch\\n'); "
                             "open('970-records.txt', 'w').write('built\\n'); "
                             "subprocess.run(['git', 'add', 'results', '970-records.txt'], check=True); "
                             "subprocess.run(['git', 'commit', '-qm', 'build'], check=True)"])
        self.scene([os.fsdecode(path)], worker=worker)
        door = self.door()
        self.refused(door)
        self.assertIn("the records conflict", door.stdout)


class DoorRetry(DoorCase):
    def setUp(self):
        super().setUp()
        self.tested = self.tmp / "door-tested"
        self.work = ("--worker", self.model("own"), "--reviewer", self.model("clean"))

    def lines(self, path):
        return path.read_text().splitlines() if path.exists() else []

    def block(self, slug, script):
        # The run's own block passes; only the joined commit in the door meets this fault.
        return (f'test -f {slug}.txt || exit 1\ncase "$PWD" in */.ceo/door)\n'
                f'  git rev-parse HEAD >> "{self.tested}"\n  {script};;\nesac')

    def told(self, slug, kind):
        return [seen["sentence"] for seen in map(json.loads, self.lines(self.repo / ".ceo" / "events.jsonl"))
                if (seen["event"], seen["slug"]) == (kind, slug)]

    def door_marks(self, slug):
        return [line.split(": ", 2)[2] for line in self.lines(self.run_dir(slug) / "door.log")
                if line.startswith("== ceo ") and ": door: " in line]

    def until(self, check, what):
        self.tick_while(check, what, *self.work, timeout=300)

    def idle(self):
        self.wait_for(lambda: not held(self.repo / ".ceo" / "locks" / "door.lock"), "the door ended", 300)

    def first_failure(self, slug):
        self.assertIn("started the door", self.tick(*self.work))
        # Observe this door's failure without a pass that could already start its retry.
        self.wait_for(lambda: "door_failures" in self.read(slug) or "attention" in self.read(slug),
                      "the first door recorded its failure", 300)
        self.idle()

    def settle(self, *merged):
        self.until(lambda: all("cleared" in ceo.read_json(self.run_dir(slug) / "result.json") for slug in merged),
                   "the merges cleared")
        for _ in range(3):
            self.idle()
            self.assertNotIn("started the door", self.tick(*self.work), "a merged or parked run was taken again")
        self.idle()
        self.wait_for(lambda: not any(held(lock) for kind in ceo.RECORD
                                     for lock in (self.repo / ".ceo" / "locks").glob(f"*.{kind}")),
                      "all workers and reviews ended", 300)
        for slug in merged:
            self.assertFalse((self.run_dir(slug) / "vision.json").exists())

    def test_a_joined_done_when_that_fails_once_is_retried_by_a_later_door_and_merged(self):
        slug, once = "070-flaky", self.tmp / "failed-once"
        self.gate()
        result = self.reviewed(slug, done_when=self.block(
            slug, f'if test ! -e "{once}"; then touch "{once}"; exit 1; fi'))
        main, head = self.main(), result["head"]
        self.first_failure(slug)
        result = self.read(slug)
        self.assertNotIn("attention", result)
        first, = result["door_failures"]
        self.assertEqual(set(first), {"what", "exit", "joined", "time"})
        self.assertEqual((first["what"], first["exit"], first["joined"]), ("its done-when block", 1, head))
        self.assertIsNotNone(ceo.datetime.fromisoformat(first["time"]).tzinfo)
        self.assertEqual(self.lines(self.tested), [head])
        self.assertEqual((self.main(), self.on_origin(f"refs/heads/order/{slug}")), (main, head))
        self.assertEqual(self.server()[0]["state"], "OPEN")
        self.assertNotIn("joins", result)
        self.assertNotIn("merge_commit", result)
        self.assertFalse(self.marks.exists(), "the gate ran after the failed done-when")
        why = f"its done-when block exited 1 on the joined commit {head}"
        retry, = self.told(slug, "retry")
        self.assertIn(f"a retry of a failed door: {why}", retry)
        self.assertIn(f"again later: {why}", (self.repo / ".ceo" / "tick.log").read_text())
        self.until(lambda: self.settled(slug) or "attention" in self.read(slug), "the later door merged it")
        self.assertTrue(self.settled(slug), self.read(slug))
        self.settle(slug)
        self.assertEqual(self.read(slug)["door_failures"], [first])
        self.assertNotIn("attention", self.read(slug))
        self.assertEqual(self.server()[0]["state"], "MERGED")
        self.assertEqual(self.lines(self.tested), [head, head])
        self.assertEqual(self.lines(self.marks), [head])
        marks = self.door_marks(slug)
        clone = f"a clean clone of origin, to join main into {head}"
        self.assertEqual(marks, [clone, f"its done-when block on the joined commit {head}", clone,
                                f"its done-when block on the joined commit {head}, a retry after {why}",
                                f"./gate.sh on the joined commit {head}, a retry after {why}",
                                f"push and merge the joined commit {head}"])
        self.assertEqual((len(self.told(slug, "retry")), self.told(slug, "blocked")), (1, []))

    def test_the_retry_joins_the_main_that_moved_after_the_failure(self):
        slug, once = "071-moving", self.tmp / "failed-once"
        git = f'git --git-dir="{self.origin}"'
        move = (f'{git} update-ref refs/heads/main '
                f'"$({git} commit-tree "$({git} rev-parse "main^{{tree}}")" -p main -m moved)"')
        self.reviewed(slug, done_when=self.block(
            slug, f'if test ! -e "{once}"; then touch "{once}"; {move}; exit 1; fi'))
        began = self.main()
        self.first_failure(slug)
        moved = self.main()
        self.assertNotEqual(moved, began)
        self.assertEqual(self.on_origin(moved + "^"), began)
        self.assertNotIn("attention", self.read(slug))
        self.until(lambda: self.settled(slug) or "attention" in self.read(slug), "the new combination merged")
        self.assertTrue(self.settled(slug), self.read(slug))
        self.settle(slug)
        result = self.read(slug)
        first, = result["door_failures"]
        joined = self.on_origin(result["merged"] + "^2")
        self.assertEqual(self.on_origin(result["merged"] + "^1"), moved)
        self.assertEqual((self.on_origin(joined + "^1"), self.on_origin(joined + "^2")), (result["head"], moved))
        self.assertEqual(self.lines(self.tested), [first["joined"], joined])
        self.assertNotEqual(first["joined"], joined)
        self.assertEqual(sum(mark.startswith("a clean clone") for mark in self.door_marks(slug)), 2)
        self.assertEqual((len(self.told(slug, "retry")), self.told(slug, "blocked")), (1, []))

    def test_a_joined_gate_that_always_fails_fails_its_review_after_two_attempts_and_never_a_third(self):
        slug = "072-red-gate"
        self.one_round()  # the door's own failure is under test: test_door_fix files its round
        self.gate("exit 7")
        result = self.reviewed(slug, done_when=self.block(slug, ":"))
        main, head = self.main(), result["head"]
        self.until(lambda: self.read(slug, "review.json")["verdict"] == "fails", "the second gate failure failed it")
        self.settle()
        result = self.read(slug)
        first, second = result["door_failures"]
        self.assertEqual([(one["what"], one["exit"], one["joined"]) for one in (first, second)],
                         [("./gate.sh", 7, head)] * 2)
        why = f"./gate.sh exited 7 on the joined commit {head}"
        self.assertNotIn("attention", result)
        self.assertEqual(self.read(slug, "review.json")["door"].splitlines()[0], f"{why}; on its retry, {why}.")
        self.assertEqual((self.lines(self.tested), self.lines(self.marks)), ([head, head], [head, head]))
        self.assertEqual((self.main(), self.on_origin(f"refs/heads/order/{slug}")), (main, head))
        self.assertEqual(self.server()[0]["state"], "OPEN")
        self.assertTrue((self.repo / result["worktree"]).is_dir())
        self.assertEqual([len(self.told(slug, kind)) for kind in ("retry", "failed", "blocked")], [1, 1, 0])
        marks = self.door_marks(slug)
        self.assertEqual(sum(mark.startswith("a clean clone") for mark in marks), 2)
        self.assertEqual([mark for mark in marks if "a retry after" in mark],
                         [f"{what} on the joined commit {head}, a retry after {why}"
                          for what in ("its done-when block", "./gate.sh")])

    def test_a_joined_done_when_that_runs_past_the_clock_twice_fails_its_review(self):
        slug = "073-clock"
        self.one_round()
        self.gate()
        result = self.reviewed(slug, done_when=self.block(slug, "sleep 60"))
        main, head = self.main(), result["head"]
        self.work += ("--timeout", "1")
        self.until(lambda: self.read(slug, "review.json")["verdict"] == "fails", "the second timeout failed it")
        self.settle()
        result = self.read(slug)
        first, second = result["door_failures"]
        self.assertEqual([(one["what"], one["exit"], one["joined"]) for one in (first, second)],
                         [("its done-when block", None, head)] * 2)
        why = f"its done-when block ran past the wall clock on the joined commit {head}"
        self.assertNotIn("attention", result)
        self.assertEqual(self.read(slug, "review.json")["door"].splitlines()[0], f"{why}; on its retry, {why}.")
        self.assertEqual(self.lines(self.tested), [head, head])
        self.assertFalse(self.marks.exists(), "the gate ran after the timed out done-when")
        self.assertEqual((self.main(), self.on_origin(f"refs/heads/order/{slug}")), (main, head))
        self.assertEqual(self.server()[0]["state"], "OPEN")
        retry, = self.told(slug, "retry")
        self.assertIn(f"a retry of a failed door: {why}", retry)
        failed, = self.told(slug, "failed")
        self.assertIn(f": at the door, {why}; on its retry, {why}.", failed)
        self.assertEqual(self.told(slug, "blocked"), [])
        self.assertEqual(sum(mark.startswith("a clean clone") for mark in self.door_marks(slug)), 2)

    def test_a_conflict_is_parked_at_once_and_never_retried(self):
        for slug in ("074-x", "075-y"):
            self.reviewed(slug, worker="same", done_when="test -f shared.txt")
        self.until(lambda: "attention" in self.read("075-y"), "the conflict was parked")
        self.settle("074-x")
        result = self.read("075-y")
        self.assertEqual(result["conflicts"], ["shared.txt"])
        self.assertIn("conflicts with main in shared.txt", result["attention"])
        self.assertNotIn("door_failures", result)
        self.assertNotIn("merged", result)
        self.assertEqual(self.told("075-y", "retry"), [])
        self.assertEqual(len(self.told("075-y", "blocked")), 1)
        self.assertEqual([pr["state"] for pr in self.server()], ["MERGED", "OPEN"])
        # Main moved past the merge by the conflict's join round alone, filed once as for any filed order.
        self.assertEqual(self.moved(self.read("074-x")["merged"]), [f"orders/075-join-{result['head'][:7]}.md"])
        self.assertTrue((self.repo / result["worktree"]).is_dir())
        self.assertEqual(self.door_marks("075-y"), [f"a clean clone of origin, to join main into {result['head']}"])

    def test_the_retry_waits_while_a_worker_of_another_order_runs_then_follows_it(self):
        slug, once = "076-wait", self.tmp / "failed-once"
        self.reviewed(slug, done_when=self.block(
            slug, f'if test ! -e "{once}"; then touch "{once}"; exit 1; fi'))
        self.commit_order("077-busy.md")
        busy = self.ceo_bg("run", "orders/077-busy.md", "--worker",
                           self.model("wait", str(self.tmp / "started"), str(self.release)))
        self.wait_for(lambda: (self.tmp / "started-077-busy").exists(), "the other order's worker started")
        self.first_failure(slug)
        for _ in range(3):
            self.idle()
            self.assertIsNone(busy.poll(), "the other worker ended before its release")
            self.assertNotIn("started the door", self.tick(*self.work), "the retry started beside the worker")
        self.idle()
        self.assertNotIn("attention", self.read(slug))
        self.assertEqual(len(self.lines(self.tested)), 1)
        self.assertEqual(len(self.read(slug)["door_failures"]), 1)
        self.assertEqual(sum(mark.startswith("a clean clone") for mark in self.door_marks(slug)), 1)
        self.assertEqual(self.server()[0]["state"], "OPEN")
        self.release.touch()
        out, err = busy.communicate(timeout=120)
        self.assertEqual(busy.returncode, 0, out + err)
        self.until(lambda: self.settled(slug) or "attention" in self.read(slug), "the retry after the worker ended")
        self.assertTrue(self.settled(slug), self.read(slug))
        self.settle(slug, "077-busy")
        self.assertEqual(len(self.lines(self.tested)), 2)
        self.assertEqual(len(self.read(slug)["door_failures"]), 1)
        self.assertNotIn("attention", self.read(slug))
        self.assertEqual([pr["state"] for pr in self.server()], ["MERGED", "MERGED"])
        self.assertEqual((len(self.told(slug, "retry")), self.told(slug, "blocked")), (1, []))


class LostReview(DoorCase):
    """Order 4's judged limit: GitHub refuses the kernel's review for a moment (a 502)."""

    def github_down_while(self, down):
        """A gh that answers 502 to every review while the file down exists."""
        shim = self.tmp / "shim"
        shim.mkdir()
        fake = self.env["PATH"].split(os.pathsep)[0]  # the fake gh of DoorCase
        (shim / "gh").write_text(f'#!/bin/sh\nif [ "$1 $2" = "pr review" ] && [ -e "{down}" ]; then\n'
                                 f'  echo "HTTP 502: Bad Gateway (https://api.github.com/graphql)" >&2; exit 1\n'
                                 f'fi\nexec "{fake}/gh" "$@"\n')
        (shim / "gh").chmod(0o755)
        self.env["PATH"] = f"{shim}{os.pathsep}{self.env['PATH']}"

    def test_a_review_github_refused_once_still_lands_on_its_pull_request(self):
        # The failing command of results/004/judge-round-3.json, finding 1, as it was written.
        down = self.tmp / "github-down"
        self.github_down_while(down)
        self.commit_order("095-post.md", order_text("test -f 095-post.txt"))
        self.assertEqual(self.ceo("run", "orders/095-post.md", "--worker", self.model("own")).returncode, 0)
        down.touch()  # GitHub is down for the moment the review is posted
        self.ceo("review", "orders/095-post.md", "--reviewer", self.model("clean"))
        down.unlink()  # and up again for every pass after
        for _ in range(3):
            self.tick("--reviewer", self.model("clean"))
            self.wait_for(lambda: not held(self.repo / ".ceo" / "locks" / "door.lock"), "the door ended")
            time.sleep(0.5)
        pr = self.server()[0]
        self.assertTrue(pr["reviews"], "the pull request has no review of the kernel's on it")

    def test_while_the_server_refuses_the_review_nothing_merges_and_one_line_says_so(self):
        down = self.tmp / "github-down"
        self.github_down_while(down)
        down.touch()  # GitHub is down when the review is posted, and stays down for two doors
        self.reviewed("096-later")
        path = self.run_dir("096-later") / "review.json"
        self.assertEqual((self.read("096-later", "review.json")["posted"], self.server()[0]["reviews"]),
                         (None, []))
        age = path.stat().st_mtime_ns
        for _ in range(2):
            door = self.door()
            self.assertEqual(door.returncode, 2, door.stdout)
            self.assertRegex(door.stdout, r"^ceo: the door takes orders/096-later\.md again later: its "
                                          r"review is not on pull request #1 yet: HTTP 502: Bad Gateway[^\n]*\n$")
            self.assertEqual((self.server()[0]["state"], self.server()[0]["reviews"]), ("OPEN", []))
        down.unlink()
        door = self.door()
        self.assertEqual(door.returncode, 0, door.stdout)
        pr = self.server()[0]
        self.assertEqual(pr["state"], "MERGED")
        self.assertEqual(len(pr["reviews"]), 1, "posted more than once")
        self.assertTrue(pr["reviews"][0].startswith("The CEO kernel's review: survives."))
        self.assertEqual(self.read("096-later", "review.json")["posted"], "comment")
        self.assertEqual(path.stat().st_mtime_ns, age, "the review lost its place in the door's queue")


class Sparse(DoorCase):
    """ceo.toml's [checkout] exclude keeps an evidence folder off every disk the kernel checks a commit
    out on, while git carries it to main."""
    def setUp(self):
        super().setUp()
        (self.repo / "orders" / "proof").mkdir()
        (self.repo / "orders" / "proof" / "shot.png").write_text("evidence no test reads\n")
        self.commit_all("evidence")
        self.seen = self.tmp / "seen"  # what orders/ held on the disk of the reviewer and of the door
        self.seen.mkdir()

    def carry(self, name):
        """Run, review and merge the order name; return what orders/ held on the disk of each checkout
        of it, and what git in its worktree tracks there and finds changed."""
        self.gate(f'ls orders | tr "\\n" " " > "{self.seen}/door"')
        self.commit_order(f"{name}.md", order_text(f"test -f {name}.txt"))
        run = self.ceo("run", f"orders/{name}.md", "--worker", self.model("own"))
        self.assertEqual(run.returncode, 0, run.stderr)
        wt = self.repo / ".ceo" / "wt" / name
        held = {"worktree": " ".join(sorted(path.name for path in (wt / "orders").iterdir())),
                "tracked": self.git("-C", str(wt), "ls-files", "orders/proof"),
                "status": self.git("-C", str(wt), "status", "--porcelain")}
        self.ceo("review", f"orders/{name}.md", "--reviewer",
                 self.model("looks", str(self.seen / "review")))
        self.assertEqual(self.read(name, "review.json")["verdict"], "survives")
        door = self.door()
        self.assertEqual(door.returncode, 0, door.stdout + door.stderr)
        self.assertTrue(self.settled(name))
        return {**held, "review": (self.seen / "review").read_text(),
                "door": (self.seen / "door").read_text().strip()}

    def test_an_excluded_folder_is_on_no_disk_of_the_run_review_or_door_and_main_keeps_it(self):
        with open(self.repo / "ceo.toml", "a") as conf:  # a folder the repository lacks is no error
            conf.write('[checkout]\nexclude = ["orders/proof", "no/such/folder"]\n')
        seen = self.carry("050-sparse")
        self.assertEqual({who: seen[who] for who in ("worktree", "review", "door")},
                         dict.fromkeys(("worktree", "review", "door"), "050-sparse.md"))
        # Kept out of the disk only: git in the worktree still tracks it and misses nothing,
        self.assertEqual((seen["tracked"], seen["status"]), ("orders/proof/shot.png", ""))
        # and main after the merge holds it as it was.
        self.assertEqual(self.on_origin("refs/heads/main:orders/proof/shot.png"),
                         self.git("rev-parse", "HEAD:orders/proof/shot.png"))

    def test_a_folder_is_kept_out_by_its_literal_name_and_no_other(self):
        # Read as a pattern, orders/proof[1] would keep out orders/proof1 and leave itself on disk.
        (self.repo / "orders" / "proof").rename(self.repo / "orders" / "proof[1]")
        (self.repo / "orders" / "proof1").mkdir()
        (self.repo / "orders" / "proof1" / "kept.txt").write_text("not excluded\n")
        with open(self.repo / "ceo.toml", "a") as conf:
            conf.write('[checkout]\nexclude = ["orders/proof[1]"]\n')
        seen = self.carry("050-literal")
        self.assertEqual({who: seen[who] for who in ("worktree", "review", "door")},
                         dict.fromkeys(("worktree", "review", "door"), "050-literal.md proof1"))

    def test_a_single_quoted_exclusion_keeps_its_backslash(self):
        # TOML keeps a single-quoted string as written; read as Python's, \1 would be chr(1).
        (self.repo / "orders" / "proof").rename(self.repo / "orders" / "proof\\1")
        (self.repo / "orders" / "proof1").mkdir()
        (self.repo / "orders" / "proof1" / "kept.txt").write_text("not excluded\n")
        with open(self.repo / "ceo.toml", "a") as conf:
            conf.write("[checkout]\nexclude = ['orders/proof\\1']\n")
        seen = self.carry("050-literal-backslash")
        self.assertEqual({who: seen[who] for who in ("worktree", "review", "door")},
                         dict.fromkeys(("worktree", "review", "door"), "050-literal-backslash.md proof1"))

    def test_without_the_table_every_checkout_holds_the_folder(self):
        seen = self.carry("051-whole")
        self.assertEqual({who: seen[who] for who in ("worktree", "review", "door")},
                         dict.fromkeys(("worktree", "review", "door"), "051-whole.md proof"))

    def test_a_run_cut_off_keeps_what_its_worker_wrote_into_an_excluded_folder(self):
        # git adds a file in a folder a sparse worktree keeps out only when told --sparse.
        with open(self.repo / "ceo.toml", "a") as conf:
            conf.write('[checkout]\nexclude = ["orders/proof"]\n')
        wt, lock = self.tmp / "wt", os.open(self.tmp / "lock", os.O_CREAT | os.O_RDWR)
        self.addCleanup(os.close, lock)
        with mock.patch.dict(os.environ, self.env):  # the scratch git config, not the owner's
            self.assertEqual(worktree(self.repo, wt, "HEAD", "--detach", lock=lock).returncode, 0)
            self.assertFalse((wt / "orders" / "proof").exists())
            (wt / "orders" / "proof").mkdir(parents=True)
            (wt / "orders" / "proof" / "new.png").write_text("the cut-off worker's evidence\n")
            self.assertEqual(wip(wt, lock), "")
        self.assertEqual(self.git("-C", str(wt), "show", "HEAD:orders/proof/new.png"),
                         "the cut-off worker's evidence")


class Gate(unittest.TestCase):
    def test_the_kernels_gate_fails_above_its_budget_of_5450_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(KERNEL / "gate.sh", tmp)
            gate = lambda: subprocess.run(["./gate.sh"], cwd=tmp, capture_output=True).returncode
            (Path(tmp) / "test_small.py").write_text(
                "import unittest\n\nclass T(unittest.TestCase):\n    def test_it(self):\n        pass\n")
            for half in ("code.py", "code_too.py"):
                (Path(tmp) / half).write_text("x = 1\n" * 2725)
            self.assertEqual(gate(), 0)
            (Path(tmp) / "more.py").write_text("y = 2\n")
            self.assertNotEqual(gate(), 0)


class RealGitHub(TickCase):
    """The door on GitHub itself, with the real gh, in one private repository of the account gh is
    logged into, ceo-kernel-door-test, made only when missing and never deleted: GitHub stopped the
    account that made one a run (9 October 2026, too many, too quickly). Each run's pull request
    comes from a branch of its own, main grows by its merge, and only that branch is deleted."""

    def call(self, *command):
        """command in the test's environment: git in the owner's folder, gh outside any checkout."""
        return subprocess.run(command, cwd=self.tmp if command[0] == "gh" else self.repo,
                              env=self.env, capture_output=True, text=True)

    def must(self, *command):
        """command's output; when it fails, so does the test, in the server's own words."""
        done = self.call(*command)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def test_the_door_merges_a_real_pull_request_at_exactly_the_tested_commit(self):
        login = self.call("gh", "api", "user", "--jq", ".login") if shutil.which("gh") else None
        if login is None or login.returncode == 4:  # gh's own code for no login; offline fails below
            self.skipTest("gh is not logged in here, so GitHub cannot be reached")
        self.assertEqual(login.returncode, 0, login.stderr)
        self.stub.write_text(STUB)
        name = login.stdout.strip() + "/ceo-kernel-door-test"
        found = self.call("gh", "api", f"repos/{name}", "--silent")
        if "(HTTP 404)" in found.stderr:  # made only when missing, so once an account
            found = self.call("gh", "repo", "create", name, "--private")
            if found.returncode:  # refused, it may stand all the same, made by a run in parallel
                found.returncode = self.call("gh", "api", f"repos/{name}", "--silent").returncode
        self.assertEqual(found.returncode, 0, found.stderr)
        # git reaches GitHub with gh's credentials; the owner's own git config stays out.
        self.env.update(GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_0="credential.helper",
                        GIT_CONFIG_VALUE_0="", GIT_CONFIG_KEY_1="credential.helper",
                        GIT_CONFIG_VALUE_1=f"!{shutil.which('gh')} auth git-credential")
        self.git("remote", "add", "origin", f"https://github.com/{name}.git")
        main = ("git", "ls-remote", "--exit-code", "origin", "refs/heads/main")
        if self.call(*main).returncode == 2:
            for text in ("main starts", "main moves on"):  # once: main, deep enough to start behind
                (self.repo / "main.txt").write_text(text + "\n")
                self.git("add", "main.txt")
                self.git("commit", "-qm", text)
            pushed = self.call("git", "push", "-q", "origin", "main")
            if pushed.returncode:  # refused, it may stand all the same, pushed by a run in parallel
                pushed.returncode = self.call(*main).returncode
            self.assertEqual(pushed.returncode, 0, pushed.stderr)
        self.must("git", "fetch", "-q", "origin", "main")
        # The run starts a commit behind main, as after a merge not yet fetched, so that the door
        # joins main into it and pushes the join: main grows by the one merge.
        self.must("git", "update-ref", "refs/remotes/origin/main", "refs/remotes/origin/main~")
        slug = f"001-real-{time.strftime('%Y%m%d-%H%M%S')}-{os.urandom(3).hex()}"  # filed: a number, then a name
        self.addCleanup(self.call, "git", "push", "-q", "origin", "--delete", f"refs/heads/order/{slug}")
        self.commit_order(f"{slug}.md", order_text(f"test -f {slug}.txt"))
        run = self.ceo("run", f"orders/{slug}.md", "--worker", self.model("own"))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(self.read(slug)["pr"], self.read(slug)["reason"])
        review = self.ceo("review", f"orders/{slug}.md", "--reviewer", self.model("clean"))
        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertEqual(self.read(slug, "review.json")["posted"], "comment")
        door = self.ceo("door")
        self.assertEqual(door.returncode, 0, door.stdout + door.stderr)
        result = self.read(slug)
        view = json.loads(self.must("gh", "pr", "view", str(result["pr"]), "-R", name, "--json",
                                    "state,headRefOid,mergeCommit,reviews"))
        self.assertEqual((view["state"], view["headRefOid"]), ("MERGED", result["merge_commit"]))
        self.assertEqual(result["merged"], view["mergeCommit"]["oid"])
        self.assertEqual(result["merged"], result["merge_commit"])
        self.assertIn("survives", view["reviews"][0]["body"])
        self.assertNotIn(f"order/{slug}", self.must("git", "ls-remote", "origin"))
        self.must("git", "fetch", "-q", "origin", "main")  # other runs may have merged since
        self.assertEqual(self.must("git", "rev-parse", result["merged"] + "^{tree}"),
                         self.must("git", "rev-parse", result["joins"][-1] + "^{tree}"))
        self.assertEqual(self.call("git", "merge-base", "--is-ancestor", result["merged"],
                                   "refs/remotes/origin/main").returncode, 0, "the merge is not on main")
        self.assertFalse((self.repo / ".ceo" / "wt" / slug).exists())


if __name__ == "__main__":
    unittest.main()
