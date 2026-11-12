"""A project's published work in its status file: the rows its own tooling appends to .ceo/proof.jsonl, the newest 50
well-formed ones copied by the real board command, the newest first; every other line skipped; nothing written."""
import json
import unittest
from datetime import datetime, timedelta, timezone

from test_tick import TickCase

START = datetime(2026, 11, 1, tzinfo=timezone.utc)


def row(n, **said):
    """A row as a content project's own exporter writes one: piece n, published n hours after START."""
    return {"at": (START + timedelta(hours=n)).isoformat(), "stage": "Publish the first pieces", "department": "posts",
            "item": f"Piece {n}", "proof": f"https://example.invalid/posts/{n}", **said}


class Proof(TickCase):
    def setUp(self):
        super().setUp()
        self.git("add", "-A")
        self.git("commit", "-qm", "start")
        self.path, self.out = self.repo / ".ceo" / "proof.jsonl", self.tmp / "repo.json"

    def append(self, *lines):
        """Lines added at the file's end by a plain write, as the project's own tooling adds them."""
        self.path.parent.mkdir(exist_ok=True)
        with open(self.path, "ab") as rows:
            for line in lines:
                rows.write((line if isinstance(line, bytes) else json.dumps(line, ensure_ascii=False).encode()) + b"\n")

    def board(self):
        """The real `ceo.py board --name`: its proof; it wrote nothing in the project, listed no record unreadable,
        and its status file still makes a page."""
        def files():
            return {path: path.read_bytes() for path in self.repo.rglob("*") if path.is_file()}
        before = files()
        done = self.ceo("board", "--name", "repo", "--host", "vps", "--out", str(self.out))
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)
        self.assertEqual(files(), before, "the board wrote in the project")
        found = json.loads(self.out.read_text())
        self.assertEqual(found["unreadable"], [], "the proof file was listed as an unreadable record")
        page = self.ceo("board", "--page", str(self.out), "--out", str(self.tmp / "page.html"))
        self.assertEqual(page.returncode, 0, page.stderr)
        return found["proof"]

    def test_the_newest_50_rows_come_out_newest_first_by_their_time_as_written(self):
        rows = [row(n) for n in range(60)]
        rows[59]["at"] = "2026-11-03T06:00:00-05:00"  # 11:00 UTC, the newest, though its text sorts below 10:00 UTC
        self.append(*rows[30:], *rows[:30])  # the newer pieces first, then a backfill of the older ones
        self.assertEqual(self.board(), rows[10:][::-1], "not the newest 50 by time, the newest first, as written")

    def test_a_line_that_is_no_such_row_is_skipped_and_never_fails_the_board(self):
        full, again = row(1, proof="é" * 300), row(2, item="Piece 2, told again")
        self.append(
            b'{"at": "2026-11-01T05:00:00+00:00", "stage": "Publish the fir',  # a line cut short
            b"\xff\xfe not UTF-8", b"", b"[1, 2]", b'"a string"', b"{}",
            {key: value for key, value in row(3).items() if key != "department"},  # a row missing a key
            row(4, proof="x" * 301),  # a value over 300 characters is skipped whole, never cut
            row(5, item="two\nlines"), row(6, stage=""), row(7, proof=7), row(8, department=None),
            row(9, at="yesterday"), full, row(2), again,  # the same time twice: the later line first
            {**row(0, stage="A stage no map names", department="a desk"), "platform": "two"})  # copied, its extra left
        self.assertEqual(self.board(), [again, row(2), full, row(0, stage="A stage no map names", department="a desk")])

    def test_a_time_at_the_edge_of_the_calendar_is_no_time_and_never_fails_the_board(self):
        self.append(row(1, at="0001-01-01T00:00:00+05:00"), row(2, at="9999-12-31T23:59:59-05:00"), row(3))
        self.assertEqual(self.board(), [row(3)])

    def test_a_line_nested_too_deep_to_read_is_skipped_and_never_fails_the_board(self):
        self.append(b"[" * 100000, b'{"at": ' * 100000, row(1))
        self.assertEqual(self.board(), [row(1)])

    def test_a_row_saying_a_lone_surrogate_in_an_escape_is_copied_and_never_fails_the_board(self):
        rows = [row(1, item=chr(0xD800)), row(2, item="a \U0001F600 pair"), row(3, proof=chr(0xDFFF) + "é")]
        self.append(*(json.dumps(one).encode() for one in rows))  # as an exporter that escapes every non-ASCII writes them
        self.assertEqual(self.board(), rows[::-1])

    def test_no_file_or_an_empty_one_gives_no_proof_and_a_pass_of_the_kernel_writes_none(self):
        self.idle_tick()  # a real pass of the kernel; with no login, nothing starts
        self.assertFalse(self.path.exists(), "the kernel wrote a proof row itself")
        self.assertEqual(self.board(), [])
        self.path.write_bytes(b"")
        self.assertEqual(self.board(), [])
        self.append(b'{"at": ')  # nothing but a line cut short
        self.assertEqual(self.board(), [])


if __name__ == "__main__":
    unittest.main()
