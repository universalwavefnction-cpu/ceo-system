"""Starts invalidate uncommitted decisions across source trees and real draft probes."""
import json
import os
import shlex
import time
from datetime import datetime, timezone
from unittest.mock import patch

import ceo_decisions as decisions
from test_ceo import order_text
from test_tick import TickCase


class StaleDecision(TickCase):
    def setUp(self):
        super().setUp()
        self.commit_order('001-stopped.md', order_text('test -f built'))
        self.stall = dict(slug='001-stopped', since=decisions.now(), last_progress=time.time() - 120,
                          state='failed', last_log='The check failed')
        self.key = decisions.identity(self.stall)
        self.folder = self.repo / '.ceo/decisions' / self.key
        for path in (self.folder, self.repo / '.ceo/locks', self.repo / '.ceo/stalls'):
            path.mkdir(parents=True)
        decisions.write_json(self.folder / 'stall.json', self.stall)
        self.patch_env = patch.dict(os.environ, self.env)
        self.patch_env.start()
        self.addCleanup(self.patch_env.stop)

    def draft(self, gate='test -f better'):
        return json.dumps(dict(decision='revise', reason='Build the smaller outcome.', order=order_text(gate)))

    def start(self, slug, at=None):
        decisions.write_json(self.repo / '.ceo/locks' / f'{slug}.json',
                             dict(started=at or decisions.now(), order=f'orders/{slug}.md'))

    def test_start_during_real_probe_discards_all_draft_writes(self):
        self.commit_order('001-round-2.md', '# Round two\nROUND: 2\n' + order_text('false'))
        head = self.git('rev-parse', 'main')
        lock = self.repo / '.ceo/locks/001-round-2.json'
        code = ('import json\nfrom pathlib import Path\n'
                f'Path({str(lock)!r}).write_text(json.dumps({{"started": {decisions.now()!r}}}))\n')
        raw = self.draft('python3 -c ' + shlex.quote(code) + '\nexit 1')
        self.assertEqual(decisions.apply(self.repo, self.stall, raw, timeout=5), {})
        self.assertTrue(lock.exists(), 'the real draft probe did not run')
        self.assertEqual(self.git('rev-parse', 'main'), head)
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertFalse((self.repo / '.ceo/waiting/001-stopped.json').exists())

    def test_origin_only_replacement_start_skips_the_model_and_repeated_dispatch(self):
        origin = self.tmp / 'origin.git'
        self.git('init', '-q', '--bare', '-b', 'main', str(origin))
        self.git('remote', 'add', 'origin', str(origin))
        old = self.git('rev-parse', 'main')
        self.commit_order('002-successor.md', '# Successor\nREPLACES: orders/001-stopped.md\n' + order_text('false'))
        self.git('push', '-q', 'origin', 'main')
        source = self.git('rev-parse', 'main')
        self.git('reset', '--hard', old)
        self.start('002-successor')  # No result.json or started event exists yet.
        self.assertFalse((self.repo / 'orders/002-successor.md').exists())
        with patch.object(decisions, 'run_bounded', side_effect=AssertionError('stale stop called the CEO')):
            decisions.run(self.repo, self.key, 5)
        self.assertEqual(decisions.read_json(self.folder / 'applied.json'), {})
        self.assertEqual(self.git('rev-parse', 'main'), old)
        self.assertEqual(self.git('rev-parse', 'origin/main'), source)
        decisions.write_json(self.repo / '.ceo/stalls/001-stopped.json', self.stall)
        children = {}
        decisions.dispatch(self.repo, 5, children)
        self.assertEqual(children, {}, 'stale stop dispatched again')

    def test_committed_decision_replays_after_its_successor_starts(self):
        raw = json.dumps(dict(decision='round', reason='Try the next round.', order=order_text('false')))
        entry = decisions.apply(self.repo, self.stall, raw, timeout=5)
        self.assertEqual(entry['decision'], 'round')
        head = self.git('rev-parse', 'main')
        self.start('001-round-2')
        with patch.object(decisions, 'run_bounded', side_effect=AssertionError('committed decision called the CEO')):
            decisions.run(self.repo, self.key, 5)
        self.assertEqual(decisions.read_json(self.folder / 'applied.json'), entry)
        self.assertEqual(self.git('rev-parse', 'main'), head)

    def test_filed_round_and_recently_touched_old_start_do_not_expire_the_stop(self):
        self.commit_order('001-round-2.md', '# Round two\nROUND: 2\n' + order_text('false'))
        prior = datetime.fromtimestamp(self.stall['last_progress'] - 1, timezone.utc).isoformat()
        self.start('001-round-2', prior)
        decisions.event(self.repo, 'started', '001-round-2', 'Earlier worker.', at=prior)
        log = self.repo / '.ceo/runs/001-round-2/worker.log'
        log.parent.mkdir(parents=True)
        log.write_text('A recently copied old log.\n')
        entry = decisions.apply(self.repo, self.stall, self.draft(), timeout=5)
        self.assertEqual(entry['decision'], 'revise')
        self.assertTrue((self.repo / 'orders/001-stopped.cut').exists())
