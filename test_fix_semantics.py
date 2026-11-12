"""The done-when of a fix round, run by bash: each counting finding fails it exactly when that command, replayed alone
as `bash -c COMMAND`, fails, so a reviewer's own `set -e` keeps its force and an unfixed head is never recorded done."""
import subprocess
import unittest

import ceo
import test_fix

SET_E = "set -e\ntest -f fixed\nprintf 'all checks passed\\n'"


class Semantics(test_fix.FixCase):
    order, commit, records, filed = (test_fix.Cases.order, test_fix.Cases.commit, test_fix.Cases.records,
                                     test_fix.Cases.filed)

    def script(self, *commands):
        """The done-when of the round filed for these counting findings of order 002, run in a repository holding built."""
        self.project(f"scene{len(list(self.tmp.glob('scene*')))}")  # a fresh repository for each call
        self.order("002-two", "Two")
        head = self.commit()
        self.records("002-two", head, review={"findings": [
            {"claim": f"Claim {i}.", "command": command, "exit": 1, "counts": True} for i, command in enumerate(commands)]})
        self.filed(head)
        (self.repo / "built").touch()
        return ceo.parse_order(self.git("show", "main:orders/002-round-2.md") + "\n")[1]

    def bash(self, script, stdin=""):
        return subprocess.run(["bash", "-c", script], cwd=self.repo, env=self.env, input=stdin, capture_output=True,
                              text=True)

    def test_a_finding_with_set_e_fails_the_round_while_it_fails_alone(self):
        script = self.script(SET_E)
        for fixed in (False, True):
            if fixed:
                (self.repo / "fixed").touch()
            alone, round_ = self.bash(SET_E), self.bash(script)
            with self.subTest(fixed=fixed):
                self.assertEqual(alone.returncode == 0, fixed, alone.stderr)
                self.assertEqual(round_.returncode == 0, fixed, round_.stdout + round_.stderr)
                self.assertEqual("finding 1 of the review still holds" in round_.stdout, not fixed)

    def test_every_finding_fails_the_round_as_it_does_alone(self):
        for command in (SET_E, "set -e\nfalse\ntrue", "set -eu\ntest -n \"$UNSET_BY_THE_TEST\"", "false\ntrue", "true\nfalse",
                        "set -e; false || true; test -f built", "set -o pipefail\nfalse | cat", "false | cat",
                        "(set -e; false; true)", "exit 3", "! true", "true"):
            script = self.script(command)
            for fixed in (False, True):
                if fixed:
                    (self.repo / "fixed").touch()
                with self.subTest(command=command, fixed=fixed):
                    self.assertEqual(self.bash(script).returncode == 0, self.bash(command).returncode == 0)

    def test_the_first_failing_finding_stops_the_round_and_names_itself(self):
        script = self.script("test -f built", "set -e\nfalse\ntrue", "echo never; true")
        done = self.bash(script)
        self.assertEqual((done.returncode, done.stdout), (1, "finding 2 of the review still holds\n"), done.stderr)

    def test_passing_findings_leave_the_orders_own_done_when_to_decide(self):
        script = self.script("set -e\ntest -d orders\ntrue", "test -d orders")
        self.assertEqual(self.bash(script).returncode, 0)
        (self.repo / "built").unlink()
        done = self.bash(script)
        self.assertEqual(done.returncode, 1, "the order's own `test -f built` was not run last")
        self.assertNotIn("still holds", done.stdout)

    def test_quotes_heredocs_and_stdin_reach_the_finding_as_written(self):
        command = ("set -e\nread word\ntest \"$word\" = 'it'\"'\"'s'\n"
                   "cat <<'END' | grep -qF 'a $HOME `x` \"q\"'\na $HOME `x` \"q\"\nEND\n"
                   "python3 - <<'PY'\nimport sys\nsys.exit(0 if 'it\\'s' == \"it's\" else 1)\nPY")
        script = self.script(command)
        for line, code in (("it's\n", 0), ("its\n", 1)):
            with self.subTest(line=line):
                self.assertEqual((self.bash(command, line).returncode, self.bash(script, line).returncode), (code, code))


if __name__ == "__main__":
    unittest.main()
