"""tools/box, run as a real process on real git, with no stand-in for the wall. Where this host builds a wall (macOS
sandbox-exec, Linux bwrap), each case proves the wall; where it cannot, the same case proves the refusal: exit 125,
nothing on stdout, one line on stderr. The scratch lives under /var/tmp, outside every folder the wall opens, with a
HOME of its own holding planted credentials; no test is skipped on any host."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BOX = str(Path(__file__).resolve().parent / "tools" / "box")
PLANTED = {".ssh/id_box": "planted-ssh-key", ".config/gh/hosts.yml": "oauth_token: planted-gh-login",
           ".git-credentials": "planted-git-credential", ".netrc": "password planted-netrc"}
SECRETS = ("SSH_AUTH_SOCK", "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")


def builds_a_wall():
    """Judged apart from the box: this host's own wall tool exists and builds a wall here."""
    if sys.platform == "darwin":
        tool = ["/usr/bin/sandbox-exec", "-p", "(version 1) (allow default)", "/usr/bin/true"]
    else:
        tool = [shutil.which("bwrap") or "bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "true"]
    try:
        return subprocess.run(tool, capture_output=True).returncode == 0
    except OSError:
        return False


WALL = builds_a_wall()


class Box(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir="/var/tmp")  # outside every temp folder the wall opens
        self.addCleanup(scratch.cleanup)
        self.tmp = Path(scratch.name).resolve()
        self.home, self.main, self.tree, self.plain = (self.tmp / name for name in ("home", "main", "tree", "plain"))
        for path, text in PLANTED.items():
            (self.home / path).parent.mkdir(parents=True, exist_ok=True)
            (self.home / path).write_text(text + "\n")
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / ".credentials.json").write_text("{}\n")
        (self.home / ".claude" / "settings.json").write_text("{}\n")
        self.plain.mkdir()
        own = ("BOX_TOOL", "XDG_CONFIG_HOME", "GH_CONFIG_DIR", "CLAUDE_CONFIG_DIR", "TMPDIR", *SECRETS)
        self.env = {key: value for key, value in os.environ.items() if key not in own and not key.startswith("GIT_")}
        self.env.update(HOME=str(self.home), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="s", GIT_AUTHOR_EMAIL="s@x.invalid", GIT_COMMITTER_NAME="s",
                        GIT_COMMITTER_EMAIL="s@x.invalid")
        self.main.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "base")
        self.git("worktree", "add", "-q", str(self.tree), "-b", "order/box")
        (self.tree / "built").write_text("built\n")
        self.hook = self.main / ".git" / "hooks" / "post-commit"

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.main, env=self.env, check=True, capture_output=True,
                              text=True).stdout.strip()

    def box(self, *cmd, cwd=None, given=None, **extra):
        return subprocess.run([BOX, *cmd], cwd=cwd or self.tree, env={**self.env, **extra}, input=given,
                              capture_output=True, text=True, timeout=120)

    def refused(self, said):
        self.assertEqual(said.returncode, 125, said)
        self.assertEqual(said.stdout, "", said)
        self.assertTrue(said.stderr.startswith("tools/box: ") and " did not run: " in said.stderr, said)
        self.assertEqual(said.stderr.count("\n"), 1, said)

    def test_a_write_to_home_is_denied_and_leaves_no_file(self):
        said = self.box("bash", "-c", 'touch "$HOME/box-proof"')
        self.assertNotEqual(said.returncode, 0, said)
        if not WALL:
            self.refused(said)
        self.assertFalse((self.home / "box-proof").exists(), "a write outside the copy got out")

    def test_a_commit_in_the_linked_worktree_lands_but_the_main_repositorys_hooks_and_config_stay(self):
        config = (self.main / ".git" / "config").read_bytes()
        said = self.box("bash", "-c", "git add built && git commit -qm walled")
        if WALL:
            self.assertEqual(said.returncode, 0, said)
            self.assertEqual(self.git("log", "-1", "--format=%s", "order/box"), "walled")
            self.assertEqual(self.git("status", "--porcelain", cwd=self.tree), "")
        else:
            self.refused(said)
            self.assertEqual(self.git("log", "-1", "--format=%s", "order/box"), "base")
        plant = self.box("bash", "-c", f'echo planted > "{self.hook}"')
        change = self.box("git", "config", "box.proof", "planted")
        for said in (plant, change):
            self.assertNotEqual(said.returncode, 0, said)
        if not WALL:
            self.refused(plant)
            self.refused(change)
        self.assertFalse(self.hook.exists(), "a hook was planted in the main repository")
        self.assertEqual((self.main / ".git" / "config").read_bytes(), config)

    def test_planted_credentials_cannot_be_read_and_their_text_never_reaches_stdout(self):
        for path, text in PLANTED.items():
            with self.subTest(path=path):
                said = self.box("cat", str(self.home / path))
                self.assertNotIn(text, said.stdout, said)
                if WALL:  # a hidden file is refused or reads empty, as the tool builds the wall
                    self.assertTrue(said.returncode != 0 or said.stdout == "", said)
                else:
                    self.refused(said)

    def test_the_login_file_of_claude_code_is_writable_and_its_settings_are_not(self):
        login, settings = self.home / ".claude" / ".credentials.json", self.home / ".claude" / "settings.json"
        saved = self.box("bash", "-c", f'echo saved > "{login}"')
        locked = self.box("bash", "-c", f'echo planted > "{settings}"')
        if WALL:
            self.assertEqual(saved.returncode, 0, saved)
            self.assertNotEqual(locked.returncode, 0, locked)
            self.assertEqual(login.read_text(), "saved\n")
        else:
            self.refused(saved)
            self.refused(locked)
            self.assertEqual(login.read_text(), "{}\n")
        self.assertEqual(settings.read_text(), "{}\n", "Claude Code's settings were written behind the wall")

    def test_the_credentials_of_the_caller_do_not_reach_the_command(self):
        sock = str(self.home / "agent.sock")
        said = self.box("env", SSH_AUTH_SOCK=sock, GH_TOKEN="planted-token", GITHUB_TOKEN="planted-token",
                        GH_ENTERPRISE_TOKEN="planted-token", GITHUB_ENTERPRISE_TOKEN="planted-token")
        if WALL:
            self.assertEqual(said.returncode, 0, said)
            names = {line.split("=", 1)[0] for line in said.stdout.splitlines()}
            self.assertFalse(names & set(SECRETS), f"a credential variable reached the command: {names & set(SECRETS)}")
            self.assertNotIn("planted-token", said.stdout)
            self.assertNotIn(sock, said.stdout)
        else:
            self.refused(said)

    def test_input_output_exit_code_and_a_temp_file_pass_through(self):
        said = self.box("sh", "-c", 'read line; f=$(mktemp); echo "got $line" > "$f"; cat "$f"; rm "$f"; '
                                    'echo to-stderr >&2; exit 7', given="piped\n")
        if WALL:
            self.assertEqual((said.returncode, said.stdout), (7, "got piped\n"), said)
            self.assertIn("to-stderr", said.stderr)
        else:
            self.refused(said)

    def test_outside_a_git_repository_it_runs_where_it_was_started_and_may_write_there(self):
        said = self.box("sh", "-c", "pwd -P; echo made > made", cwd=self.plain)
        if WALL:
            self.assertEqual((said.returncode, said.stdout), (0, f"{self.plain}\n"), said)
            self.assertEqual((self.plain / "made").read_text(), "made\n")
        else:
            self.refused(said)
            self.assertFalse((self.plain / "made").exists())

    def test_the_tool_none_refuses_on_every_host_names_bwrap_and_runs_nothing(self):
        for cmd in (("bash", "-c", 'touch "$HOME/box-proof"'), ("bash", "-c", f'echo planted > "{self.hook}"'),
                    ("cat", str(self.home / ".ssh" / "id_box"))):
            with self.subTest(cmd=cmd[-1]):
                said = self.box(*cmd, BOX_TOOL="none")
                self.refused(said)
                self.assertIn("bwrap", said.stderr)
        self.assertFalse((self.home / "box-proof").exists() or self.hook.exists(), "BOX_TOOL=none ran the command")

    def test_no_command_is_a_usage_error_not_a_refusal(self):
        said = self.box()
        self.assertEqual((said.returncode, said.stdout), (2, ""), said)
        self.assertIn("usage: tools/box", said.stderr)


if __name__ == "__main__":
    unittest.main()
