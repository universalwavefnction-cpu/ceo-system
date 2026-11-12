"""gate.sh itself, in a scratch copy: real sh, python3 and unittest, stand-in test files whose tests fail,
skip or pass on each of their runs as a test here says, and a sleep first on PATH that writes down the
wait it was asked for and returns at once."""
import os
import re
import shutil
import signal
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


# Each stand-in file of Parallel starts so. A test leaves its mark while it runs, writes `<id> <pid> <marks now>`
# to $RAN, prints three lines a moment apart, meets the test named if any, then does the next of the outcomes given,
# one a run.
PARALLEL_ACT = '''import os, signal, subprocess, time, unittest
from pathlib import Path


def act(test, outcomes, meet=None):
    live, saw = Path(os.environ["MARKS"], "live"), Path(os.environ["MARKS"], "saw")
    (live / test.id()).touch()
    try:
        with open(os.environ["RAN"], "a+") as ran:
            ran.seek(0)
            outcome = outcomes[[line.split()[0] for line in ran.read().splitlines()].count(test.id())]
            ran.write(f"{test.id()} {os.getpid()} {len(list(live.iterdir()))}\\n")
        for line in range(3):
            print(f"{test.id()} says {line}", flush=True)
            time.sleep(0.05)
        if meet:  # met: each saw the other's mark while its own was there, and waited until it was seen
            until = time.monotonic() + float(os.environ["WAIT"])
            while not (live / meet).exists() and time.monotonic() < until:
                time.sleep(0.01)
            if (live / meet).exists():
                (saw / test.id()).touch()
                while not (saw / meet).exists() and time.monotonic() < until:
                    time.sleep(0.01)
            with open(os.environ["RAN"], "a") as ran:
                ran.write(f"meet {test.id()} {'met' if (saw / test.id()).exists() else 'alone'}\\n")
    finally:
        if outcome not in ("kill", "exit"):
            (live / test.id()).unlink()
    if outcome == "kill":
        os.kill(os.getpid(), signal.SIGKILL)
    if outcome == "exit":  # ends with 0 before unittest has a result
        os._exit(0)
    if outcome == "error":  # as gh exiting 1 under check=True, one of the faults of 8 October 2026
        raise subprocess.CalledProcessError(1, ["gh", "api", "user"])
    if outcome == "skip":
        test.skipTest("gh is not logged in here")
    if outcome == "fail":
        test.fail(f"{test.id()} failed on purpose")
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
        ran = ran.read_text().splitlines()  # files run at once write their first runs in no fixed order
        cut = ran.index("sleep 30") if "sleep 30" in ran else len(ran)
        return done.returncode, done.stdout + done.stderr, sorted(ran[:cut]) + ran[cut:]

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


class Parallel(unittest.TestCase):
    def suite(self, files, jobs=None, wait=30):
        """Run gate.sh with SIGINT at its default in a scratch copy of stand-in files, {module: {class: outcomes or
        (outcomes, the test it meets)} or a file's text}; return its exit code, its output and $RAN's lines."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        copy, fakes, marks, temp, ran = tmp / "copy", tmp / "fakes", tmp / "marks", tmp / "temp", tmp / "ran"
        for folder in (copy, fakes, marks / "live", marks / "saw", temp):
            folder.mkdir(parents=True)
        shutil.copy(KERNEL / "gate.sh", copy)
        for name, classes in files.items():
            (copy / f"{name}.py").write_text(classes if isinstance(classes, str) else PARALLEL_ACT + "".join(
                f"\n\nclass {cls}(unittest.TestCase):\n    def test_it(self):\n"
                f"        act(self, *{spec if isinstance(spec, tuple) else (spec,)!r})\n"
                for cls, spec in classes.items()))
        (fakes / "sleep").write_text('#!/bin/sh\necho "sleep $*" >> "$RAN"\n')
        (fakes / "sleep").chmod(0o755)
        ran.write_text("")
        env = {**os.environ, "RAN": str(ran), "MARKS": str(marks), "WAIT": str(wait), "TMPDIR": str(temp),
               "PATH": f"{fakes}{os.pathsep}{os.environ['PATH']}"}
        env.pop("CEO_GATE_JOBS", None)
        if jobs is not None:
            env["CEO_GATE_JOBS"] = str(jobs)
        before = sorted(path.name for path in copy.iterdir())
        done = subprocess.run(["./gate.sh"], cwd=copy, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, timeout=300,
                              preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
        left = (sorted(path.name for path in copy.iterdir()), sorted(path.name for path in temp.iterdir()))
        self.assertEqual(left, (before, []), "the gate left files behind, in the tree or in $TMPDIR")
        return done.returncode, done.stdout, ran.read_text().splitlines()

    def test_each_file_runs_once_in_a_process_of_its_own_at_most_the_jobs_given_at_once(self):
        files = {f"test_{letter}": {"Case": ["pass"]} for letter in "abcdef"}
        for jobs in (None, 2, 1):
            with self.subTest(jobs=jobs):
                code, output, ran = self.suite(files, jobs)
                self.assertEqual(code, 0, output)
                self.assertEqual(sorted(line.split()[0] for line in ran), [f"{name}.Case.test_it" for name in files])
                self.assertEqual(len({line.split()[1] for line in ran}), 6, f"two files shared a process: {ran}")
                self.assertLessEqual(max(int(line.split()[2]) for line in ran), jobs or 8, ran)
                self.assertIn(f"\ngate: 6 test files ran, at most {jobs or 8} at once\n", output)

    def test_two_slow_files_run_beside_each_other_at_eight_and_never_at_one(self):
        files = {"test_a": {"Slow": (["pass"], "test_b.Slow.test_it")},
                 "test_b": {"Slow": (["pass"], "test_a.Slow.test_it")}}
        for jobs, wait, seen in ((None, 30, "met"), (8, 30, "met"), (1, 1, "alone")):
            with self.subTest(jobs=jobs):
                code, output, ran = self.suite(files, jobs, wait)
                self.assertEqual(code, 0, output)
                self.assertEqual(sorted(line for line in ran if line.startswith("meet ")),
                                 [f"meet test_a.Slow.test_it {seen}", f"meet test_b.Slow.test_it {seen}"], output)

    def test_each_files_output_is_whole_in_name_order_and_a_failing_files_traceback_ends_it(self):
        files = {f"test_{letter}": {"Case": ["fail" if letter == "b" else "pass"]} for letter in "abcd"}
        for jobs in (None, 1):
            with self.subTest(jobs=jobs):
                code, output, ran = self.suite(files, jobs)
                self.assertEqual(code, 1, output)
                self.assertEqual(sorted(line.split()[0] for line in ran), [f"{name}.Case.test_it" for name in files])
                said = re.findall(r"^(test_\w)\.Case\.test_it says (\d)$", output, re.M)
                self.assertEqual(said, [(f"test_{letter}", str(n)) for letter in "acdb" for n in range(3)], output)
                tail = output.splitlines()[-40:]  # all the door keeps of a failed gate (ceo_door.py)
                self.assertIn("Traceback (most recent call last):", tail, output)
                self.assertIn("AssertionError: test_b.Case.test_it failed on purpose", tail, output)
                self.assertEqual(tail[-1], f"gate: 4 test files ran, at most {jobs or 8} at once; these failed: test_b")

    def test_one_and_eight_at_once_give_the_same_verdict_on_the_same_runs(self):
        again = ["sleep 30", GITHUB]
        for (github, door, other), verdict in (
                ((["pass"], ["pass"], ["pass"]), (0, False, [])), ((["skip"], ["pass"], ["pass"]), (0, False, [])),
                ((["error", "pass"], ["pass"], ["pass"]), (0, True, again)),
                ((["fail", "fail"], ["pass"], ["pass"]), (1, False, again)),
                ((["fail", "skip"], ["pass"], ["pass"]), (1, False, again)),
                ((["fail", "exit"], ["pass"], ["pass"]), (1, False, again)),
                ((["fail", "pass"], ["fail", "pass"], ["pass"]), (1, False, [])),
                ((["fail", "pass"], ["pass"], ["error", "pass"]), (1, False, []))):
            for jobs in (1, 8):
                with self.subTest(github=github, door=door, other=other, jobs=jobs):
                    code, output, ran = self.suite({"test_door": {"Door": door, "RealGitHub": github},
                                                    "test_other": {"Other": other}}, jobs)
                    ids = [line if line.startswith("sleep ") else line.split()[0] for line in ran]
                    cut = ids.index("sleep 30") if "sleep 30" in ids else len(ids)
                    self.assertEqual((code, SENTENCE in output, sorted(ids[:cut]), ids[cut:]),
                                     (verdict[0], verdict[1], [DOOR, GITHUB, OTHER], verdict[2]), output)

    def test_a_file_that_ends_without_a_result_fails_the_gate_and_every_other_file_still_runs(self):
        for death in ("kill", "exit", "syntax"):
            for jobs in (None, 1):
                with self.subTest(death=death, jobs=jobs):
                    code, output, ran = self.suite({"test_a": {"Case": ["pass"]}, "test_c": {"Case": ["pass"]},
                                                    "test_b": "def broken(:\n" if death == "syntax" else
                                                    {"Case": [death]}}, jobs)
                    self.assertEqual(code, 1, output)
                    self.assertEqual(sorted(line.split()[0] for line in ran if not line.startswith("test_b.")),
                                     ["test_a.Case.test_it", "test_c.Case.test_it"], output)
                    self.assertEqual(output.splitlines()[-1],
                                     f"gate: 3 test files ran, at most {jobs or 8} at once; these failed: test_b")

    def test_jobs_given_as_anything_but_a_whole_number_of_at_least_one_is_refused_before_any_test_runs(self):
        for jobs in ("0", "x", "-2", "1.5", "4 4"):
            with self.subTest(jobs=jobs):
                code, output, ran = self.suite({"test_a": {"Case": ["pass"]}}, jobs)
                self.assertEqual((code, ran), (1, []), output)
                self.assertIn(f"gate: CEO_GATE_JOBS is {jobs}, not a whole number of at least 1\n", output)

    def test_its_test_files_start_with_sigint_at_its_default_as_the_gate_got_it(self):
        sigint = ("import signal, unittest\n\n\nclass Case(unittest.TestCase):\n    def test_it(self):\n"
                  "        self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)\n")
        code, output, _ = self.suite({"test_a": sigint, "test_b": sigint})
        self.assertEqual(code, 0, output)
        self.assertIn("\ngate: 2 test files ran, at most 8 at once\n", output)



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
