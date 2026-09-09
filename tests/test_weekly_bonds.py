import datetime as dt
import sys
import types
import unittest
from unittest import mock

with mock.patch.dict(sys.modules, {"yfinance": types.ModuleType("yfinance")}):
    import fetch_data_weekly as weekly
import render_weekly


class WeeklyBondTests(unittest.TestCase):
    args = ("ktb10y", "010210000", "국고채 10년", "kr",
            dt.date(2026, 8, 31), dt.date(2026, 9, 4), dt.date(2026, 1, 1))

    def test_fallback_preserves_rate_and_computes_weekly_and_ytd_bp(self):
        points = {dt.date(2026, 1, 2): 3., dt.date(2026, 8, 28): 4.2,
                  dt.date(2026, 9, 4): 4.36}
        with mock.patch.object(weekly, "_collect_ecos_rate", return_value=(None, ("stale", None))), \
                mock.patch.object(weekly, "bond_history", return_value=points):
            rec, flag = weekly.collect_ecos_rate(*self.args)
        self.assertEqual((rec["value"], rec["wow_pct"], rec["ytd_pct"]), (4.36, 16., 136.))
        self.assertEqual(flag, ("ok", 0))
        output = render_weekly.build_table({"ktb10y": rec}, ["ktb10y"], set(), set())
        self.assertIn("4.360%", output)
        self.assertNotIn("미확보", output)
        self.assertIn("Reuters", output)

    def test_missing_comparison_history_does_not_hide_available_yield(self):
        with mock.patch.object(weekly, "_collect_ecos_rate", return_value=(None, ("stale", None))), \
                mock.patch.object(weekly, "bond_history", return_value={dt.date(2026, 9, 4): 4.36}):
            rec, _ = weekly.collect_ecos_rate(*self.args)
        self.assertEqual(rec["value"], 4.36)
        self.assertIsNone(rec["wow_pct"])
        self.assertIsNone(rec["ytd_pct"])

    def test_history_filters_future_rows_and_pages_back_to_january(self):
        replies = []
        for entries in ((('2026-09-09', '99'), ('2026-09-04', '4.36')),
                        (('2026-01-02', '3.0'), ('2025-12-31', '2.9'))):
            response = mock.Mock()
            response.json.return_value = [{"localTradedAt": day + "T16:00:00+09:00", "closePrice": value}
                                          for day, value in entries]
            replies.append(response)
        with mock.patch.object(weekly.D.requests, "get", side_effect=replies, create=True) as get:
            points = weekly.bond_history("ktb10y", self.args[5], self.args[6])
        self.assertNotIn(dt.date(2026, 9, 9), points)
        self.assertEqual(points[dt.date(2026, 1, 2)], 3.)
        self.assertEqual(get.call_count, 2)

    def test_freshness_uses_current_market_calendar(self):
        self.assertEqual(weekly.classify_freshness(dt.date(2026, 9, 4), dt.date(2026, 9, 7), "sp500"),
                         ("ok", 0))
        self.assertEqual(weekly.classify_freshness(dt.date(2026, 9, 4), dt.date(2026, 9, 7), "kospi"),
                         ("stale", 3))
