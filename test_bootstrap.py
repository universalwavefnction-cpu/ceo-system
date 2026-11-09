"""tools/bootstrap-host.sh end to end, this machine playing the host: real sh, git, the kernel and its
first tick, in a HOME of the test's own. Replaced: ssh, which runs the command here; GitHub, by a gh
keeping repositories bare in the test's folder; the model, by a claude that answers or refuses; and the
agent's loaders (launchctl, systemctl, loginctl), which only note their calls."""
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from ceo import held, parse_order, read_json

KERNEL = Path(__file__).resolve().parent
HOST, NAME = "someone@trial-host", "trial-host"

# ssh <host> <command>: the command runs here, as a fresh login there would run it.
# No identity supplied by this machine may mask the bootstrapped host's missing identity.
SSH = ('#!/bin/sh\nshift\ncd "$TEST_HOME"\nexec env -i HOME="$TEST_HOME" PATH=/usr/bin:/bin:/usr/sbin:/sbin '
       'GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 TZ="$TEST_TZ" sh -c "$*"\n')
FAKES = {
    "gh": r'''#!/bin/sh
case "$1 $2" in
"auth status") test ! -e STATE/gh-logged-out ;;
"auth setup-git") ;;
"api user") if [ "$4" = .login ]; then echo owner; else echo 1+owner@users.noreply.github.com; fi ;;
"api repos/"*) [ -e STATE/gh-offline ] && { echo "error connecting to api.github.com" >&2; exit 1; }
    test -d "BARE/${2##*/}.git" || { echo "gh: Not Found (HTTP 404)" >&2; exit 1; } ;;
"repo create") echo "$3" >>STATE/created.log; git init -q --bare -b main "BARE/${3#*/}.git" ;;
"repo clone") from=BARE/${3#*/}.git; [ -d "$3" ] && from=$3; git clone -q "$from" "$4" 2>/dev/null ;;
*) echo "the fake gh does not know: $*" >&2; exit 1 ;;
esac
''',
    "claude": r'''#!/bin/sh
if [ "$1 $2" = "auth status" ]; then test ! -e STATE/claude-logged-out; exit; fi
cat >/dev/null
[ -e STATE/claude-slow ] && sleep 3
if [ -e STATE/claude-refused ]; then
    echo '{"type": "result", "terminal_reason": "api_error", "result": "REFUSED"}'
    exit 1
fi
if [ -f orders/001-hello.md ] && [ "$(git branch --show-current)" = order/001-hello ]; then
    printf 'Hello from %s.\n' "$(hostname)" >HELLO.md
    git add HELLO.md && git commit -qm hello || exit 1
fi
echo ok
''',
    "brew": '#!/bin/sh\necho "the fake brew installs nothing: $*" >&2\nexit 1\n',
    **dict.fromkeys(("launchctl", "systemctl", "loginctl"),
                    '#!/bin/sh\necho "$(basename "$0") $*" >>STATE/loaded.log\n'),
}
REFUSED = "Your organization has disabled Claude subscription access for Claude Code"


@unittest.skipUnless((KERNEL / ".git").exists(), "the bootstrap ships its checkout's commit: none here")
class Bootstrap(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.home, self.state, self.bare, ssh = (self.tmp / name for name in ("home", "state", "bare", "bin"))
        tools = self.home / ".local" / "bin"
        for folder in (tools, self.state, self.bare, ssh):
            folder.mkdir(parents=True)
        for path, text in [(tools / name, text) for name, text in FAKES.items()] + [(ssh / "ssh", SSH)]:
            text = text.replace("STATE", str(self.state)).replace("BARE", str(self.bare))
            path.write_text(text.replace("REFUSED", REFUSED))
            path.chmod(0o755)
        # The owner's checkout: a clone of this one, at its commit, run with this script as it stands.
        self.kernel = self.tmp / "kernel"
        subprocess.run(["git", "clone", "-q", str(KERNEL), str(self.kernel)], check=True)
        script = self.kernel / "tools" / "bootstrap-host.sh"
        script.write_bytes((KERNEL / "tools" / "bootstrap-host.sh").read_bytes())
        self.project = self.home / f"ceo-trial-{NAME}"
        # Keep the fake host at noon: test_night covers scheduled research and its extra commits.
        self.env = {**os.environ, "PATH": f"{ssh}{os.pathsep}{os.environ['PATH']}",
                    "TEST_HOME": str(self.home), "TEST_TZ": f"BOOTSTRAP{time.gmtime().tm_hour - 12:+d}"}

    def bootstrap(self):
        return subprocess.run([str(self.kernel / "tools" / "bootstrap-host.sh"), HOST], env=self.env,
                              cwd=self.tmp, capture_output=True, text=True, timeout=300)

    def main(self):
        """The subjects of the scratch repository's main on the server, newest first."""
        found = subprocess.run(["git", "--git-dir", str(self.bare / f"ceo-trial-{NAME}.git"), "log",
                                "--format=%s", "main"], capture_output=True, text=True)
        return found.stdout.splitlines()

    def loaded(self):
        log = self.state / "loaded.log"
        return [line for line in (log.read_text().splitlines() if log.exists() else [])
                if " bootstrap " in line or " enable " in line]

    def test_a_missing_login_stops_it_in_one_sentence_before_anything_is_made(self):
        for logged_out, what, tool in (("gh-logged-out", "GitHub", "gh"),
                                       ("claude-logged-out", "Claude", "claude")):
            with self.subTest(what):
                (self.state / logged_out).touch()
                stopped = self.bootstrap()
                self.assertEqual((stopped.returncode, stopped.stdout, stopped.stderr), (1, "", (
                    f"bootstrap-host: {NAME}: log in to {what} there by hand, ssh -t {HOST} "
                    f"{self.home}/.local/bin/{tool} auth login, then run this again.\n")))
                self.assertEqual(sorted(path.name for path in self.home.iterdir()), [".local"])
                self.assertEqual(list(self.bare.iterdir()), [])
                (self.state / logged_out).unlink()

    def test_the_host_gets_the_kernel_a_scratch_project_and_the_agent_keeping_its_tick(self):
        done = self.bootstrap()
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, (
            f"bootstrap-host: {NAME}: its own tick runs; order 001 of owner/ceo-trial-{NAME} is filed: "
            "collect its proof within two days of its merge, before the host's sweep removes it.\n"), ""))
        head = lambda repo: subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                                           text=True).stdout
        self.assertEqual(head(self.home / "ceo-kernel"), head(self.kernel))
        self.assertEqual(self.main(), ["File 001-hello", "A scratch project with one order, 001-hello"])
        self.assertFalse((self.home / ".gitconfig").exists(), "bootstrap wrote a global Git identity")
        self.assertEqual((self.home / ".ceo" / "projects.txt").read_text(), f"{self.project}\n")
        self.assertRegex((self.project / "STATUS.md").read_text(), r"\nHealth: \S+, login ok, ")
        self.assertFalse(held(self.project / ".ceo" / "tick.lock"), "the first tick is still running")
        agent = next((self.home / "Library" / "LaunchAgents").glob("*.plist"), None) or next(
            (self.home / ".config" / "systemd" / "user").glob("*.service"))
        self.assertIn("--every=60.0", agent.read_text())
        self.assertEqual(len(self.loaded()), 1, self.loaded())
        self.assertIn(agent.stem.split(".", 1)[-1], self.loaded()[0])
        # Its one order: the worker's file, naming this host and committed, for a pull request needs a
        # commit, is what its done-when block waits for.
        title, done_when = parse_order((self.project / "orders" / "001-hello.md").read_text())[:2]
        machine = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(title, f"Hello from {machine}")
        work = self.tmp / "work"
        git = lambda *args: subprocess.run(["git", "-C", str(work), "-c", "user.name=w", "-c",
                                            "user.email=w@example.invalid", *args], check=True)
        run = lambda: subprocess.run(["bash", "-c", done_when], cwd=work, capture_output=True).returncode
        subprocess.run(["git", "clone", "-q", str(self.bare / f"ceo-trial-{NAME}.git"), str(work)],
                       check=True)
        self.assertNotEqual(run(), 0)
        (work / "HELLO.md").write_text(f"Hello from {machine}.\n")
        self.assertNotEqual(run(), 0)  # written, not committed
        git("add", "HELLO.md")
        git("commit", "-qm", "hello")
        self.assertEqual(run(), 0)
        self.assertFalse((self.project / "drafts" / "orders" / "001-hello.md").exists())
        # Once more, as after a stop: the same project, listed once, its order filed once. Its first tick
        # now starts the order, whose run, in a session of its own, is let finish before the folder goes.
        # Host resource policy is covered elsewhere; this stub worker must actually finish.
        conf = self.project / "ceo.toml"
        self.assertIn("disk_floor_gb = 5\nram_floor_gb = 1\n", conf.read_text())
        conf.write_text("disk_floor_gb = 0\nram_floor_gb = 0\nrecheck_wait = 0\n")
        self.assertEqual(self.bootstrap().returncode, 0)
        self.assertEqual(len(self.main()), 2)
        self.assertEqual((self.state / "created.log").read_text(), f"owner/ceo-trial-{NAME}\n")
        self.assertEqual((self.home / ".ceo" / "projects.txt").read_text(), f"{self.project}\n")
        ceo, deadline = self.project / ".ceo", time.monotonic() + 60
        ended = lambda: ((ceo / "runs" / "001-hello" / "result.json").exists()
                         and not held(ceo / "locks" / "001-hello.run"))
        while not ended():
            self.assertLess(time.monotonic(), deadline, (ceo / "tick.log").read_text())
            time.sleep(0.1)
        result = read_json(ceo / "runs" / "001-hello" / "result.json")
        self.assertEqual((result["state"], result["worker_exit"], result["done_when_exit"]), ("done", 0, 0))

    def test_a_scratch_repository_is_created_only_when_github_says_it_is_missing(self):
        (self.state / "gh-offline").touch()
        stopped = self.bootstrap()
        self.assertEqual((stopped.returncode, stopped.stdout, stopped.stderr), (1, "", (
            f"bootstrap-host: {NAME}: GitHub did not say whether owner/ceo-trial-{NAME} exists (error "
            "connecting to api.github.com), then run this again.\n")))
        (self.state / "gh-offline").unlink()
        # One GitHub has, made by an earlier run, is the host's scratch repository: none is created.
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.bare / f"ceo-trial-{NAME}.git")],
                       check=True)
        done = self.bootstrap()
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(self.main(), ["File 001-hello", "A scratch project with one order, 001-hello"])
        self.assertFalse((self.state / "created.log").exists())

    def test_a_login_whose_worker_does_not_answer_is_one_to_make_and_is_probed_afresh_after(self):
        (self.state / "claude-refused").touch()
        stopped = self.bootstrap()
        self.assertEqual((stopped.returncode, stopped.stdout), (1, ""))
        self.assertEqual(stopped.stderr, f"bootstrap-host: {NAME}: log in to Claude there by hand, ssh -t "
                         f"{HOST} {self.home}/.local/bin/claude auth login (its worker said: {REFUSED}.), "
                         "then run this again.\n")
        self.assertEqual((self.loaded(), self.main()), ([], ["A scratch project with one order, 001-hello"]))
        (self.state / "claude-refused").unlink()  # logged in anew: no failure the kernel trusts is in the way
        # A day later, so the first pass sweeps, which changes STATUS.md before its slow probe ends.
        old = time.time() - 2 * 86400
        os.utime(self.project / ".ceo" / "last-sweep", (old, old))
        (self.state / "claude-slow").touch()
        done = self.bootstrap()
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(self.main()[0], "File 001-hello")


if __name__ == "__main__":
    unittest.main()
