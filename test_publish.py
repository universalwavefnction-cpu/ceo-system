"""Public releases through real git repositories and processes; no GitHub or models are needed."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools import publish

ROOT = Path(__file__).resolve().parent
AUTHOR = "ceo-kernel <1+owner@users.noreply.github.com>"
PRIVATE_TESTS = ("test_bakeoff.py", "test_ledger.py", "test_private.py", "test_replay.py", "test_scorecard.py")  # the
# client kits, private names, the replay tool, which public.txt does not ship, and the bake-off, which stands on it
GATE = '''#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
around = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
row = dict(folder=os.getcwd(), repository=around.returncode == 0,
           tokens={key: os.environ.get(key) for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN")},
           home=os.environ["CEO_HOME"], gh=os.environ["GH_CONFIG_DIR"],
           home_empty=not list(Path(os.environ["CEO_HOME"]).glob("*")),
           gh_empty=not list(Path(os.environ["GH_CONFIG_DIR"]).glob("*")), stdin=sys.stdin.read())
with open(os.environ["PUBLISH_RAN"], "a") as ran:
    ran.write(json.dumps(row) + "\\n")
print("the stand-in gate ran")
sys.exit(int(os.environ.get("PUBLISH_GATE_EXIT", "0")))
'''


class Publish(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.tmp = Path(scratch.name).resolve()
        self.repo, self.public, self.home = (self.tmp / name for name in ("private", "public.git", "home"))
        self.repo.mkdir()
        self.home.mkdir()
        self.ran = self.tmp / "gate.jsonl"
        self.word = "hidden-" + "customer"
        self.words = self.home / "public-words.txt"
        self.words.write_text(self.word + "\n")
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("GIT_") and key != "PUBLISH_GATE_EXIT"}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", PYTHONDONTWRITEBYTECODE="1",
                        GIT_AUTHOR_NAME="Private author", GIT_AUTHOR_EMAIL="author@example.invalid",
                        GIT_COMMITTER_NAME="Private committer", GIT_COMMITTER_EMAIL="committer@example.invalid",
                        CEO_HOME=str(self.home), PUBLISH_RAN=str(self.ran), GH_CONFIG_DIR=str(self.tmp / "login"))
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN"):
            self.env[key] = "gh" + "p_" + "a" * 36
        self.git("init", "-q", "-b", "main")
        self.git("init", "-q", "--bare", "-b", "main", str(self.public))
        self.listed = ["gate.sh", "hello.txt", "nested/with space.txt", "public.txt"]
        (self.repo / "nested").mkdir()
        (self.repo / "nested/with space.txt").write_text("A listed file with a space.\n")
        (self.repo / "hello.txt").write_text("Hello.\n")
        (self.repo / "gate.sh").write_text(GATE)
        (self.repo / "gate.sh").chmod(0o755)
        (self.repo / "private.txt").write_text(self.word + "\n")
        (self.repo / "public.txt").write_text("# Only these files ship.\n" + "\n".join(self.listed) + "\n")
        self.release = self.commit()

    def git(self, *args, public=False, check=True):
        return subprocess.run(["git", *(["--git-dir", str(self.public)] if public else []), *args],
                              cwd=self.repo, env=self.env, capture_output=True, text=True, check=check)

    def commit(self):
        self.git("add", "--", "public.txt", "gate.sh", "hello.txt", "nested", "private.txt")
        self.git("commit", "-qm", "A private release")
        return self.git("rev-parse", "HEAD").stdout.strip()

    def run_publish(self, release=None, *, author=AUTHOR, dry=False, env=None):
        args = [sys.executable, "-B", str(ROOT / "tools/publish.py"), "--to", str(self.public)]
        if author is not None:
            args += ["--author", author]
        if dry:
            args += ["--dry-run"]
        return subprocess.run([*args, release or self.release], cwd=self.repo, env=env or self.env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)

    def empty(self):
        self.assertEqual(self.git("for-each-ref", public=True).stdout, "")

    def test_a_release_is_one_commit_of_exactly_the_listed_files_by_a_noreply_address_and_twice_makes_nothing(self):
        # Neither the changed list nor dirty contents may enter an export of the committed release.
        (self.repo / "public.txt").write_text("private.txt\n")
        (self.repo / "hello.txt").write_text(self.word + "\n")
        done = self.run_publish()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("gate.sh passed in a fresh folder", done.stdout)
        self.assertEqual(self.git("rev-list", "--count", "main", public=True).stdout.strip(), "1")
        self.assertEqual(self.git("ls-tree", "-r", "--name-only", "main", public=True).stdout.splitlines(),
                         self.listed)
        for path in self.listed:
            self.assertEqual(self.git("show", f"main:{path}", public=True).stdout,
                             self.git("show", f"{self.release}:{path}").stdout)
        self.assertTrue(self.git("ls-tree", "main", "gate.sh", public=True).stdout.startswith("100755 "))
        self.assertEqual(self.git("log", "-1", "--format=%an <%ae>|%cn <%ce>|%s", "main", public=True).stdout.strip(),
                         f"{AUTHOR}|{AUTHOR}|Release {self.release}")
        rows = [json.loads(line) for line in self.ran.read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertFalse(row["repository"])
        self.assertEqual(row["tokens"], dict.fromkeys(("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN")))
        self.assertTrue(row["home_empty"] and row["gh_empty"])
        self.assertEqual(row["stdin"], "")
        self.assertNotEqual(row["home"], str(self.home))
        self.assertNotEqual(row["gh"], self.env["GH_CONFIG_DIR"])
        self.assertFalse(Path(row["folder"]).is_relative_to(self.tmp))
        self.assertFalse(Path(row["folder"]).exists(), "the fresh gate copy was not removed")
        tip = self.git("rev-parse", "main", public=True).stdout
        again = self.run_publish(env={**self.env, "PUBLISH_GATE_EXIT": "9"})
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("nothing to do", again.stdout)
        self.assertEqual(self.git("rev-parse", "main", public=True).stdout, tip)
        self.assertEqual(len(self.ran.read_text().splitlines()), 1, "a repeat publish ran the gate again")
        # A new release replaces the tree, dropping paths its new allowlist leaves out.
        (self.repo / "public.txt").write_text("gate.sh\nhello.txt\npublic.txt\n")
        (self.repo / "hello.txt").write_text("A later release.\n")
        later = self.commit()
        done = self.run_publish(later)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.git("rev-list", "--count", "main", public=True).stdout.strip(), "2")
        self.assertEqual(self.git("rev-parse", "main^", public=True).stdout, tip)
        self.assertEqual(self.git("ls-tree", "-r", "--name-only", "main", public=True).stdout.splitlines(),
                         ["gate.sh", "hello.txt", "public.txt"])

    def test_a_planted_secret_or_private_word_in_a_listed_file_is_refused_and_nothing_is_pushed(self):
        plants = [("a private key", "-----BEGIN " + "PRIVATE KEY-----"),
                  ("a GitHub token", "gh" + "p_" + "a" * 36),
                  ("a GitHub token", "github_" + "pat_" + "b" * 40),
                  ("an API key", "sk" + "-" + "c" * 32),
                  ("an API key", "AK" + "IA" + "A" * 16),
                  ("an API key", "xox" + "b-" + "d" * 24),
                  ("a bot token", "123456789" + ":" + "e" * 35),
                  ("an email address", "person" + "@" + "mail.test"),
                  ("an IP address", "198.51." + "100.24"),
                  ("a home folder", "/" + "home" + "/someone"),
                  ("a home folder", "/" + "Users" + "/someone"),
                  (f"the private word {self.word!r}", self.word.upper())]
        for what, value in plants:
            with self.subTest(what=what, prefix=value[:4]):
                (self.repo / "hello.txt").write_text("Hello.\n" + value + "\n")
                done = self.run_publish(self.commit())
                self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn(f"hello.txt:2: {what}", done.stderr)
                self.empty()
                self.assertFalse(self.ran.exists(), "a leaking export ran its gate")

    def test_a_failing_gate_a_missing_or_folder_path_or_another_address_is_refused(self):
        for author in (None, "Someone <someone@example.invalid>", AUTHOR + " extra"):
            with self.subTest(author=author):
                done = self.run_publish(author=author)
                self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn("no other address may sign", done.stderr)
                self.empty()
        self.words.unlink()
        done = self.run_publish()
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("no private word list", done.stderr)
        self.empty()
        self.words.write_text("")  # an explicitly empty private list is allowed
        done = self.run_publish(env={**self.env, "PUBLISH_GATE_EXIT": "7"})
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("gate.sh exited 7 in a fresh folder", done.stderr)
        self.assertIn("the stand-in gate ran", done.stderr)
        self.empty()
        for path in ("missing.txt", "nested"):
            with self.subTest(path=path):
                (self.repo / "public.txt").write_text("\n".join([*self.listed, path]) + "\n")
                done = self.run_publish(self.commit())
                self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn(f"holds as no file: {path}", done.stderr)
                self.empty()
        self.assertEqual(len(self.ran.read_text().splitlines()), 1)

    def test_a_dry_run_prints_what_would_ship_and_touches_nothing(self):
        (self.repo / "hello.txt").write_text(self.word + "\n")
        before = self.git("status", "--porcelain").stdout
        done = self.run_publish(author=None, dry=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines()[:-1], self.listed)
        self.assertIn(f"{len(self.listed)} files of {self.release[:12]} would ship", done.stdout)
        self.assertIn("the leak scan found nothing", done.stdout)
        self.empty()
        self.assertFalse(self.ran.exists())
        self.assertEqual(self.git("status", "--porcelain").stdout, before)


class Allowlist(unittest.TestCase):
    def test_every_module_of_the_kernel_ships(self):
        listed = [line for line in (ROOT / "public.txt").read_text().splitlines()
                  if line and not line.startswith("#")]
        self.assertEqual(listed, sorted(set(listed)))
        modules = {path.name for path in ROOT.glob("*.py") if not path.name.startswith("test_")}
        self.assertFalse(modules - set(listed), f"public.txt leaves out modules: {sorted(modules - set(listed))}")
        self.assertNotIn("test_private.py", listed)
        for path in listed:
            self.assertTrue((ROOT / path).is_file(), path)
            self.assertFalse(path.split("/")[0] in ("adopt", "bugs", "plans", "results"), path)
            self.assertTrue(not path.startswith("orders/") or path == "orders/README.md", path)
            self.assertFalse(path.startswith("routines/night-") or path.endswith(".png"), path)
            self.assertNotIn(path, ("IDEAS.md", "VISION.md", "GUIDE.md", "decisions.md"))

    def test_every_top_level_test_is_listed_or_named_private(self):
        listed = {line for line in (ROOT / "public.txt").read_text().splitlines() if line and not line.startswith("#")}
        tests = {path.name for path in ROOT.glob("test_*.py")}
        neither = sorted(tests - listed - set(PRIVATE_TESTS))
        both = sorted(listed & set(PRIVATE_TESTS))
        self.assertFalse(neither, f"neither in public.txt nor in PRIVATE_TESTS: {neither}")
        self.assertFalse(both, f"in public.txt and in PRIVATE_TESTS: {both}")

    def test_this_commit_exports_with_no_leak_the_scan_knows_without_a_private_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            if (ROOT / ".git").exists():
                # export uses git in its caller's directory, as the command does.
                done = subprocess.run([sys.executable, "-B", "-c",
                                       "import json, sys; from pathlib import Path; from tools import publish; "
                                       "p = Path(sys.argv[1]); files = publish.export('HEAD', p); "
                                       "print(json.dumps(publish.leaks(p, files, [])))", str(Path(tmp) / "export")],
                                      cwd=ROOT, capture_output=True, text=True, timeout=30)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertEqual(json.loads(done.stdout), [])
            else:  # the publisher's gate runs on this commit's export, without its git history
                listed = [line for line in (ROOT / "public.txt").read_text().splitlines()
                          if line and not line.startswith("#")]
                self.assertEqual(publish.leaks(ROOT, listed, []), [])


if __name__ == "__main__":
    unittest.main()
