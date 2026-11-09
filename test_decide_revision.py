"""An owner answer releases its revision ask, without releasing a later unanswered ask."""
import json
import os
from unittest.mock import patch

import ceo_decisions as decisions
from test_ceo import order_text
from test_tick import TickCase


class RevisionAnswer(TickCase):
    def setUp(self):
        super().setUp()
        original = 'orders/001-original.md'
        self.commit_order('001-original.md', order_text('test -f built'))
        self.commit_order('002-revised.md', '# Revised\n\nORDER: 002\nROUND: 1\n'
                          f'Revises: {original}\n' + order_text('test -f built').split('\n', 1)[1])
        self.path = 'orders/002-round-3.md'
        self.commit_order('002-round-3.md', '# Last round\n\nORDER: 002\nROUND: 3\n'
                          'REPLACES: orders/002-revised.md\n' + order_text('test -f built').split('\n', 1)[1])
        self.stall = dict(slug='002-round-3', since='initial', last_progress=1,
                          state='failed', last_log='The proof needs repair.')
        self.raw = json.dumps(dict(decision='revise', reason='Repair the proof with the same outcome.',
                                   order=order_text('test -f built', title='Repaired proof')))

    def apply(self, raw=None):
        with patch.dict(os.environ, self.env):
            return decisions.apply(self.repo, self.stall, raw or self.raw, timeout=10)

    def reply(self):
        decisions.decision_answer(self.repo, self.stall['slug'], dict(
            pick='answer', comment='Proceed with the further revision and the same outcome.'))

    def test_answered_refusal_files_revision_and_replays_once(self):
        refused = self.apply()
        self.assertEqual(refused['decision'], 'ask')
        self.assertIn('a second revision needs the owner', refused['reason'])
        self.reply()
        words = decisions.records(self.repo, self.stall)['owner.json']['words']
        self.assertEqual(words[-1]['decision'], refused['id'])
        self.stall['since'] = 'answered'
        result = self.apply()
        self.assertEqual(result['decision'], 'revise', result)
        target = 'orders/003-repaired-proof.md'
        text = self.git('show', f'main:{target}')
        self.assertEqual(decisions.header(text, 'Revises'), self.path)
        self.assertEqual(decisions.header(text, 'ORDER'), '003')
        self.assertEqual(decisions.header(text, 'ROUND'), '1')
        self.assertTrue((self.repo / target).is_file())
        self.assertTrue((self.repo / self.path).with_suffix('.cut').is_file())
        self.assertFalse((self.repo / '.ceo/waiting/002-round-3.json').exists())
        head = self.git('rev-parse', 'main')
        self.assertEqual(self.apply(), result)
        self.assertEqual(self.git('rev-parse', 'main'), head)

    def test_earlier_answer_does_not_release_a_later_ask_with_the_same_question(self):
        first = self.apply()
        self.reply()
        self.stall['since'] = 'another-ask'
        asked = self.apply(json.dumps(dict(decision='ask', reason='The revised proposal needs approval.',
                                          question=first['question'])))
        self.assertNotEqual(first['id'], asked['id'])
        self.stall['since'] = 'unanswered'
        result = self.apply()
        self.assertEqual(result['decision'], 'ask', result)
        self.assertIn('a second revision needs the owner', result['reason'])
        self.assertFalse((self.repo / self.path).with_suffix('.cut').exists())
        self.assertFalse((self.repo / 'orders/003-repaired-proof.md').exists())

    def test_answer_on_another_order_does_not_release_this_revision(self):
        self.apply()
        other = {**self.stall, 'slug': '001-original', 'since': 'other-ask'}
        with patch.dict(os.environ, self.env):
            decisions.apply(self.repo, other, json.dumps(dict(decision='ask', reason='Need a decision.',
                                                             question='May this proceed?')), timeout=10)
        decisions.decision_answer(self.repo, other['slug'], dict(pick='answer', comment='Proceed.'))
        self.stall['since'] = 'after-other-answer'
        result = self.apply()
        self.assertEqual(result['decision'], 'ask', result)
        self.assertIn('a second revision needs the owner', result['reason'])
        self.assertFalse((self.repo / self.path).with_suffix('.cut').exists())
