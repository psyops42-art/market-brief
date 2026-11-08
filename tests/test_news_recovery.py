import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import make_brief as mb


class ResponseRecoveryTests(unittest.TestCase):
    def call(self, payloads):
        sent = []
        def post(*args, **kwargs):
            sent.append(copy.deepcopy(kwargs['json']))
            return Mock(status_code=200, json=lambda: payloads.pop(0))
        with patch.object(mb.requests, 'post', side_effect=post, create=True), patch.object(mb, 'record_response'):
            result = mb.call_api('prompt', 'test', set())
        return result, sent

    def test_pause_preserves_all_assistant_blocks(self):
        blocks = [{'type': 'server_tool_use', 'id': 's', 'name': 'web_search', 'input': {}},
                  {'type': 'web_search_tool_result', 'content': []}]
        result, sent = self.call([{'stop_reason': 'pause_turn', 'content': blocks},
                                 {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'done'}]}])
        self.assertEqual(result, 'done')
        self.assertEqual(sent[1]['messages'][-1], {'role': 'assistant', 'content': blocks})
        self.assertEqual(sent[0]['tools'], sent[1]['tools'])

    def test_max_tokens_replays_at_higher_limit(self):
        result, sent = self.call([{'stop_reason': 'max_tokens', 'content': [{'type': 'text', 'text': '{"headlines":'}]},
                                 {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'complete'}]}])
        self.assertEqual(result, 'complete')
        self.assertEqual([x['max_tokens'] for x in sent], [6000, 12000])
        self.assertEqual(sent[0]['messages'], sent[1]['messages'])

    def test_pause_is_bounded(self):
        with self.assertRaises(mb.GenerationError):
            self.call([{'stop_reason': 'pause_turn', 'content': []}] * 3)

    def test_double_truncation_never_accepted(self):
        with self.assertRaises(mb.GenerationError):
            self.call([{'stop_reason': 'max_tokens', 'content': []}] * 2)

    def test_transport_failure_returns_fallback(self):
        with patch.object(mb, 'call_api', side_effect=mb.GenerationError('HTTP 500')):
            b = mb.generate_fresh_brief('p', 'k', dt.date(2026, 11, 9))
        self.assertEqual(b['headlines'], [])


class SelectionTests(unittest.TestCase):
    def item(self, title, name='CNBC', stamp='2026-11-09T06:00:00+09:00', url=None):
        return {'title': title, 'name': name, 'published_at': stamp,
                'url': url or 'https://example.test/' + str(abs(hash(title))), 'summary': 'English summary must not be shown'}

    def select(self, items):
        from news_selection import select_fallback
        return select_fallback({str(i): x for i, x in enumerate(items)}, *mb.news_window(dt.date(2026, 11, 9)))

    def test_exclusions_and_general_words(self):
        from news_selection import market_score
        for title in ['바르셀로나 전시서 352만 달러 수출 상담', '렌터카비 같은 등급 시장 요금 기준',
                      '은행 수출 시장', 'Interest rates at rental car exhibition']:
            self.assertEqual(market_score(self.item(title)), 0)

    def test_regions_and_empty_english_body(self):
        h, cp = self.select([self.item('Stocks rally on earnings'), self.item('한은 금리 동결', '연합뉴스')])
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]['body'], '')
        self.assertEqual(cp['sources'][0]['name'], '연합뉴스')

    def test_friday_competes_on_score_before_weekend_recency(self):
        h, _ = self.select([self.item('Fed interest rates and bond yields fall', stamp='2026-11-06T09:00:00+09:00'),
                           self.item('Oil prices rise', stamp='2026-11-08T09:00:00+09:00')])
        self.assertEqual(len(h), 2)
        self.assertTrue(h[0]['title'].startswith('Fed'))

    def test_duplicates_url_and_similar_titles(self):
        h, _ = self.select([self.item('Stocks rally after earnings report', url='https://example.test/a'),
                           self.item('Stocks rally after earnings reports', name='BBC'),
                           self.item('Oil prices rise', url='https://example.test/a?utm_source=x')])
        self.assertEqual(len(h), 1)

    def test_no_qualified_candidates_keeps_empty_slots(self):
        self.assertEqual(self.select([self.item('Local festival opens')]), ([], None))


class FeedTests(unittest.TestCase):
    def test_all_feeds_down_is_empty(self):
        import news_sources
        with patch.object(news_sources.requests, 'get', side_effect=OSError('offline'), create=True):
            self.assertEqual(news_sources.fetch_news_sources(*mb.news_window(dt.date(2026, 11, 9))), {})

    def test_market_feeds_replace_top_news(self):
        from news_sources import FEEDS
        self.assertFalse(any('100003114' in u for _, u in FEEDS))
        self.assertTrue(any('10000664' in u for _, u in FEEDS))
        self.assertTrue(any('20910258' in u for _, u in FEEDS))
        self.assertTrue(any(n == 'MarketWatch' for n, _ in FEEDS))


class StatusTests(unittest.TestCase):
    def test_empty_news_renderer_and_notification(self):
        import render, notify
        from news_status import NO_NEWS, preview_description
        brief = {'headlines': [], 'checkpoint': {}, '_news_status': 'failed', 'og_description': '주요 뉴스'}
        self.assertIn(NO_NEWS, render.build_news(brief))
        self.assertEqual(preview_description(brief), NO_NEWS)
        title, body, _ = notify.compose({'slug': '2026-11-09', 'news_status': 'failed'}, 'https://example.test', '1')
        self.assertIn('뉴스 생성 실패', title)
        self.assertIn(NO_NEWS, body)

    def test_recovery_once_and_only_today_degraded(self):
        import json
        from schedule_guard import should_recover
        now = dt.datetime.fromisoformat('2026-11-09T08:00:00+09:00')
        with tempfile.TemporaryDirectory() as root:
            docs = Path(root)
            for status, tried, slug, expected in [('normal', False, '2026-11-09', False),
                    ('partial', False, '2026-11-09', True), ('failed', False, '2026-11-09', True),
                    ('failed', True, '2026-11-09', False), ('failed', False, '2026-11-06', False)]:
                (docs / 'report.json').write_text(json.dumps({'slug': slug, 'news_status': status, 'news_recovery_attempted': tried}))
                self.assertEqual(should_recover(now, docs)[0], expected)
            self.assertFalse(should_recover(now.replace(hour=7), docs)[0])


class BudgetTests(unittest.TestCase):
    def test_budget_reserves_publish_time(self):
        with patch.dict('os.environ', {'PIPELINE_DEADLINE_EPOCH': '1000'}):
            self.assertEqual(mb.generation_seconds(100), 540)
            self.assertEqual(mb.generation_seconds(400), 420)
            self.assertEqual(mb.generation_seconds(900), 0)

    def test_killed_worker_uses_snapshot_without_any_ai(self):
        import argparse, json, subprocess
        with tempfile.TemporaryDirectory() as root:
            data, out = Path(root) / 'data.json', Path(root) / 'brief.json'
            data.write_text(json.dumps({'briefing_date': '2026-11-09', 'cutoff': '2026-11-06', 'series': {}}))
            process = Mock(returncode=-9)
            process.communicate.side_effect = [subprocess.TimeoutExpired('worker', 1), ('worker stopped\n', None)]
            def spawn(command, **kw):
                snapshot = Path(command[command.index('--candidate-snapshot') + 1])
                candidate = SelectionTests().item('Stocks rally on earnings')
                snapshot.write_text(json.dumps({'end': '2026-11-09T07:20:59+09:00', 'evidence': {'x': candidate}}))
                return process
            with patch.object(mb.subprocess, 'Popen', side_effect=spawn), patch.object(mb, 'generation_seconds', return_value=1), \
                 patch.object(mb, 'call_api') as api:
                mb.supervised_generation(argparse.Namespace(data=str(data), out=str(out)))
            process.kill.assert_called_once()
            api.assert_not_called()
            result = json.loads(out.read_text())
            self.assertEqual(result['_news_status'], 'partial')
            self.assertEqual(len(result['headlines']), 1)

    def test_zero_budget_still_writes_today_empty_brief(self):
        import argparse, json
        with tempfile.TemporaryDirectory() as root:
            data, out = Path(root) / 'data.json', Path(root) / 'brief.json'
            data.write_text(json.dumps({'briefing_date': '2026-11-09', 'cutoff': '2026-11-06', 'series': {}}))
            with patch.object(mb, 'generation_seconds', return_value=0), patch.object(mb.subprocess, 'Popen') as spawn:
                mb.supervised_generation(argparse.Namespace(data=str(data), out=str(out)))
            spawn.assert_not_called()
            self.assertEqual(json.loads(out.read_text())['_news_status'], 'failed')


class IncidentRegressionTests(unittest.TestCase):
    def test_real_recovered_nov9_rss_rejects_incident_articles(self):
        import json
        from news_selection import select_fallback
        fixture = json.loads((Path(__file__).parent / 'fixtures/rss_20261109_recovered.json').read_text())
        candidates = fixture['candidates']
        titles = ' '.join(x['title'] for x in candidates)
        self.assertIn('바르셀로나', titles)
        self.assertIn('렌터비', titles)
        end = dt.datetime.fromisoformat(fixture['end'])
        heads, checkpoint = select_fallback({x['url']: x for x in candidates}, *mb.news_window(end.date(), end))
        chosen = heads + ([checkpoint] if checkpoint else [])
        self.assertTrue(heads)
        for card in chosen:
            self.assertNotIn('바르셀로나', card['title'])
            self.assertNotIn('렌터비', card['title'])
        for card in heads:
            self.assertIn(card['sources'][0]['name'], ('CNBC', 'BBC'))
            if card.get('title_only'):
                self.assertEqual(card['body'], '')
        print('11/9 recovered RSS:', len(candidates), 'candidates; selected:', [x['title'] for x in chosen])

    def test_three_ai_failures_and_original_three_feeds_down_render_today(self):
        import json, sys, io, news_sources, render
        from contextlib import redirect_stdout
        from news_status import NO_NEWS
        with tempfile.TemporaryDirectory() as root:
            data, out = Path(root) / 'data.json', Path(root) / 'brief.json'
            data.write_text(json.dumps({'briefing_date': '2026-11-09', 'cutoff': '2026-11-06', 'series': {}}))
            with patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'test'}), \
                 patch.object(sys, 'argv', ['make_brief.py', '--worker', '--data', str(data), '--out', str(out)]), \
                 patch.object(news_sources, 'FEEDS', (('CNBC','https://example.test/a'), ('BBC','https://example.test/b'), ('연합뉴스','https://example.test/c'))), \
                 patch.object(news_sources.requests, 'get', side_effect=OSError('offline'), create=True), \
                 patch.object(mb, 'call_api', side_effect=mb.GenerationError('failure')) as api, redirect_stdout(io.StringIO()):
                mb.main()
            self.assertEqual(api.call_count, 3)
            self.assertEqual(json.loads(out.read_text())['headlines'], [])
            rendered = Path(root) / 'out'
            with patch.object(sys, 'argv', ['render.py', '--data', str(data), '--brief', str(out), '--out', str(rendered)]), \
                 patch.object(render.make_og, 'build', create=True), redirect_stdout(io.StringIO()):
                render.main()
            html = (rendered / 'daily.html').read_text()
            self.assertIn(NO_NEWS, html)
            self.assertIn('뉴스 생성 실패', html)
            self.assertNotIn('{{', html)
            self.assertNotIn('확인 중', html)
            self.assertIn('주요 금융 지표', html)
            self.assertEqual(json.loads((rendered / 'report.json').read_text())['news_status'], 'failed')

    def test_diagnostics_have_usage_and_search_errors_without_raw_stdout(self):
        import os, json, io
        from contextlib import redirect_stdout
        payload = {'stop_reason': 'max_tokens', 'usage': {'output_tokens': 6000, 'server_tool_use': {'web_search_requests': 8}},
                   'content': [{'type': 'text', 'text': 'RAW_PRIVATE_RESPONSE'},
                               {'type': 'web_search_tool_result', 'content': {'type': 'web_search_tool_result_error', 'error_code': 'max_uses_exceeded'}}]}
        with tempfile.TemporaryDirectory() as root:
            previous = os.getcwd()
            try:
                os.chdir(root)
                output = io.StringIO()
                with redirect_stdout(output):
                    mb.record_response(payload)
                files = list(Path('debug/news').glob('*.json'))
                self.assertEqual(json.loads(files[0].read_text()), payload)
                self.assertFalse(Path('docs').exists())
                self.assertNotIn('RAW_PRIVATE_RESPONSE', output.getvalue())
                for term in ('max_tokens', '6000', '8', 'max_uses_exceeded'):
                    self.assertIn(term, output.getvalue())
            finally:
                os.chdir(previous)


class SupervisorIntegrationTests(unittest.TestCase):
    def test_real_hung_process_is_killed_within_wall_clock_budget(self):
        import argparse, json, subprocess, sys, time
        original_popen = subprocess.Popen
        spawned = []
        def sleeper(command, **kwargs):
            process = original_popen([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
            spawned.append(process)
            return process
        with tempfile.TemporaryDirectory() as root:
            data, out = Path(root) / 'data.json', Path(root) / 'brief.json'
            data.write_text(json.dumps({'briefing_date': '2026-11-09', 'cutoff': '2026-11-06', 'series': {}}))
            start = time.monotonic()
            with patch.object(mb.subprocess, 'Popen', side_effect=sleeper), patch.object(mb, 'generation_seconds', return_value=1):
                mb.supervised_generation(argparse.Namespace(data=str(data), out=str(out)))
            self.assertLess(time.monotonic() - start, 5)
            self.assertIsNotNone(spawned[0].poll())
            self.assertEqual(json.loads(out.read_text())['_news_status'], 'failed')

    def test_successful_worker_publishes_its_completed_output(self):
        import argparse, json
        with tempfile.TemporaryDirectory() as root:
            data, out = Path(root) / 'data.json', Path(root) / 'brief.json'
            data.write_text(json.dumps({'briefing_date': '2026-11-09', 'cutoff': '2026-11-06', 'series': {}}))
            def spawn(command, **kwargs):
                Path(command[command.index('--out') + 1]).write_text(json.dumps({'headlines': [], '_news_status': 'failed'}))
                return Mock(returncode=0, communicate=Mock(return_value=('completed\n', None)))
            with patch.object(mb.subprocess, 'Popen', side_effect=spawn):
                mb.supervised_generation(argparse.Namespace(data=str(data), out=str(out)))
            self.assertEqual(json.loads(out.read_text())['_news_status'], 'failed')


class RecoveryClaimTests(unittest.TestCase):
    def test_failed_recovery_cannot_be_claimed_again(self):
        import json
        from schedule_guard import should_recover
        with tempfile.TemporaryDirectory() as root:
            docs = Path(root) / 'docs'; docs.mkdir()
            state = Path(root) / '.state'; state.mkdir()
            (docs / 'report.json').write_text(json.dumps({'slug': '2026-11-09', 'news_status': 'failed'}))
            now = dt.datetime.fromisoformat('2026-11-09T08:01:00+09:00')
            self.assertTrue(should_recover(now, docs)[0])
            (state / 'news-recovery.json').write_text(json.dumps({'date': '2026-11-09'}))
            self.assertFalse(should_recover(now, docs)[0])
