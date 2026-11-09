"""Decision snapshots retain real Git paths without reading each proof image."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ceo_decisions
from test_ceo import kernel_patch

ROOT = Path(__file__).resolve().parent


class DecidePaths(unittest.TestCase):
    def test_snapshot_keeps_quoted_names_and_reads_only_order_level_files(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                       GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                       GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid')
            names = ('é', 'space name', 'quote"', 'back\\slash', 'tab\tname', 'line\nname', 'carriage\rname')
            expected = {'CEO.md': '# CEO\n', 'LESSONS.md': '# Lessons\n', 'decisions.md': '# Decisions\n'}
            for n, name in enumerate(names, 1):
                expected[f'orders/{n:03d}-{name}.md'] = f'Order {n}\n'
                expected[f'orders/{n:03d}-{name}.cut'] = ''
                expected[f'plans/{name}.md'] = f'Plan {n}\n'
                expected[f'orders/{n:03d}-{name}/proof-{name}.png'] = ''
            for name, content in expected.items():
                path = repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('proof bytes\n' if name.endswith('.png') else content)
            for args in (('init', '-q', '-b', 'main'), ('add', '.'), ('commit', '-qm', 'paths')):
                subprocess.run(['git', *args], cwd=repo, env=env, check=True, capture_output=True)
            with patch.dict(os.environ, env), kernel_patch('blob', wraps=ceo_decisions.blob) as blob:
                snapshot = ceo_decisions.snapshot(repo, 'main')
            for name, content in expected.items():
                self.assertEqual(snapshot.get(name), content, repr(name))
            self.assertEqual({call.args[2] for call in blob.call_args_list},
                             {name for name in expected if not name.endswith('.png')})
            # The stall's CEO is told the page beside the owner's file and the decisions.
            with patch.dict(os.environ, env, CEO_HOME=str(repo / '.ceo' / 'home')):
                told = ceo_decisions.brief(repo, snapshot, {'slug': '001-é'}).decode()
            quoted = json.loads(told.split('Quoted records:\n', 1)[1])
            self.assertEqual(quoted['LESSONS.md'], '# Lessons\n')
            self.assertLessEqual({'$CEO_HOME/OWNER.md', 'decisions.md'}, set(quoted))


if __name__ == '__main__':
    unittest.main()
