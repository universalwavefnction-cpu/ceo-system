"""An order that names its worker, end to end: real git, real processes, real files. `run` and the tick
start the command its '## Worker' names, and its review goes to the reviewer ceo.toml maps that worker's
vendor to. Each model is a stub that prints a marker naming itself, so each log shows who ran."""
import json
import os
import shlex
import sys
import unittest
from unittest import mock

import ceo
from test_ceo import order_text
from test_tick import TickCase

# One stub, installed under each program name a test needs: it prints its marker, then builds as a
# worker, or answers as a reviewer when its folder is a reviewer's.
STUB = r'''
import json, os, subprocess, sys
name, brief = os.path.basename(sys.argv[0]), sys.stdin.read()
role = "reviewer" if os.path.basename(os.path.dirname(os.getcwd())) == "review" else "worker"
print(f"marker: {name} ran as the {role}", flush=True)
if role == "reviewer":
    json.dump({"summary": f"{name} checked the work", "findings": []}, open("REVIEW.json", "w"))
else:
    open("built", "w").write(f"{name}\n")
    subprocess.run(["git", "add", "built"], check=True)
    subprocess.run(["git", "commit", "-qm", f"built by {name}"], check=True)
'''
# A claude and a claude-auto: each keeps its name and arguments in KEPT and answers the login probe, or,
# while FAILS, with the api error of a spent account; set by the lines ahead of it.
CLI = r'''
import json, os, sys
sys.stdin.read()
with open(KEPT, "a") as out:
    out.write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]) + "\n")
print(json.dumps({"type": "result", "is_error": FAILS, "result": "weekly limit" if FAILS else "ok",
                  "terminal_reason": "api_error" if FAILS else "completed"}))
sys.exit(FAILS)
'''
FLAGS = "-p --permission-mode bypassPermissions --output-format stream-json --verbose"
SAME = ("the order's worker and its reviewer are both of the vendor {0}: map {0} to another vendor's "
        "reviewer under [reviewers] in ceo.toml, or set same_vendor_review = true")


class WorkerChoice(TickCase):
    def setUp(self):
        super().setUp()
        self.bin = self.tmp / "models"
        self.bin.mkdir()
        for name in ("astra", "opus", "codex", "plain"):
            (self.bin / name).write_text(f"#!{sys.executable}\n{STUB}")
            (self.bin / name).chmod(0o755)
        self.conf()

    def cmd(self, name, *args):
        return shlex.join([str(self.bin / name), *args])

    def conf(self, *lines, anthropic="codex"):
        """ceo.toml with no floor of the machine's: astra and opus are anthropic's, codex is openai's and
        plain, named for no vendor, claude's; anthropic's work is reviewed by the stub named anthropic."""
        (self.repo / "ceo.toml").write_text("\n".join([
            "disk_floor_gb = 0", "ram_floor_gb = 0", 'night = ""', 'cleaner = ""', *lines,
            "[workers]", f'astra = "{self.cmd("astra", "--drive")}"', f'opus = "{self.cmd("opus")}"',
            "[vendor]", 'astra = "anthropic"', 'opus = "anthropic"', 'codex = "openai"',
            "[reviewers]", f'anthropic = "{self.cmd(anthropic, "--review")}"',
            f'openai = "{self.cmd("opus")}"']) + "\n")

    def named(self, name, worker):
        self.commit_order(f"{name}.md", order_text("test -f built") + f"\n## Worker\n{worker}\n")

    def log(self, slug, name):
        return (self.run_dir(slug) / name).read_text()

    def test_the_tick_builds_an_order_with_the_worker_it_names_and_one_without_with_its_own(self):
        self.named("040-astra", "astra")  # each committed in orders/: filed, the tick starts both
        self.commit_order("041-plain.md")
        self.tick_until(lambda: all((self.run_dir(name) / "review.json").exists()
                                    for name in ("040-astra", "041-plain")),
                        "both orders were built and reviewed",
                        "--worker", self.cmd("plain"), "--reviewer", self.cmd("plain"))
        astra, plain = self.read("040-astra"), self.read("041-plain")
        self.assertEqual((astra["state"], astra["worker"]), ("done", self.cmd("astra", "--drive")))
        self.assertEqual((plain["state"], plain["worker"]), ("done", self.cmd("plain")))
        self.assertIn("marker: astra ran as the worker", self.log("040-astra", "worker.log"))
        self.assertNotIn("marker: plain", self.log("040-astra", "worker.log"))
        self.assertIn("marker: plain ran as the worker", self.log("041-plain", "worker.log"))
        self.assertEqual((self.repo / astra["worktree"] / "built").read_text(), "astra\n")
        # astra is anthropic's, so the reviewer [reviewers] maps anthropic to reviews it, not --reviewer.
        review = self.read("040-astra", "review.json")
        self.assertEqual((review["verdict"], review["reviewer"]),
                         ("survives", self.cmd("codex", "--review")))
        self.assertIn("marker: codex ran as the reviewer", self.log("040-astra", "review.log"))
        self.assertEqual(self.read("041-plain", "review.json")["reviewer"], self.cmd("plain"))
        told = [json.loads(line) for line in (self.repo / ".ceo" / "events.jsonl").read_text().splitlines()]
        started = {one["slug"]: one["sentence"] for one in told if one["event"] == "started"}
        self.assertEqual(started, {
            "040-astra": f"An order: a worker is building it: {self.cmd('astra', '--drive')}.",
            "041-plain": f"An order: a worker is building it: {self.cmd('plain')}."})

    def test_run_takes_a_command_from_the_section_before_its_flag_and_review_follows_its_vendor(self):
        self.named("042-codex", f"`{self.cmd('codex', '--build')}`")  # in backticks, as orders write code
        run = self.ceo("run", "orders/042-codex.md", "--worker", self.cmd("plain"))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.read("042-codex")["worker"], self.cmd("codex", "--build"))
        self.assertIn("marker: codex ran as the worker", self.log("042-codex", "worker.log"))
        # codex is openai's, and [reviewers] maps openai to opus, whatever --reviewer says.
        review = self.ceo("review", "orders/042-codex.md", "--reviewer", self.cmd("plain"))
        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertEqual(self.read("042-codex", "review.json")["reviewer"], self.cmd("opus"))
        self.assertIn("marker: opus ran as the reviewer", self.log("042-codex", "review.log"))

    def test_a_worker_and_reviewer_of_one_vendor_are_refused_before_anything_starts(self):
        self.named("043-alone", "plain")  # claude's, mapped to no reviewer: it would review itself
        self.named("044-twins", "astra")  # anthropic's, mapped below to opus, anthropic's too
        self.conf(anthropic="opus")
        for name, vendor in (("043-alone", "claude"), ("044-twins", "anthropic")):
            with self.subTest(name):
                run = self.ceo("run", f"orders/{name}.md", "--worker", self.cmd("codex"))
                self.assertEqual((run.returncode, run.stderr),
                                 (2, f"ceo: refused: {SAME.format(vendor)}.\n"))
                self.assertFalse((self.repo / ".ceo").exists(), "something was created")
                self.assertEqual(self.git("branch", "--list", "order/*"), "")
        said = self.tick("--worker", self.cmd("codex"))  # both are filed: the tick tries each and says why not
        for name, vendor in (("043-alone", "claude"), ("044-twins", "anthropic")):
            self.assertIn(f"ceo: not started: the run of orders/{name}.md: {SAME.format(vendor)}.", said)
            self.assertFalse(self.run_dir(name).exists(), f"{name} ran")
        # On the owner's word in ceo.toml, a vendor may review its own work.
        self.conf("same_vendor_review = true", anthropic="opus")
        self.assertEqual(self.ceo("run", "orders/044-twins.md").returncode, 0)
        review = self.ceo("review", "orders/044-twins.md")
        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertEqual(self.read("044-twins", "review.json")["reviewer"], self.cmd("opus", "--review"))
        # A section of two lines names no one worker.
        self.commit_order("045-two.md", order_text("test -f built") + "\n## Worker\nastra\nopus\n")
        run = self.ceo("run", "orders/045-two.md")
        self.assertEqual((run.returncode, run.stderr), (2, "ceo: refused: the '## Worker' section must "
                                                           "hold one line, a name under [workers] or a "
                                                           "command.\n"))
        # Nor does a name whose command in ceo.toml is empty.
        self.named("046-empty", "nobody")
        self.conf('workers.nobody = ""')
        run = self.ceo("run", "orders/046-empty.md")
        self.assertEqual((run.returncode, run.stderr),
                         (2, "ceo: refused: the order's worker `` is no command.\n"))
        self.assertFalse(self.run_dir("046-empty").exists(), "something was created")

    def test_the_floors_hold_the_quota_of_the_named_workers_vendor(self):
        self.named("047-low", "astra")
        self.commit_order("048-plain.md")
        self.conf('quota_cmd.anthropic = "echo 3"', 'quota_cmd.claude = "echo 90"')
        run = self.ceo("run", "orders/047-low.md", "--worker", self.cmd("plain"))
        self.assertEqual((run.returncode, run.stderr),
                         (2, "ceo: refused: the anthropic quota is at 3 %, under its floor of 5 %.\n"))
        run = self.ceo("run", "orders/048-plain.md", "--worker", self.cmd("plain"))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.read("048-plain")["worker"], self.cmd("plain"))

    def toml(self, *lines):
        """ceo.toml with no floor of the machine's and no [vendor] line: codex and gemini are vendors by name."""
        (self.repo / "ceo.toml").write_text("\n".join(["disk_floor_gb = 0", "ram_floor_gb = 0", 'night = ""', 'cleaner = ""',
                                                       *lines]) + "\n")

    def test_ceo_tomls_worker_is_the_default_worker_and_reviewer_and_without_a_worker_section_no_vendor_check(self):
        self.commit_order("049-default.md")
        self.commit_order("051-unread.md")
        self.toml(f'worker = "{self.cmd("codex", "exec", "-")}"')
        self.assertEqual(self.ceo("run", "orders/049-default.md").returncode, 0)
        review = self.ceo("review", "orders/049-default.md")
        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertEqual((self.read("049-default")["worker"], self.read("049-default", "review.json")["reviewer"]),
                         (self.cmd("codex", "exec", "-"), self.cmd("codex", "exec", "-")))
        self.assertIn("marker: codex ran as the reviewer", self.log("049-default", "review.log"))
        self.assertEqual(ceo.default_worker(self.repo), self.cmd("codex", "exec", "-"))
        with mock.patch("shutil.which", return_value=None):  # no worker and no claude: nothing to start
            self.toml()
            self.assertIsNone(ceo.default_worker(self.repo))
            self.assertIn("no `worker` is set in ceo.toml", ceo.NO_WORKER)
        self.toml("worker = codex exec")  # unreadable: refused where ceo.toml is read, as before
        self.assertEqual(ceo.default_worker(self.repo), ceo.default_worker())
        run = self.ceo("run", "orders/051-unread.md")
        self.assertEqual((run.returncode, run.stderr),
                         (2, "ceo: refused: ceo.toml cannot be read at line 5: worker = codex exec.\n"))

    def programs(self, folder, *names, kept=None, fails=()):
        """In folder, an executable for each name: a fake claude or claude-auto that fails the probe if named in fails."""
        folder.mkdir()
        for name in names:
            (folder / name).write_text(f"#!{sys.executable}\nKEPT = {str(kept)!r}\nFAILS = {name in fails}\n{CLI}")
            (folder / name).chmod(0o755)

    def test_the_default_worker_is_claude_auto_on_path_else_claude_else_none_and_ceo_toml_wins(self):
        self.toml()
        for names, want in ((("claude", "claude-auto"), "claude-auto"), (("claude",), "claude"),
                            (("claude-auto",), "claude-auto"), ((), None)):
            with self.subTest(names=names):
                folder = self.tmp / f"path-{'-'.join(names) or 'empty'}"
                self.programs(folder, *names)
                with mock.patch.dict(os.environ, {"PATH": str(folder)}):
                    for repo in (self.repo, None):
                        self.assertEqual(ceo.default_worker(repo), want and f"{want} {FLAGS}")
        both = self.tmp / "path-claude-claude-auto"
        (both / "claude-auto").chmod(0o644)  # a wrapper that cannot run is not on PATH
        with mock.patch.dict(os.environ, {"PATH": str(both)}):
            self.assertEqual(ceo.default_worker(self.repo), f"claude {FLAGS}")
            (both / "claude-auto").chmod(0o755)
            self.toml('worker = "mine -p"')  # the owner's own line beats both
            self.assertEqual(ceo.default_worker(self.repo), "mine -p")
            self.assertEqual(ceo.default_worker(), f"claude-auto {FLAGS}")

    def test_the_default_worker_claude_auto_is_probed_and_isolated_as_claude_is_though_claude_would_fail(self):
        self.toml()  # no isolation.claude-auto line: its probe and its flags are the kernel's own
        kept = self.tmp / "kept.jsonl"
        folder = self.tmp / "account-a-spent"
        self.programs(folder, "claude", "claude-auto", kept=kept, fails=("claude",))
        (self.repo / ".ceo").mkdir()
        with mock.patch.dict(os.environ, {"PATH": f"{folder}{os.pathsep}{os.environ['PATH']}"}):
            default = ceo.default_worker(self.repo)
            self.assertTrue(ceo.healthy(self.repo, default))
        isolated = ["--setting-sources", "project"]
        self.assertEqual(ceo.clean(self.repo, default)[0], [*shlex.split(default), *isolated])
        self.assertEqual(ceo.isolation(self.repo, "claude-auto"), ceo.isolation(self.repo, "claude"))
        self.assertEqual([json.loads(line) for line in kept.read_text().splitlines()],
                         [["claude-auto", *FLAGS.split(), *isolated]])  # one probe, never the plain claude
        login = json.loads((self.repo / ".ceo" / "login.json").read_text())
        self.assertEqual({model: why for model, (_, why) in login.items()}, {default: ""})

    def test_codex_is_openais_and_gemini_googles_so_one_reviews_the_other_and_the_quota_follows(self):
        self.named("050-codex", "cx")
        (self.bin / "gemini").write_text(f"#!{sys.executable}\n{STUB}")
        (self.bin / "gemini").chmod(0o755)
        workers = ["[workers]", f'cx = "{self.cmd("codex", "exec", "-")}"', "[reviewers]"]
        self.toml(*workers, f'openai = "{self.cmd("codex", "--review")}"')  # openai's own: refused
        run = self.ceo("run", "orders/050-codex.md")
        self.assertEqual((run.returncode, run.stderr), (2, f"ceo: refused: {SAME.format('openai')}.\n"))
        self.toml('quota_cmd.openai = "echo 3"', 'quota_cmd.claude = "echo 90"', *workers,
                  f'openai = "{self.cmd("gemini", "--yolo")}"')
        run = self.ceo("run", "orders/050-codex.md")
        self.assertEqual((run.returncode, run.stderr),
                         (2, "ceo: refused: the openai quota is at 3 %, under its floor of 5 %.\n"))
        self.toml(*workers, f'openai = "{self.cmd("gemini", "--yolo")}"')
        self.assertEqual(self.ceo("run", "orders/050-codex.md").returncode, 0)
        review = self.ceo("review", "orders/050-codex.md")
        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertEqual(self.read("050-codex", "review.json")["reviewer"], self.cmd("gemini", "--yolo"))
        self.assertIn("marker: gemini ran as the reviewer", self.log("050-codex", "review.log"))
        # vendor.<program> still wins over the name, and any other program is claude's.
        for conf, model, vendor in (({}, "/x/codex exec -", "openai"), ({}, "gemini --yolo", "google"),
                                    ({}, "claude-auto -p", "claude"), ({}, "/x/plain", "claude"),
                                    ({"vendor.codex": "anthropic"}, "codex exec -", "anthropic")):
            self.assertEqual(ceo.vendor_of(conf, model), vendor, model)


if __name__ == "__main__":
    unittest.main()
