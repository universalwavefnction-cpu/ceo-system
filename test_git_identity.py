"""Kernel commits use real Git without a host identity, leaving its configuration unchanged."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ceo


class Identity(unittest.TestCase):
    def test_init_records_and_merge_commit_without_a_host_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
            env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                       CEO_HOME=str(repo / ".ceo-home"))

            def raw(*args):
                return subprocess.run(["git", *args], cwd=repo, env=env,
                                      capture_output=True, text=True)

            for args in (("init", "-q", "-b", "main"), ("config", "user.useConfigOnly", "true"),
                         ("config", "core.hooksPath", os.devnull)):
                self.assertEqual(raw(*args).returncode, 0)
            config = (repo / ".git/config").read_bytes()
            self.assertNotEqual(raw("commit", "--allow-empty", "-qm", "no identity").returncode, 0)

            # init uses an isolated index and commit; an idea records main with commit-tree.
            for args in (("init",), ("idea", "A test idea.")):
                done = subprocess.run([sys.executable, str(ceo.KERNEL), *args], cwd=repo, env=env,
                                      capture_output=True, text=True, timeout=30)
                self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("A test idea.", raw("show", "main:IDEAS.md").stdout)

            with patch.dict(os.environ, env, clear=True):
                for args in (("checkout", "-qb", "feature"),
                             ("commit", "--allow-empty", "-qm", "feature"),
                             ("checkout", "-q", "main"),
                             ("commit", "--allow-empty", "-qm", "main moves"),
                             ("merge", "-q", "--no-ff", "-m", "join", "feature")):
                    done = ceo.git(*args, cwd=repo)
                    self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(dict(os.environ), env, "the kernel changed its process environment")

            self.assertEqual(len(raw("rev-list", "--parents", "-n", "1", "HEAD").stdout.split()), 3)
            identities = raw("log", "--format=%an <%ae>|%cn <%ce>").stdout.splitlines()
            self.assertEqual(identities, ["CEO kernel <ceo@localhost>|CEO kernel <ceo@localhost>"] * 5)
            self.assertEqual((repo / ".git/config").read_bytes(), config)
            self.assertEqual(raw("config", "--get", "user.name").returncode, 1)
            self.assertEqual(raw("config", "--get", "user.email").returncode, 1)


if __name__ == "__main__":
    unittest.main()
