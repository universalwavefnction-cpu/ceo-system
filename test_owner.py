"""The owner's file end to end: real git, real processes, real files, and a CEO_HOME of the test's own.
Only the model is replaced, by the stub of test_door with one more part, and GitHub by its fake gh."""
import os
import signal
import subprocess
import sys
import unittest

from ceo import ENDING
from test_ceo import order_text
from test_door import STUB, DoorCase
from test_tick import CEO

LINE = "A placeholder rule for every project, written by this test."
BLOCK = f"# From the owner, for every project\n\n{LINE}\n\n".encode()
PAGE = "A placeholder trap of this project, written by this test."
LESSONS = f"# What this project's work has taught (LESSONS.md)\n\n{PAGE}\n\n".encode()

STUB += r'''
elif mode == "record":  # any part: keeps what it was given, then plays the part its folder names
    part, slug = os.path.basename(os.path.dirname(os.getcwd())), os.path.basename(os.getcwd())
    kept = os.path.join(args[0], f"{part}-{slug}")
    open(kept + ".part", "wb").write(brief)
    os.replace(kept + ".part", kept)  # whole once it shows, for the test reads it as soon as it does
    if part == "wt":
        open(slug + ".txt", "w").write(slug + "\n")
        subprocess.run(["git", "add", slug + ".txt"], check=True)
        subprocess.run(["git", "commit", "-qm", slug], check=True)
    elif part == "review":
        answer()
'''


class Owner(DoorCase):
    def setUp(self):
        super().setUp()
        self.stub.write_text(STUB)
        self.home = self.tmp / "home" / ".ceo"  # missing, and so is the folder it would be in
        self.env["CEO_HOME"] = str(self.home)
        self.given = self.tmp / "given"  # what each model got on its standard input, by part and order
        self.given.mkdir()
        self.record = self.model("record", str(self.given))

    def given_to(self, part, slug):
        return (self.given / f"{part}-{slug}").read_bytes()

    def tick_on(self, *args):
        """A tick that runs on in a process group of its own, stopped with every git it started when the
        test ends: a git left running would write into the folder being removed."""
        proc = subprocess.Popen([sys.executable, str(CEO), "tick", *args], cwd=self.repo, env=self.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        self.addCleanup(self.stop_group, proc)

    def stop_group(self, proc):
        def alive():
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                return False
            return True
        if alive():
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=60)
        self.wait_for(lambda: not alive(), "every process of the tick ended")

    def test_a_line_written_while_the_tick_runs_reaches_the_next_worker_and_reviewer(self):
        def file(slug):  # the owner files a draft from a copy of his own: on origin's main, it starts
            copy = self.tmp / slug
            self.git("clone", "-q", str(self.origin), str(copy))
            (copy / "orders").mkdir(exist_ok=True)
            self.git("-C", str(copy), "mv", f"drafts/orders/{slug}.md", f"orders/{slug}.md")
            self.git("-C", str(copy), "commit", "-qm", f"File {slug}")
            self.git("-C", str(copy), "push", "-q", "origin", "main")

        orders, drafts = {}, self.repo / "drafts" / "orders"
        drafts.mkdir(parents=True)
        for slug in ("070-before", "071-after"):
            orders[slug] = order_text(f"test -f {slug}.txt").encode()
            (drafts / f"{slug}.md").write_bytes(orders[slug])
        self.commit_all("two drafts")  # before the tick, so no git of the test races it
        self.assertFalse(self.home.parent.exists())
        self.tick_on("--every", "0.5", "--worker", self.record)
        owner = self.home / "OWNER.md"
        self.wait_for(owner.exists, "the tick made the owner's file")
        self.assertEqual(owner.read_bytes(), b"")  # beside it, at most the projects list (order 5)
        self.assertLessEqual(set(os.listdir(self.home)), {"OWNER.md", "projects.txt"})
        file("070-before")
        self.wait_for((self.given / "wt-070-before").exists, "the first worker started")
        self.assertEqual(self.given_to("wt", "070-before"), orders["070-before"] + ENDING)  # no owner's block
        owner.write_text(LINE + "\n")  # while the same tick runs on
        file("071-after")
        for slug in ("070-before", "071-after"):
            self.wait_for(lambda: (self.run_dir(slug) / "result.json").exists() and self.settled(slug),
                          f"{slug} merged and cleared", timeout=120)
        self.assertEqual(self.given_to("wt", "071-after"), BLOCK + orders["071-after"] + ENDING)
        self.assertTrue(self.given_to("review", "071-after").startswith(
            BLOCK + b"You are a fresh reviewer"))
        self.assertFalse((self.run_dir("071-after") / "vision.json").exists())
        self.assertFalse((self.given / "vision-071-after").exists(), "a checker was started")

    def test_one_line_reaches_the_workers_of_two_repositories_and_none_from_a_repository(self):
        self.home.mkdir(parents=True)
        (self.home / "OWNER.md").write_text(LINE + "\n")
        other = self.tmp / "other"
        (other / "orders").mkdir(parents=True)
        (other / ".gitignore").write_text(".ceo/\n")
        # As in self.repo, host resources are outside this owner-propagation scene.
        (other / "ceo.toml").write_text("disk_floor_gb = 0\nram_floor_gb = 0\nrecheck_wait = 0\n")
        (other / "OWNER.md").write_text("A line of the repository's own, which no model gets.\n")
        (other / "orders" / "072-other.md").write_text(order_text("test -f 072-other.txt"))
        for step in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "an order"]):
            subprocess.run(["git", *step], cwd=other, env=self.env, check=True, capture_output=True)
        self.commit_order("073-here.md", order_text("test -f 073-here.txt"))
        for repo, slug in ((other, "072-other"), (self.repo, "073-here")):  # each from its folder
            run = subprocess.run([sys.executable, str(CEO), "run", f"orders/{slug}.md", "--worker",
                                  self.record], cwd=repo, env=self.env, capture_output=True,
                                 encoding="utf-8", timeout=120)
            self.assertEqual(run.returncode, 0, run.stderr)
            order = (repo / "orders" / f"{slug}.md").read_bytes()
            self.assertEqual(self.given_to("wt", slug), BLOCK + order + ENDING)

    def test_the_lessons_page_reaches_the_worker_as_its_branch_starts_from_it_and_no_reviewer(self):
        self.home.mkdir(parents=True)
        (self.home / "OWNER.md").write_text(LINE + "\n")
        (self.repo / "LESSONS.md").write_text(PAGE + "\n")
        self.commit_order("075-taught.md", order_text("test -f 075-taught.txt"))
        (self.repo / "LESSONS.md").write_text("A line only the folder holds, which no worker gets.\n")
        run = self.ceo("run", "orders/075-taught.md", "--worker", self.record)
        self.assertEqual(run.returncode, 0, run.stderr)
        order = (self.repo / "orders" / "075-taught.md").read_bytes()
        self.assertEqual(self.given_to("wt", "075-taught"), BLOCK + LESSONS + order + ENDING)
        self.ceo("review", "orders/075-taught.md", "--reviewer", self.record)
        self.assertTrue(self.given_to("review", "075-taught").startswith(BLOCK + b"You are a fresh reviewer"))
        self.assertNotIn(PAGE.encode(), self.given_to("review", "075-taught"))

    def test_an_owners_file_that_cannot_be_read_refuses_and_starts_nothing(self):
        self.home.mkdir(parents=True)
        (self.home / "OWNER.md").write_bytes(b"\xff not text\n")
        self.commit_order("074-unread.md", order_text("test -f 074-unread.txt"))
        run = self.ceo("run", "orders/074-unread.md", "--worker", self.record)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertRegex(run.stderr, r"^ceo: refused: the owner's file [^\n]+ cannot be read: [^\n]+\.\n$")
        self.assertFalse((self.repo / ".ceo").exists(), "something was created")
        self.assertEqual(list(self.given.iterdir()), [], "the worker was started")


if __name__ == "__main__":
    unittest.main()
