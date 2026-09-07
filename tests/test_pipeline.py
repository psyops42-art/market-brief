# -*- coding: utf-8 -*-
import datetime as dt
import io
import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


# 로컬의 최소 Python에서도 순수 로직을 검사할 수 있게 외부 패키지만 대체한다.
requests_stub = types.ModuleType("requests")
requests_stub.RequestException = Exception
requests_exceptions_stub = types.ModuleType("requests.exceptions")
requests_exceptions_stub.RequestException = Exception
requests_stub.exceptions = requests_exceptions_stub
sys.modules.setdefault("requests", requests_stub)
sys.modules.setdefault("requests.exceptions", requests_exceptions_stub)
sys.modules.setdefault("make_og", types.ModuleType("make_og"))

import make_brief
import make_brief_weekly
import render
import render_weekly
from pipeline_utils import md_date_in_range, safe_rich_text


class SafetyTests(unittest.TestCase):
    def test_only_plain_b_tags_survive(self):
        value = '<b>강조</b><script>alert(1)</script><b onclick="x">위험</b>'
        actual = safe_rich_text(value)
        self.assertIn("<b>강조</b>", actual)
        self.assertNotIn("<script>", actual)
        self.assertNotIn("<b onclick=", actual.replace("&quot;", '"'))

    def test_daily_renderer_escapes_generated_html(self):
        brief = {
            "headlines": [{"title": "제목", "body": "<b>허용</b><img src=x onerror=x>", "source": "매체 · 9/1"}],
            "checkpoint": {"title": "국내", "body": "<script>x</script>", "source": "매체 · 9/1"},
        }
        output = render.build_news(brief)
        self.assertIn("<b>허용</b>", output)
        self.assertNotIn("<img ", output)
        self.assertNotIn("<script>", output)


class ValidationTests(unittest.TestCase):
    def test_daily_kpi_accepts_missing_fields(self):
        self.assertEqual(render.kpi("X", None), ("X", "확인필요", "－", "fl"))
        for unit in ("price", "bp"):
            for fields in ({}, {"value": None, "chg": None, "pct": None}):
                with self.subTest(unit=unit, fields=fields):
                    self.assertEqual(render.kpi("X", {"unit": unit, **fields}),
                                     ("X", "-", "-", "fl"))

    def test_daily_kpi_preserves_numeric_changes(self):
        for change, arrow, color in ((1.25, "▲", "up"), (-1.25, "▼", "dn"), (0, "－", "fl")):
            for unit, key, value, delta in (
                ("price", "pct", "1,234.50", f"{arrow} {abs(change):.2f}%"),
                ("bp", "chg", "1234.500%", f"{arrow} {abs(change):.1f}bp"),
            ):
                with self.subTest(unit=unit, change=change):
                    self.assertEqual(render.kpi("X", {"unit": unit, "value": 1234.5, key: change}),
                                     ("X", value, delta, color))
                    self.assertEqual(render.kpi("X", {"unit": unit, key: change}),
                                     ("X", "-", delta, color))
                    self.assertEqual(render.kpi("X", {"unit": unit, "value": 1234.5}),
                                     ("X", value, "-", "fl"))

    def test_daily_row_accepts_change_without_percent(self):
        output = render.row({"label": "X", "value": 100, "chg": -2, "pct": None}, "")
        self.assertIn('c3 dn', output)
        self.assertIn("▼ 2.00", output)
        self.assertNotIn("None", output)

    def test_md_date_handles_year_boundary(self):
        start, end = dt.date(2025, 12, 29), dt.date(2026, 1, 2)
        self.assertEqual(md_date_in_range("매체 · 1/2", start, end), dt.date(2026, 1, 2))

    def test_weekly_validation_accepts_year_boundary(self):
        brief = {
            "last_week_headlines": [
                {"source": "A · 12/29", "body": "a"},
                {"source": "B · 12/31", "body": "b"},
                {"source": "C · 1/2", "body": "c"},
            ],
            "checkpoints": [{"day": "월", "date": "1/5", "text": "일정"}],
            "quotes": ["<b>가</b>", "<b>나</b>", "<b>다</b>"],
        }
        issues = make_brief_weekly.validate(
            brief,
            {"mon": "2025-12-29", "fri": "2026-01-02"},
            {"mon": "2026-01-05", "fri": "2026-01-09"},
        )
        self.assertEqual(issues, [])

    def test_none_values_are_safe_in_summaries(self):
        daily = {"series": {"x": {"label": "X", "value": None, "pct": None, "asof": "-"}}}
        weekly = {
            "last_week": {"mon": "2026-08-24", "fri": "2026-08-28"},
            "this_week": {"mon": "2026-08-31", "fri": "2026-09-04"},
            "series": {"x": {"label": "X", "unit": "price", "value": None,
                              "wow_pct": None, "ytd_pct": None, "asof": "-"}},
        }
        self.assertIn("X: -", make_brief.summarize(daily))
        self.assertIn("주간 -", make_brief_weekly.summarize(weekly))

    def test_weekly_row_accepts_partial_metric(self):
        row = render_weekly.row_html(
            "x", {"label": "X", "badge": "", "unit": "price", "value": None,
                  "wow_pct": None, "ytd_pct": None, "trend": "－", "trend_color": "fl"},
            set(), set())
        self.assertIn("X", row)
        self.assertIn("－", row)


class NewsFreshnessTests(unittest.TestCase):
    day = dt.date(2026, 9, 7)

    def fresh_brief(self):
        def item(i):
            return {"title": "새로운 보도", "body": "최신 기사에 근거한 설명",
                    "source": "매체 · 9/7", "sources": [{"name": "매체",
                    "url": f"https://example.test/news/{i}",
                    "published_at": "2026-09-07T06:00:00+09:00"}]}
        return {"headlines": [item(i) for i in range(3)], "checkpoint": item(3),
                "mindset": [{"title": "원칙", "body": "분산"}] * 3,
                "quotes": ["<b>분산</b>이 필요합니다."] * 3,
                "og_description": "최신 요약", "oneline_market": "시장 요약",
                "oneline_pension": "연금 요약", "next_events": "예정 일정"}

    def urls(self):
        return {f"https://example.test/news/{i}" for i in range(4)}

    def test_news_window_is_independent_of_friday_close(self):
        self.assertEqual(make_brief.validate(self.fresh_brief(), "2026-09-04", self.day), [])
        start, end = make_brief.news_window(self.day)
        self.assertEqual(start.isoformat(), "2026-09-05T07:00:00+09:00")
        self.assertEqual(end.isoformat(), "2026-09-07T07:00:00+09:00")

    def test_old_checkpoint_is_rejected_even_with_a_recent_secondary_source(self):
        for reverse in (False, True):
            brief = self.fresh_brief()
            old = {"name": "과거 매체", "url": "https://example.test/old",
                   "published_at": "2026-08-27T09:00:00+09:00"}
            brief["checkpoint"]["sources"].append(old)
            if reverse:
                brief["checkpoint"]["sources"].reverse()
            brief["checkpoint"]["source"] = "매체 · 9/7, 과거 매체 · 8/27"
            with self.subTest(reverse=reverse):
                issues = make_brief.news_issues(brief, self.day)
                self.assertTrue(any("국내 체크포인트" in x and "기간" in x for x in issues))

    def test_stale_future_and_missing_timezone_are_rejected(self):
        for timestamp in ("2026-09-04T23:00:00+09:00", "2026-09-07T07:00:01+09:00",
                          "2026-09-07", "2026-09-07T06:00:00", None):
            brief = self.fresh_brief()
            brief["headlines"][0]["sources"][0]["published_at"] = timestamp
            with self.subTest(timestamp=timestamp):
                self.assertTrue(make_brief.news_issues(brief, self.day))

    def test_timezone_conversion_and_year_boundary(self):
        for day, timestamp, display in (
            (self.day, "2026-09-06T17:00:00-04:00", "9/6"),
            (dt.date(2027, 1, 1), "2026-12-31T17:00:00-05:00", "12/31"),
        ):
            brief = self.fresh_brief()
            for item in brief["headlines"] + [brief["checkpoint"]]:
                item["sources"][0]["published_at"] = timestamp
                item["source"] = f"매체 · {display}"
            self.assertEqual(make_brief.news_issues(brief, day), [])

    def test_old_display_date_cannot_be_hidden_by_fresh_metadata(self):
        brief = self.fresh_brief()
        brief["checkpoint"]["source"] += ", 과거 매체 · 8/27"
        self.assertTrue(make_brief.news_issues(brief, self.day))

    def test_unsearched_url_and_missing_sources_are_rejected(self):
        brief = self.fresh_brief()
        self.assertTrue(make_brief.news_issues(brief, self.day, set()))
        del brief["checkpoint"]["sources"]
        self.assertTrue(make_brief.news_issues(brief, self.day, self.urls()))

    def test_only_actual_search_results_count_as_evidence(self):
        payload = [{"type": "text", "text": "https://example.test/invented"},
                   {"type": "web_search_tool_result", "content": [
                       {"type": "web_search_result", "url": "https://example.test/real"}]},
                   {"type": "web_search_tool_result", "content": {
                       "type": "web_search_tool_result_error", "error_code": "unavailable"}}]
        self.assertEqual(make_brief.search_result_urls(payload), {"https://example.test/real"})

    def test_failed_freshness_triggers_new_search_then_accepts_correction(self):
        bad = self.fresh_brief()
        bad["checkpoint"]["sources"][0]["published_at"] = "2026-08-27T06:00:00+09:00"
        responses = iter([bad, self.fresh_brief()])
        def api(prompt, key, source_urls):
            source_urls.update(self.urls())
            return json.dumps(next(responses))
        with mock.patch.object(make_brief, "call_api", side_effect=api) as call, redirect_stdout(io.StringIO()):
            result = make_brief.generate_fresh_brief("원래 조건", "test", self.day)
        self.assertEqual(call.call_count, 2)
        self.assertIn("국내 체크포인트", call.call_args.args[0])
        self.assertEqual(result, self.fresh_brief())

    def test_repeated_failure_writes_honest_renderable_output(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / "data.json"
            out = Path(temp) / "brief.json"
            data.write_text(json.dumps({"briefing_date": "2026-09-07", "cutoff": "2026-09-04",
                                        "series": {}}), encoding="utf-8")
            with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}), \
                    mock.patch.object(sys, "argv", ["make_brief.py", "--data", str(data), "--out", str(out)]), \
                    mock.patch.object(make_brief, "call_api", return_value=json.dumps(self.fresh_brief())) as call, \
                    redirect_stdout(io.StringIO()):
                make_brief.main()  # No actual search evidence: both attempts must fail.
            self.assertEqual(call.call_count, 2)
            result = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(result["_news_status"], "partial")
            self.assertTrue(result["_issues"])
            html = render.build_news(result)
            self.assertIn("최신 보도 미확보", html)
            self.assertNotIn("최신 기사에 근거한 설명", html)
            self.assertEqual(len(result["headlines"]), 3)
            rendered = Path(temp) / "out"
            template = Path(__file__).resolve().parents[1] / "template.html"
            with mock.patch.object(sys, "argv", ["render.py", "--data", str(data), "--brief", str(out),
                                                "--template", str(template), "--out", str(rendered)]), \
                    mock.patch.object(render.make_og, "build", create=True), redirect_stdout(io.StringIO()):
                render.main()
            self.assertIn("최신 보도 미확보", (rendered / "2026-09-07.html").read_text(encoding="utf-8"))
            report = json.loads((rendered / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["brief_issues"])

    def test_partial_failure_keeps_good_cards_and_removes_old_summary(self):
        bad = self.fresh_brief()
        for item in (bad["headlines"][0], bad["checkpoint"]):
            item["sources"][0]["published_at"] = "2026-08-27T06:00:00+09:00"
            item["body"] = "오래된 국채 매입 뉴스"
        for key in ("og_description", "oneline_market", "oneline_pension", "next_events"):
            bad[key] = "오래된 국채 매입 뉴스"
        bad["mindset"][0]["body"] = "오래된 국채 매입 뉴스"
        bad["quotes"][0] = "오래된 국채 매입 뉴스"
        def api(prompt, key, source_urls):
            source_urls.update(self.urls())
            return json.dumps(bad)
        with mock.patch.object(make_brief, "call_api", side_effect=api), redirect_stdout(io.StringIO()):
            result = make_brief.generate_fresh_brief("원래 조건", "test", self.day)
        self.assertEqual(result["headlines"][1:], bad["headlines"][1:])
        self.assertNotIn("오래된 국채 매입 뉴스", json.dumps(result, ensure_ascii=False))
        self.assertEqual(result["headlines"][0]["sources"], [])
        self.assertEqual(result["checkpoint"]["sources"], [])

    def test_display_mismatch_is_excluded_without_relabeling(self):
        bad = self.fresh_brief()
        bad["checkpoint"]["source"] = "매체 · 8/27"
        def api(prompt, key, source_urls):
            source_urls.update(self.urls())
            return json.dumps(bad)
        with mock.patch.object(make_brief, "call_api", side_effect=api), redirect_stdout(io.StringIO()):
            result = make_brief.generate_fresh_brief("원래 조건", "test", self.day)
        self.assertEqual(result["headlines"], bad["headlines"])
        self.assertEqual(result["checkpoint"]["sources"], [])


class RenderSmokeTests(unittest.TestCase):
    def test_daily_main_renders_with_missing_optional_data(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            data_path = temp_path / "data.json"
            brief_path = temp_path / "brief.json"
            out_path = temp_path / "out"
            data_path.write_text(json.dumps({
                "generated_at": "2026-09-07T23:00:00+09:00",
                "briefing_date": "2026-09-07",
                "series": {"sp500": {"label": "S&P 500", "badge": "us", "asof": "2026-09-04",
                                      "value": None, "chg": None, "pct": None}},
                "cutoff": "2026-09-04", "stale": [], "delayed": [],
            }), encoding="utf-8")
            brief_path.write_text(json.dumps({"_issues": ["필수 항목 누락"]}), encoding="utf-8")

            def fake_og(_html, png, *_args, **_kwargs):
                Path(png).write_bytes(b"png")

            argv = ["render.py", "--data", str(data_path), "--brief", str(brief_path),
                    "--template", str(root / "template.html"), "--out", str(out_path)]
            # Freeze the execution date independently of the older input fixture.
            now = dt.datetime(2026, 9, 8, 23, tzinfo=render.KST)
            with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()), \
                    mock.patch.object(render.dt, "datetime") as clock, \
                    mock.patch.object(render.make_og, "build", side_effect=fake_og, create=True) as og:
                clock.now.return_value = now
                render.main()
            output = (out_path / "2026-09-07.html").read_text(encoding="utf-8")
            self.assertNotIn("{{", output)
            self.assertIn("美 9/4 뉴욕 마감", output)
            self.assertNotIn("9/7 종가", output)
            self.assertTrue((out_path / "og-2026-09-07.png").exists())
            report = json.loads((out_path / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["slug"], "2026-09-07")
            self.assertIn(("S&P 500", "-", "-", "fl"), og.call_args.args[3])

    def test_weekly_main_renders_with_missing_optional_data(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            data_path = temp_path / "data.json"
            brief_path = temp_path / "brief.json"
            out_path = temp_path / "out"
            data_path.write_text(json.dumps({
                "series": {}, "missing": [], "stale": [], "delayed": [],
                "last_week": {"mon": "2026-08-24", "fri": "2026-08-28"},
                "this_week": {"mon": "2026-08-31", "fri": "2026-09-04"},
            }), encoding="utf-8")
            brief_path.write_text(json.dumps({"_issues": ["필수 항목 누락"]}), encoding="utf-8")
            weekly_og = types.ModuleType("make_og_weekly")
            weekly_og.build = lambda _html, png, *_args, **_kwargs: Path(png).write_bytes(b"png")
            sys.modules["make_og_weekly"] = weekly_og
            argv = ["render_weekly.py", "--data", str(data_path), "--brief", str(brief_path),
                    "--template", str(root / "template_weekly.html"), "--base", "https://example.test/x/",
                    "--out", str(out_path)]
            with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
                render_weekly.main()
            output = (out_path / "weekly-2026-08-31.html").read_text(encoding="utf-8")
            self.assertNotIn("{{", output)
            self.assertTrue((out_path / "report_weekly.json").exists())


if __name__ == "__main__":
    unittest.main()
