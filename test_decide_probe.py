"""Draft admission uses real probes in clean Git copies before a round is filed."""
import json
import os
import subprocess
import time
from unittest.mock import patch

import ceo
import ceo_decisions as decisions
from test_ceo import order_text
from test_tick import TickCase


class DraftProbe(TickCase):
    def setUp(self):
        super().setUp()
        (self.repo / 'repro_app.py').write_text('ready = False\n')
        self.commit_order('026-ready.md', order_text('test -f built'))
        self.stall = dict(slug='026-ready', since=ceo.now(), last_progress=1,
                          state='failed', last_log='AssertionError: the project is not ready')

    def draft(self, probe, kind='round'):
        return json.dumps(dict(decision=kind, reason='The ready flag is still false.',
                               order=order_text(probe)))

    def apply(self, raw, **kwargs):
        with patch.dict(os.environ, self.env):
            return decisions.apply(self.repo, self.stall, raw, **kwargs)

    def gate(self, body):
        path = self.repo / 'gate.sh'
        path.write_text('#!/bin/sh\nset -e\n' + body + '\n')
        path.chmod(0o755)
        self.git('add', '--', 'gate.sh', 'ceo.toml')
        self.git('commit', '-qm', 'project gate')

    def test_gate_refuses_round_and_revision_without_landing_any_draft_writes(self):
        with (self.repo / 'ceo.toml').open('a') as config:
            config.write('test_cmd = "true"\n')
        self.gate("if grep -q 'Forbidden detail' orders/*.md; then\n"
                  "    echo 'project law: forbidden detail'; exit 7\nfi")
        self.assertEqual(subprocess.run(['./gate.sh'], cwd=self.repo, env=self.env).returncode, 0)
        (self.repo / 'gate.sh').write_text('#!/bin/sh\nexit 0\n')
        for kind in ('round', 'revise'):
            with self.subTest(kind=kind):
                self.stall['since'] = kind
                base = self.git('rev-parse', 'main')
                raw = json.loads(self.draft('test -f built', kind))
                raw['order'] += '\nForbidden detail\n'
                result = self.apply(json.dumps(raw))
                self.assertEqual(result['decision'], 'ask', result)
                self.assertIn(decisions.REFUSED, result['reason'])
                self.assertIn('./gate.sh exited 7', result['reason'])
                self.assertIn('project law: forbidden detail', result['reason'])
                self.assertEqual(self.git('diff', '--name-only', base, 'main'), 'decisions.md')
                self.assertEqual(sorted(p.name for p in (self.repo / 'orders').iterdir()), ['026-ready.md'])
        self.assertEqual((self.repo / 'gate.sh').read_text(), '#!/bin/sh\nexit 0\n')

    def test_gate_sees_complete_revision_and_its_writes_stay_in_the_clean_copy(self):
        (self.repo / 'plans').mkdir()
        (self.repo / 'plans/work.md').write_text('# Work\n- orders/026-ready.md — First\n')
        self.git('add', '--', 'plans/work.md')
        self.gate("if test -f orders/026-ready.cut; then\n"
                  "    test -f orders/027-an-order.md\n"
                  "    grep -q orders/027-an-order.md plans/work.md\n"
                  "    grep -q '\"decision\": \"revise\"' decisions.md\nfi\n"
                  "test \"$(cat repro_app.py)\" = 'ready = False'\n"
                  "echo 'SyntaxError: a caught gate diagnostic'\n"
                  "echo spoiled > repro_app.py\ngit update-ref refs/heads/gate HEAD\ntouch generated")
        (self.repo / 'repro_app.py').write_text('ready = True\n')
        result = self.apply(self.draft('python3 -c "from repro_app import ready; assert ready"', 'revise'))
        self.assertEqual(result['decision'], 'revise', result)
        self.assertTrue((self.repo / 'orders/027-an-order.md').is_file())
        self.assertEqual((self.repo / 'repro_app.py').read_text(), 'ready = True\n')
        self.assertEqual(self.git('branch', '--list', 'gate'), '')
        self.assertFalse((self.repo / 'generated').exists())

    def test_timed_out_gate_files_no_round(self):
        self.gate('sleep 60')
        began = time.monotonic()
        result = self.apply(self.draft('test -f built'), timeout=.1)
        self.assertLess(time.monotonic() - began, 10)
        self.assertEqual(result['decision'], 'ask', result)
        self.assertIn('./gate.sh timed out', result['reason'])
        self.assertFalse((self.repo / 'orders/026-round-2.md').exists())

    def test_diagnostics_are_refused_before_a_round_or_revision_is_filed(self):
        probes = (
            ("python3 -I -c 'import repro_app'", "ModuleNotFoundError: No module named 'repro_app'"),
            ("python3 -c 'if'", 'SyntaxError: invalid syntax'),
            ("python3 -c \"open('missing')\"", "FileNotFoundError: [Errno 2] No such file or directory: 'missing'"),
            ('bash missing.sh', 'No such file or directory'),
            ('there_is_no_such_probe_command', 'command not found'),
        )
        for kind in ('round', 'revise'):
            for index, (probe, diagnostic) in enumerate(probes):
                with self.subTest(kind=kind, probe=probe):
                    self.stall['since'] = f'{kind}-{index}'
                    result = self.apply(self.draft(probe, kind))
                    self.assertEqual(result['decision'], 'ask')
                    self.assertIn(decisions.REFUSED, result['reason'])
                    self.assertIn(diagnostic, result['reason'])
        self.assertEqual(sorted(path.name for path in (self.repo / 'orders').iterdir()), ['026-ready.md'])
        self.assertFalse((self.repo / 'orders/026-ready.cut').exists())

    def test_probe_uses_committed_base_and_cannot_change_project_or_branches(self):
        (self.repo / 'repro_app.py').write_text('ready = True\n')
        probe = ("test \"$(cat repro_app.py)\" = 'ready = False' || { echo 'ImportError: dirty base'; exit 1; }\n"
                 "printf 'spoiled\\n' > repro_app.py\n"
                 'git update-ref refs/heads/probe HEAD\ntouch generated\nexit 1')
        result = self.apply(self.draft(probe))
        self.assertEqual(result['decision'], 'round', result)
        self.assertTrue((self.repo / 'orders/026-round-2.md').exists())
        self.assertEqual((self.repo / 'repro_app.py').read_text(), 'ready = True\n')
        self.assertEqual(self.git('branch', '--list', 'probe'), '')
        self.assertFalse((self.repo / 'generated').exists())

    def test_passing_probe_can_print_a_caught_import_error_and_still_be_filed(self):
        probe = ("python3 -I - <<'CHECK'\ntry:\n    import repro_app\n"
                 "except ImportError as error:\n    print(f'{type(error).__name__}: {error}')\nCHECK")
        result = self.apply(self.draft(probe))
        self.assertEqual(result['decision'], 'round', result)
        self.assertTrue((self.repo / 'orders/026-round-2.md').exists())

    def test_timed_out_probe_files_no_round(self):
        began = time.monotonic()
        result = self.apply(self.draft('sleep 60'), timeout=.1)
        self.assertLess(time.monotonic() - began, 10)
        self.assertEqual(result['decision'], 'ask')
        self.assertIn('timed out', result['reason'])
        self.assertFalse((self.repo / 'orders/026-round-2.md').exists())

    def test_replaying_a_recorded_round_does_not_run_its_probe_again(self):
        marker = self.tmp / 'probed'
        raw = self.draft(f'touch {marker}\nexit 1')
        result = self.apply(raw)
        self.assertEqual(result['decision'], 'round', result)
        self.assertTrue(marker.exists())
        marker.unlink()
        head = self.git('rev-parse', 'main')
        self.assertEqual(self.apply(raw), result)
        self.assertEqual(self.git('rev-parse', 'main'), head)
        self.assertFalse(marker.exists())

    def test_ceo_can_correct_the_probe_after_receiving_its_import_error(self):
        owner = self.tmp / 'ceo'
        owner.mkdir()
        (owner / 'OWNER.md').write_text('The owner wants proof before filing.\n')
        payload, count, captured = (self.tmp / name for name in ('answers', 'count', 'brief'))
        payload.write_text(json.dumps([
            json.loads(self.draft("python3 -I -c 'import repro_app'")),
            json.loads(self.draft("python3 -c 'from repro_app import ready; assert ready'")),
        ]))
        self.stub.write_text('import json, pathlib, sys\n'
                             'payload, count, captured = map(pathlib.Path, sys.argv[1:])\n'
                             'captured.write_text(sys.stdin.read())\n'
                             'n = int(count.read_text()) if count.exists() else 0\n'
                             'count.write_text(str(n + 1))\n'
                             'print(json.dumps(json.loads(payload.read_text())[n]))\n')
        with (self.repo / 'ceo.toml').open('a') as config:
            config.write('ceo = ' + json.dumps(self.model(str(payload), str(count), str(captured))) + '\n')
        self.git('add', '--', 'ceo.toml')
        self.git('commit', '-qm', 'configure the deciding CEO')
        key = decisions.identity(self.stall)
        folder = self.repo / '.ceo/decisions' / key
        folder.mkdir(parents=True)
        ceo.write_json(folder / 'stall.json', self.stall)
        with patch.dict(os.environ, self.env):
            decisions.run(self.repo, key, 5)
        self.assertEqual(ceo.read_json(folder / 'applied.json')['decision'], 'round')
        self.assertEqual(count.read_text(), '2')
        self.assertIn("ModuleNotFoundError: No module named 'repro_app'", captured.read_text())
        self.assertIn('ModuleNotFoundError', ceo.read_json(folder / 'refused.json')['refusal'])
        self.assertTrue((self.repo / 'orders/026-round-2.md').exists())
