"""The status end to end: two repositories carried through the tick with real
git, real processes and real files; only the model is replaced, by the stub of test_tick, and GitHub,
by the fake gh of test_door. The waiting order is written as order 9's run writes it, for that run is not
in the kernel yet."""
import contextlib
import json
import os
import pty
import re
import subprocess
import sys
import unittest
from pathlib import Path

from ceo import held, read_json
from test_ceo import order_text
from test_door import DoorCase
from test_tick import CEO

MOCKS = ["A dark sheet with large type", "A light sheet with small type"]


class DeskCase(DoorCase):
    def setUp(self):
        super().setUp()
        self.home = Path(self.env["CEO_HOME"])

    def beta(self):
        """A second repository, without origin, listed by its own tick."""
        beta = self.tmp / "beta"
        (beta / "orders").mkdir(parents=True)
        (beta / ".gitignore").write_text(".ceo/\n")
        # Host resources and the night are outside this scene, as in TickCase.
        (beta / "ceo.toml").write_text('disk_floor_gb = 0\nram_floor_gb = 0\nrecheck_wait = 0\nnight = ""\ncleaner = ""\n')
        (beta / "orders" / "060-building.md").write_text(order_text("test -f built", title="A long build"))  # filed
        for step in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "an order"]):
            subprocess.run(["git", *step], cwd=beta, env=self.env, check=True, capture_output=True)
        return beta

    def ceo_in(self, repo, *args):
        done = subprocess.run([sys.executable, str(CEO), *args], cwd=repo, env=self.env,
                              capture_output=True, encoding="utf-8", timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def waiting(self, repo, slug="061-pick"):
        """A run of mocks from before mocks left the kernel, still waiting for the owner's pick, as its run wrote it."""
        ceo, mocks = repo / ".ceo", []
        for number, line in enumerate(MOCKS, 1):
            preview = Path(".ceo", "wt", slug, f"preview-{number}.html")
            (repo / preview).parent.mkdir(parents=True, exist_ok=True)
            (repo / preview).write_text(f"<p>mock {number}</p>\n")
            mocks.append({"spin": number, "line": line, "branch": f"order/{slug}", "head": "0" * 40,
                          "preview": str(preview)})
        (ceo / "runs" / slug).mkdir(parents=True)
        (ceo / "waiting").mkdir(exist_ok=True)
        record = {"order": f"orders/{slug}.md", "title": "The page of the shop"}
        (ceo / "runs" / slug / "result.json").write_text(json.dumps(
            {**record, "state": "waiting", "mocks": [{**one, "finished": True} for one in mocks],
             "started": "2026-10-08T09:00:00+00:00", "ended": "2026-10-08T09:30:00+00:00"}))
        (ceo / "waiting" / f"{slug}.json").write_text(json.dumps(
            {**record, "spins": mocks, "question": "Which mock do you pick?", "detail": "2 mocks wait for your pick"}))

    def world(self):
        """Here, one order merged and one failed then cut; in beta, one building and one waiting."""
        for slug, done_when, title in (("050-merged", "test -f 050-merged.txt", "A merged order"),
                                       ("051-failing", "test -f never.txt", "A failing order")):
            (self.repo / "orders" / f"{slug}.md").write_text(order_text(done_when, title=title))
        self.commit_all("two orders filed")
        work = ("--worker", self.model("own"), "--reviewer", self.model("clean"))
        self.tick_while(lambda: "cleared" in read_json(self.run_dir("050-merged") / "result.json")
                        and (self.run_dir("051-failing") / "result.json").exists(),
                        "one merged, one failed", *work)
        self.wait_for(lambda: not held(self.repo / ".ceo" / "locks" / "door.lock"), "the door ended")
        (self.repo / "orders" / "051-failing.cut").touch()  # cut on the owner's word
        self.tick(*work)
        self.tick(*work)  # a second pass tells the cut no second time
        self.assertFalse((self.run_dir("050-merged") / "vision.json").exists())
        beta = self.beta()
        self.ceo_in(beta, "tick", "--worker", self.model("wait", str(self.tmp / "began"),
                                                        str(self.release)))
        self.wait_for((self.tmp / "began-060-building").exists, "beta's worker started")
        self.waiting(beta)
        return beta


class Desk(DeskCase):
    def test_two_repositories_reach_status(self):
        beta = self.world()
        rows = {row["slug"]: row for repo in (self.repo, beta)
                for row in json.loads(self.ceo_in(repo, "status", "--json"))["rows"]}
        self.assertEqual({slug: (row["state"], row["detail"]) for slug, row in rows.items()}, {
            "050-merged": ("finished", "merged into main"),
            "051-failing": ("finished", "cut; no more work goes into it"),
            "060-building": ("in progress", "a worker is building it"),
            "061-pick": ("needs attention", "mocks left the kernel, so its pick is taken no more: cut it")})
        self.assertEqual(rows["050-merged"]["sentence"], "A merged order.")
        self.assertNotIn("choices", rows["061-pick"], "an old run of mocks asks for no pick")
        told = [json.loads(line) for line in (self.repo / ".ceo" / "events.jsonl").read_text().splitlines()]
        self.assertEqual(sorted((one["event"], one["slug"]) for one in told), [
            ("cut", "051-failing"), ("done", "050-merged"), ("failed", "051-failing"),
            ("merged", "050-merged"), ("started", "050-merged"), ("started", "051-failing")])
        self.assertIn("A merged order: merged into main.", [one["sentence"] for one in told])
        lines = ["repo · nothing running", "beta · 1 running · 1 needs attention"]
        self.assertEqual([self.ceo_in(repo, "status").strip() for repo in (self.repo, beta)], lines)
        # No caller asks for the events since a time: --since is refused as any unknown flag is.
        since = self.ceo("status", "--since", told[-2]["time"])
        self.assertEqual(since.returncode, 2, since.stderr)
        self.assertIn("unrecognized arguments: --since", since.stderr)

    def test_each_record_reads_as_the_state_the_owner_sees(self):
        cases = {  # result.json, review.json, as the kernel writes them, and a vision.json from before the cut
            "070-fails": ({"state": "done"}, {"verdict": "fails", "findings": [{"counts": True}]}, {}),
            "071-review-blocked": ({"state": "done"}, {"verdict": "blocked", "reason": "no answer"}, {}),
            "072-conflict": ({"state": "done", "attention": "it conflicts with main in a.txt"}, {}, {}),
            "073-diverges": ({"state": "done", "merged": "1" * 40}, {"verdict": "survives"},
                             {"verdict": "diverges", "suggestion": "Say what it is for."}),
            "074-survives": ({"state": "done"}, {"verdict": "survives"}, {}),
            "075-blocked": ({"state": "blocked", "reason": "the worker ran past the clock."}, {}, {}),
            # Runs of spins from before spins left the kernel: one still waiting for its pick, one picked and merged.
            "076-old-spins": ({"state": "waiting", "spins": [{"spin": 1, "line": "red"}, {"spin": 2, "line": "blue"}]}, {}, {}),
            "077-old-picked": ({"state": "done", "picked": 1, "spins": [{"spin": 1}], "merged": "2" * 40},
                               {"verdict": "survives"}, {})}
        (self.repo / ".ceo" / "waiting").mkdir(parents=True)
        (self.repo / ".ceo" / "waiting" / "076-old-spins.json").write_text(json.dumps(  # as its run wrote it: no question
            {"order": "orders/076-old-spins.md", "title": "076-old-spins", "spins": cases["076-old-spins"][0]["spins"]}))
        for slug, records in cases.items():
            (self.run_dir(slug)).mkdir(parents=True)
            for name, record in zip(("result.json", "review.json", "vision.json"), records):
                if record:
                    (self.run_dir(slug) / name).write_text(json.dumps({"title": slug, **record}))
        rows = {row["slug"]: (row["state"], row["detail"])
                for row in json.loads(self.ceo_in(self.repo, "status", "--json"))["rows"]}
        self.assertEqual(rows, {
            "070-fails": ("needs attention", "a fresh reviewer found 1 problem"),
            "071-review-blocked": ("needs attention", "its review is blocked: no answer"),
            "072-conflict": ("needs attention", "the door cannot merge it: it conflicts with main in a.txt"),
            "073-diverges": ("finished", "merged into main"),
            "074-survives": ("in progress", "reviewed; the door merges it next"),
            "075-blocked": ("needs attention", "the worker ran past the clock"),
            "076-old-spins": ("needs attention", "spins left the kernel, so its pick is taken no more: cut it"),
            "077-old-picked": ("finished", "merged into main")})
        self.assertEqual(self.ceo_in(self.repo, "status").strip(), "repo · 1 running · 5 need attention")
        for args in (("answer", "076-old-spins", "1"), ("pick", "076-old-spins", "1")):  # nothing asks for its pick
            done = self.ceo(*args)
            self.assertEqual(done.returncode, 2, done.stdout)
            self.assertRegex(done.stderr, r"^ceo: refused: [^\n]+\.\n$")
        self.assertFalse((self.repo / ".ceo" / "answers").exists())


class Edges(DeskCase):
    """The shared files as a hand or a full disk can leave them (order 5, round 2)."""

    def test_the_next_repository_gets_a_line_of_its_own_after_a_last_line_with_no_newline(self):
        beta = self.beta()
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "projects.txt").write_text(str(beta))  # a hand edit left off the last newline
        self.ceo_in(self.repo, "tick")
        self.assertEqual((self.home / "projects.txt").read_text(), f"{beta}\n{self.repo}\n")

    def test_a_cut_line_of_events_is_skipped_and_counted_once_and_the_rest_is_read(self):
        def line(hour, slug, said):
            return json.dumps({"time": f"2020-01-01T{hour}:00:00.000001+00:00", "event": "done",
                               "slug": slug, "sentence": said}, ensure_ascii=False).encode()
        cut = line("09", "080-cut", "Заказ: built.")
        cut = cut[:cut.index("З".encode()) + 1]  # a write cut short by a full disk, inside a character
        (self.repo / ".ceo").mkdir(exist_ok=True)
        (self.repo / ".ceo" / "events.jsonl").write_bytes(b"\n".join([
            line("08", "079-before", "Before: built."),
            cut + line("09", "081-glued", "Glued: built."),  # the next write lands on the same line
            line("10", "082-after", "After: built.")[:40],  # cut short between characters: no JSON
            line("11", "083-last", "Last: built."), b""]))
        self.assertEqual(self.ceo_in(self.repo, "status").strip(),
                         "repo · nothing running · 2 unreadable lines in .ceo/events.jsonl")
        (self.repo / "orders" / "084-gone.cut").touch()  # the tick reads the events to tell a cut once
        self.ceo_in(self.repo, "tick")
        self.ceo_in(self.repo, "tick")
        after = (self.repo / ".ceo" / "events.jsonl").read_bytes().splitlines()[4:]
        self.assertEqual([(json.loads(told)["event"], json.loads(told)["slug"]) for told in after],
                         [("cut", "084-gone")])


class Memory(DeskCase):
    """Each memory file over what a session loads, a red line of its own under the status line (order 231)."""

    def notebook(self, config, folder=None):
        """The index of the Claude Code notebook kept for folder, this one by default, under the Claude Code folder
        config."""
        return config / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(folder or self.repo)) / "memory" / "MEMORY.md"

    def on_terminal(self):
        """What status prints to a terminal of its own."""
        main, child = pty.openpty()
        proc = subprocess.Popen([sys.executable, str(CEO), "status"], cwd=self.repo, env=self.env, stdout=child,
                                stderr=subprocess.DEVNULL)
        os.close(child)
        seen = b""
        with contextlib.suppress(OSError):  # read until its last writer has left
            while chunk := os.read(main, 4096):
                seen += chunk
        os.close(main)
        self.assertEqual(proc.wait(timeout=120), 0)
        return seen.decode().splitlines()

    def test_each_file_over_200_lines_or_25000_bytes_is_a_line_of_its_own_red_on_a_terminal(self):
        config = Path(self.env["CLAUDE_CONFIG_DIR"])  # a scratch folder, as TickCase sets it
        lessons, notebook = self.repo / "LESSONS.md", self.notebook(config)

        def memory():
            return json.loads(self.ceo_in(self.repo, "status", "--json"))["memory"]
        self.assertEqual((self.ceo_in(self.repo, "status"), memory()), ("repo · nothing running\n", []))  # none
        notebook.parent.mkdir(parents=True)
        lessons.write_text("- a lesson\n" * 200)
        notebook.write_bytes((b"x" * 124 + b"\n") * 200)  # 200 lines and 25,000 bytes load whole
        self.assertEqual((self.ceo_in(self.repo, "status"), memory()), ("repo · nothing running\n", []))
        lessons.write_text("- a lesson\n" * 201)
        notebook.write_bytes(b"x" * 25001)  # one line, with no newline
        written = {path: path.read_bytes() for path in config.rglob("*") if path.is_file()}
        self.assertEqual(memory(), [{"file": str(lessons), "lines": 201, "bytes": 2211},
                                    {"file": str(notebook), "lines": 1, "bytes": 25001}])
        said = [f"LESSONS.md is 201 lines and 2,211 bytes, over the 200 lines or 25,000 bytes a session loads: only "
                f"its top loads ({lessons})", f"MEMORY.md is 1 line and 25,001 bytes, over the 200 lines or 25,000 "
                f"bytes a session loads: only its top loads ({notebook})"]
        self.assertEqual(self.ceo_in(self.repo, "status").splitlines(), ["repo · nothing running", *said])
        self.assertEqual(self.on_terminal(), ["repo · nothing running", *(f"\033[31m{one}\033[0m" for one in said)])
        self.assertEqual({path: path.read_bytes() for path in config.rglob("*") if path.is_file()}, written)
        # A worktree shares its main folder's notebook; with CLAUDE_CONFIG_DIR empty, the folder is ~/.claude.
        self.git("worktree", "add", "-q", "--detach", str(self.tmp / "wt"))
        home = self.notebook(self.tmp / "home" / ".claude")
        home.parent.mkdir(parents=True)
        home.write_text("- a note\n" * 250)
        self.env.update(CLAUDE_CONFIG_DIR="", HOME=str(self.tmp / "home"))
        self.assertEqual(json.loads(self.ceo_in(self.tmp / "wt", "status", "--json"))["memory"],
                         [{"file": str(home), "lines": 250, "bytes": 2250}])  # the worktree has no LESSONS.md

    def test_the_notebook_path_is_absolute_for_a_relative_config_and_found_with_the_metadata_kept_apart(self):
        # A relative CLAUDE_CONFIG_DIR is taken from the folder status runs in, and named absolute.
        notebook = self.notebook(self.repo / "claude-config")
        notebook.parent.mkdir(parents=True)
        notebook.write_text("- a note\n" * 201)
        self.env["CLAUDE_CONFIG_DIR"] = "claude-config"
        self.assertEqual(json.loads(self.ceo_in(self.repo, "status", "--json"))["memory"],
                         [{"file": str(notebook), "lines": 201, "bytes": 1809}])
        # With its git metadata in a folder apart (--separate-git-dir), the notebook is the work tree's own.
        apart, config = self.tmp / "apart", self.tmp / "claude"
        subprocess.run(["git", "init", "-q", "-b", "main", "--separate-git-dir", str(self.tmp / "metadata"),
                        str(apart)], env=self.env, check=True, capture_output=True)
        notebook = self.notebook(config, apart)
        notebook.parent.mkdir(parents=True)
        notebook.write_text("- a note\n" * 201)
        self.env["CLAUDE_CONFIG_DIR"] = str(config)
        self.assertEqual(json.loads(self.ceo_in(apart, "status", "--json"))["memory"],
                         [{"file": str(notebook), "lines": 201, "bytes": 1809}])


if __name__ == "__main__":
    unittest.main()
