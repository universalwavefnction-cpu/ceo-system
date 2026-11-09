"""The tick holds the machine awake (order 23) end to end: a real tick in a scratch repository, the real
caffeinate of macOS in the real process table, the real status. Only the model is replaced, by the stub
of test_tick, and GitHub, by the fake gh of test_door. Elsewhere than macOS the tick runs as it runs there:
sys.platform is set before the kernel loads, which on Linux itself changes nothing."""
import json
import os
import signal
import subprocess
import sys
import unittest

from test_desk import DeskCase
from test_tick import CEO

MAC = sys.platform == "darwin"
LINE = r"ceo: caffeinate cannot start \(No such file or directory\), so this tick does not hold the machine awake"
# The kernel as it runs off macOS: its arguments follow, the first of them the path of ceo.py.
ELSEWHERE = ("import os, runpy, sys; sys.platform = 'linux'; sys.path.insert(0, os.path.dirname(sys.argv[1])); sys.argv = sys.argv[1:]; "
             "runpy.run_path(sys.argv[0], run_name='__main__')")


def table():
    """(pid, ppid, state, command words) of each process; a zombie has exited and is left out."""
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,stat=,command="], capture_output=True,
                         errors="replace", check=True).stdout
    return [(int(row[0]), int(row[1]), row[3:]) for row in map(str.split, out.splitlines())
            if not row[2].startswith("Z")]


def hold_of(tick):
    """The pid of each live child of tick that runs `caffeinate -i -s -w <tick>`."""
    return [pid for pid, ppid, words in table()
            if ppid == tick and words == ["caffeinate", "-i", "-s", "-w", str(tick)]]


def lives(hold, tick):
    """True while the process hold still runs the command it ran as the hold of tick."""
    return any(pid == hold and words == ["caffeinate", "-i", "-s", "-w", str(tick)]
               for pid, _, words in table())


class Awake(DeskCase):
    def ticking(self, *kernel):
        """A tick with --every 1 in a session of its own, writing to a log; stopped when the test ends."""
        log = open(self.tmp / "tick.log", "wb")
        self.addCleanup(log.close)
        proc = subprocess.Popen([*(kernel or (sys.executable, str(CEO))), "tick", "--every", "1"],
                                cwd=self.repo, env=self.env, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
        self.addCleanup(self.stop, proc)
        return proc

    def stop(self, proc):
        if proc.poll() is None:  # its group is surely this test's while it lives: its hold is in it too
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=60)

    def held(self, tick):
        """The pid of the hold of tick, once there is one, within 5 seconds."""
        self.wait_for(lambda: hold_of(tick.pid), "the tick started its hold", timeout=5)
        [hold] = hold_of(tick.pid)
        # Stopped by its own pid if it outlives its tick, as a broken hold would: never left holding.
        self.addCleanup(lambda: lives(hold, tick.pid) and os.kill(hold, signal.SIGTERM))
        return hold

    def status(self, *args):
        done = self.ceo("status", *args)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def awake(self):
        return json.loads(self.status("--json"))["awake"]

    def path_without(self, name):
        """This test's PATH with each folder that holds name replaced by one of links to all else in it."""
        folders = self.env["PATH"].split(os.pathsep)
        for number, folder in enumerate(folders):
            if os.path.exists(os.path.join(folder, name)):
                folders[number] = str(self.tmp / f"without-{number}")
                os.mkdir(folders[number])
                for entry in os.listdir(folder):
                    if entry != name:
                        os.symlink(os.path.join(folder, entry), os.path.join(folders[number], entry))
        return os.pathsep.join(folders)

    @unittest.skipUnless(MAC, "the hold is macOS's caffeinate")
    def test_a_tick_holds_the_machine_awake_while_it_lives_and_the_hold_ends_with_it(self):
        tick = self.ticking()
        hold = self.held(tick)
        self.wait_for(self.awake, "status --json says awake: true", timeout=5)
        self.assertEqual(self.status().strip(), "repo · nothing running · holding the machine awake")
        tick.send_signal(signal.SIGTERM)  # the tick's own pid, and nothing else
        self.wait_for(lambda: not lives(hold, tick.pid), "the hold ended with its tick", timeout=5)
        self.assertEqual(tick.wait(timeout=30), -signal.SIGTERM)
        self.assertFalse(self.awake())
        self.assertEqual(self.status().strip(), "repo · nothing running")

    @unittest.skipUnless(MAC, "only macOS has a caffeinate to miss")
    def test_a_path_without_caffeinate_lets_the_tick_run_and_says_so_in_one_line(self):
        done = self.ceo("tick", env={**self.env, "PATH": self.path_without("caffeinate")})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(done.stderr, rf"^{LINE}\n$")
        self.assertNotIn("caffeinate", done.stdout)
        self.assertIn("Last tick: ", (self.repo / "STATUS.md").read_text())  # the pass ran whole

    def test_elsewhere_than_macos_no_hold_starts_and_awake_is_false(self):
        tick = self.ticking(sys.executable, "-c", ELSEWHERE, str(CEO))
        passed = lambda: "Last tick: " in (self.repo / "STATUS.md").read_text()
        self.wait_for(lambda: (self.repo / "STATUS.md").exists() and passed(), "the first pass ran")
        self.assertIsNone(tick.poll())
        self.assertEqual(hold_of(tick.pid), [])  # it would have started before the first pass
        self.assertFalse(self.awake())
        self.assertEqual(self.status().strip(), "repo · nothing running")
        self.assertNotIn(b"caffeinate", (self.tmp / "tick.log").read_bytes())


if __name__ == "__main__":
    unittest.main()
