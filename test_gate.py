"""gate.sh itself, in a scratch copy: real sh, python3 and unittest, stand-in test files whose tests fail,
skip or pass on each of their runs as a test here says, and a sleep first on PATH that writes down the
wait it was asked for and returns at once."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

KERNEL = Path(__file__).resolve().parent
SENTENCE = "RealGitHub passed on the second run"
DOOR, GITHUB, OTHER = "test_door.Door.test_it", "test_door.RealGitHub.test_it", "test_other.Other.test_it"

# Each stand-in file starts so: every run of a test writes its name to $RAN, then does the next of the
# outcomes it was given.
ACT = '''import os
import subprocess
import unittest


def act(test, outcomes):
    with open(os.environ["RAN"], "a+") as ran:
        ran.seek(0)
        outcome = outcomes[ran.read().splitlines().count(test.id())]
        ran.write(test.id() + "\\n")
    if outcome == "error":  # as gh exiting 1 under check=True, one of the faults of 8 October 2026
        raise subprocess.CalledProcessError(1, ["gh", "api", "user"])
    if outcome == "skip":
        test.skipTest("gh is not logged in here")
    test.assertEqual(outcome, "pass")
'''


class Gate(unittest.TestCase):
    def gate(self, github, door=("pass",), other=("pass",)):
        """Run gate.sh in a scratch copy whose test_door.RealGitHub, test_door.Door and test_other.Other
        do the outcomes given, one a run; return its exit code, its output and what ran, waits included."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        copy, fakes, ran = tmp / "copy", tmp / "fakes", tmp / "ran"
        copy.mkdir()
        fakes.mkdir()
        shutil.copy(KERNEL / "gate.sh", copy)
        for name, classes in (("test_door", {"Door": door, "RealGitHub": github}),
                              ("test_other", {"Other": other})):
            (copy / f"{name}.py").write_text(ACT + "".join(
                f"\n\nclass {cls}(unittest.TestCase):\n    def test_it(self):\n"
                f"        act(self, {list(outcomes)!r})\n" for cls, outcomes in classes.items()))
        (fakes / "sleep").write_text('#!/bin/sh\necho "sleep $*" >> "$RAN"\n')
        (fakes / "sleep").chmod(0o755)
        ran.write_text("")
        env = {**os.environ, "RAN": str(ran), "PATH": f"{fakes}{os.pathsep}{os.environ['PATH']}"}
        done = subprocess.run(["./gate.sh"], cwd=copy, env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout + done.stderr, ran.read_text().splitlines()

    def test_github_failing_alone_runs_once_more_30_seconds_on_and_the_gate_passes_if_it_passes(self):
        code, output, ran = self.gate(["error", "pass"])
        self.assertEqual(code, 0, output)
        self.assertIn(SENTENCE, output)
        self.assertEqual(ran, [DOOR, GITHUB, OTHER, "sleep 30", GITHUB])

    def test_a_second_failure_or_a_skip_on_the_second_run_fails_the_gate(self):
        for second in ("fail", "skip"):
            with self.subTest(second=second):
                code, output, ran = self.gate(["fail", second])
                self.assertNotEqual(code, 0, output)
                self.assertNotIn(SENTENCE, output)
                self.assertEqual(ran, [DOOR, GITHUB, OTHER, "sleep 30", GITHUB])

    def test_a_failure_of_any_other_test_runs_nothing_again_not_even_github_beside_it(self):
        for elsewhere in ({"door": ["fail", "pass"]}, {"other": ["error", "pass"]}):
            with self.subTest(**elsewhere):
                code, output, ran = self.gate(["fail", "pass"], **elsewhere)
                self.assertNotEqual(code, 0, output)
                self.assertNotIn(SENTENCE, output)
                self.assertEqual(ran, [DOOR, GITHUB, OTHER])

    def test_github_passing_or_skipping_at_once_passes_the_gate_as_before(self):
        for first in ("pass", "skip"):
            with self.subTest(first=first):
                code, output, ran = self.gate([first])
                self.assertEqual(code, 0, output)
                self.assertNotIn(SENTENCE, output)
                self.assertEqual(ran, [DOOR, GITHUB, OTHER])


class SizeCaps(unittest.TestCase):
    def gate(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            shutil.copy(KERNEL / "gate.sh", copy)
            for name, content in files.items():
                (copy / name).parent.mkdir(parents=True, exist_ok=True)
                (copy / name).write_bytes(content)
            return subprocess.run(["./gate.sh"], cwd=copy, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=120)

    def test_code_over_32000_bytes_is_refused_with_its_size_and_cap(self):
        done = self.gate({"big.py": b"#" * 32000 + b"\n", "small.py": b"#\n"})
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("gate: big.py is 32001 bytes (~8000 tokens), over its cap of 32000 bytes: "
                      "split it by concern into top-level files of at most 8,000 tokens each", done.stdout)

    def test_listed_test_may_keep_its_size_or_shrink_but_not_grow(self):
        for size in (40000, 39999, 40001):
            with self.subTest(size=size):
                done = self.gate({"test_big.py": b"#" * (size - 1) + b"\n",
                                  "oversize.txt": b"test_big.py 40000\nbig.py 40000\n"})
                if size <= 40000:
                    self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                else:
                    self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
                    self.assertIn("gate: test_big.py is 40001 bytes (~10000 tokens), "
                                  "over its cap of 40000 bytes", done.stdout)

    def test_listing_a_code_file_does_not_raise_its_cap(self):
        done = self.gate({"big.py": b"#" * 39999 + b"\n",
                          "test_big.py": b"#" * 39999 + b"\n",
                          "oversize.txt": b"test_big.py 40000\nbig.py 40000\n"})
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("gate: big.py is 40000 bytes (~10000 tokens), over its cap of 32000 bytes", done.stdout)

    def test_code_over_5450_lines_is_refused_with_its_count(self):
        done = self.gate({"long.py": b"\n" * 5451})
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("gate: the kernel's code is 5451 lines, over its cap of 5,450 (CEO.md): "
                      "to add, remove", done.stdout)

    def test_python_in_ears_and_tools_has_the_same_cap_and_counts_no_line(self):
        for folder in ("ears", "tools"):
            with self.subTest(folder=folder):
                done = self.gate({"small.py": b"#\n", f"{folder}/big.py": b"#\n" * 6000})
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                done = self.gate({"small.py": b"#\n", f"{folder}/big.py": b"#" * 32000 + b"\n"})
                self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn(f"gate: {folder}/big.py is 32001 bytes (~8000 tokens), "
                              "over its cap of 32000 bytes", done.stdout)


if __name__ == "__main__":
    unittest.main()
