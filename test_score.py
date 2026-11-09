"""`ceo.py score`: every row of the weekly table measured on scratch repositories with dated commits, through the
real CLI, in a zone 14 hours ahead of UTC, where a local day or a local --since gives other numbers than the UTC day."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ceo_score
from tools import scorecard

KERNEL = Path(__file__).resolve().parent
ROWS = ("merges", "changed lines", "order files", "round files", "cut files", "bugs filed", "bugs closed",
        "round-1 share", "send to merge, median", "start to merge, median", "idle days", "waiting rows, now",
        "waiting age, median", "escaped per 100 merges", "owner touches per merge", "red-main minutes",
        "cost per merge, Claude", "tokens per merge, Codex", "agent runs unpriced")
WEEK = ("--from", "2026-10-17", "--to", "2026-10-23")
DASH = "–"


def order(title, head=""):
    """An order file whose `# ` title is title and whose part above its first `## ` heading holds head."""
    return f"# {title}\n{head}\n## Goal\nIt.\n\n## Done when\n```bash\ntrue\n```\n\n## Not asked\nMore.\n"


class ScoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.invalid", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t.invalid", "TZ": "Pacific/Kiritimati",
                    "CEO_HOME": str(self.tmp / "home"), "PYTHONDONTWRITEBYTECODE": "1"}

    def repo(self, name="p"):
        path = self.tmp / name
        path.mkdir()
        self.git(path, "init", "-q", "-b", "main")
        return path

    def git(self, repo, *args, at=None, author=None):
        dated = {"GIT_AUTHOR_DATE": author or at, "GIT_COMMITTER_DATE": at} if at else {}
        return subprocess.run(["git", *args], cwd=repo, env={**self.env, **dated}, check=True, capture_output=True,
                              text=True).stdout

    def commit(self, repo, at, message, files=None, author=None):
        """One commit at the committer time at, writing each file of files, or removing it where its text is None."""
        for name, text in (files or {}).items():
            if text is None:
                self.git(repo, "rm", "-q", name)
                continue
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_bytes(text if isinstance(text, bytes) else text.encode())
            self.git(repo, "add", name)
        self.git(repo, "commit", "-q", "--allow-empty", "-m", message, at=at, author=author)

    def merge(self, repo, at, message, branch="side"):
        self.git(repo, "merge", "-q", "--no-ff", "-m", message, branch, at=at)

    def score(self, *args, cwd=None, kernel=KERNEL):
        return subprocess.run([sys.executable, "-B", str(kernel / "ceo.py"), "score", *args], cwd=cwd or self.tmp,
                              env=self.env, capture_output=True, text=True, timeout=120)

    def table(self, *args, **kw):
        """The printed table, its names and each row's cells, and the lines after it."""
        done = self.score(*args, **kw)
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(lines[1], "")
        cells = [[cell.strip() for cell in line[1:-1].split("|")] for line in lines[2:2 + 2 + len(ROWS)]]
        self.assertEqual(lines[3], "|---" * len(cells[0]) + "|")
        return cells[0][1:], {row[0]: row[1:] for row in cells[2:]}, lines[2 + 2 + len(ROWS):]

    def column(self, *args, **kw):
        """Each row's one cell, for one repository."""
        return {label: cells[0] for label, cells in self.table(*args, **kw)[1].items()}


class Table(ScoreCase):
    def test_the_rows_are_fixed_and_printed_in_their_order(self):
        self.assertEqual(ceo_score.ROWS + scorecard.ROWS, ROWS)
        repo = self.repo()
        self.commit(repo, "2026-10-18T10:00:00Z", "One (#1)")
        done = self.score(*WEEK, str(repo))
        lines = done.stdout.splitlines()
        self.assertEqual(lines[:4], ["Week 2026-10-17 to 2026-10-23, UTC days of the committer date, from each "
                                     "repository's HEAD.", "", "| measure | p |", "|---|---|"])
        self.assertEqual([line.split(" | ")[0][2:] for line in lines[4:]], list(ROWS))
        self.assertEqual(len(lines), 4 + len(ROWS))
        self.assertFalse((self.tmp / "home").exists(), "score read or made the owner's file")

    def test_merges_are_first_parent_subjects_ending_in_a_number_on_a_utc_committer_day_of_head(self):
        repo = self.repo()
        self.commit(repo, "2026-10-16T10:00:00Z", "Base", {"a.txt": "1\n2\n"})
        self.commit(repo, "2026-10-16T23:30:00Z", "Local day 17, UTC day 16 (#1)", {"x.txt": "1\n"})
        self.commit(repo, "2026-10-24T00:00:00Z", "Written in the week, committed after it (#2)", {"y.txt": "1\n"},
                    author="2026-10-20T10:00:00Z")
        self.commit(repo, "2026-10-17T00:00:00Z", "Committed on the first day (#3)", {"c.txt": "1\n2\n"},
                    author="2026-10-01T10:00:00Z")
        self.git(repo, "checkout", "-qb", "side")
        self.commit(repo, "2026-10-18T09:00:00Z", "On the side (#4)", {"b.txt": "1\n2\n3\n", "bin": b"\0\1\2\0"})
        self.git(repo, "checkout", "-q", "main")
        self.merge(repo, "2026-10-18T12:00:00Z", "A true merge (#5)")
        for subject in ("Not a number (#x)", "No space(#6)", "(#7) at the start", "Not a merge"):
            self.commit(repo, "2026-10-19T08:00:00Z", subject, {"z.txt": subject})
        self.commit(repo, "2026-10-19T09:00:00Z", "Two numbers (#8) (#9)", {"a.txt": "1\nB\n"})
        self.git(repo, "checkout", "-q", "--detach")  # HEAD alone is read: main lags it, and leads it elsewhere
        self.commit(repo, "2026-10-23T23:59:59Z", "The last second (#10)", {"d.txt": "1\n"})
        self.commit(repo, "2026-10-24T00:00:00Z", "The next day (#11)", {"e.txt": "1\n"})
        head = self.git(repo, "rev-parse", "HEAD").strip()
        self.git(repo, "checkout", "-q", "main")
        self.commit(repo, "2026-10-20T10:00:00Z", "On main alone (#12)", {"f.txt": "1\n2\n3\n4\n5\n"})
        self.git(repo, "checkout", "-q", head)
        rows = self.column(*WEEK, str(repo))
        # #3, #5 and #9 and #10; #5 changes 3 lines and a binary file against its first parent, #9 one line.
        self.assertEqual((rows["merges"], rows["changed lines"], rows["idle days"]), ("4", "8", "3"))
        self.assertEqual(rows["round-1 share"], DASH)
        self.assertEqual(rows["send to merge, median"], DASH)

    def test_file_rows_count_each_path_at_its_oldest_add_in_heads_history(self):
        repo = self.repo()
        self.commit(repo, "2026-10-16T10:00:00Z", "Old", {"orders/001-old.md": "x", "bugs/1.md": "x"})
        self.commit(repo, "2026-10-17T10:00:00Z", "Gone", {"orders/001-old.md": None, "bugs/1.md": None})
        self.commit(repo, "2026-10-18T10:00:00Z", "Back, first added before the week",
                    {"orders/001-old.md": "y", "bugs/1.md": "y"})
        self.commit(repo, "2026-10-18T11:00:00Z", "Added in the week", {
            "orders/002-two.md": "x", "orders/003-round-2.md": "x", "orders/004-join-abcdef1.md": "x",
            "orders/005-join-abcdef12.md": "x", "orders/006-join-ABCDEF1.md": "x", "orders/007-round-2b.md": "x",
            "orders/README.md": "x", "orders/sub/008-deep.md": "x", "orders/two.cut": "x", "orders/009-nine.cut": "x",
            "orders/two.txt": "x", "bugs/2.md": "x", "bugs/2/repro.sh": "x", "bugs/notes.md": "x", "bug/3.md": "x"})
        self.git(repo, "checkout", "-qb", "side")
        self.commit(repo, "2026-10-19T10:00:00Z", "Off the first parent", {"orders/010-side.md": "x",
                                                                          "orders/010-round-3.md": "x"})
        self.git(repo, "checkout", "-q", "main")
        self.merge(repo, "2026-10-24T11:00:00Z", "Side (#1)")  # its commit, not this merge, adds them
        self.commit(repo, "2026-10-16T23:00:00Z", "Local day 17, UTC day 16", {"orders/011-utc.md": "x",
                                                                              "bugs/11.md": "x"})
        self.commit(repo, "2026-10-24T00:00:00Z", "After", {"orders/012-late.md": "x", "orders/012-late.cut": "x",
                                                            "bugs/12.md": "x"})
        rows = self.column(*WEEK, str(repo))
        self.assertEqual([rows[label] for label in ("order files", "round files", "cut files")], ["5", "3", "2"])

    def test_a_path_with_letters_beyond_ascii_counts_as_any_other(self):
        repo = self.repo()
        self.commit(repo, "2026-10-16T10:00:00Z", "Base", {"base.txt": "base\n"})
        stem = "orders/001-caf\u00e9"
        self.commit(repo, "2026-10-17T10:00:00Z", "Order filed, and its cut",
                    {f"{stem}.md": order("Caf\u00e9"), f"{stem}.cut": "cut\n"})
        self.commit(repo, "2026-10-17T12:00:00Z", "Caf\u00e9 (#1)", {"work.txt": "done\n"})
        rows = self.column(*WEEK, str(repo))
        labels = ("order files", "cut files", "round-1 share", "send to merge, median")
        self.assertEqual([rows[label] for label in labels], ["1", "1", "100 % (1 of 1)", "2.0 h"])

    def test_bugs_closed_are_the_bug_files_closed_in_the_weeks_last_tree_and_not_in_its_first(self):
        repo = self.repo()
        self.commit(repo, "2026-10-10T10:00:00Z", "Filed", {f"bugs/{n}.md": f"# Bug {n}\n" for n in range(1, 8)})
        self.commit(repo, "2026-10-16T10:00:00Z", "Closed before", {"bugs/2.md": "# Bug 2\n\n## Closed\nYes.\n"})
        self.commit(repo, "2026-10-18T10:00:00Z", "Closed", {"bugs/1.md": "# Bug 1\n\n## Closed\nYes.\n",
                                                             "bugs/8.md": "# Bug 8\n\n## Closed\nAt once.\n"})
        self.commit(repo, "2026-10-19T10:00:00Z", "Reopened", {"bugs/1.md": "# Bug 1\n"})
        self.commit(repo, "2026-10-20T10:00:00Z", "Closed again", {"bugs/1.md": "# Bug 1\n\n## Closed\nNow.\n"})
        self.commit(repo, "2026-10-20T11:00:00Z", "Two days left", {"bugs/2.md": "# Bug 2\n\n## Closed\nYes, still.\n"})
        self.git(repo, "checkout", "-qb", "side")
        self.commit(repo, "2026-10-21T10:00:00Z", "Closed on the side", {"bugs/4.md": "# Bug 4\n\n## Closed\n"})
        self.git(repo, "checkout", "-q", "main")
        self.merge(repo, "2026-10-24T11:00:00Z", "Side (#1)")  # dated after the week, under the 22nd: bug 4 closed
        self.commit(repo, "2026-10-22T10:00:00Z", "Not closings", {
            "notes/5.md": "## Closed\n", "bugs/5/notes.md": "## Closed\n", "bugs/6.md": "# Bug 6\n## Closed later\n",
            "bugs/3.md": "# Bug 3\n\n### Closed\n"})
        self.commit(repo, "2026-10-24T00:00:00Z", "Closed after", {"bugs/7.md": "# Bug 7\n\n## Closed\n"})
        rows = self.column(*WEEK, str(repo))
        self.assertEqual((rows["bugs closed"], rows["bugs filed"]), ("3", "1"))  # 1, 4 and 8; 8 filed

    def test_a_merge_of_the_week_closes_each_bug_it_brings_closed_to_the_first_parent(self):
        repo = self.repo()
        self.commit(repo, "2026-10-10T10:00:00Z", "Filed", {f"bugs/{n}.md": f"# Bug {n}\n" for n in range(1, 4)})
        self.git(repo, "checkout", "-qb", "side")
        self.commit(repo, "2026-10-16T10:00:00Z", "Closed on the side before the week",
                    {"bugs/2.md": "# Bug 2\n\n## Closed\n", "bugs/3.md": "# Bug 3\n\n## Closed\n"})
        self.git(repo, "checkout", "-q", "main")
        self.commit(repo, "2026-10-16T11:00:00Z", "Main", {"main.txt": "main\n"})
        self.git(repo, "merge", "-q", "--no-ff", "--no-commit", "side")
        # The merge closes bug 1 itself and brings 2 and 3 closed from the side, all three open on the first parent.
        self.commit(repo, "2026-10-18T12:00:00Z", "Closed during the merge (#1)",
                    {"bugs/1.md": "# Bug 1\n\n## Closed\n", "bugs/3.md": "# Bug 3, seen\n\n## Closed\n"})
        self.assertEqual(self.column(*WEEK, str(repo))["bugs closed"], "3")

    def test_bugs_filed_and_closed_are_the_weeks_net_change_on_heads_first_parent(self):
        repo = self.repo()
        self.commit(repo, "2026-10-10T10:00:00Z", "Filed", {f"bugs/{n}.md": f"# Bug {n}\n" for n in range(1, 4)})
        self.git(repo, "checkout", "-qb", "early")
        self.commit(repo, "2026-10-15T10:00:00Z", "Filed on a side before the week",
                    {"bugs/4.md": "# Bug 4\n", "bugs/5.md": "# Bug 5\n"})
        self.git(repo, "checkout", "-q", "main")
        self.commit(repo, "2026-10-16T23:30:00Z", "Local day 17, UTC day 16", {"bugs/6.md": "# Bug 6\n"})
        self.merge(repo, "2026-10-17T09:00:00Z", "Early (#1)", "early")
        self.git(repo, "checkout", "-qb", "side")
        self.commit(repo, "2026-10-18T10:00:00Z", "Closed on the side", {"bugs/1.md": "# Bug 1\n\n## Closed\n"})
        self.commit(repo, "2026-10-19T10:00:00Z", "Reopened on the side", {"bugs/1.md": "# Bug 1\n"})
        self.git(repo, "checkout", "-q", "main")
        self.merge(repo, "2026-10-19T12:00:00Z", "Side (#2)")
        self.commit(repo, "2026-10-20T10:00:00Z", "Closed on main", {"bugs/2.md": "# Bug 2\n\n## Closed\n"})
        self.commit(repo, "2026-10-20T11:00:00Z", "Reopened on main", {"bugs/2.md": "# Bug 2\n"})
        self.git(repo, "checkout", "-qb", "during")
        self.commit(repo, "2026-10-20T12:00:00Z", "Filed on a side in the week", {"bugs/7.md": "# Bug 7\n"})
        self.git(repo, "checkout", "-q", "main")
        self.merge(repo, "2026-10-20T13:00:00Z", "During (#3)", "during")
        self.git(repo, "checkout", "-qb", "never")
        self.commit(repo, "2026-10-21T10:00:00Z", "Filed on a branch never merged", {"bugs/8.md": "# Bug 8\n"})
        self.git(repo, "checkout", "-q", "main")
        self.commit(repo, "2026-10-21T12:00:00Z", "Filed and closed",
                    {"bugs/9.md": "# Bug 9\n\n## Closed\r\nAt once.\n"})
        self.commit(repo, "2026-10-22T10:00:00Z", "Filed for a day", {"bugs/10.md": "# Bug 10\n"})
        self.commit(repo, "2026-10-22T11:00:00Z", "Removed", {"bugs/10.md": None, "bugs/3.md": None})
        self.commit(repo, "2026-10-23T23:59:59Z", "The last second", {"bugs/6.md": "# Bug 6\n\n## Closed\n"})
        self.commit(repo, "2026-10-24T00:00:00Z", "The next day", {"bugs/4.md": "# Bug 4\n\n## Closed\n",
                                                                   "bugs/11.md": "# Bug 11\n"})
        rows = self.column(*WEEK, str(repo))
        # Filed 4 and 5, merged in the week, 7 and 9; closed 6 and 9. 6 was there before the UTC week.
        self.assertEqual([rows["bugs filed"], rows["bugs closed"]], ["4", "2"])
        young = self.repo("young")  # its first commit is in the week: the tree before it is empty
        self.commit(young, "2026-10-18T10:00:00Z", "First", {"bugs/1.md": "# Bug 1\n",
                                                            "bugs/2.md": "# Bug 2\n\n## Closed\n"})
        rows = self.column(*WEEK, str(young))
        self.assertEqual([rows["bugs filed"], rows["bugs closed"]], ["2", "1"])

    def test_a_merge_is_its_orders_by_its_title_and_round_one_when_its_head_says_so(self):
        repo = self.repo()
        files = {"orders/010-a.md": order("Alpha"), "orders/011-b.md": order("Beta", "\nORDER: 011\nROUND: 1\n"),
                 "orders/014-t.md": order("Twin"), "orders/016-e.md": order("Epsilon"),
                 "plans/w.md": "# W\n\n- orders/011-b.md after 010 — beta\n- orders/016-e.md — epsilon\n"}
        self.commit(repo, "2026-10-16T12:00:00Z", "Orders", files)
        (repo / "orders" / "012-c.md").write_text(order("Gamma", "\nRevises: orders/011-b.md\n"))  # never committed
        (repo / "orders" / "015-t.md").write_text(order("Twin"))  # as the folder stands: two orders bear one title
        self.commit(repo, "2026-10-16T20:00:00Z", "Epsilon back to drafts",
                    {"orders/016-e.md": None, "drafts/orders/016-e.md": order("Epsilon")})
        self.commit(repo, "2026-10-17T00:00:00Z", "Epsilon filed again, a rename",
                    {"drafts/orders/016-e.md": None, "orders/016-e.md": order("Epsilon")})
        (repo / ".ceo" / "ready").mkdir(parents=True)  # this host's record: Beta first eligible later than its filing
        (repo / ".ceo" / "ready" / "011-b.json").write_text(json.dumps(
            {"since": "2026-10-17T00:00:00+00:00", "reason": ""}))
        self.commit(repo, "2026-10-17T02:00:00Z", "Epsilon (#6)")
        self.commit(repo, "2026-10-17T03:00:00Z", "Beta (#3)")
        self.commit(repo, "2026-10-17T12:00:00Z", "Alpha (#1)")
        self.commit(repo, "2026-10-18T00:00:00Z", "Round 2 filed", {"orders/010-round-2.md": order(
            "Alpha: round 2", "\nORDER: 010\nROUND: 2\nREPLACES: orders/010-a.md\n")})
        self.commit(repo, "2026-10-18T06:00:00Z", "Alpha: round 2 (#2)")
        for subject in ("Gamma (#4)", "Delta (#5)", "Twin (#7)", "Nobody (#8)"):
            self.commit(repo, "2026-10-19T10:00:00Z", subject)
        self.commit(repo, "2026-10-20T10:00:00Z", "Delta filed after its merge",
                    {"orders/013-d.md": order("Delta").replace("It.\n", "It.\nROUND: 3\nREPLACES: x\n")})
        self.commit(repo, "2026-10-24T10:00:00Z", "Beta (#9)")  # after the week
        rows = self.column(*WEEK, str(repo))
        # Alpha, Beta, Delta and Epsilon of six known merges; sent to merged 24, 6, 3 and 2 hours: Alpha and its round
        # from their filing, Beta from its ready record, Epsilon from its last filing; Gamma and Delta have none before.
        self.assertEqual(rows["merges"], "8")
        self.assertEqual(rows["round-1 share"], "67 % (4 of 6)")
        self.assertEqual(rows["send to merge, median"], "4.5 h")

    def test_the_folders_events_and_waiting_rows(self):
        repo = self.repo()
        self.commit(repo, "2026-10-18T10:00:00Z", "One (#1)")
        rows = self.column(*WEEK, str(repo))  # a folder with no .ceo of its own, as a worktree or a clean clone
        self.assertEqual([rows[label] for label in ROWS[9:13]], [DASH, "6", DASH, DASH])
        (repo / ".ceo").mkdir()
        rows = self.column(*WEEK, str(repo))
        self.assertEqual([rows[label] for label in ROWS[9:13]], [DASH, "6", "0", DASH])
        seen = [("started", "a", "2026-10-16T20:00:00+00:00"), ("started", "a", "2026-10-17T01:00:00+00:00"),
                ("merged", "a", "2026-10-17T02:00:00+00:00"), ("started", "b", "2026-10-18T00:00:00+00:00"),
                ("merged", "b", "2026-10-18T03:00:00+00:00"), ("merged", "c", "2026-10-19T00:00:00+00:00"),
                ("started", "d", "2026-10-23T00:00:00+00:00"), ("merged", "d", "2026-10-23T23:30:00-01:00"),
                ("started", "e", "2026-10-16T00:00:00+00:00"), ("merged", "e", "2026-10-17T09:00:00+14:00"),
                ("started", "f", "2026-10-20T10:00:00+02:00"), ("merged", "f", "2026-10-20T12:00:00+02:00"),
                ("failed", "g", "2026-10-20T00:00:00+00:00"), ("started", "h", "no time")]
        (repo / ".ceo" / "events.jsonl").write_text("".join(json.dumps(
            {"time": at, "event": kind, "slug": slug, "sentence": "s."}) + "\n" for kind, slug, at in seen) + "{\n")
        (repo / ".ceo" / "waiting").mkdir()
        clock = datetime.now(timezone.utc)
        for slug, hours in (("x", 1), ("y", 5), ("z", 9)):
            (repo / ".ceo" / "waiting" / f"{slug}.json").write_text(json.dumps(
                {"order": f"orders/{slug}.md", "time": (clock - timedelta(hours=hours)).isoformat()}))
        (repo / ".ceo" / "waiting" / "old.json").write_text(json.dumps({"order": "orders/old.md"}))
        rows = self.column(*WEEK, str(repo))
        # a, b and f, from each one's first start: 6, 3 and 2 hours; d and e merged outside the UTC week.
        self.assertEqual([rows[label] for label in ROWS[9:13]], ["3.0 h", "6", "4", "5.0 h"])

    def test_several_repositories_and_their_total(self):
        one, two, bare = self.repo("one"), self.repo("two"), self.repo("bare")
        self.commit(one, "2026-10-16T00:00:00Z", "Orders", {"orders/1-a.md": order("A")})
        self.commit(one, "2026-10-17T02:00:00Z", "A (#1)", {"a": "1\n"})
        self.commit(one, "2026-10-17T03:00:00Z", "B (#2)", {"a": "2\n"})
        self.commit(one, "2026-10-17T04:00:00Z", "B filed after its merge", {"orders/2-b.md": order("B", "ROUND: 2\n")})
        self.commit(two, "2026-10-16T00:00:00Z", "Orders", {"orders/1-c.md": order("C")})
        self.commit(two, "2026-10-17T10:00:00Z", "C (#1)", {"c": "1\n2\n3\n"})
        self.commit(two, "2026-10-19T10:00:00Z", "C (#2)", {"c": "1\n"})
        (two / ".ceo").mkdir()
        (two / ".ceo" / "events.jsonl").write_text(json.dumps(
            {"time": "2026-10-17T09:00:00+00:00", "event": "started", "slug": "1-c"}) + "\n" + json.dumps(
            {"time": "2026-10-17T10:00:00+00:00", "event": "merged", "slug": "1-c"}) + "\n")
        names, rows, after = self.table(*WEEK, str(one), str(two), str(bare))
        self.assertEqual(names, ["one", "two", "bare", "total"])
        self.assertEqual(rows["merges"], ["2", "2", "0", "4"])
        self.assertEqual(rows["changed lines"], ["3", "5", "0", "8"])
        self.assertEqual(rows["round-1 share"], ["50 % (1 of 2)", "100 % (2 of 2)", DASH, "75 % (3 of 4)"])
        # The samples pooled, 26 hours, then 34 and 82: their median, not the median of the medians, 42.
        self.assertEqual(rows["send to merge, median"], ["26.0 h", "58.0 h", DASH, "34.0 h"])
        self.assertEqual(rows["start to merge, median"], [DASH, "1.0 h", DASH, "1.0 h"])
        self.assertEqual(rows["idle days"], ["6", "5", "7", "5"])  # the days with no merge in any of them
        self.assertEqual(rows["waiting rows, now"], [DASH, "0", DASH, "0"])
        self.assertEqual(after, [], "a share of the kernel printed with no kernel among the folders")

    def test_the_kernels_own_folder_is_a_cost_when_two_or_more_are_scored(self):
        kernel, other = self.repo("kernel"), self.repo("other")
        for path in KERNEL.glob("*.py"):
            if not path.name.startswith("test_"):
                shutil.copy(path, kernel)
        shutil.copytree(KERNEL / "tools", kernel / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        for repo in (kernel, other):
            self.commit(repo, "2026-10-01T10:00:00Z", "Base")
        self.commit(kernel, "2026-10-18T10:00:00Z", "K (#1)", {"k": "1\n2\n3\n"})
        self.commit(kernel, "2026-10-19T10:00:00Z", "K (#2)", {"k": "1\n2\n"})
        self.commit(other, "2026-10-18T10:00:00Z", "O (#1)", {"o": "1\n"})
        self.assertEqual(self.table(*WEEK, str(kernel), kernel=kernel)[2], [])
        self.assertEqual(self.table(*WEEK, str(other), str(other), kernel=kernel)[2], [])
        for folders in ((str(kernel), str(other)), (str(other), str(kernel / "ears"))):
            (kernel / "ears").mkdir(exist_ok=True)
            self.assertEqual(self.table(*WEEK, *folders, kernel=kernel)[2], [
                "", "The kernel's share, a cost: 2 of 3 merges (67 %), 4 of 5 changed lines (80 %); the agents' "
                "cost is not recorded for every folder."])

    def test_the_week_ends_yesterday_by_default_and_the_folder_is_this_one(self):
        repo = self.repo("here")
        self.commit(repo, "2026-10-18T10:00:00Z", "One (#1)")
        (repo / "deep").mkdir()
        today = datetime.now(timezone.utc).date()
        for given, start, end in (((), today - timedelta(days=7), today - timedelta(days=1)),
                                  (("--to", "2026-10-23"), "2026-10-17", "2026-10-23"),
                                  (("--from", "2026-10-18", "--to", "2026-10-18"), "2026-10-18", "2026-10-18"),
                                  (("--from", "2026-10-01"), "2026-10-01", today - timedelta(days=1))):
            done = self.score(*given, cwd=repo / "deep")
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(done.stdout.splitlines()[:3], [
                f"Week {start} to {end}, UTC days of the committer date, from each repository's HEAD.", "",
                "| measure | here |"])
        self.assertEqual(self.column("--from", "2026-10-18", "--to", "2026-10-18", cwd=repo)["idle days"], "0")

    def test_what_is_refused(self):
        self.repo()
        (self.tmp / "plain").mkdir()
        for given, why in ((["plain"], "plain is not a git repository"),
                           (["p", "./missing"], "./missing is not a git repository"),
                           ([], ". is not a git repository"),
                           ([*WEEK[:1], "2026-10-23", *WEEK[2:3], "2026-10-17", "p"], "--from must not be after --to"),
                           (["--from", "2026-1-17", "p"], "--from and --to take a day as YYYY-MM-DD"),
                           (["--to", "20261023", "p"], "--from and --to take a day as YYYY-MM-DD"),
                           (["--to", "2026-02-30", "p"], "--from and --to take a day as YYYY-MM-DD"),
                           (["--from", "2026-10-17T00:00", "p"], "--from and --to take a day as YYYY-MM-DD")):
            done = self.score(*given)
            self.assertEqual((done.returncode, done.stdout, done.stderr), (2, "", f"ceo: refused: {why}.\n"), given)
        self.assertFalse((self.tmp / "home").exists(), "score read or made the owner's file")


if __name__ == "__main__":
    unittest.main()
