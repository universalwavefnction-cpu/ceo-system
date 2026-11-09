"""Open a listed project through the real CLI and a stateful herdr on PATH; no network or live panes."""
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KERNEL = Path(__file__).resolve().parent
HERDR = '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ["HERDR_CALLS"]).open("a") as log:
    log.write(json.dumps({"args": args, "cwd": os.getcwd()}) + "\\n")
state = Path(os.environ["HERDR_STATE"])
panes = json.loads(state.read_text()) if state.exists() else []
if args == ["agent", "list"]:
    print(os.environ.get("HERDR_LIST", json.dumps({"id": "cli:agent:list",
                                                 "result": {"agents": panes, "type": "agents"}})))
    sys.exit(int(os.environ.get("HERDR_LIST_EXIT", "0")))
if args[:2] == ["agent", "start"]:
    panes.append({"name": args[2], "terminal_id": "test-terminal"})
    state.write_text(json.dumps(panes))
elif args[:2] != ["agent", "focus"]:
    sys.exit(99)
sys.exit(int(os.environ.get("HERDR_EXIT", "0")))
'''


class Open(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.home = self.tmp / "ceo"
        self.home.mkdir()
        self.repo = self.tmp / "projects" / "a project"
        self.repo.mkdir(parents=True)
        self.other = self.tmp / "beta"
        self.other.mkdir()
        self.listed = self.home / "projects.txt"
        self.listed.write_text(f"{self.repo}\n{self.other}\n")
        self.log = self.tmp / "calls.jsonl"
        self.state = self.tmp / "panes.json"
        commands = self.tmp / "bin"
        commands.mkdir()
        stub = self.tmp / "herdr.py"
        stub.write_text(HERDR)
        herdr = commands / "herdr"
        herdr.write_text(f'#!/bin/sh\nexec {shlex.join([sys.executable, str(stub)])} "$@"\n')
        herdr.chmod(0o755)
        self.env = {**os.environ, "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
                    "CEO_HOME": str(self.home), "HERDR_CALLS": str(self.log), "HERDR_STATE": str(self.state)}

    def ceo(self, project):
        return subprocess.run([sys.executable, "-B", str(KERNEL / "ceo.py"), "open", project],
                              cwd=self.tmp, env=self.env, capture_output=True, text=True, timeout=10)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def assert_started(self, repo, project):
        done = self.ceo(project)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        calls = self.calls()
        self.assertEqual([call["args"] for call in calls], [
            ["agent", "list"],
            ["agent", "start", f"ceo-{repo.name}", "--cwd", str(repo), "--",
             sys.executable, str(KERNEL / "ceo.py"), "session"]])
        self.assertEqual(calls[-1]["cwd"], str(repo))

    def test_folder_name_starts_the_session_in_its_project(self):
        self.assert_started(self.repo, self.repo.name)

    def test_listed_path_starts_the_session_in_its_project(self):
        self.assert_started(self.repo, str(self.repo))

    def test_second_open_focuses_without_starting_again(self):
        self.state.write_text(json.dumps([{"name": None}, {}]))
        self.assert_started(self.repo, self.repo.name)
        done = self.ceo(str(self.repo))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([call["args"] for call in self.calls()][2:],
                         [["agent", "list"], ["agent", "focus", f"ceo-{self.repo.name}"]])

    def test_existing_unrelated_pane_does_not_match_by_substring(self):
        self.state.write_text(json.dumps([{"name": f"ceo-{self.repo.name}-other"}]))
        self.assert_started(self.repo, self.repo.name)

    def test_null_and_missing_pane_names_start_the_session_once(self):
        self.state.write_text(json.dumps([{"name": None}, {}]))
        self.assert_started(self.repo, self.repo.name)

    def test_list_trims_deduplicates_and_ignores_missing_directories_when_matching(self):
        missing = self.tmp / "gone" / self.repo.name
        self.listed.write_text(f"{missing}\n\n  {self.repo}\t\n{self.repo}")
        self.assert_started(self.repo, self.repo.name)

    def assert_refused(self, project, entries):
        done = self.ceo(project)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        for entry in entries:
            self.assertIn(str(entry), done.stderr)
        self.assertEqual(self.calls(), [])
        return done

    def test_unknown_name_lists_every_project_without_calling_herdr(self):
        missing = self.tmp / "gone"
        with self.listed.open("a") as listed:
            listed.write(str(missing))
        self.assert_refused("unknown", [self.repo, self.other, missing])

    def test_unlisted_path_is_refused(self):
        self.assert_refused(str(self.tmp), [self.repo, self.other])

    def test_ambiguous_name_lists_every_project_without_calling_herdr(self):
        twin = self.tmp / "another" / self.repo.name
        twin.mkdir(parents=True)
        with self.listed.open("a") as listed:
            listed.write(str(twin))
        done = self.assert_refused(self.repo.name, [self.repo, self.other, twin])
        self.assertIn("ambiguous", done.stderr)
        self.assert_started(twin, str(twin))

    def test_missing_list_explains_how_to_add_a_project_without_calling_herdr(self):
        self.listed.unlink()
        done = self.assert_refused(self.repo.name, [self.listed])
        self.assertIn("ceo.py init", done.stderr)

    def test_empty_list_explains_how_to_add_a_project(self):
        self.listed.write_text(" \n\t\n")
        done = self.assert_refused(self.repo.name, [self.listed])
        self.assertIn("ceo.py init", done.stderr)

    def test_unreadable_pane_list_never_starts_a_duplicate(self):
        for response in ("not json", "{}", '{"result":{"agents":{}}}',
                         '{"result":{"agents":[null]}}'):
            with self.subTest(response=response):
                self.env["HERDR_LIST"] = response
                done = self.ceo(self.repo.name)
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual([call["args"] for call in self.calls()], [["agent", "list"]] * 4)

    def test_failed_pane_list_never_starts_a_duplicate(self):
        self.env["HERDR_LIST_EXIT"] = "7"
        done = self.ceo(self.repo.name)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual([call["args"] for call in self.calls()], [["agent", "list"]])

    def test_start_and_focus_propagate_herdr_exit_status(self):
        self.env["HERDR_EXIT"] = "17"
        self.assertEqual(self.ceo(self.repo.name).returncode, 17)
        self.assertEqual(self.ceo(self.repo.name).returncode, 17)
        self.assertEqual(self.calls()[-1]["args"], ["agent", "focus", f"ceo-{self.repo.name}"])


if __name__ == "__main__":
    unittest.main()
