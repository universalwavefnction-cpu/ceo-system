"""install.sh, the stranger's first line: the real script under /bin/sh, a scratch HOME holding the stranger's own
files, and a PATH of the system's own tools beside wrappers of the real Python and git, a fake gh and a fake agent,
and fakes of every package manager and service command that record any call."""
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KERNEL = Path(__file__).resolve().parent
MAC = platform.system() == "Darwin"
GET = "brew install" if MAC else "sudo apt-get install"
OWN = {".zshrc": b"export EDITOR=vi\n", ".bashrc": b"# mine\n", ".profile": b"umask 022\n",
       ".claude/settings.json": b'{\n  "theme": "dark",\n  "model": "opus"\n}\n'}
# What the script must never run: a package manager, a downloader, a service command.
NEVER = ("brew", "apt-get", "apt", "sudo", "npm", "npx", "pip", "pip3", "curl", "wget", "launchctl", "systemctl",
         "crontab", "dnf", "yum", "snap", "port")
# What the script checks for, so the system's own copy stays off the PATH: the fakes come in their place.
CHECKED = re.compile(r"python.*|pip.*|git.*|gh|claude|codex|node|" + "|".join(map(re.escape, NEVER)))
GH = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE/gh.log"
[ "$1 $2" = "api user" ] || exit 1
if [ -e "$FAKE/logged-out" ]; then echo "To get started with GitHub CLI, please run:  gh auth login" >&2; exit 4; fi
echo stranger
"""
FAKES = {"python3": f'#!/bin/sh\nexec "{sys.executable}" "$@"\n',
         "git": f'#!/bin/sh\nexec "{shutil.which("git")}" "$@"\n', "gh": GH,
         "claude": '#!/bin/sh\necho "$*" >> "$FAKE/agent.log"\n', "codex": '#!/bin/sh\necho "$*" >> "$FAKE/agent.log"\n',
         **{name: f'#!/bin/sh\necho "{name} $*" >> "$FAKE/never.log"\nexit 1\n' for name in NEVER}}
OLD_PYTHON = "#!/bin/sh\necho 3.9.6\nexit 1\n"  # python3 -c that prints its version and says it is too old


class Install(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, cls.shared, True)
        cls.tools = cls.shared / "tools"
        cls.tools.mkdir()
        for folder in ("/bin", "/usr/bin"):  # every tool of the system's own, but none the script checks or must not run
            for path in sorted(Path(folder).iterdir()):
                if not CHECKED.fullmatch(path.name) and os.access(path, os.X_OK) and not (cls.tools / path.name).exists():
                    (cls.tools / path.name).symlink_to(path)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home, self.fake = self.tmp / "stranger", self.tmp / "fake"
        (self.fake / "bin").mkdir(parents=True)
        for name, data in OWN.items():
            (self.home / name).parent.mkdir(parents=True, exist_ok=True)
            (self.home / name).write_bytes(data)
        self.settings = self.home / ".claude" / "settings.json"
        self.bin = self.home / ".local" / "bin"
        self.link = self.bin / "ceo"
        self.env = {"HOME": str(self.home), "FAKE": str(self.fake), "LANG": "C.UTF-8", "TERM": "dumb",
                    "PATH": os.pathsep.join(map(str, (self.bin, self.fake / "bin", self.tools))),
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "S", "GIT_AUTHOR_EMAIL": "s@example.invalid",
                    "GIT_COMMITTER_NAME": "S", "GIT_COMMITTER_EMAIL": "s@example.invalid"}
        self.put()

    def put(self, *leave, **instead):
        """The fakes on the PATH, except those named in leave; a name in instead gets that script."""
        for path in (self.fake / "bin").iterdir():
            path.unlink()
        for name, text in {**FAKES, **instead}.items():
            if name not in leave and not (name == "codex" and name not in instead):
                (self.fake / "bin" / name).write_text(text)
                (self.fake / "bin" / name).chmod(0o755)

    def install(self, answer="", script=None, shell="/bin/sh", cwd=None, **env):
        return subprocess.run([shell, str(script or KERNEL / "install.sh")], cwd=cwd or self.home,
                              env={**self.env, **env}, input=answer, capture_output=True, text=True, timeout=60)

    def tree(self):
        return {str(p.relative_to(self.home)): os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None
                for p in sorted(self.home.rglob("*"))}

    def lines(self, done):
        self.assertNotIn("", done.stdout.splitlines(), "a blank line")  # one line a thing, nothing between
        return done.stdout.splitlines()

    def crosses(self, done):
        return [line for line in self.lines(done) if line.startswith("✗ ")]

    def asked(self, done):
        return [line for line in self.lines(done) if "[y/N]" in line]

    def assertNeverRan(self):
        self.assertFalse((self.fake / "never.log").exists(), (self.fake / "never.log").read_text()
                         if (self.fake / "never.log").exists() else "")

    def test_all_present_links_the_launcher_ticks_each_check_and_is_ready_and_changes_nothing_else(self):
        before = self.tree()
        done = self.install("n\n")
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        lines = self.lines(done)
        ticks = [line for line in lines if line.startswith("✓ ")]
        self.assertEqual([line.split(":")[0] for line in ticks], ["✓ python3", "✓ git", "✓ GitHub", "✓ agent", "✓ ceo"])
        self.assertEqual(ticks[2], "✓ GitHub: logged in as stranger")
        self.assertEqual(ticks[3], "✓ agent: claude")
        self.assertRegex(ticks[0], r"^✓ python3: 3\.\d+\.\d+$")
        self.assertRegex(ticks[1], r"^✓ git: \d+\.\d+")
        self.assertEqual(self.crosses(done), [])
        self.assertEqual(lines[-1], "Ready. Next: ceo try")
        self.assertTrue(self.link.is_symlink())
        self.assertEqual(os.readlink(self.link), str(KERNEL / "tools" / "launch.py"))
        after = self.tree()
        self.assertEqual(sorted(set(after) - set(before)), [".local", ".local/bin", ".local/bin/ceo"])
        self.assertEqual({name: after[name] for name in before}, before, "install.sh changed the stranger's own files")
        again = self.install("n\n")  # harmless again: the same words, nothing changed
        self.assertEqual((again.returncode, again.stdout, again.stderr), (0, done.stdout, ""))
        self.assertEqual(self.tree(), after)
        self.assertNeverRan()

    def test_each_missing_thing_gets_its_own_cross_with_the_one_command_that_fixes_it_and_a_failing_exit(self):
        git_fix = "xcode-select --install" if MAC else f"{GET} git"
        for leave, name, fix in ((("python3",), "python3", f"{GET} python3"), (("git",), "git", git_fix),
                                 (("gh",), "GitHub", f"{GET} gh"),
                                 (("claude",), "agent", "npm install -g @anthropic-ai/claude-code")):
            with self.subTest(leave=leave):
                self.put(*leave)
                done = self.install()
                crosses = self.crosses(done)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertEqual(len(crosses), 1, done.stdout)
                self.assertTrue(crosses[0].startswith(f"✗ {name}: ") and crosses[0].endswith(f". Fix: {fix}"), crosses)
                ticked = [line.split(":")[0] for line in self.lines(done) if line.startswith("✓ ")]
                self.assertEqual(ticked, [f"✓ {n}" for n in ("python3", "git", "GitHub", "agent") if n != name] + ["✓ ceo"])
                self.assertEqual(self.lines(done)[-1], f"1 missing: run each fix above, then {KERNEL}/install.sh again.")
                self.assertEqual(self.tree()[".local/bin/ceo"], str(KERNEL / "tools" / "launch.py"), "the link, whatever else")
        self.assertNeverRan()

    def test_two_missing_are_counted_and_a_run_again_says_the_same_and_changes_nothing(self):
        self.put("gh", "claude")
        done = self.install()
        self.assertEqual([line.split(":")[0] for line in self.crosses(done)], ["✗ GitHub", "✗ agent"])
        self.assertEqual((done.returncode, self.lines(done)[-1]), (1, f"2 missing: run each fix above, then {KERNEL}/install.sh again."))
        tree = self.tree()
        again = self.install()
        self.assertEqual((again.returncode, again.stdout), (1, done.stdout))
        self.assertEqual(self.tree(), tree)

    def test_python_older_than_3_10_is_a_cross_naming_its_version_and_no_question_is_asked(self):
        self.put(python3=OLD_PYTHON)
        done = self.install("y\n")
        self.assertEqual(self.crosses(done), [f"✗ python3: Python 3.9.6 is older than 3.10. Fix: {GET} python3"])
        self.assertEqual((done.returncode, self.asked(done)), (1, []))
        self.assertEqual(self.settings.read_bytes(), OWN[".claude/settings.json"])

    def test_git_that_cannot_sign_a_commit_is_a_cross_with_the_config_command(self):
        env = {**self.env, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.useConfigOnly", "GIT_CONFIG_VALUE_0": "true"}
        for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            del env[name]
        done = subprocess.run(["/bin/sh", str(KERNEL / "install.sh")], cwd=self.home, env=env, input="",
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(self.crosses(done), ['✗ git: git has no name and email to sign a commit with. Fix: git config '
                                              '--global user.name "Your Name" && git config --global user.email you@example.com'])
        self.assertEqual(done.returncode, 1)

    def test_a_logged_out_gh_is_the_github_cross_with_gh_auth_login_and_no_token_is_ever_read(self):
        (self.fake / "logged-out").touch()
        done = self.install()
        self.assertEqual(self.crosses(done), ["✗ GitHub: gh is not logged in to GitHub. Fix: gh auth login"])
        self.assertEqual(done.returncode, 1)
        (self.fake / "logged-out").unlink()
        self.install("y\n")
        calls = (self.fake / "gh.log").read_text().splitlines()
        self.assertEqual(calls, ["api user --jq .login"] * 2)  # never `gh auth token`
        self.assertNeverRan()

    def test_codex_alone_is_an_agent_and_both_are_named_and_the_question_wants_claude(self):
        self.put("claude", codex=FAKES["codex"])
        done = self.install("y\n")
        self.assertEqual((done.returncode, self.crosses(done)), (0, []), done.stdout)
        self.assertIn("✓ agent: codex", self.lines(done))
        self.assertEqual((self.asked(done), self.settings.read_bytes()), ([], OWN[".claude/settings.json"]))
        self.put(codex=FAKES["codex"])
        done = self.install("n\n")
        self.assertIn("✓ agent: claude, codex", self.lines(done))
        self.assertEqual(len(self.asked(done)), 1)

    def test_without_the_bin_folder_on_the_path_the_line_to_add_is_said_and_no_rc_file_changes(self):
        path = os.pathsep.join(map(str, (self.fake / "bin", self.tools)))
        done = self.install(PATH=path)
        self.assertEqual((done.returncode, self.crosses(done)), (0, []), done.stdout)
        lines = self.lines(done)
        self.assertIn("Ready.", lines)
        self.assertIn('  export PATH="$HOME/.local/bin:$PATH"', lines)
        self.assertEqual(lines[-1], "  ~/.local/bin/ceo try")
        self.assertNotIn("Ready. Next: ceo try", lines)
        self.assertTrue(all((self.home / name).read_bytes() == OWN[name] for name in (".zshrc", ".bashrc", ".profile")))
        self.assertEqual(sorted(self.tree()), sorted([*OWN, ".claude", ".local", ".local/bin", ".local/bin/ceo"]))

    def test_the_link_is_left_when_right_replaced_when_elsewhere_and_a_file_that_is_no_link_is_never_touched(self):
        target = str(KERNEL / "tools" / "launch.py")
        self.bin.mkdir(parents=True)
        self.link.symlink_to(target)
        inode = os.lstat(self.link).st_ino
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual((os.readlink(self.link), os.lstat(self.link).st_ino), (target, inode))
        self.link.unlink()
        self.link.symlink_to(self.tmp / "elsewhere.py")
        done = self.install()
        self.assertEqual((done.returncode, os.readlink(self.link)), (0, target))
        self.assertIn(f"✓ ceo: ~/.local/bin/ceo -> {target}", self.lines(done))
        self.link.unlink()
        self.link.write_bytes(b"mine\n")
        done = self.install()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(self.crosses(done), [f"✗ ceo: {self.link} exists and is not a link. Fix: mv {self.link} {self.link}.old"])
        self.assertEqual((self.link.is_symlink(), self.link.read_bytes()), (False, b"mine\n"))

    def test_a_relative_link_that_already_resolves_to_the_launcher_is_left_as_it_is(self):
        target = os.path.relpath(KERNEL / "tools" / "launch.py", self.bin)
        self.bin.mkdir(parents=True)
        self.link.symlink_to(target)
        inode = os.lstat(self.link).st_ino
        done = self.install("n\n")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual((os.readlink(self.link), os.lstat(self.link).st_ino), (target, inode))
        self.assertIn(f"✓ ceo: ~/.local/bin/ceo -> {KERNEL / 'tools' / 'launch.py'}", self.lines(done))

    def test_the_clone_is_the_scripts_own_folder_whatever_folder_it_is_run_from_and_through_a_link_to_it(self):
        clone = self.tmp / "real" / "ceo-system"
        (clone / "tools").mkdir(parents=True)
        shutil.copy(KERNEL / "install.sh", clone / "install.sh")
        (self.tmp / "alias").symlink_to(self.tmp / "real")
        for cwd, script in ((self.tmp, "alias/ceo-system/install.sh"), (clone / "tools", "../install.sh"),
                            (clone, "install.sh")):
            with self.subTest(cwd=cwd):
                self.link.unlink(missing_ok=True)
                done = self.install(script=script, cwd=cwd)
                self.assertEqual(done.returncode, 0, done.stdout)
                self.assertEqual(os.readlink(self.link), str(clone.resolve() / "tools" / "launch.py"))

    def test_the_question_changes_nothing_on_n_empty_or_no_answer_and_a_y_or_yes_adds_its_key_alone(self):
        theirs = OWN[".claude/settings.json"]
        for answer in ("n\n", "\n", "", "no\n", "yep\n", "sure\n", "Y\n", "YES\n", "Yes\n", " y \n", "\ty\n", "y \n"):
            with self.subTest(answer=answer):
                done = self.install(answer)
                self.assertEqual(done.returncode, 0, done.stdout)
                question = self.asked(done)
                self.assertEqual(len(question), 1, done.stdout)
                self.assertTrue(question[0].endswith("[y/N] "), question)
                for word in ("transcripts 30 days", "remembers a project", '"cleanupPeriodDays": 90', str(self.settings)):
                    self.assertIn(word, question[0])
                self.assertEqual(self.lines(done)[-1], "Ready. Next: ceo try")  # its own line, answered or not
                self.assertEqual(self.settings.read_bytes(), theirs)
                self.assertEqual(sorted(p.name for p in self.settings.parent.iterdir()), ["settings.json"])
        self.settings.chmod(0o640)
        done = self.install("y\n")
        self.assertEqual((done.returncode, self.lines(done)[-1]), (0, "Ready. Next: ceo try"))
        self.assertIn(f'Added "cleanupPeriodDays": 90 to {self.settings}.', self.lines(done))
        self.assertEqual(json.loads(self.settings.read_text()), {"theme": "dark", "model": "opus", "cleanupPeriodDays": 90})
        self.assertEqual(list(json.loads(self.settings.read_text())), ["theme", "model", "cleanupPeriodDays"])
        self.assertEqual((stat.S_IMODE(self.settings.stat().st_mode), sorted(p.name for p in self.settings.parent.iterdir())),
                         (0o640, ["settings.json"]))
        self.assertTrue(all((self.home / name).read_bytes() == OWN[name] for name in (".zshrc", ".bashrc", ".profile")))
        before = self.tree()
        self.assertEqual(self.asked(self.install("y\n")), [])  # once set, never asked again
        self.assertEqual(self.tree(), before)
        self.settings.write_text('{"cleanupPeriodDays": 30}')
        self.assertEqual(self.asked(self.install("y\n")), [])  # their own number is theirs
        self.assertEqual(json.loads(self.settings.read_text()), {"cleanupPeriodDays": 30})
        self.settings.write_text("{}")
        self.assertEqual(len(self.asked(self.install("yes\n"))), 1)
        self.assertEqual(json.loads(self.settings.read_text()), {"cleanupPeriodDays": 90})

    def test_a_settings_file_that_is_not_readable_json_is_left_alone_and_nothing_is_asked(self):
        for text in (b"{not json", b"", b"[1, 2]", b"\xff\xfe"):
            with self.subTest(text=text):
                self.settings.write_bytes(text)
                done = self.install("y\n")
                self.assertEqual((done.returncode, self.asked(done)), (0, []), done.stdout)
                self.assertEqual(self.settings.read_bytes(), text)

    def test_the_settings_a_link_points_at_is_the_file_written_and_the_link_stays(self):
        dotfiles = self.tmp / "dotfiles.json"
        dotfiles.write_bytes(b'{"theme": "dark"}')
        self.settings.unlink()
        self.settings.symlink_to(dotfiles)
        self.assertEqual(self.install("y\n").returncode, 0)
        self.assertEqual((self.settings.is_symlink(), json.loads(dotfiles.read_text())), (True, {"theme": "dark", "cleanupPeriodDays": 90}))

    def test_the_question_follows_claude_config_dir_and_makes_the_file_and_its_folder_when_missing(self):
        config = self.tmp / "elsewhere" / "claude"
        theirs = OWN[".claude/settings.json"]
        done = self.install("y\n", CLAUDE_CONFIG_DIR=str(config))
        self.assertIn(str(config / "settings.json"), self.asked(done)[0])
        self.assertEqual(json.loads((config / "settings.json").read_text()), {"cleanupPeriodDays": 90})
        self.assertEqual(sorted(p.name for p in config.iterdir()), ["settings.json"])
        self.assertEqual(self.settings.read_bytes(), theirs, "the default folder is not the one asked about")
        done = self.install("y\n", CLAUDE_CONFIG_DIR="")  # empty is unset: ~/.claude
        self.assertIn(str(self.settings), self.asked(done)[0])
        self.assertEqual(json.loads(self.settings.read_text())["cleanupPeriodDays"], 90)

    def test_it_runs_as_posix_sh_under_dash_and_bash_and_installs_nothing(self):
        text = (KERNEL / "install.sh").read_text()
        self.assertEqual(text.splitlines()[0], "#!/bin/sh")
        self.assertTrue(os.access(KERNEL / "install.sh", os.X_OK), "executable")
        self.assertTrue(stat.S_IMODE((KERNEL / "install.sh").stat().st_mode) & stat.S_IXUSR)  # Git keeps the bit; umask the rest
        self.assertNotRegex(text, r"(?m)\[\[|^\s*local\s|\w+=\(")  # no [[, local or array
        for shell in dict.fromkeys(filter(None, ("/bin/sh", shutil.which("dash"), shutil.which("bash")))):
            with self.subTest(shell=shell):
                shutil.rmtree(self.bin.parent, ignore_errors=True)
                done = self.install("n\n", shell=shell)
                self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        self.assertNeverRan()


if __name__ == "__main__":
    unittest.main()
