import os, shlex, shutil, signal, subprocess, sys, unittest
import ceo
from test_tick import CEO, TickCase
SAID = "the kernel's code changed (ceo.py), so this tick restarts"
# A git that, once armed, moves one kernel file aside at a pass's first call, as a pull caught between git's
# unlink and its write leaves it, and puts it back at the next pass's first call.
SHIM = """#!/bin/sh
if [ "$1 $2 $3" = "remote get-url origin" ]; then
  if [ -e {arm} ]; then mv {file} {file}.away && mv {arm} {arm}.done
  elif [ -e {arm}.done ] && [ -e {file}.away ]; then mv {file}.away {file} && touch {back}; fi
fi
exec {git} "$@"
"""
class ProjectTick(TickCase):
    def setUp(self):
        super().setUp()
        self.kernel = self.tmp / "kernel"  # a copy of the kernel's code, in a folder that is not the project's
        self.kernel.mkdir()
        for path in CEO.parent.glob("*.py"):
            if not path.name.startswith("test_"):
                shutil.copyfile(path, self.kernel / path.name)
        for folder in ("routines", "tools"):
            shutil.copytree(CEO.parent / folder, self.kernel / folder, ignore=shutil.ignore_patterns("__pycache__"))
        self.env.pop("PYTHONPATH", None)  # the copy's own modules only, never the checkout's
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.git("add", "-A")
        self.git("commit", "-qm", "a project")
    def start(self, *args, env=None):
        out = self.tmp / "tick.out"
        with out.open("w") as log:
            proc = subprocess.Popen([sys.executable, str(self.kernel / "ceo.py"), "tick", "--every", "1", *args],
                                    cwd=self.repo, env=env or self.env, stdout=log, stderr=log,
                                    start_new_session=True)
        self.addCleanup(self.stop, proc)
        return proc, out
    def stop(self, proc):
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)
    def passes(self, count=2):
        status = self.repo / "STATUS.md"
        last = lambda: next((line for line in status.read_text().splitlines() if line.startswith("Last tick:")), "") \
            if status.exists() else ""
        for _ in range(count):
            previous = last()
            self.wait_for(lambda: last() != previous, "the project's tick finished another pass")
    def restarted(self):
        return [row for row in ceo.events(self.repo)[0] if row["event"] == "restarted"]
    def change(self, line=b"# a harmless comment\n"):
        path = self.kernel / "ceo.py"
        path.write_bytes(path.read_bytes() + line)
    def test_a_projects_tick_restarts_on_the_kernels_new_code_and_keeps_ticking_on_it(self):
        proc, out = self.start()
        self.passes()
        self.change()
        self.wait_for(self.restarted, "the project's tick restarted on the kernel's new code")
        self.passes(3)
        self.assertEqual([row["sentence"] for row in self.restarted()], [SAID], "its new image ran old code")
        self.assertEqual((self.repo / ".ceo" / "tick.lock").read_text().strip(), str(proc.pid))
        self.change(b"# another harmless comment\n")
        self.wait_for(lambda: len(self.restarted()) == 2, "the new image watched the kernel's code too")
        self.passes(3)
        self.assertEqual(len(self.restarted()), 2)
        self.assertEqual(out.read_text().count(f"ceo: {SAID}\n"), 2)
        self.assertNotIn("Traceback", out.read_text())
        self.assertIsNone(proc.poll(), "the tick exited")
        second = subprocess.run([sys.executable, str(self.kernel / "ceo.py"), "tick"], cwd=self.repo, env=self.env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(second.returncode, 2, second.stderr)
        self.assertIn("another tick is running", second.stderr)
    def test_a_projects_tick_waits_for_its_run_to_end_before_it_restarts(self):
        slug = "001-slow"
        self.commit_order(f"{slug}.md")
        proc, out = self.start("--worker", self.model("wait", str(self.tmp / "pid"), str(self.release)),
                               "--reviewer", self.model("clean"))
        self.wait_for((self.tmp / f"pid-{slug}").exists, "the project's worker started")
        self.change()
        self.passes(4)
        self.assertEqual(self.restarted(), [], "the tick restarted while its run was at work")
        self.assertIsNone(proc.poll())
        self.release.touch()
        self.wait_for(self.restarted, "the tick restarted once nothing of the project was at work", timeout=120)
        self.assertEqual(self.read(slug)["state"], "done")
        self.wait_for(lambda: (self.run_dir(slug) / "review.json").exists(), "the review ended", timeout=120)
        self.passes(3)
        self.assertEqual(len(self.restarted()), 1)
        self.assertIsNone(proc.poll(), out.read_text())
    def test_new_kernel_code_that_does_not_compile_never_restarts_or_stops_a_projects_tick(self):
        proc, out = self.start()
        self.passes()
        original = (self.kernel / "ceo.py").read_bytes()
        self.change(b"def broken(:\n")
        told = lambda: out.read_text().count("ceo: the kernel's new code does not compile (ceo.py: ")
        self.wait_for(lambda: told() == 1, "the project's tick said the new code does not compile")
        self.passes(3)
        self.assertEqual((told(), self.restarted(), proc.poll()), (1, [], None), out.read_text())
        (self.kernel / "ceo.py").write_bytes(original + b"# fixed\n")
        self.wait_for(self.restarted, "the fix restarted the tick")
        self.passes()
        self.assertEqual((told(), len(self.restarted()), proc.poll()), (1, 1, None), out.read_text())
    def test_a_kernel_file_caught_mid_write_for_one_pass_never_restarts_or_stops_a_projects_tick(self):
        shim, arm, back = self.tmp / "shim", self.tmp / "arm", self.tmp / "back"
        shim.mkdir()
        (shim / "git").write_text(SHIM.format(arm=shlex.quote(str(arm)), back=shlex.quote(str(back)),
                                              file=shlex.quote(str(self.kernel / "ceo_base.py")),
                                              git=shlex.quote(shutil.which("git"))))
        (shim / "git").chmod(0o755)
        proc, out = self.start(env={**self.env, "PATH": f"{shim}{os.pathsep}{self.env['PATH']}"})
        self.passes()
        arm.touch()
        self.wait_for(lambda: back.exists() or proc.poll() is not None, "the next pass put the kernel file back")
        self.assertIsNone(proc.poll(), out.read_text())
        self.passes(3)
        self.assertEqual(self.restarted(), [], out.read_text())
        self.assertIsNone(proc.poll(), out.read_text())
        self.assertNotIn("Traceback", out.read_text())
    def test_a_one_shot_projects_tick_never_restarts(self):
        shim, mark = self.tmp / "shim", self.tmp / "changed"
        shim.mkdir()
        (shim / "git").write_text(
            f'#!/bin/sh\nif [ "$1 $2 $3" = "remote get-url origin" ] && [ ! -e {shlex.quote(str(mark))} ]; then\n'
            f"  printf '# changed during the pass\\n' >> {shlex.quote(str(self.kernel / 'ceo.py'))}\n"
            f"  touch {shlex.quote(str(mark))}\nfi\nexec {shlex.quote(shutil.which('git'))} \"$@\"\n")
        (shim / "git").chmod(0o755)
        once = subprocess.run([sys.executable, str(self.kernel / "ceo.py"), "tick"], cwd=self.repo,
                              env={**self.env, "PATH": f"{shim}{os.pathsep}{self.env['PATH']}"},
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(once.returncode, 0, once.stderr)
        self.assertTrue(mark.exists(), "the kernel's code never changed during the pass")
        self.assertNotIn("so this tick restarts", once.stdout)
        self.assertEqual(self.restarted(), [])
if __name__ == "__main__":
    unittest.main()
