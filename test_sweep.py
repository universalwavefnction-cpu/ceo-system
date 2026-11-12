"""The floors, the quota floor and the daily sweep end to end: real git with a local bare repository as
origin, real processes, real files and the machine's real free disk and RAM. Only the model is
replaced, by the stub of test_door, and GitHub, by its fake gh."""
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ceo import claim, read_json
from test_ceo import alive, order_text
from test_door import DoorCase
from test_tick import CEO, TickCase

DAY = 86400
FREE = r"disk \d+\.\d GB free \(floor {disk} GB\), RAM \d+\.\d GB free \(floor {ram} GB\)"


def age(path, days=3):
    """Make path and everything in it look untouched for days."""
    when = time.time() - days * DAY
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            os.utime(os.path.join(root, name), (when, when), follow_symlinks=False)
    os.utime(path, (when, when), follow_symlinks=False)


def du(*paths):
    """Bytes on disk, as du counts them."""
    out = subprocess.run(["du", "-sk", *map(str, paths)], capture_output=True, text=True, check=True)
    return sum(int(line.split()[0]) * 1024 for line in out.stdout.splitlines())


class Floors(TickCase):
    def floors(self, text):
        # No night, as in TickCase: its key goes before any [table], where TOML keeps it at the top.
        at = text.find("\n[") + 1 or len(text)
        (self.repo / "ceo.toml").write_text(text[:at] + 'night = ""\ncleaner = ""\n' + text[at:])

    def above(self):
        """A floor in GB above the real free space of the disk the repository is on."""
        return int(shutil.disk_usage(self.repo).free / 1e9) + 1000

    def made(self, slug):
        return [path for path in (self.run_dir(slug), self.repo / ".ceo" / "wt" / slug)
                if path.exists()] + self.git("branch", "--list", f"order/{slug}").split()

    def tick_log(self):
        log = self.repo / ".ceo" / "tick.log"
        return log.read_text() if log.exists() else ""

    def test_below_the_disk_floor_a_run_creates_nothing_and_the_tick_starts_it_once_above(self):
        self.commit_order("070-disk.md")  # filed: each tick finds it eligible
        floor = self.above()
        self.floors(f"disk_floor_gb = {floor}\nram_floor_gb = 0\n")
        run = self.ceo("run", "orders/070-disk.md", "--worker", self.model("build"))
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertRegex(run.stderr, r"^ceo: refused: below the disk floor: "
                         + FREE.format(disk=floor, ram=0) + r"\.\n$")
        free = float(re.search(r"disk (\d+\.\d) GB free", run.stderr)[1])
        self.assertAlmostEqual(free, shutil.disk_usage(self.repo).free / 1e9, delta=2)
        self.assertFalse((self.repo / ".ceo").exists(), "something was created")
        self.assertEqual(self.made("070-disk"), [])
        work = ("--worker", self.model("build"))
        # The tick admits every run start itself: below the floor it says why and spawns no child.
        said = self.tick(*work)
        self.assertRegex(said, r"(?m)^ceo: not started: orders/070-disk\.md: below the disk floor: "
                         + FREE.format(disk=floor, ram=0) + r"\.$")
        self.assertNotIn("started the run", said)
        self.assertEqual(self.made("070-disk"), [])
        ready = self.repo / ".ceo" / "ready" / "070-disk.json"  # its readiness record keeps why
        self.assertEqual((read_json(ready)["reason"][:21], sorted(read_json(ready))),
                         ("below the disk floor:", ["reason", "since"]))  # and no count
        self.floors("disk_floor_gb = 0\nram_floor_gb = 0\n")
        self.assertIn("started the run of orders/070-disk.md", self.tick(*work))  # the next pass
        self.wait_for((self.run_dir("070-disk") / "result.json").exists, "the run ended")
        self.assertEqual(read_json(ready)["reason"], "")  # its worker began: why it did not start went
        self.assertEqual(self.read("070-disk")["state"], "done")

    def test_below_the_ram_floor_nothing_starts_and_each_line_names_the_floors_by_default(self):
        self.commit_order("071-ram.md")
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")  # all of it, macOS or Linux
        for text, floor in (("ram_floor_gb = 100000\n", FREE.format(disk=20, ram=100000)),
                            (f"disk_floor_gb = {self.above()}\n", FREE.format(disk=self.above(), ram=4))):
            with self.subTest(text):
                self.floors(text)  # the floor not named is at its default
                run = self.ceo("run", "orders/071-ram.md", "--worker", self.model("build"))
                self.assertEqual(run.returncode, 2, run.stderr)
                self.assertRegex(run.stderr, rf"^ceo: refused: below the (disk|RAM) floor: {floor}\.\n$")
                ram = float(re.search(r"RAM (\d+\.\d) GB free", run.stderr)[1])
                self.assertTrue(0 < ram <= memory / 1e9, ram)
                self.assertFalse((self.repo / ".ceo").exists(), "something was created")

    def test_below_a_floor_a_review_starts_nothing_and_the_tick_reviews_once_above(self):
        self.commit_order("072-review.md")
        self.assertEqual(self.ceo("run", "orders/072-review.md", "--worker", self.model("build"))
                         .returncode, 0)
        self.floors("ram_floor_gb = 100000\ndisk_floor_gb = 0\n")
        review = self.ceo("review", "orders/072-review.md", "--reviewer", self.model("clean"))
        self.assertEqual(review.returncode, 2, review.stderr)
        self.assertRegex(review.stderr, r"^ceo: refused: below the RAM floor: [^\n]+\.\n$")
        made = [self.run_dir("072-review") / "review.json", self.run_dir("072-review") / "review.log",
                self.repo / ".ceo" / "review" / "072-review",
                self.repo / ".ceo" / "locks" / "072-review.review"]
        self.assertEqual([path for path in made if path.exists()], [])
        work = ("--reviewer", self.model("clean"))
        # Pressure can refuse in the tick itself; the disk floor exercises the review's refusal.
        self.floors(f"disk_floor_gb = {self.above()}\nram_floor_gb = 0\n")
        self.assertIn("started the review of orders/072-review.md", self.tick(*work))
        self.wait_for(lambda: "below the disk floor" in self.tick_log(), "the review refused")
        self.assertFalse(made[0].exists())
        self.floors("ram_floor_gb = 0\ndisk_floor_gb = 0\n")
        self.tick_until(made[0].exists, "the review was made", *work)
        self.assertEqual(self.read("072-review", "review.json")["verdict"], "survives")

    def test_a_run_alive_is_never_stopped_by_a_floor(self):
        self.commit_order("073-alive.md")
        ceo = self.ceo_bg("run", "orders/073-alive.md", "--worker",
                          self.model("wait", str(self.tmp / "pid"), str(self.release)))
        self.wait_for((self.tmp / "pid-073-alive").exists, "the worker started")
        worker = int((self.tmp / "pid-073-alive").read_text())
        self.floors(f"disk_floor_gb = {self.above()}\nram_floor_gb = 100000\n")
        self.tick()
        self.assertTrue(alive(worker), "a floor stopped a run alive")
        self.release.touch()
        ceo.communicate(timeout=60)
        self.assertEqual(ceo.returncode, 0)
        self.assertEqual(self.read("073-alive")["state"], "done")

    def test_a_quota_under_its_floor_starts_nothing_and_one_above_starts_the_run(self):
        self.commit_order("074-quota.md")
        for left, code in (("3", 2), ("50", 0)):
            with self.subTest(left):
                self.floors(f'disk_floor_gb = 0\nram_floor_gb = 0\nquota_cmd.claude = "echo {left}"\n')
                run = self.ceo("run", "orders/074-quota.md", "--worker", self.model("build"))
                self.assertEqual(run.returncode, code, run.stderr)
        self.assertEqual(self.read("074-quota")["state"], "done")

    def test_a_quota_of_3_percent_refuses_in_one_line_and_creates_nothing(self):
        self.commit_order("075-low.md")
        self.floors('disk_floor_gb = 0\nram_floor_gb = 0\nquota_cmd.claude = "echo 3"\n')
        run = self.ceo("run", "orders/075-low.md", "--worker", self.model("build"))
        self.assertEqual((run.returncode, run.stderr),
                         (2, "ceo: refused: the claude quota is at 3 %, under its floor of 5 %.\n"))
        self.assertFalse((self.repo / ".ceo").exists(), "something was created")

    def test_each_vendor_has_its_own_quota_and_a_command_is_of_the_vendor_of_its_program(self):
        self.commit_order("076-vendor.md")
        program = Path(sys.executable).name  # the stub worker's program
        conf = ("disk_floor_gb = 0\nram_floor_gb = 0\nquota_floor_pct = 40  # the owner's own floor\n"
                "[quota_cmd]\nclaude = \"echo {claude}\"\nopenai = 'echo {openai}'\n"
                f"[vendor]\n{program} = \"openai\"\n")
        self.floors(conf.format(claude=90, openai=30))
        run = self.ceo("run", "orders/076-vendor.md", "--worker", self.model("build"))
        self.assertEqual((run.returncode, run.stderr),
                         (2, "ceo: refused: the openai quota is at 30 %, under its floor of 40 %.\n"))
        self.floors(conf.format(claude=3, openai=40))
        run = self.ceo("run", "orders/076-vendor.md", "--worker", self.model("build"))
        self.assertEqual(run.returncode, 0, run.stderr)
        # A reviewer whose program is named for no vendor is claude's, and claude is at 3 %.
        answer = '{"summary": "checked", "findings": []}'
        reviewer = shlex.join(["sh", "-c", f"printf '%s' '{answer}' > REVIEW.json"])
        review = self.ceo("review", "orders/076-vendor.md", "--reviewer", reviewer)
        self.assertEqual((review.returncode, review.stderr),
                         (2, "ceo: refused: the claude quota is at 3 %, under its floor of 40 %.\n"))
        self.floors(conf.format(claude=41, openai=3))
        review = self.ceo("review", "orders/076-vendor.md", "--reviewer", reviewer)
        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertEqual(self.read("076-vendor", "review.json")["verdict"], "survives")

    def test_a_quota_or_a_ceo_toml_that_cannot_be_read_starts_nothing(self):
        self.commit_order("077-unread.md")
        for text, said in (('quota_cmd.claude = "echo plenty"', "the claude quota cannot be read"),
                           ('quota_cmd.claude = "echo 50; exit 1"', "the claude quota cannot be read"),
                           ('quota_cmd.claude = "echo 150"', "the claude quota cannot be read"),
                           ("ram_floor_gb: 4", "ceo.toml cannot be read at line 2: ram_floor_gb: 4"),
                           ('ram_floor_gb = "4"', "a floor in ceo.toml is not a number")):
            with self.subTest(text):
                self.floors(f"disk_floor_gb = 0\n{text}\n"
                            + ("ram_floor_gb = 0\n" if text.startswith("quota_cmd.") else ""))
                run = self.ceo("run", "orders/077-unread.md", "--worker", self.model("build"))
                self.assertEqual(run.returncode, 2, run.stderr)
                self.assertRegex(run.stderr, rf"^ceo: refused: {said}[^\n]*\.\n$")
                self.assertFalse((self.repo / ".ceo").exists(), "something was created")


class SweepCase(DoorCase):
    def sweep(self, *args):
        done = self.ceo("sweep", *args)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def status(self):
        return (self.repo / "STATUS.md").read_text().splitlines()

    def sweep_line(self):
        return [line for line in self.status() if line.startswith("Sweep: ")]

    def run_done(self, name, worker="own"):
        self.commit_order(f"{name}.md", order_text(f"test -f {name}.txt"))
        run = self.ceo("run", f"orders/{name}.md", "--worker", self.model(worker))
        self.assertEqual(run.returncode, 0, run.stderr)

    def branches(self, pattern):
        return self.git("branch", "--list", "--format=%(refname:short)", pattern).split()

    def snapshot(self):
        return sorted((str(path), path.lstat().st_mtime_ns) for path in self.tmp.rglob("*"))


class Sweep(SweepCase):
    def test_a_merged_order_is_swept_two_days_after_and_never_runs_again(self):
        self.reviewed("080-merged")
        self.assertEqual(self.door().returncode, 0)
        self.assertTrue(self.settled("080-merged"))
        # The owner pulls the merge into main: it is finished, and it stays filed in orders/.
        self.git("pull", "-q", "--ff-only", "origin", "main")
        copy = self.repo / ".ceo" / "review" / "080-merged"  # the copy of a review killed in its time
        copy.mkdir(parents=True)
        (copy / "left.bin").write_bytes(os.urandom(30_000_000))
        self.assertNotIn("080-merged", self.sweep(), "a fresh one was swept")
        mine = [self.run_dir("080-merged"), copy]
        for path in mine:
            age(path)
        freed = du(*mine)
        out = self.sweep()
        self.assertIn("ceo: removed .ceo/runs/080-merged, ", out)
        self.assertIn("ceo: removed .ceo/review/080-merged, 0.03 GB", out)
        self.assertEqual([path for path in mine if path.exists()], [])
        locks = self.repo / ".ceo" / "locks"
        self.assertEqual(sorted(path.name for path in locks.glob("080-merged.*")), [])
        self.assertTrue((self.repo / ".ceo" / "swept" / "080-merged").is_file())
        self.assertRegex(self.sweep_line()[0], rf"^Sweep: \S+, freed {freed / 1e9:.2f} GB$")
        self.assertTrue(out.endswith(f"ceo: {self.sweep_line()[0]}\n"), out)
        self.assertNotIn("080-merged", self.tick("--worker", self.model("own")), "it ran again")
        self.assertFalse(self.run_dir("080-merged").exists())
        self.assertFalse((self.repo / ".ceo" / "wt" / "080-merged").exists())

    def test_a_cut_order_goes_with_its_worktree_and_branches_but_uncommitted_work_stays(self):
        for name in ("081-cut", "082-dirty", "083-fresh"):
            self.run_done(name)
            (self.repo / "orders" / f"{name}.cut").touch()  # the CEO cut it
        self.commit_order("084-never.md")
        (self.repo / "orders" / "084-never.cut").touch()  # filed, then cut before it ever ran
        self.commit_all("cut")
        wt = self.repo / ".ceo" / "wt"
        (wt / "082-dirty" / "notes.txt").write_text("the worker's, not committed\n")
        for kept in ("cut-1", "spin-2", "judge"):  # kept when cut off (order 7), a spin (order 9), another
            self.git("branch", f"order/081-cut-{kept}", "order/081-cut")
        for name in ("081-cut", "082-dirty"):
            for path in (wt / name, self.run_dir(name), self.repo / "orders" / f"{name}.cut"):
                age(path)
        dry = self.sweep("--dry-run")
        for what in (".ceo/wt/081-cut", ".ceo/runs/081-cut", "the branch order/081-cut-cut-1"):
            self.assertIn(f"ceo: would remove {what}", dry)
        self.assertTrue((wt / "081-cut").is_dir(), "the dry run removed something")
        out = self.sweep()
        self.assertFalse((wt / "081-cut").exists() or self.run_dir("081-cut").exists())
        self.assertEqual(self.branches("order/081-*"), ["order/081-cut-judge"])
        self.assertNotIn("081-cut", self.git("worktree", "list"))
        self.assertTrue((self.repo / ".ceo" / "swept" / "081-cut").is_file())
        for name in ("082-dirty", "083-fresh"):
            self.assertTrue((wt / name).is_dir() and self.run_dir(name).is_dir(), name)
            self.assertEqual(self.branches(f"order/{name}"), [f"order/{name}"])
        self.assertTrue((wt / "082-dirty" / "notes.txt").is_file())
        self.assertRegex(self.sweep_line()[0], r"^Sweep: \S+, freed \d+\.\d\d GB; kept, with uncommitted "
                                               r"changes: \.ceo/wt/082-dirty$")
        self.assertEqual([line for line in out.splitlines() if "removed" in line and "082" in line], [])
        tick = self.tick("--worker", self.model("own"))
        self.assertNotIn("084-never", tick, "a cut order started")
        self.assertNotIn("started the review", tick)  # every remaining finished run is cut
        for name in ("082-dirty", "083-fresh"):
            self.assertFalse((self.run_dir(name) / "review.log").exists(), name)
            self.assertFalse((self.run_dir(name) / "review.json").exists(), name)

    def test_a_cut_orders_failed_commit_origin_lacks_stays_until_it_is_pushed(self):
        slug = "908-unpublished"
        self.commit_order(f"{slug}.md", order_text("test -f complete"))
        run = self.ceo("run", f"orders/{slug}.md", "--worker", self.model("build"))
        result = self.read(slug)
        self.assertEqual((run.returncode, result["state"]), (1, "failed"), run.stdout + run.stderr)
        head, wt, cut = result["head"], self.repo / result["worktree"], self.repo / "orders" / f"{slug}.cut"
        self.assertEqual(self.git("show", f"{head}:built"), "built")
        remote = lambda: self.git("--git-dir", str(self.origin), "rev-list", "--all").split()
        self.assertNotIn(head, remote())
        cut.touch()
        self.commit_all("cut the failed order")
        for path in (wt, self.run_dir(slug), cut):
            age(path)
        for args in (("--dry-run",), ()):
            self.assertNotIn(slug, self.sweep(*args), args)
            self.assertTrue(wt.is_dir() and self.run_dir(slug).is_dir(), args)
            self.assertEqual((self.git("-C", str(wt), "rev-parse", "HEAD"), self.git("rev-parse", f"order/{slug}")), (head, head))
        self.assertFalse((self.repo / ".ceo" / "swept" / slug).exists())
        self.git("push", "-q", "origin", f"{head}:refs/heads/order/{slug}")
        self.assertIn(f"would remove .ceo/wt/{slug},", self.sweep("--dry-run"))
        self.sweep()
        self.assertFalse(wt.exists() or self.run_dir(slug).exists())
        self.assertEqual(self.branches(f"order/{slug}"), [])
        self.assertIn(head, remote())

    def test_a_superseded_checkout_needs_a_clean_idle_head_retained_on_origin(self):
        slug = "103-round"
        self.run_done(slug)
        wt = self.repo / ".ceo" / "wt" / slug
        record = self.run_dir(slug) / "result.json"
        before = record.read_bytes()
        age(self.repo / ".ceo")
        self.sweep()
        self.assertTrue(wt.exists(), "the active round was removed")
        self.commit_order(f"{slug}-round-2.md", order_text("true").replace(
            "\n## Goal", f"\nROUND: 2\nREPLACES: orders/{slug}.md\n\n## Goal"))
        wt.touch()
        self.sweep()
        self.assertTrue(wt.exists(), "a fresh checkout was removed")
        notes = wt / "notes.txt"
        notes.write_text("uncommitted work\n")
        self.git("config", "status.showUntrackedFiles", "no")
        age(wt)
        self.sweep()
        self.assertTrue(notes.exists(), "untracked work was removed")
        notes.unlink()
        self.git("-C", str(wt), "commit", "-qm", "local work", "--allow-empty")
        head = self.git("-C", str(wt), "rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/backup/saved", head)
        age(wt)
        self.sweep()
        self.assertTrue(wt.exists(), "only the recorded HEAD was on origin")
        self.git("push", "-q", "origin", f"order/{slug}:refs/heads/order/later")
        lock = claim(self.repo / ".ceo" / "locks" / f"{slug}.run")
        self.assertIsNotNone(lock)
        try:
            self.sweep()
            self.assertTrue(wt.exists(), "the round is still held by a process")
        finally:
            os.close(lock)
        dry = self.sweep("--dry-run")
        self.assertIn(f"would remove .ceo/wt/{slug},", dry)
        self.assertTrue(wt.exists(), "the dry run removed the checkout")
        self.sweep()
        self.assertFalse(wt.exists())
        self.assertNotIn(str(wt), self.git("worktree", "list", "--porcelain"))
        self.assertEqual(record.read_bytes(), before, "later rounds still need the execution records")
        self.assertEqual(self.git("rev-parse", f"order/{slug}"), head)

    def test_a_run_review_or_door_alive_is_never_touched(self):
        self.run_done("086-reviewed")
        review = self.ceo_bg("review", "orders/086-reviewed.md", "--reviewer",
                             self.model("wait", str(self.tmp / "pid"), str(self.tmp / "go-review")))
        self.wait_for((self.tmp / "pid-086-reviewed").exists, "the reviewer started")
        (self.repo / "orders" / "086-reviewed.cut").touch()  # cut only after its review is alive
        self.run_done("087-door")
        (self.repo / "orders" / "087-door.cut").touch()
        door = claim(self.repo / ".ceo" / "locks" / "door.lock")  # held as a door at work holds it
        everything = [self.run_dir(name) for name in ("086-reviewed", "087-door")] + [
            self.repo / ".ceo" / "wt" / name for name in ("086-reviewed", "087-door")] + [
            self.repo / "orders" / f"{name}.cut" for name in ("086-reviewed", "087-door")] + [
            self.repo / ".ceo" / "review" / "086-reviewed"]
        for path in everything:
            age(path)  # quiet for days, and alive
        try:
            self.assertEqual(self.sweep().splitlines(), [f"ceo: {self.sweep_line()[0]}"])
        finally:
            os.close(door)
        self.assertEqual([path for path in everything if not path.exists()], [])
        out = self.sweep()
        self.assertIn("removed .ceo/runs/087-door", out)
        self.assertNotIn("086-reviewed", out)
        (self.tmp / "go-review").touch()
        review.communicate(timeout=60)

    def test_records_caches_and_listed_paths_go_and_a_dry_run_only_lists_them(self):
        ceo, home = self.repo / ".ceo", self.tmp / "home"
        self.env["HOME"] = str(home)
        # Whatever is listed, the repository, a folder holding it, home, .git and .ceo never go,
        # however old they look, also when named through a link (macOS's /var is one).
        through_link = Path(tempfile.gettempdir(), self.tmp.name, "repo")
        listed = ["# the owner's list", "build", "*.log", "~/cache/*", "~", "..", ".git", ".ceo/*",
                  ".ceo", str(through_link), str(self.repo), "/"]
        (self.repo / "sweep.txt").write_text("\n".join(listed) + "\n")
        (self.repo / ".gitignore").write_text(".ceo/\nbuild/\n*.log\n__pycache__/\n")
        self.commit_all("sweep.txt")
        old = {"record": ceo / "interrupted" / "090-x-20261001T000000000000Z", "build": self.repo / "build",
               "log": self.repo / "old.log", "cache": home / "cache" / "old"}
        for path in (old["record"], old["build"], old["cache"]):
            path.mkdir(parents=True)
        (old["record"] / "record.json").write_text("{}\n")
        (old["build"] / "out.bin").write_bytes(os.urandom(20_000_000))
        (old["cache"] / "blob").write_bytes(os.urandom(10_000_000))
        old["log"].write_text("old\n")
        for path in (self.repo, home):
            age(path)
        fresh = {"record": ceo / "interrupted" / "091-y-20261007T000000000000Z",
                 "cache": home / "cache" / "new", "pycache": self.repo / "pkg" / "__pycache__",
                 "inside": ceo / "wt" / "092-live" / "__pycache__"}
        for path in fresh.values():
            path.mkdir(parents=True)
        (fresh["pycache"] / "m.cpython-310.pyc").write_bytes(b"\0" * 5000)
        Path(self.env["CEO_HOME"]).mkdir()  # the owner's file, which any command makes (order 8)
        Path(self.env["CEO_HOME"], "OWNER.md").touch()
        before = self.snapshot()
        dry = self.sweep("--dry-run")
        self.assertEqual(self.snapshot(), before, "the dry run changed something")
        expected = sorted([*old.values(), fresh["pycache"]])  # a __pycache__ goes at any age
        self.assertEqual(sorted(line.split(" would remove ")[1].rsplit(", ", 1)[0]
                                for line in dry.splitlines()[:-1]),
                         sorted(str(path.relative_to(self.repo)) if self.repo in path.parents
                                else str(path) for path in expected))
        self.assertRegex(dry.splitlines()[-1], r"^ceo: the sweep would free \d+\.\d\d GB$")
        freed = du(*expected)
        self.sweep()
        self.assertEqual([path for path in expected if os.path.lexists(path)], [])
        self.assertTrue(all(fresh[name].is_dir() for name in ("record", "cache", "inside")))
        self.assertTrue(self.repo.is_dir() and (self.repo / ".git").is_dir() and home.is_dir())
        self.assertEqual(self.git("status", "--porcelain", "--", ":!STATUS.md"), "")
        self.assertRegex(self.sweep_line()[0], rf"^Sweep: \S+, freed {freed / 1e9:.2f} GB$")

    def test_the_tick_sweeps_first_once_a_day_and_writes_free_space_every_pass(self):
        (self.repo / "sweep.txt").write_text("build\n")
        self.commit_all("sweep.txt")
        build = self.repo / "build"

        def old_build():
            build.mkdir()
            (build / "out.bin").write_bytes(b"\0" * 100_000)
            age(build)

        old_build()
        self.assertIn("ceo: removed build, ", self.tick())
        first = self.sweep_line()
        old_build()
        self.assertNotIn("removed build", self.tick(), "it swept twice in a day")
        self.assertTrue(build.is_dir())
        self.assertEqual(self.sweep_line(), first)
        then = time.time() - DAY - 60
        os.utime(self.repo / ".ceo" / "last-sweep", (then, then))  # a day later
        self.assertIn("ceo: removed build, ", self.tick())
        self.assertFalse(build.exists())
        status = self.status()
        self.assertRegex(status[0], r"^Last tick: ")
        self.assertRegex(status[1], r"^Health: ")  # order 10's, between the tick's and free space
        self.assertRegex(status[2], r"^Free: disk \d+\.\d GB, RAM \d+\.\d GB$")
        disk = float(status[2].split()[2])
        self.assertAlmostEqual(disk, shutil.disk_usage(self.repo).free / 1e9, delta=2)
        self.assertEqual(status[3], "Orders: orders run from origin")
        self.assertRegex(status[4], r"^Sweep: \S+, freed 0\.00 GB$")

    def test_a_merge_not_yet_in_the_owners_main_keeps_its_record_until_it_is_pulled(self):
        (self.repo / "notes.txt").write_text("the owner's notes\n")
        self.commit_all("notes")
        self.reviewed("100-behind")
        (self.repo / "notes.txt").write_text("changed and not committed\n")  # the owner's folder is dirty
        self.assertEqual(self.door().returncode, 0)  # merged: only main lacks it
        self.tick()
        self.assertIn("main is 2 behind origin", self.status())
        age(self.run_dir("100-behind"))
        self.assertNotIn("100-behind", self.sweep())
        self.assertTrue(self.run_dir("100-behind").is_dir(), "the record of a merge main lacks was swept")
        self.tick()
        self.assertIn("main is 2 behind origin", self.status())
        self.git("checkout", "--", "notes.txt")  # the owner cleans the folder
        self.tick()
        self.assertEqual(self.git("rev-parse", "main"), self.main(), "the clean folder was never pulled")
        self.assertNotIn("behind origin", self.status())
        age(self.run_dir("100-behind"))
        self.assertIn("ceo: removed .ceo/runs/100-behind, ", self.sweep())  # pulled

    def test_a_line_status_has_not_shown_yet_keeps_its_record_until_it_is_shown(self):
        self.run_done("102-unshown")
        review = self.ceo("review", "orders/102-unshown.md", "--reviewer", self.model("endless"), "--timeout=1")
        self.assertEqual(review.returncode, 2, review.stderr)  # blocked, by hand: no tick yet
        (self.repo / "orders" / "102-unshown.cut").touch()  # and cut by the CEO
        self.commit_all("cut")
        mine = [self.run_dir("102-unshown"), self.repo / ".ceo" / "wt" / "102-unshown",
                self.repo / "orders" / "102-unshown.cut"]
        for path in mine:
            age(path)
        self.assertNotIn("102-unshown", self.sweep())
        self.assertTrue(self.run_dir("102-unshown").is_dir(), "a record was swept before its line showed")
        self.tick()
        said = f"- `orders/102-unshown.md` the review is blocked: {self.read('102-unshown', 'review.json')['reason']}"
        self.assertIn(said, self.status())
        self.assertFalse(mine[1].exists(), "the tick kept the clean, saved cut checkout")
        for path in (mine[0], mine[2]):
            age(path)
        self.assertIn("ceo: removed .ceo/runs/102-unshown, ", self.sweep())
        self.assertIn(said, self.status(), "the line went with its record")

    def test_a_line_that_fails_is_one_line_and_the_tick_sweeps_the_rest(self):
        locked, build = self.repo / "locked", self.repo / "build"
        (locked / "inner").mkdir(parents=True)
        (locked / "inner" / "f").write_text("the owner's\n")
        build.mkdir()
        (build / "out.bin").write_bytes(b"\0" * 100_000)
        for path in (locked, build):
            age(path)
        locked.chmod(0o555)  # nothing in it can be removed
        self.addCleanup(locked.chmod, 0o755)
        (self.repo / "loop").symlink_to("loop")  # a link to itself: Python 3.10 cannot resolve it
        (self.repo / "sweep.txt").write_text("loop\nlocked/inner\nbuild\n")
        self.assertIn("ceo: the sweep would free", self.sweep("--dry-run"))
        out = self.tick()  # it exits 0: the tick goes on
        self.assertLessEqual(len([line for line in out.splitlines() if "loop" in line]), 1, out)
        refused = [line for line in out.splitlines() if "locked/inner" in line]
        self.assertEqual(len(refused), 1, out)
        self.assertRegex(refused[0], r"^ceo: not removed: locked/inner: \[Errno 13\] Permission denied")
        self.assertTrue((locked / "inner").is_dir())
        self.assertFalse(build.exists(), "the line after the one that failed was not swept")
        self.assertIn("ceo: removed build, ", out)
        self.assertRegex(self.sweep_line()[0], r"^Sweep: \S+, freed \d+\.\d\d GB$")
        self.assertRegex(self.status()[0], r"^Last tick: ")

    def test_a_repository_or_home_at_a_path_with_a_pattern_never_sweeps_beside_itself(self):
        # Read as patterns, p[1]*? and h[1]*? would be p1-x and h1-x, the folders beside them.
        repo, home = self.tmp / "p[1]*?", self.tmp / "h[1]*?"
        beside = [self.tmp / "p1-x" / "build", self.tmp / "h1-x" / "cache"]
        mine = [repo / "build", home / "cache"]
        for path in beside + mine:
            path.mkdir(parents=True)
            (path / "f").write_text("old\n")
            age(path)
        subprocess.run(["git", "init", "-q", str(repo)], env=self.env, check=True)
        self.git("-C", str(repo), "config", "maintenance.auto", "false")
        (repo / "sweep.txt").write_text("build\n~/cache\n")
        done = subprocess.run([sys.executable, str(CEO), "sweep"], cwd=repo, capture_output=True,
                              env={**self.env, "HOME": str(home)}, encoding="utf-8", timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([path for path in beside if not path.is_dir()], [], done.stdout)
        self.assertEqual([path for path in mine if path.exists()], [], done.stdout)


if __name__ == "__main__":
    unittest.main()
