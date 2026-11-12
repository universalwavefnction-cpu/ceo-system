"""The first-review pass rate: a family, the files of one order number, passed when the door merged it with no round
file. `ceo.py score --passes` prints it by builder, plan and ISO week; the tick records last week's once on main."""
import subprocess
import time
import unittest
from datetime import datetime, timedelta, timezone

from test_score import ScoreCase
from test_tick import TickCase


def order(title, worker="", head=""):
    """An order titled title, its `## Worker` line worker when given, head above its first section."""
    return (f"# {title}\n{head}\n## Goal\nIt.\n\n" + (f"## Worker\n{worker}\n\n" if worker else "")
            + "## Done when\n```bash\ntrue\n```\n\n## Not asked\nMore.\n")


def again(n, slug, title, kind):
    """The round or join file kind of order n, titled after title."""
    name, says, at = (f"round-{kind}", f"round {kind}, its review's findings fixed", kind) if isinstance(kind, int) \
        else (f"join-{kind}", "joined with main", 1)
    return {f"orders/{n}-{name}.md": order(f"{title}: {says}", head=f"\nORDER: {n}\nROUND: {at}\n"
                                                                     f"REPLACES: orders/{n}-{slug}.md\n")}


def history(commit, first):
    """Two ISO weeks of a project, the first from Monday first; commit(at, message, files) makes one commit. Week one:
    001 passes, 002 merges at round 2. Week two: 003 passes through a join, 004 merges at round 3, 005 is cut, 006
    passes with no worker and is cut after its merge, 007 passes in no plan at the last UTC second. 008 stays open,
    009 merged the second before week one, 010 the second after week two; no order bears the title of #8."""
    at = lambda n, clock="10:00:00": f"{first + timedelta(days=n)}T{clock}Z"
    files = {"plans/alpha.md": "# Alpha\n\nat once 2\n- orders/001-one.md — One.\n- orders/002-two.md — Two.\n"
                               "- orders/003-three.md after 001 — Three.\n- orders/008-eight.md — Eight.\n"
                               "- orders/009-nine.md — Nine.\n",
             "plans/beta.md": "# Beta\n\n- orders/002-two.md — Two again.\n- orders/004-four.md after 002 — Four.\n"
                              "- orders/005-five.md — Five.\n- orders/006-six.md — Six.\n- orders/010-ten.md — Ten.\n"}
    for n, slug, worker in (("001", "one", "deep"), ("002", "two", "deep"), ("003", "three", "quick"),
                            ("004", "four", "deep"), ("005", "five", "spare"), ("006", "six", ""),
                            ("007", "seven", "`quick`"), ("008", "eight", "quick"), ("009", "nine", "deep"),
                            ("010", "ten", "deep")):
        files[f"orders/{n}-{slug}.md"] = order(slug.capitalize(), worker)
    commit(at(-8, "08:00:00"), "Filed", files)
    commit(at(-1, "23:59:59"), "Nine (#1)", {})
    commit(at(1), "One (#2)", again("002", "two", "Two", 2))
    commit(at(2), "Two: round 2, its review's findings fixed (#3)", {})
    commit(at(3), "Eight goes round", again("008", "eight", "Eight", 2))
    commit(at(7), "Three joins, four goes round", {**again("003", "three", "Three", "abcdef1"),
                                                   **again("004", "four", "Four", 2)})
    commit(at(8), "Three: joined with main (#4)", again("004", "four", "Four", 3))
    commit(at(9), "Four: round 3, its review's findings fixed (#5)", {})
    commit(at(10), "Six (#6)", {"orders/005-five.cut": "Cut.\n"})
    commit(at(11), "Six cut after its merge", {"orders/006-six.cut": "Cut late.\n"})
    commit(at(13, "23:59:59"), "Seven (#7)", {})
    commit(at(13, "23:59:59"), "Nobody (#8)", {})
    commit(at(14, "00:00:00"), "Ten (#9)", {})


def section(start, end, head, *tables):
    """The lines --passes prints: head, then a table for each of builder, plan and week."""
    out = [f"First-review pass, {start} to {end}: {head}"]
    for name, rows in zip(("builder", "plan", "week"), tables):
        out += ["", f"| {name} | first-review pass | cut |", "|---|---|---|", *(f"| {row} |" for row in rows)]
    return "".join(f"{line}\n" for line in out)


def week(day):
    return "%d-W%02d" % day.isocalendar()[:2]


def two_weeks(first):
    return section(first, first + timedelta(days=13), "4 of 6 merged families had no round file (67 %); 1 cut.",
                   ["deep | 33 % (1 of 3) | 0", "default | 100 % (1 of 1) | 0", "quick | 100 % (2 of 2) | 0",
                    "spare | – | 1"],
                   ["plans/alpha.md | 67 % (2 of 3) | 0", "plans/beta.md | 33 % (1 of 3) | 1",
                    "no plan | 100 % (1 of 1) | 0"],
                   [f"{week(first)} | 50 % (1 of 2) | 0", f"{week(first + timedelta(days=7))} | 75 % (3 of 4) | 1"])


def second_week(first):
    start = first + timedelta(days=7)
    return section(start, start + timedelta(days=6), "3 of 4 merged families had no round file (75 %); 1 cut.",
                   ["deep | 0 % (0 of 1) | 0", "default | 100 % (1 of 1) | 0", "quick | 100 % (2 of 2) | 0",
                    "spare | – | 1"],
                   ["plans/alpha.md | 100 % (1 of 1) | 0", "plans/beta.md | 50 % (1 of 2) | 1",
                    "no plan | 100 % (1 of 1) | 0"],
                   [f"{week(start)} | 75 % (3 of 4) | 1"])


class Passes(ScoreCase):
    def test_the_pass_rate_by_builder_plan_and_week_over_utc_days_through_the_cli(self):
        repo, first = self.repo(), datetime(2026, 10, 19).date()
        history(lambda at, message, files: self.commit(repo, at, message, files), first)
        done = self.score("--passes", "--from", str(first), "--to", str(first + timedelta(days=13)), str(repo))
        self.assertEqual((done.returncode, done.stderr, done.stdout), (0, "", two_weeks(first)))
        start = first + timedelta(days=7)
        done = self.score("--passes", "--from", str(start), "--to", str(start + timedelta(days=6)), cwd=repo)
        self.assertEqual((done.returncode, done.stderr, done.stdout), (0, "", second_week(first)))
        self.assertFalse((self.tmp / "home").exists(), "score read or made the owner's file")

    def test_a_window_with_no_family_and_several_folders(self):
        repo = self.repo()
        self.commit(repo, "2026-10-18T10:00:00Z", "One (#1)")
        done = self.score("--passes", "--from", "2026-10-17", "--to", "2026-10-23", str(repo))
        self.assertEqual(done.stdout, section("2026-10-17", "2026-10-23",
                                              "0 of 0 merged families had no round file (–); 0 cut.", [], [], []))
        done = self.score("--passes", str(repo), str(repo))
        self.assertEqual((done.returncode, done.stdout, done.stderr),
                         (2, "", "ceo: refused: --passes reads one repository.\n"))

    def test_a_cut_dates_from_its_oldest_addition_on_a_merged_side_branch(self):
        repo = self.repo()
        self.commit(repo, "2026-01-01T10:00:00Z", "Filed", {"orders/001-one.md": order("One", "deep")})
        self.git(repo, "checkout", "-qb", "side")
        self.commit(repo, "2026-01-05T10:00:00Z", "Earlier cut", {"orders/001-one.cut": "Cut.\n"})
        self.git(repo, "checkout", "-q", "main")
        self.commit(repo, "2026-01-12T10:00:00Z", "Later cut", {"orders/001-one.cut": "Cut.\n"})
        self.merge(repo, "2026-01-13T10:00:00Z", "Both branches")
        for start, end, cuts in (("2026-01-05", "2026-01-11", 1), ("2026-01-12", "2026-01-18", 0)):
            done = self.score("--passes", "--from", start, "--to", end, str(repo))
            self.assertEqual((done.returncode, done.stderr, done.stdout.splitlines()[0]), (0, "", (
                f"First-review pass, {start} to {end}: 0 of 0 merged families had no round file (–); {cuts} cut.")))

    def test_an_order_with_crlf_line_endings_counts_under_its_worker(self):
        repo = self.repo()
        self.commit(repo, "2026-01-05T10:00:00Z", "Filed",
                    {"orders/001-one.md": order("One", "deep").replace("\n", "\r\n")})
        self.commit(repo, "2026-01-06T10:00:00Z", "One (#1)")
        done = self.score("--passes", "--from", "2026-01-05", "--to", "2026-01-11", str(repo))
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertIn("| deep | 100 % (1 of 1) | 0 |", done.stdout)
        self.assertNotIn("default", done.stdout)

    def test_a_cut_first_added_by_a_merge_commit_counts(self):
        repo = self.repo()
        self.commit(repo, "2026-01-01T10:00:00Z", "Filed", {"orders/001-one.md": order("One", "deep")})
        self.git(repo, "checkout", "-qb", "side")
        self.commit(repo, "2026-01-03T10:00:00Z", "Side change", {"side": "side\n"})
        self.git(repo, "checkout", "-q", "main")
        self.commit(repo, "2026-01-04T10:00:00Z", "Main change", {"main": "main\n"})
        self.git(repo, "merge", "--no-commit", "--no-ff", "side")
        self.commit(repo, "2026-01-06T10:00:00Z", "Merge and cut", {"orders/001-one.cut": "Cut.\n"})
        self.assertEqual(self.git(repo, "rev-list", "--parents", "-n", "1", "HEAD").count(" "), 2)
        for start, end, cuts in (("2026-01-05", "2026-01-11", 1), ("2026-01-12", "2026-01-18", 0)):
            done = self.score("--passes", "--from", start, "--to", end, str(repo))
            self.assertEqual((done.returncode, done.stderr, done.stdout.splitlines()[0]), (0, "", (
                f"First-review pass, {start} to {end}: 0 of 0 merged families had no round file (–); {cuts} cut.")))

    def test_a_fenced_worker_example_is_not_the_orders_worker(self):
        repo = self.repo()
        text = order("One", "deep").replace("## Goal\nIt.\n", "## Goal\nIt.\n\n```text\n## Worker\nspare\n```\n")
        self.commit(repo, "2026-01-05T10:00:00Z", "Filed", {"orders/001-one.md": text,
                                                            "orders/002-two.md": "# Two\n\n## Worker\nspare\n"})
        self.commit(repo, "2026-01-06T10:00:00Z", "One (#1)")
        self.commit(repo, "2026-01-06T11:00:00Z", "Two (#2)")
        done = self.score("--passes", "--from", "2026-01-05", "--to", "2026-01-11", str(repo))
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertIn("| deep | 100 % (1 of 1) | 0 |", done.stdout)
        self.assertIn("| default | 100 % (1 of 1) | 0 |", done.stdout, "an order the kernel refuses has no builder")
        self.assertNotIn("| spare |", done.stdout)


class Weekly(TickCase):
    def commit(self, at, message, files):
        for name, text in files.items():
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(text)
            self.git("add", name)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", message], cwd=self.repo, check=True,
                       capture_output=True, env={**self.env, "GIT_AUTHOR_DATE": at, "GIT_COMMITTER_DATE": at})

    def shown(self, what):
        return subprocess.run(["git", "show", what], cwd=self.repo, env=self.env, capture_output=True,
                              text=True).stdout

    def test_the_tick_records_last_weeks_pass_rate_once_on_main_while_main_keeps_results(self):
        clock = datetime.now(timezone.utc)
        monday = datetime.combine(clock.date() - timedelta(days=clock.weekday()), datetime.min.time(), timezone.utc)
        if (left := (monday + timedelta(days=7) - clock).total_seconds()) < 300:
            time.sleep(left + 1)  # this test and the tick must read the same week as last week
        today = datetime.now(timezone.utc).date()
        first = today - timedelta(days=today.weekday() + 14)
        history(self.commit, first)
        commits = lambda: self.git("rev-list", "--count", "main")
        before = commits()
        self.idle_tick()
        self.assertEqual(commits(), before, "a main with no results/ folder got a score")
        self.commit(clock.strftime("%Y-%m-%dT%H:%M:%SZ"), "Results", {"results/.gitkeep": ""})
        self.idle_tick()
        path = f"results/score-{week(first + timedelta(days=7))}.md"
        self.assertEqual(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "main"), path)
        self.assertEqual(self.shown(f"main:{path}"), second_week(first))
        start = first + timedelta(days=7)
        done = self.ceo("score", "--passes", "--from", str(start), "--to", str(start + timedelta(days=6)))
        self.assertEqual(done.stdout, self.shown(f"main:{path}"))
        after = commits()
        self.idle_tick()
        self.assertEqual((commits(), self.shown(f"main:{path}")), (after, second_week(first)),
                         "a second pass in the same week wrote the score again")


if __name__ == "__main__":
    unittest.main()
