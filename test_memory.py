"""One machine's memory shares and pressure: scratch homes, fake readings, real inherited flocks."""
import contextlib
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import ceo
from test_ceo import kernel_patch, order_text
from test_tick import TickCase


class Memory(TickCase):
    def setUp(self):
        super().setUp()
        self.patch(mock.patch.dict(os.environ, self.env))
        self.ram = self.patch(kernel_patch("free_ram", return_value=64 * ceo.GB))
        self.pressure = self.patch(kernel_patch("pressure", return_value=""))
        self.sysconf = self.patch(mock.patch.object(os, "sysconf", side_effect={
            "SC_PHYS_PAGES": 4_000_000, "SC_PAGE_SIZE": 4096}.__getitem__))
        self.patch(kernel_patch("healthy", return_value=True))
        self.home = Path(self.env["CEO_HOME"])
        self.home.mkdir()
        self.budget(3)
        (self.repo / ".ceo").mkdir()
        self.commit_order("001-one.md")
        self.args = SimpleNamespace(worker=f"{sys.executable} -c pass", reviewer=None, checker=None, timeout=60)
        self.spawned, self.fds = [], set()
        self.addCleanup(self.close_shares)
        self.real_popen = subprocess.Popen
        self.child = None
        self.patch(mock.patch.object(subprocess, "Popen", self.popen))

    def patch(self, patch):
        made = patch.start()
        self.addCleanup(patch.stop)
        return made

    def budget(self, value):
        (self.home / "machine.toml").write_text(f"memory_budget_gb = {value}\n")

    def floors(self, floor):
        (self.repo / "ceo.toml").write_text(
            f'disk_floor_gb = 0\nram_floor_gb = {floor}\nrecheck_wait = 0\nnight = ""\n')

    def close_shares(self):
        for fd in self.fds:
            os.close(fd)
        self.fds.clear()

    def popen(self, cmd, *args, **kwargs):
        if list(cmd[:2]) != [sys.executable, str(ceo.KERNEL)]:
            return self.real_popen(cmd, *args, **kwargs)
        self.spawned.append((cmd, kwargs))
        if self.child:
            return self.child(cmd, **kwargs)
        # A child owns duplicates of the inherited descriptors after start() closes the tick's copies.
        self.fds.update(os.dup(fd) for fd in kwargs["pass_fds"])
        return SimpleNamespace(poll=lambda: None)

    def start(self, kind="run", slug="001-one", repo=None, planned=False):
        repo = repo or self.repo
        order = repo / "orders" / f"{slug}.md" if kind in ("run", "review") else None
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ceo.start(repo, kind, order, self.args, {}, set(), slug, planned=planned)
        return out.getvalue()

    def shares(self):
        return sorted(int(path.read_text()) for path in (self.home / "memory").iterdir() if ceo.held(path))

    def test_two_projects_draw_one_budget_and_returning_shares_allows_the_next_start(self):
        self.budget(2)
        other = self.tmp / "other"
        (other / "orders").mkdir(parents=True)
        (other / ".ceo").mkdir()
        (other / "ceo.toml").write_text('disk_floor_gb = 0\nram_floor_gb = 0\nnight = ""\n'
                                        'memory_budget_gb = 100\n')
        (other / "orders" / "001-one.md").write_text(order_text("test -f built"))
        ceo.git("init", "-q", "-b", "main", cwd=other)
        ceo.git("add", "orders", "ceo.toml", cwd=other)
        ceo.git("commit", "-qm", "an order", cwd=other)
        self.commit_order("002-two.md")
        for repo in (self.repo, other):
            ceo.admit(repo, self.args.worker)
            self.assertIn("ceo: started the run", self.start(repo=repo))
        self.assertEqual(self.shares(), [1, 1])
        before = (self.repo / ".ceo" / "tick.log").read_bytes()
        said = self.start(slug="002-two")
        self.assertEqual(said, "ceo: not started: the run of orders/002-two.md: the machine's memory "
                         "budget is spent: its agents hold 2 of 2 GB (memory_budget_gb in "
                         "$CEO_HOME/machine.toml), and this run needs 1.\n")
        self.assertEqual(len(self.spawned), 2)
        self.assertEqual((self.repo / ".ceo" / "tick.log").read_bytes(), before)
        self.assertEqual(ceo.agents(self.repo), [])
        self.assertFalse((self.repo / ".ceo" / "locks").exists())
        os.close(self.fds.pop())
        self.assertIn("ceo: started the run", self.start(slug="002-two"))
        self.assertEqual(len(self.spawned), 3)
        self.assertEqual(self.shares(), [1, 1])

    def test_every_agent_kind_draws_but_the_door_draws_nothing(self):
        self.budget(1)
        self.start()
        for kind in ("review", "night"):
            with self.subTest(kind=kind):
                self.assertIn("the machine's memory budget is spent", self.start(kind, slug="" if
                              kind == "night" else "001-one"))
        self.assertEqual(len(self.spawned), 1)
        with kernel_patch("draw", side_effect=AssertionError("the door must not draw")):
            self.assertEqual(self.start("door", slug=""), "ceo: started the door\n")
        self.assertEqual(self.spawned[-1][1]["pass_fds"], [])
        for kind in ("review", "night"):
            with self.subTest(kind=kind):
                self.close_shares()
                self.assertIn(f"ceo: started the {kind}", self.start(kind, slug="" if
                              kind == "night" else "001-one"))
                self.assertEqual(self.shares(), [1])

    def test_spins_draw_one_share_each_and_mocks_draw_only_one(self):
        for section, expected in (("Spins", 3), ("Mocks", 1)):
            with self.subTest(section=section):
                self.commit_order("001-one.md", order_text("test -f built") +
                                  f"\n## {section}\n- One\n- Two\n- Three\n\n## Preview\npage.html\n")
                self.assertIn("ceo: started the run", self.start())
                self.assertEqual(self.shares(), [expected])
                self.close_shares()
        self.budget(2)
        self.commit_order("001-one.md", order_text("test -f built") +
                          "\n## Spins\n- One\n- Two\n- Three\n\n## Preview\npage.html\n")
        self.assertIn("and this run needs 3", self.start())
        self.assertEqual(len(self.spawned), 2)
        args = SimpleNamespace(order=str(self.repo / "orders" / "001-one.md"), source=None,
                               worker=self.args.worker)
        with self.assertRaisesRegex(ceo.Refused, "and this run needs 3"):
            ceo.run(args, b"")
        self.assertFalse((self.repo / ".ceo" / "runs").exists())

    def test_the_tick_child_uses_its_inherited_share_without_deciding_again(self):
        # Execute a real child through start()'s Popen call and pass_fds, replacing only its program.
        code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import ceo
from test_ceo import kernel_patch
from unittest import mock
with kernel_patch('settings', side_effect=AssertionError('read settings twice')), \
     kernel_patch('free_ram', side_effect=AssertionError('read RAM twice')), \
     kernel_patch('pressure', side_effect=AssertionError('read pressure twice')), \
     mock.patch.object(ceo.os, 'sysconf', side_effect=AssertionError('read capacity twice')):
    assert ceo.draw(Path.cwd(), 'run', '001-one') is None
print('inherited', flush=True)
sys.stdin.read()
"""
        children = []
        def child(cmd, **kwargs):
            proc = self.real_popen([sys.executable, "-B", "-c", code, str(ceo.KERNEL.parent)],
                                   cwd=kwargs["cwd"], pass_fds=kwargs["pass_fds"], env=self.env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True)
            children.append(proc)
            return proc
        self.child = child
        self.budget(1)
        try:
            self.assertIn("ceo: started the run", self.start())
            proc = children[0]
            self.assertEqual(proc.stdout.readline(), "inherited\n")
            self.assertEqual(self.shares(), [1])
            with self.assertRaisesRegex(ceo.Refused, "the machine's memory budget is spent"):
                ceo.draw(self.repo, "run", "by-hand")
            proc.kill()  # even a crash returns the inherited share
            _, err = proc.communicate(timeout=10)
            self.assertEqual(err, "")
            self.assertEqual(self.shares(), [])
            share = ceo.draw(self.repo, "run", "by-hand")
            self.assertIsNotNone(share)
            os.close(share)
        finally:
            for proc in children:
                if proc.poll() is None:
                    proc.kill()
                proc.communicate(timeout=10)

    def test_pressure_refuses_what_free_ram_allows_and_zero_floor_skips_pressure(self):
        warn = "macOS memory pressure is warn"
        for floor, pressure, starts in ((4, warn, False), (4, "", True), (0, warn, True)):
            with self.subTest(floor=floor, pressure=pressure):
                self.close_shares()
                self.floors(floor)
                self.pressure.reset_mock()
                self.pressure.return_value = pressure
                self.spawned.clear()
                ceo.admit(self.repo, self.args.worker)
                said = self.start()
                if starts:
                    self.assertIn("ceo: started the run", said)
                    self.assertEqual(len(self.spawned), 1)
                    share = ceo.draw(self.repo, "run", "by-hand")
                    os.close(share)
                else:
                    self.assertEqual(said, "ceo: not started: the run of orders/001-one.md: "
                                     f"the machine is swapping: {warn}.\n")
                    self.assertEqual(self.spawned, [])
                    self.assertFalse((self.repo / ".ceo" / "tick.log").exists())
                    with self.assertRaisesRegex(ceo.Refused, f"the machine is swapping: {warn}"):
                        ceo.draw(self.repo, "run", "by-hand")
                if floor == 0:
                    self.pressure.assert_not_called()
                else:
                    self.assertEqual(self.pressure.call_count, 2)

    def test_default_budget_is_the_machines_ram(self):
        (self.home / "machine.toml").unlink()
        with self.assertRaisesRegex(ceo.Refused, "hold 0 of 16.384 GB"):
            ceo.draw(self.repo, "run", "too-many", 17)
        share = ceo.draw(self.repo, "run", "one")
        self.assertIsNotNone(share)
        os.close(share)
        self.sysconf.assert_has_calls([mock.call("SC_PHYS_PAGES"), mock.call("SC_PAGE_SIZE")] * 2)

    def test_a_nonnumeric_budget_is_refused_before_spawning(self):
        self.budget('"many"')
        message = "memory_budget_gb in $CEO_HOME/machine.toml is not a number"
        self.assertEqual(self.start(), f"ceo: not started: the run of orders/001-one.md: {message}.\n")
        self.assertEqual(self.spawned, [])
        with self.assertRaises(ceo.Refused) as caught:
            ceo.draw(self.repo, "run", "by-hand")
        self.assertEqual(str(caught.exception), message)

    def test_a_held_plan_start_lock_returns_the_unused_memory_share(self):
        launch = ceo.claim(self.repo / ".ceo" / "plan-starts" / "001-one")
        try:
            self.assertEqual(self.start(planned=True), "")
            self.assertEqual(self.spawned, [])
            self.assertEqual(self.shares(), [])
        finally:
            os.close(launch)
        self.assertIn("ceo: started the run", self.start(planned=True))
        self.assertEqual(len(self.spawned[-1][1]["pass_fds"]), 2)
        self.assertEqual(self.shares(), [1])


class Pressure(unittest.TestCase):
    def test_macos_uses_the_system_pressure_level(self):
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(subprocess, "run") as run:
            for level, why in ((1, ""), (2, "macOS memory pressure is warn"),
                               (4, "macOS memory pressure is critical")):
                with self.subTest(level=level):
                    run.return_value = SimpleNamespace(stdout=f"{level}\n")
                    self.assertEqual(ceo.pressure(), why)
                    run.assert_called_with(["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                                           capture_output=True, text=True)

    def test_linux_uses_some_avg10_and_tolerates_a_missing_reading(self):
        with mock.patch.object(sys, "platform", "linux"), mock.patch.object(Path, "read_text",
                                                                           autospec=True) as read:
            for value in (0, 9.99, 10, 12.5):
                with self.subTest(value=value):
                    read.return_value = f"some avg10={value:.2f} avg60=0.00 avg300=0.00 total=0\n"
                    why = f"some task waited on memory {value:g} % of the last 10 s" if value >= 10 else ""
                    self.assertEqual(ceo.pressure(), why)
                    read.assert_called_with(Path("/proc/pressure/memory"))
            read.side_effect = FileNotFoundError
            self.assertEqual(ceo.pressure(), "")


if __name__ == "__main__":
    unittest.main()
