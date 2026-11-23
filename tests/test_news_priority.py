import copy
import datetime as dt
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import make_brief as mb
import news_priority as priority
import render

DAY = dt.date(2026, 11, 24)
ASOF = dt.datetime.fromisoformat('2026-11-24T07:21:22+09:00')
DATA = {'cutoff': '2026-11-23', 'series': {
    'sp500': {'asof': '2026-11-23', 'pct': 4.73},
    'ndx': {'asof': '2026-11-23', 'pct': 6.86}}}
DRIVER = json.loads((Path(__file__).parent / 'fixtures/market_driver_20261124.json').read_text(encoding='utf-8'))['candidate']


def draft():
    cards = []
    for i, title in enumerate(('토큰 주식 출범', '연준 추가 금리 인상 가능성', '국제 금값 반등', '소비심리 하락')):
        cards.append({'title': title, 'body': title, 'source': '매체 · 11/24',
                      'sources': [{'name': '매체', 'url': f'https://example.test/{i}',
                                   'published_at': '2026-11-24T06:00:00+09:00'}]})
    return {'headlines': cards[:3], 'checkpoint': cards[3]}


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.required = priority.requirements_for(DATA)
        self.evidence = {DRIVER['url']: DRIVER}

    def test_reproduces_fresh_valid_news_with_missing_market_driver(self):
        brief = draft()
        self.assertEqual(mb.news_issues(brief, DAY, asof=ASOF), [])
        self.assertTrue(priority.coverage_issues(brief, self.required))

    def test_stale_or_small_moves_do_not_trigger(self):
        for data in ({'cutoff': '2026-11-23', 'series': {'sp500': {'asof': '2026-11-23', 'pct': 1}}},
                     {'cutoff': '2026-11-23', 'series': {'sp500': {'asof': '2026-11-20', 'pct': 4}}},
                     {**DATA, 'stale': [{'key': 'sp500'}, {'key': 'ndx'}]}, {'series': {}}):
            with self.subTest(data=data):
                self.assertEqual(priority.requirements_for(data), [])

    def generate(self, brief, evidence):
        def api(prompt, key, source_urls):
            source_urls.update(s['url'] for card in brief['headlines'] + [brief['checkpoint']] for s in card['sources'])
            return json.dumps(brief)
        with patch.object(mb, 'call_api', side_effect=api) as call, redirect_stdout(io.StringIO()):
            result = mb.generate_fresh_brief('p', 'test', DAY, asof=ASOF,
                                             evidence=evidence, requirements=self.required)
        return result, call

    def test_retries_then_promotes_verified_bbc_driver_over_minor_cards(self):
        result, call = self.generate(draft(), self.evidence)
        self.assertEqual(call.call_count, 3)
        self.assertIn('필수 시장 핵심 뉴스', call.call_args.args[0])
        self.assertEqual(result['headlines'][0]['title'], DRIVER['title'])
        self.assertEqual(len(result['headlines']), 3)
        self.assertEqual(result['checkpoint']['title'], '소비심리 하락')
        self.assertEqual(priority.coverage_issues(result, self.required, self.evidence), [])
        self.assertFalse(any('주요 시장 뉴스:' in x for x in result['_news_issues']))
        self.assertEqual(mb.news_issues(result, DAY, asof=ASOF), [])

    def test_verified_driver_already_third_is_promoted_without_retry(self):
        brief = draft()
        brief['headlines'][2] = {'title': '젠슨 황 복귀에 엔비디아 폭등', 'body': '나스닥도 급등했습니다.',
                                 'sources': [{k: DRIVER[k] for k in ('name', 'url', 'published_at')}]}
        result, call = self.generate(brief, self.evidence)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result['headlines'][0]['title'], '젠슨 황 복귀에 엔비디아 폭등')

    def test_no_verified_driver_reports_gap_without_inventing_a_cause(self):
        result, _ = self.generate(draft(), {})
        self.assertTrue(priority.coverage_issues(result, self.required))
        self.assertTrue(any('주요 시장 뉴스:' in x for x in result['_news_issues']))
        self.assertNotIn('엔비디아', json.dumps(result, ensure_ascii=False))
        self.assertIn('추가 확인이 필요', render.build_news(result))

    def test_prior_session_rally_cannot_explain_current_session(self):
        old = {**DRIVER, 'published_at': '2026-11-21T06:00:00+09:00'}
        self.assertFalse(priority.matches(old, self.required[0]))
        result = priority.prioritize(draft()['headlines'] + [draft()['checkpoint']],
                                     {old['url']: old}, self.required, *mb.news_window(DAY, ASOF))
        self.assertNotEqual(result[0]['title'], DRIVER['title'])

    def test_rally_story_cannot_satisfy_selloff_guard(self):
        self.assertFalse(priority.matches(DRIVER, {**self.required[0], 'direction': 'down'}))

    def test_malformed_cards_are_reported_instead_of_crashing(self):
        self.assertTrue(priority.coverage_issues({'headlines': [None]}, self.required))
        self.assertFalse(priority.matches({'title': '나스닥 급등', 'sources': [None]}, self.required[0]))

    def test_important_previous_day_story_outranks_fresher_minor_news(self):
        self.assertGreater(priority.rank_key(DRIVER, self.required),
                           priority.rank_key({'title': 'Token stocks launch on exchange',
                                              'published_at': '2026-11-24T07:00:00+09:00'}, self.required))

    def test_only_driver_available_keeps_domestic_slot_separate(self):
        items = priority.prioritize([None, None, None, None], self.evidence,
                                    self.required, *mb.news_window(DAY, ASOF))
        self.assertEqual(len(items), 4)
        self.assertIsNone(items[3])


class ResponseRecoveryTests(unittest.TestCase):
    def call(self, payloads):
        sent = []
        self.urls = set()
        def post(*args, **kwargs):
            sent.append(copy.deepcopy(kwargs['json']))
            return Mock(status_code=200, json=lambda: payloads.pop(0))
        with patch.object(mb.requests, 'post', side_effect=post, create=True), patch.object(mb, 'record_response'):
            result = mb.call_api('prompt', 'test', self.urls)
        return result, sent

    def test_pause_preserves_all_blocks_and_search_evidence(self):
        blocks = [{'type': 'server_tool_use', 'id': 's', 'name': 'web_search', 'input': {}},
                  {'type': 'web_search_tool_result', 'content': [
                      {'type': 'web_search_result', 'url': 'https://example.test/prior-search'}]}]
        result, sent = self.call([{'stop_reason': 'pause_turn', 'content': blocks},
                                 {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'done'}]}])
        self.assertEqual(result, 'done')
        self.assertEqual(sent[1]['messages'][-1], {'role': 'assistant', 'content': blocks})
        self.assertEqual(sent[0]['tools'], sent[1]['tools'])
        self.assertIn('https://example.test/prior-search', self.urls)

    def test_truncation_replays_with_larger_limit(self):
        result, sent = self.call([{'stop_reason': 'max_tokens', 'content': [{'type': 'text', 'text': 'partial'}]},
                                 {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'complete'}]}])
        self.assertEqual(result, 'complete')
        self.assertEqual([x['max_tokens'] for x in sent], [6000, 12000])
        self.assertEqual(sent[0]['messages'], sent[1]['messages'])

    def test_repeated_pauses_and_truncation_are_bounded(self):
        for stop, count in [('pause_turn', 3), ('max_tokens', 2)]:
            with self.subTest(stop=stop), self.assertRaises(mb.GenerationError):
                self.call([{'stop_reason': stop, 'content': []}] * count)

    def test_transport_failure_degrades_instead_of_exiting(self):
        with patch.object(mb, 'call_api', side_effect=mb.GenerationError('HTTP 500')), redirect_stdout(io.StringIO()):
            brief = mb.generate_fresh_brief('p', 'test', DAY)
        self.assertEqual(brief['_news_status'], 'partial')


if __name__ == '__main__':
    unittest.main()
