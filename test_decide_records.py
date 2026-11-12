"""Decisions from stall records: seeded pure transformations and real asynchronous ticks, no network."""
import json
import os
import random
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import ceo
import ceo_decisions as decisions
from test_ceo import kernel_patch, order_text
from test_tick import TickCase

AT = '2026-10-12T12:00:00+00:00'
ORDER = 'orders/001-stopped.md'
TEXT = '# Stopped\n\nORDER: 001\nROUND: 1\n' + order_text('test -f built').split('\n', 1)[1]
PLAN = '# Plan\n- orders/001-stopped.md — First\n- orders/002-next.md after 001 — Next\n- orders/003-last.md after 001, 002 — Last\n'
STALL = dict(slug='001-stopped', since=AT, last_progress=1, state='failed', last_log='The check failed')
SHELL_GATE = r"""set -e
printf '%s\n' 'pytest.mark.skip' | grep -Eq 'pytest\.(mark\.)?'
python3 -B -m unittest \
    test_probe
printf '%s\n' 'C:\cache' | grep -Eq 'C:\\cache'
"""


def answer(kind, **more):
    return json.dumps(dict(decision=kind, reason='The test needs a different implementation.',
                           order=order_text('test -f better', title='Better'), question='Which approach should continue?', **more))


def files():
    return {ORDER: TEXT, 'orders/002-next.md': TEXT, 'orders/003-last.md': TEXT,
            'orders/009-reserved.cut': '', 'plans/work.md': PLAN}


class Generated(unittest.TestCase):
    def test_a_merge_needs_a_run_whose_done_when_passed_and_names_each_counting_finding(self):
        records = {'result.json': dict(state='done', done_when_exit=0, head='a' * 40, pr=92),
                   'lock.json': dict(done_when='test -f built'),
                   'review.json': dict(commit='a' * 40, verdict='fails', findings=[
                       dict(claim='One  counting\nfinding', counts=True),
                       dict(claim='Another finding', counts=True), dict(claim='Not counted', counts=False)])}
        accepts = [dict(claim='One counting finding', why='The owner accepts this limit.'),
                   dict(claim='Another finding', why='This does not hold the order.')]
        raw, snapshot = answer('merge', accepts=accepts), files()
        before = json.dumps([records, snapshot], sort_keys=True)
        entry, writes = decisions.transform(raw, snapshot, STALL, 3, AT, records)
        self.assertEqual((entry['decision'], entry['head'], entry['accepts']), ('merge', 'a' * 40, accepts))
        self.assertEqual(decisions.entries(writes['decisions.md']), [entry])
        self.assertEqual(set(writes), {'decisions.md'})
        self.assertEqual(json.dumps([records, snapshot], sort_keys=True), before, 'the transformation mutated input')
        for key in ('accepts', 'head'):
            missing = {k: v for k, v in entry.items() if k != key}
            self.assertEqual(decisions.entries('- ' + json.dumps(missing)), [])
        invalid = []
        for name in records:
            invalid.append((f'no {name}', {k: v for k, v in records.items() if k != name}))
        for name, fields in [('result.json', ('state', 'done_when_exit', 'head', 'pr')),
                              ('lock.json', ('done_when',)), ('review.json', ('commit',))]:
            for field in fields:
                invalid.append((f'no {field}', {**records, name: {k: v for k, v in records[name].items() if k != field}}))
        for name, field, value in [('result.json', 'state', 'failed'), ('result.json', 'done_when_exit', 1),
                                    ('result.json', 'done_when_exit', False), ('result.json', 'head', ''),
                                    ('result.json', 'pr', 0), ('result.json', 'merged', 'b' * 40),
                                    ('result.json', 'cleared', AT),
                                    ('lock.json', 'done_when', ''), ('lock.json', 'done_when', ' \n'),
                                    ('review.json', 'commit', 'b' * 40)]:
            invalid.append((f'{field}={value!r}', {**records, name: {**records[name], field: value}}))
        for label, bad in invalid:
            with self.subTest(label=label):
                refused, changed = decisions.transform(raw, snapshot, STALL, 3, AT, bad)
                self.assertEqual(refused['decision'], 'ask')
                self.assertTrue(refused['reason'].startswith(decisions.REFUSED), refused)
                self.assertEqual(set(changed), {'decisions.md'})
        for wrong in (None, {}, [], accepts[:1], accepts + [dict(claim='extra', why='accepted')],
                      [dict(claim='One counting finding', why=''), accepts[1]],
                      [dict(claim='One counting finding'), accepts[1]]):
            with self.subTest(accepts=wrong):
                refused, _ = decisions.transform(answer('merge', accepts=wrong), snapshot, STALL, 3, AT, records)
                self.assertEqual(refused['decision'], 'ask')
                self.assertTrue(refused['reason'].startswith(decisions.REFUSED))
        clean = {**records, 'review.json': dict(commit='a' * 40, verdict='survives', findings=[])}
        passed, _ = decisions.transform(answer('merge', accepts=[]), snapshot, STALL, 3, AT, clean)
        self.assertEqual((passed['decision'], passed['accepts']), ('merge', []))
        # An existing decision replays even after the door has consumed its override.
        self.assertEqual(decisions.transform('{}', {**snapshot, **writes}, STALL, 3, AT), (entry, {}))

    def test_360_seeded_answers_and_every_round_obey_the_contract(self):
        covered = set()
        for seed in range(360):
            rng = random.Random(seed)
            kind = rng.choice(('round', 'revise', 'cut', 'ask', 'wait', 'broken', 'bad-order', 'second-revision'))
            covered.add(kind)
            cap = rng.randrange(1, 7)
            for round_ in range(1, cap + 3):
                snapshot = files()
                snapshot[ORDER] = TEXT.replace('ROUND: 1', f'ROUND: {round_}')
                occupied = rng.sample(range(10, 200), rng.randrange(1, 12))
                snapshot.update({f'orders/{n:03d}-taken.cut': '' for n in occupied})
                raw = answer(kind)
                if kind == 'broken':
                    raw = rng.choice(('{', '[]', 'null', '3', '{}', '{"decision":"round"}',
                                      '{"decision":"ask","reason":"x","question":7}'))
                if kind == 'bad-order':
                    raw = json.dumps(dict(decision='round', reason='retry', order='not an order'))
                if kind == 'second-revision':
                    snapshot[ORDER] = snapshot[ORDER].replace('ROUND:', 'Revises: orders/000-older.md\nROUND:')
                    raw = answer('revise')
                saved = dict(snapshot)
                result, writes = decisions.transform(raw, snapshot, STALL, cap, AT)
                want = 'ask' if kind in ('broken', 'bad-order', 'second-revision') or kind == 'round' and round_ >= cap else kind
                self.assertEqual(result['decision'], want, (seed, round_, result))
                self.assertEqual(snapshot, saved, 'the pure function mutated its input')
                self.assertEqual(len(decisions.entries(writes['decisions.md'])), 1)
                self.assertEqual(decisions.entries(writes['decisions.md'])[0], result)
                self.assertEqual(result['stop'], STALL)
                changed = set(writes) - {'decisions.md'}
                if want == 'round':
                    target = f'orders/001-round-{round_ + 1}.md'
                    self.assertEqual(changed, {target})
                    self.assertEqual(decisions.header(writes[target], 'REPLACES'), ORDER)
                    self.assertEqual(decisions.header(writes[target], 'ROUND'), str(round_ + 1))
                    ceo.parse_order(writes[target])
                elif want == 'revise':
                    n = max(occupied) + 1
                    target = f'orders/{n:03d}-better.md'
                    self.assertEqual(changed, {target, ORDER[:-3] + '.cut', 'plans/work.md'})
                    self.assertEqual(writes['plans/work.md'], PLAN.replace(ORDER, target).replace('after 001', f'after {n:03d}'))
                    self.assertEqual(decisions.header(writes[target], 'ORDER'), f'{n:03d}')
                    self.assertEqual(decisions.header(writes[target], 'ROUND'), '1')
                    self.assertEqual(decisions.header(writes[target], 'Revises'), ORDER)
                    ceo.parse_order(writes[target])
                elif want == 'cut':
                    self.assertEqual(changed, {ORDER[:-3] + '.cut'})
                    self.assertEqual(writes[ORDER[:-3] + '.cut'], result['reason'] + '\n')
                else:
                    self.assertEqual(changed, set())
                if want == 'ask':
                    self.assertTrue(result['question'])
                repeated, more = decisions.transform(raw, {**snapshot, **writes}, STALL, cap, AT)
                self.assertEqual((repeated, more), (result, {}))
        self.assertEqual(len(covered), 8)

    def test_unrelated_draft_plan_does_not_prevent_revision(self):
        result, writes = decisions.transform(answer('revise'), {**files(), 'plans/draft.md': '# Undecided'}, STALL, 3, AT)
        self.assertEqual(result['decision'], 'revise')
        self.assertNotIn('plans/draft.md', writes)

    def test_revision_follows_all_round_ancestors_and_preserves_plan_prose(self):
        snapshot = files()
        path = 'orders/001-round-3.md'
        snapshot[path] = TEXT.replace('ROUND: 1', f'ROUND: 3\nREPLACES: {ORDER}')
        stall = {**STALL, 'slug': Path(path).stem}
        result, writes = decisions.transform(answer('revise'), snapshot, stall, 3, AT)
        self.assertEqual(result['decision'], 'revise')
        self.assertEqual(writes['plans/work.md'], PLAN.replace(ORDER, 'orders/010-better.md').replace('after 001', 'after 010'))
        snapshot[ORDER] = TEXT.replace('ROUND:', 'Revises: orders/000-old.md\nROUND:')
        result, writes = decisions.transform(answer('revise'), snapshot, stall, 3, AT)
        self.assertEqual(result['decision'], 'ask')
        self.assertEqual(set(writes), {'decisions.md'})

    def test_second_wait_is_ask_but_progress_allows_a_new_wait(self):
        result, writes = decisions.transform(answer('wait'), files(), STALL, 3, AT)
        progress = datetime.fromisoformat(AT).timestamp()
        stall = {**STALL, 'since': 'later', 'last_progress': progress}
        result, _ = decisions.transform(answer('wait'), {**files(), **writes}, stall, 3, AT)
        self.assertEqual(result['decision'], 'ask')
        result, _ = decisions.transform(answer('wait'), {**files(), **writes}, {**stall, 'last_progress': progress + 1}, 3, AT)
        self.assertEqual(result['decision'], 'wait')

    def test_owner_judgment_is_not_replaced_by_matches_in_any_record(self):
        text = 'live data on the server, version 2; cut the deployment rather than spend money'
        for kind in ('round', 'revise', 'cut', 'wait', 'ask'):
            raw = json.loads(answer(kind))
            raw['reason'] = text
            raw['order'] += '\n' + text
            snapshot = {**files(), ORDER: TEXT + '\n' + text,
                        'CEO.md': '## The owner is asked\n1. Money.\n2. A version going live on a server.'}
            result, _ = decisions.transform(json.dumps(raw), snapshot, {**STALL, 'last_log': text}, 3, AT)
            self.assertEqual(result['decision'], kind)

    def test_drafted_round_with_prose_keeps_number_and_cap(self):
        for value, current, expected in [('2 of 3. Builds on round 1 at b932822 ...', 1, 2),
                                         ('3 of 3', 1, 3), ('4 of 3', 1, None),
                                         ('2 of 3', 3, None), ('two of 3', 1, None),
                                         ('2.5 of 3', 1, None)]:
            with self.subTest(value=value, current=current):
                raw = json.loads(answer('round'))
                raw['order'] = TEXT.replace('ROUND: 1', f'ROUND: {value}')
                snapshot = {**files(), ORDER: TEXT.replace('ROUND: 1', f'ROUND: {current}')}
                result, writes = decisions.transform(json.dumps(raw), snapshot, STALL, 3, AT)
                if expected is None:
                    self.assertEqual(result['decision'], 'ask')
                    self.assertEqual(set(writes), {'decisions.md'})
                else:
                    self.assertEqual(result['decision'], 'round')
                    target = f'orders/001-round-{expected}.md'
                    self.assertEqual(decisions.header(writes[target], 'ROUND'), str(expected))
                    self.assertEqual(decisions.header(writes[target], 'REPLACES'), ORDER)
                    self.assertEqual(set(writes), {'decisions.md', target})

    def test_copied_gate_with_doubled_backslashes_is_refused_before_round_or_revision(self):
        drafts = [SHELL_GATE.replace('\\\n', '\\\\\n'),
                  SHELL_GATE.replace(r'pytest\.(mark\.)?', r'pytest\\.(mark\\.)?'),
                  SHELL_GATE.replace('\\', '\\\\')]
        for kind in ('round', 'revise'):
            for gate in drafts:
                with self.subTest(kind=kind, gate=gate):
                    raw = json.loads(answer(kind))
                    raw['order'] = order_text(gate)
                    snapshot = {**files(), ORDER: order_text(SHELL_GATE)}
                    result, writes = decisions.transform(json.dumps(raw), snapshot, STALL, 3, AT)
                    self.assertEqual(result['decision'], 'ask')
                    self.assertIn('backslashes', result['reason'])
                    self.assertEqual(set(writes), {'decisions.md'})

    def test_legitimate_escaping_in_unchanged_new_and_changed_gates_is_preserved(self):
        goal = r'Keep the literal \\ path and the \n text in the order.'
        for kind in ('round', 'revise'):
            for current, gate in [(SHELL_GATE, SHELL_GATE),
                                  ('test -f built', SHELL_GATE),
                                  (SHELL_GATE, SHELL_GATE.replace('test_probe', 'test_other'))]:
                with self.subTest(kind=kind, current=current, gate=gate):
                    raw = json.loads(answer(kind))
                    raw['order'] = order_text(gate, title='Better', goal=goal)
                    snapshot = {**files(), ORDER: order_text(current)}
                    result, writes = decisions.transform(json.dumps(raw), snapshot, STALL, 3, AT)
                    self.assertEqual(result['decision'], kind)
                    target = 'orders/001-round-2.md' if kind == 'round' else 'orders/010-better.md'
                    self.assertEqual(ceo.parse_order(writes[target])[1], ceo.parse_order(raw['order'])[1])
                    self.assertIn(goal, writes[target])

    def test_unreadable_old_gate_does_not_prevent_a_valid_draft(self):
        snapshot = {**files(), ORDER: TEXT.replace('## Done when', '## Old proof')}
        result, _ = decisions.transform(answer('round'), snapshot, STALL, 3, AT)
        self.assertEqual(result['decision'], 'round')

    def test_bad_headers_become_asks_and_a_filed_round_is_recorded(self):
        for snapshot, raw in [(files(), answer('round').replace('## Goal', 'ROUND: 999\n\n## Goal')),
                               ({**files(), ORDER: TEXT.replace('ROUND: 1', 'ROUND: nope')}, answer('round'))]:
            result, writes = decisions.transform(raw, snapshot, STALL, 3, AT)
            self.assertEqual(result['decision'], 'ask')
            self.assertEqual(set(writes), {'decisions.md'})
        result, writes = decisions.transform(answer('round'), {**files(), 'orders/001-round-2.md': TEXT}, STALL, 3, AT)
        self.assertEqual((result['decision'], set(writes)), ('round', {'decisions.md'}))
        self.assertTrue(result['reason'].startswith('orders/001-round-2.md is already filed: '), result['reason'])

    def test_last_object_handles_prose_fences_and_nested_json(self):
        final = dict(decision='cut', reason='A {brace} and an escaped "quote".', extra={'nested': True})
        text = 'Earlier: {"decision":"wait"}\n```json\n' + json.dumps(final, indent=2) + '\n```\n'
        self.assertEqual(json.loads(decisions.last_object(text)), final)
        for raw in ('', 'garbage', '{not json'):
            with self.assertRaises(ceo.Refused):
                decisions.last_object(raw)


STUB = r'''
import json, os, sys, time
from pathlib import Path
from unittest.mock import patch
mode, ready, release, supplied, count, captured = sys.argv[1:]
brief = sys.stdin.read()
Path(captured).write_text(brief)
p = Path(count)
n = int(p.read_text()) + 1 if p.exists() else 1
p.write_text(str(n))
Path(ready).write_text(str(os.getpid()))
while not Path(release).exists():
    time.sleep(.02)
if mode == 'hang':
    time.sleep(60)
elif mode == 'garbage':
    print('not json')
elif mode == 'invalid-json':
    Path('DECISION.json').write_text('not json')
elif mode == 'stdout':
    print('{"decision":"wait","reason":"Earlier answer"}\nThe records have been read.\n' + Path(supplied).read_text())
    print('{"decision":"ask","reason":"stderr is not stdout","question":"Wrong stream?"}', file=sys.stderr)
elif mode == 'silent':
    pass
elif mode == 'refused-api' and n == 1:
    print('{}')
elif mode in ('api', 'refused-api') or mode == 'retry' and n == 1:
    print('{"type":"result","terminal_reason":"api_error","result":"test outage"}')
else:
    print(Path(supplied).read_text())
'''


class RealTicks(TickCase):
    def setUp(self):
        super().setUp()
        self.stub.write_text(STUB)
        self.ready, self.payload, self.count, self.captured = (self.tmp / n for n in ('ready', 'payload', 'count', 'brief'))
        self.commit_order('001-stopped.md', TEXT)
        for name in ('002-next.md', '003-last.md'):
            (self.repo / 'orders' / name).write_text(TEXT)
        (self.repo / 'orders/009-reserved.cut').touch()
        (self.repo / 'plans').mkdir()
        (self.repo / 'plans/work.md').write_text(PLAN)
        (self.repo / 'CEO.md').write_text('Only the owner approves money and a version going live.\n')
        owner = Path(self.env['CEO_HOME'])
        owner.mkdir(exist_ok=True)
        (owner / 'OWNER.md').write_text('The owner wants proof from records.\n')
        self.run_dir('001-stopped').mkdir(parents=True)
        (self.run_dir('001-stopped') / 'result.json').write_text(json.dumps(dict(state='failed', reason='test failed')))
        (self.run_dir('001-stopped') / 'review.json').write_text(json.dumps(dict(verdict='fails', findings=[dict(claim='bad output')])) )
        self.stall = {**STALL, 'since': ceo.now(), 'last_progress': time.time() + 30}
        self.stall_path = self.repo / '.ceo/stalls/001-stopped.json'
        self.stall_path.parent.mkdir()
        ceo.write_json(self.stall_path, self.stall)
        # Preexisting stalls remain input even while the model is held; no new worker starts.
        self.configure('answer')
        self.git('add', 'orders', 'plans', 'CEO.md', 'ceo.toml')
        self.git('commit', '-qm', 'records')

    def configure(self, mode):
        command = self.model(mode, str(self.ready), str(self.release), str(self.payload), str(self.count), str(self.captured))
        (self.repo / 'ceo.toml').write_text('disk_floor_gb = 0\nram_floor_gb = 0\nnight = ""\ncleaner = ""\nstall_minutes = 1\nceo = ' + json.dumps(command) + '\n')

    def age(self):
        # Keep the order stalled: eligible since, and its records untouched since, old (whole, as the ISO time
        # keeps it exactly), the stall's last progress; an Order 43 record.
        old, ready = int(time.time()) - 120, self.repo / '.ceo/ready'
        ready.mkdir(exist_ok=True)
        ceo.write_json(ready / '001-stopped.json',
                       dict(since=datetime.fromtimestamp(old, timezone.utc).isoformat(), reason='', passes=0))
        for path in self.run_dir('001-stopped').glob('*'):
            os.utime(path, (old, old))
        self.stall['last_progress'] = old
        ceo.write_json(self.stall_path, self.stall)

    def launch(self, kind, mode='answer', timeout=15, raw=None):
        self.payload.write_text(answer(kind) if raw is None else raw)
        self.configure(mode)
        self.age()
        start = time.monotonic()
        self.idle_tick('--timeout', str(timeout))
        self.assertLess(time.monotonic() - start, 5, 'tick waited for a held CEO')
        self.wait_for(self.ready.exists, 'CEO began')
        self.folder = self.repo / '.ceo/decisions' / decisions.identity(self.stall)
        self.addCleanup(lambda: self.wait_for(lambda: (self.folder / 'applied.json').exists(), 'owned CEO finished', timeout=30))
        self.addCleanup(self.release.touch)
        self.assertFalse((self.repo / 'decisions.md').exists())

    def finish(self):
        self.release.touch()
        self.wait_for(lambda: (self.folder / 'applied.json').exists(), 'decision applied')
        self.assertEqual(len(decisions.entries((self.repo / 'decisions.md').read_text())), 1)
        self.assertEqual(self.git('show', 'main:decisions.md'), (self.repo / 'decisions.md').read_text().strip())
        return ceo.read_json(self.folder / 'applied.json')

    def test_round(self):
        self.launch('round')
        self.assertEqual(self.finish()['decision'], 'round')
        self.assertEqual(decisions.header(self.git('show', 'main:orders/001-round-2.md'), 'REPLACES'), ORDER)
        brief = self.captured.read_text()
        for expected in ('$CEO_HOME/OWNER.md', 'CEO.md', 'plans/work.md', ORDER, 'result.json', 'review.json', 'bad output', 'stall', 'decisions.md'):
            self.assertIn(expected, brief)

    def test_revise(self):
        self.launch('revise')
        self.assertEqual(self.finish()['decision'], 'revise')
        self.assertTrue((self.repo / 'orders/001-stopped.cut').exists())
        self.assertTrue((self.repo / 'orders/010-better.md').exists())
        self.assertEqual((self.repo / 'plans/work.md').read_text(), PLAN.replace(ORDER, 'orders/010-better.md').replace('after 001', 'after 010'))
        snap = ceo.plan_snapshot(self.repo)
        self.assertEqual(ceo.decide_plans(snap)['start'], ['orders/010-better.md'])

    def test_second_draft_with_doubled_backslashes_records_ask_without_an_order(self):
        self.commit_order('001-stopped.md', order_text(SHELL_GATE))
        base = self.git('rev-parse', 'main')
        raw = json.loads(answer('round'))
        raw['order'] = order_text(SHELL_GATE.replace('\\\n', '\\\\\n'))
        self.launch('round', raw=json.dumps(raw))
        result = self.finish()
        self.assertEqual(result['decision'], 'ask')
        self.assertIn('backslashes', result['reason'])
        self.assertEqual(self.count.read_text(), '2')
        self.assertEqual(self.git('diff', '--name-only', base, 'main'), 'decisions.md')
        self.assertFalse((self.repo / 'orders/001-round-2.md').exists())
        self.assertEqual(ceo.read_json(self.folder / 'refused.json')['answer'], raw)
        self.assertIn('one second chance', self.captured.read_text())

    def test_cut(self):
        self.launch('cut')
        result = self.finish()
        self.assertEqual(result['decision'], 'cut')
        self.assertEqual((self.repo / 'orders/001-stopped.cut').read_text().strip(), result['reason'])

    def test_stdout_answer_is_saved_and_applied(self):
        self.launch('cut', 'stdout')
        self.assertEqual(self.finish()['decision'], 'cut')
        self.assertEqual(json.loads((self.folder / 'DECISION.json').read_text()), json.loads(self.payload.read_text()))

    def test_silent_ceo_becomes_one_ask(self):
        self.launch('cut', 'silent')
        self.assertEqual(self.finish()['decision'], 'ask')
        self.assertEqual(self.count.read_text(), '1')
        self.assertTrue((self.repo / '.ceo/waiting/001-stopped.json').exists())

    def test_live_data_on_the_server_version_2_is_still_cut(self):
        (self.repo / ORDER).write_text(TEXT + '\nlive data on the server, version 2\n')
        self.git('add', ORDER)
        self.git('commit', '-qm', 'order mentions a version')
        self.launch('cut', 'stdout')
        self.assertEqual(self.finish()['decision'], 'cut')

    def test_origin_ahead_replays_its_decision_without_local_main_entry(self):
        self.age()
        origin = self.tmp / 'origin.git'
        self.git('init', '-q', '--bare', '-b', 'main', str(origin))
        self.git('remote', 'add', 'origin', str(origin))
        self.git('push', '-q', '-u', 'origin', 'main')
        initial = self.git('rev-parse', 'main')
        other = self.tmp / 'other'
        self.git('clone', '-q', str(origin), str(other))
        entry, writes = decisions.transform(answer('cut'), files(), self.stall, 3, AT)
        for name, text in writes.items():
            (other / name).write_text(text)
        self.git('-C', str(other), 'add', 'decisions.md', ORDER[:-3] + '.cut')
        self.git('-C', str(other), 'commit', '-qm', 'remote decision')
        self.git('-C', str(other), 'push', '-q', 'origin', 'main')
        with patch.dict(os.environ, self.env):
            self.assertEqual(decisions.apply(self.repo, self.stall, answer('round')), entry)
        self.assertEqual(self.git('rev-parse', 'main'), initial)
        self.assertEqual(len(decisions.entries(self.git('show', 'origin/main:decisions.md'))), 1)
        self.idle_tick()
        folder = self.repo / '.ceo/decisions' / decisions.identity(self.stall)
        self.wait_for(lambda: (folder / 'applied.json').exists(), 'remote decision replayed')
        self.assertEqual(ceo.read_json(folder / 'applied.json'), entry)
        self.assertFalse(self.count.exists(), 'the recorded decision called the CEO again')

    def test_apply_read_error_records_one_ask(self):
        with patch.dict(os.environ, self.env), patch.object(decisions, 'snapshot', side_effect=OSError('records unreadable')):
            entry = decisions.apply(self.repo, self.stall, answer('cut'))
        self.assertEqual(entry['decision'], 'ask')
        self.assertIn('records unreadable', entry['reason'])
        self.assertEqual(decisions.entries(self.git('show', 'main:decisions.md')), [entry])
        self.assertEqual(ceo.read_json(self.repo / '.ceo/waiting/001-stopped.json')['decision'], entry['id'])

    def test_unwritable_main_still_keeps_one_ask(self):
        with patch.dict(os.environ, self.env), kernel_patch('record_on_main', side_effect=ceo.Refused('main unavailable')):
            entry = decisions.apply(self.repo, self.stall, answer('cut'))
        self.assertEqual(entry['decision'], 'ask')
        self.assertIn('main unavailable', entry['reason'])
        self.assertEqual(ceo.read_json(self.repo / '.ceo/waiting/001-stopped.json')['decision'], entry['id'])

    def test_ask(self):
        self.launch('ask')
        self.assertEqual(self.finish()['decision'], 'ask')
        waiting = ceo.read_json(self.repo / '.ceo/waiting/001-stopped.json')
        self.assertEqual(waiting['question'], 'Which approach should continue?')
        rows = json.loads(self.ceo('status', '--json').stdout)['rows']
        self.assertEqual((rows[0]['state'], rows[0]['question']), ('waiting', waiting['question']))

    def test_terminal_events_clear_only_the_earlier_stop_question(self):
        self.launch('ask')
        entry = self.finish()
        waiting = self.repo / '.ceo/waiting/001-stopped.json'
        saved = waiting.read_bytes()
        for kind in ('done', 'failed', 'cut', 'merged'):
            with self.subTest(event=kind):
                waiting.write_bytes(saved)
                ceo.event(self.repo, kind, '001-stopped', 'The run reached a new state.')
                self.idle_tick()
                self.assertFalse(waiting.exists(), f'{kind} left the old question waiting')
                self.idle_tick()
                self.assertFalse(waiting.exists(), 'the cleared ask was replayed')
        self.assertEqual(decisions.entries(self.git('show', 'main:decisions.md')), [entry])

    def test_current_question_survives_progress_and_other_orders_finishing(self):
        self.launch('ask')
        self.finish()
        waiting = self.repo / '.ceo/waiting/001-stopped.json'
        saved = waiting.read_bytes()
        at = datetime.fromtimestamp(self.stall['last_progress'] - 1, timezone.utc).isoformat()
        ceo.event(self.repo, 'failed', '001-stopped', 'The failure this ask is about.', at=at)
        for kind in ('waiting', 'moving', 'stalled', 'started', 'reviewed', 'wait'):
            ceo.event(self.repo, kind, '001-stopped', 'Progress within the current run.')
        (self.run_dir('001-stopped') / 'worker.log').write_text('Still working.\n')
        ceo.event(self.repo, 'done', '002-next', 'Another order finished.')
        self.idle_tick()
        self.assertEqual(waiting.read_bytes(), saved)

    def test_run_finishing_while_ceo_answers_expires_the_late_ask(self):
        self.launch('ask')
        ceo.event(self.repo, 'done', '001-stopped', 'Finished while the CEO was answering.')
        self.finish()
        self.idle_tick()
        self.assertFalse((self.repo / '.ceo/waiting/001-stopped.json').exists())

    def test_legacy_ask_without_stall_record_expires_after_its_timestamp(self):
        self.launch('ask')
        self.finish()
        (self.folder / 'stall.json').unlink()
        waiting = self.repo / '.ceo/waiting/001-stopped.json'
        saved = waiting.read_bytes()
        ceo.event(self.repo, 'failed', '001-stopped', 'The failure predates the ask.', at=AT)
        self.idle_tick()
        self.assertEqual(waiting.read_bytes(), saved)
        ceo.event(self.repo, 'done', '001-stopped', 'The run finished after the ask.')
        self.idle_tick()
        self.assertFalse(waiting.exists())

    def test_terminal_event_preserves_nondecision_waiting_rows(self):
        waiting = self.repo / '.ceo/waiting/001-stopped.json'
        for extra in ({'spins': []}, {'bug': 13}):
            with self.subTest(waiting=extra):
                ceo.wait_on(self.repo, '001-stopped', {'order': ORDER, 'question': 'An owner choice is still needed.', **extra})
                saved = waiting.read_bytes()
                ceo.event(self.repo, 'done', '001-stopped', 'The worker finished.')
                self.idle_tick()
                self.assertEqual(waiting.read_bytes(), saved)

    def test_the_owners_word_on_a_stop_its_ceo_asked_about_reaches_the_next_brief(self):
        self.launch('ask')
        self.assertEqual(self.finish()['decision'], 'ask')
        waiting = self.repo / '.ceo/waiting/001-stopped.json'
        self.assertEqual(ceo.read_json(waiting)['choices'], ceo.DECIDE_CHOICES)
        said = self.ceo('answer', '001-stopped', 'answer', '--comment', 'The feed follows the network.')
        self.assertEqual(said.returncode, 0, said.stderr)
        self.idle_tick()
        self.assertFalse(waiting.exists(), 'the ask the owner answered still waits for his word')
        with patch.dict(os.environ, self.env):
            brief = decisions.brief(self.repo, decisions.snapshot(self.repo, 'main'), self.stall).decode()
        self.assertIn('The feed follows the network.', brief)

    def test_the_owners_cut_of_a_stop_its_ceo_asked_about_is_recorded_on_main(self):
        self.launch('ask')
        self.assertEqual(self.finish()['decision'], 'ask')
        waiting = self.repo / '.ceo/waiting/001-stopped.json'  # a row written before the choices were
        ceo.write_json(waiting, {key: value for key, value in ceo.read_json(waiting).items()
                                 if key != 'choices'})
        said = self.ceo('answer', '001-stopped', 'cut', '--comment', 'Another round is not worth it.')
        self.assertEqual(said.returncode, 0, said.stderr)
        self.idle_tick()
        self.assertFalse((self.repo / '.ceo/waiting/001-stopped.json').exists())
        self.assertIn('Another round is not worth it', self.git('show', 'main:orders/001-stopped.cut'))

    def test_wait_and_second_wait(self):
        self.launch('wait')
        self.assertEqual(self.finish()['decision'], 'wait')
        self.assertFalse(self.stall_path.exists())
        self.idle_tick()
        self.assertFalse(self.stall_path.exists(), 'wait did not grant another interval')
        config = self.repo / 'ceo.toml'
        config.write_text(config.read_text().replace('stall_minutes = 1', 'stall_minutes = 0'))
        self.idle_tick()
        self.wait_for(lambda: len(decisions.entries((self.repo / 'decisions.md').read_text())) == 2, 'second wait escalated')
        self.assertEqual(decisions.entries((self.repo / 'decisions.md').read_text())[-1]['decision'], 'ask')

    def test_wait_does_not_mark_an_unstarted_order_as_run(self):
        for path in self.run_dir('001-stopped').glob('*'):
            path.unlink()
        self.run_dir('001-stopped').rmdir()
        self.launch('wait')
        self.assertEqual(self.finish()['decision'], 'wait')
        self.assertFalse(ceo.run_recorded(self.repo, '001-stopped'))

    def test_brief_carries_exactly_the_last_50_entries_as_quoted_data(self):
        history = [{"id": str(n), "order": 'orders/999-other.md', "decision": 'cut', "reason": str(n)}
                   for n in range(60)]
        (self.repo / 'decisions.md').write_text(''.join('- ' + json.dumps(one) + '\n' for one in history))
        self.git('add', 'decisions.md')
        self.git('commit', '-qm', 'prior decisions')
        with patch.dict(os.environ, self.env):
            raw = decisions.brief(self.repo, decisions.snapshot(self.repo, 'main'), self.stall).decode()
        data = json.loads(raw.split('Quoted records:\n', 1)[1])
        self.assertEqual(data['decisions.md'], history[-50:])
        self.assertEqual(data['stall'], self.stall)
        self.assertEqual(data[ORDER], TEXT)
        self.assertIn('The owner wants proof', data['$CEO_HOME/OWNER.md'])
        self.assertIn('Use no tool', raw)
        self.assertIn('Print exactly one JSON object as the last thing on stdout', raw)

    def test_hang_becomes_one_ask(self):
        self.launch('wait', 'hang', 2)
        result = self.finish()
        self.assertEqual(result['decision'], 'ask')
        self.assertIn('timed out', result['reason'])
        self.assertEqual(self.count.read_text(), '1')

    def test_garbage_becomes_one_ask(self):
        self.launch('cut', 'garbage')
        self.assertEqual(self.finish()['decision'], 'ask')
        self.assertEqual(self.count.read_text(), '1')

    def test_unparseable_file_becomes_one_ask(self):
        self.launch('cut', 'invalid-json')
        result = self.finish()
        self.assertEqual(result['decision'], 'ask')
        self.assertIn('CEO answer refused', result['reason'])
        self.assertEqual(self.count.read_text(), '1')

    def assert_api_error_retries_next_tick(self, mode, attempts):
        self.launch('round', mode)
        self.release.touch()
        self.wait_for(lambda: not any(ceo.held(self.folder / name) for name in ('launch', 'lock')),
                      'CEO deferred its api error')
        self.assertEqual(self.count.read_text(), str(attempts))
        for path in (self.folder / 'applied.json', self.repo / 'decisions.md',
                     self.repo / '.ceo/waiting/001-stopped.json'):
            self.assertFalse(path.exists(), f'api error left a decision at {path}')
        self.configure('answer')
        self.idle_tick()
        self.assertEqual(self.finish()['decision'], 'round')
        self.assertEqual(self.count.read_text(), str(attempts + 1))
        self.assertFalse((self.folder / 'retry.json').exists())
        self.assertTrue((self.repo / 'orders/001-round-2.md').exists())
        self.assertFalse((self.repo / '.ceo/waiting/001-stopped.json').exists())

    def test_api_error_retries_once_then_retries_next_tick(self):
        self.assert_api_error_retries_next_tick('api', 2)

    def test_api_error_on_corrected_answer_retries_next_tick(self):
        self.assert_api_error_retries_next_tick('refused-api', 3)

    def test_api_error_stays_pending_then_a_later_tick_applies_the_answer(self):
        self.launch('cut', 'api')
        config = self.repo / 'ceo.toml'
        isolation = f'\n[isolation]\n{Path(sys.executable).name} = ""\n'
        config.write_text(config.read_text() + isolation)
        model = ceo.settings(self.repo)['ceo']
        # A login success before the failed decision cannot release its retry.
        ceo.write_json(self.repo / '.ceo/login.json', {model: [time.time() - 60, '']})
        self.release.touch()
        self.wait_for(lambda: not ceo.held(self.folder / 'launch') and not ceo.held(self.folder / 'lock'),
                      'CEO left its API failure pending')
        self.assertIn('api error after one retry', ceo.read_json(self.folder / 'retry.json')['reason'])
        self.assertEqual(self.count.read_text(), '2')
        self.assertFalse((self.folder / 'applied.json').exists())
        self.assertFalse((self.repo / 'decisions.md').exists())
        self.assertFalse((self.repo / '.ceo/waiting/001-stopped.json').exists())
        self.commit_order('004-independent.md', order_text('true'))
        self.tick()  # the decision backs off; unrelated work can still start
        self.assertEqual(self.count.read_text(), '2')
        self.wait_for(self.run_dir('004-independent').exists, 'unrelated order started')
        self.assertNotIn('LOGIN FAILED', (self.repo / 'STATUS.md').read_text())
        self.configure('answer')
        config.write_text(config.read_text() + isolation)
        model = ceo.settings(self.repo)['ceo']
        ceo.write_json(self.repo / '.ceo/login.json', {model: [time.time(), '']})
        self.tick()
        self.assertEqual(self.finish()['decision'], 'cut')
        self.assertEqual(self.count.read_text(), '3')
        self.assertFalse((self.folder / 'retry.json').exists())

    def test_api_retry_can_answer(self):
        self.launch('cut', 'retry')
        self.assertEqual(self.finish()['decision'], 'cut')
        self.assertEqual(self.count.read_text(), '2')

    def test_three_passes_and_restart_keep_one_decision(self):
        self.launch('round')
        for _ in range(3):
            self.idle_tick()
        self.assertEqual(self.count.read_text(), '1')
        self.finish()
        for _ in range(3):
            self.idle_tick()
        self.assertEqual(self.count.read_text(), '1')
        self.assertEqual(len(decisions.entries((self.repo / 'decisions.md').read_text())), 1)
        (self.folder / 'applied.json').unlink()
        self.idle_tick()
        self.wait_for(lambda: (self.folder / 'applied.json').exists(), 'journal replay')
        self.assertEqual(self.count.read_text(), '1')
        self.assertEqual(len(decisions.entries((self.repo / 'decisions.md').read_text())), 1)


if __name__ == '__main__':
    unittest.main()
