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


class RenderSmokeTests(unittest.TestCase):
    def test_daily_main_renders_with_missing_optional_data(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            data_path = temp_path / "data.json"
            brief_path = temp_path / "brief.json"
            out_path = temp_path / "out"
            data_path.write_text(json.dumps({
                "generated_at": "2026-09-03T07:00:00+09:00",
                "series": {"sp500": {"label": "S&P 500", "badge": "us", "asof": "2026-09-02",
                                      "value": None, "chg": None, "pct": None}},
                "cutoff": None, "stale": [], "delayed": [],
            }), encoding="utf-8")
            brief_path.write_text(json.dumps({"_issues": ["필수 항목 누락"]}), encoding="utf-8")

            def fake_og(_html, png, *_args, **_kwargs):
                Path(png).write_bytes(b"png")

            render.make_og.build = fake_og
            argv = ["render.py", "--data", str(data_path), "--brief", str(brief_path),
                    "--template", str(root / "template.html"), "--out", str(out_path)]
            with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
                render.main()
            output = (out_path / "2026-09-03.html").read_text(encoding="utf-8")
            self.assertNotIn("{{", output)
            self.assertTrue((out_path / "report.json").exists())

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
