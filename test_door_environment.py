"""The real door checks the fallback Python's installed metadata before blaming an order."""
import os
import shlex
import subprocess
import venv
from pathlib import Path

from test_door import DoorCase


class DoorPython(DoorCase):
    def fallback(self, version):
        root = self.tmp / "fallback"
        venv.EnvBuilder(with_pip=False, symlinks=True).create(root)
        python = root / "bin/python3"
        self.site = Path(subprocess.check_output(
            [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            text=True).strip())
        self.distribution(version)
        self.env["PATH"] = f"{root / 'bin'}{os.pathsep}{self.env['PATH']}"
        for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            self.env.pop(key, None)

    def distribution(self, version):
        info = self.site / "playwright-1.63.0.dist-info"
        if version is not None:
            info.mkdir(exist_ok=True)
            (info / "METADATA").write_text(
                f"Metadata-Version: 2.1\nName: playwright\nVersion: {version}\n")

    def prepare(self, pins="playwright==1.63.0\n", door_exit=0):
        slug = "090-python"
        (self.repo / "requirements-dev.txt").write_text(pins)
        self.gate()
        self.checked = self.tmp / "done-when-ran"
        block = (f'test -f {slug}.txt || exit 1\ncase "$PWD" in */.ceo/door)\n'
                 f'  touch {shlex.quote(str(self.checked))}; exit {door_exit};;\nesac')
        self.reviewed(slug, done_when=block)
        return slug

    def deferred(self, slug, installed):
        result, review = self.read(slug), self.read(slug, "review.json")
        main = self.main()
        for _ in range(3):
            door = self.door()
            self.assertEqual(door.returncode, 2, door.stdout + door.stderr)
            self.assertIn("fallback Python", door.stdout)
            self.assertEqual(self.read(slug), result)
            self.assertEqual(self.read(slug, "review.json"), review)
            self.assertEqual(self.main(), main)
            self.assertFalse(self.checked.exists(), "done-when ran with the wrong dependency")
            self.assertFalse(self.marks.exists(), "gate ran with the wrong dependency")
            self.assertFalse((self.repo / ".ceo/door").exists())
        log = (self.run_dir(slug) / "door.log").read_text()
        self.assertIn(f"playwright {installed}", log)
        self.assertIn("requirements-dev.txt pins 1.63.0", log)
        self.assertEqual(self.server()[0]["state"], "OPEN")

    def test_stale_fallback_waits_without_parking_and_merges_after_the_environment_is_fixed(self):
        slug = self.prepare()
        self.fallback("1.48.0")
        self.deferred(slug, "1.48.0")
        self.distribution("1.63.0")
        door = self.door()
        self.assertEqual(door.returncode, 0, door.stdout + door.stderr)
        self.assertTrue(self.read(slug).get("cleared"))
        self.assertTrue(self.checked.exists())
        self.assertTrue(self.marks.exists())
        self.assertEqual(self.server()[0]["state"], "MERGED")

    def test_a_missing_pin_is_diagnosed_before_running_the_checks(self):
        slug = self.prepare("# video dependency\nplaywright==1.63.0  # fixture\n")
        self.fallback(None)
        self.deferred(slug, "not installed")

    def test_unpinned_requirements_do_not_block_a_working_order(self):
        slug = self.prepare("# playwright==1.63.0\nplaywright>=1.48.0\n")
        self.fallback("1.48.0")
        door = self.door()
        self.assertEqual(door.returncode, 0, door.stdout + door.stderr)
        self.assertTrue(self.read(slug).get("cleared"))
        self.assertTrue(self.checked.exists())

    def test_an_aligned_fallback_still_records_an_actual_order_failure(self):
        slug = self.prepare(door_exit=1)
        self.fallback("1.63.0")
        door = self.door()
        self.assertEqual(door.returncode, 2, door.stdout + door.stderr)
        failure, = self.read(slug)["door_failures"]
        self.assertEqual((failure["what"], failure["exit"]), ("its done-when block", 1))
        self.assertTrue(self.checked.exists())
        self.assertFalse(self.marks.exists())
