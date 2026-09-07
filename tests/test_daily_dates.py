import datetime as dt
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

import pandas as pd

# No market API calls or yfinance initialization during regression tests.
with mock.patch.dict(sys.modules, {"yfinance": types.ModuleType("yfinance"),
                                  "requests": types.ModuleType("requests")}):
    import fetch_data
from pipeline_utils import morning_cutoff, validate_daily_dates
from market_dates import expected_close


class MorningDateTests(unittest.TestCase):
    def test_previous_weekday_including_weekend_and_year_boundary(self):
        for day, expected in (("2026-09-07", "2026-09-04"),
                              ("2026-09-08", "2026-09-07"),
                              ("2026-09-06", "2026-09-04"),
                              ("2026-09-05", "2026-09-04"),
                              ("2026-01-01", "2025-12-31")):
            with self.subTest(day=day):
                self.assertEqual(morning_cutoff(dt.date.fromisoformat(day)).isoformat(), expected)

    def test_yahoo_excludes_monday_and_calculates_friday_change(self):
        history = pd.DataFrame({"Close": [100., 102., 999.]},
                               index=pd.to_datetime(["2026-09-03", "2026-09-04", "2026-09-07"]))
        ticker = mock.Mock()
        ticker.history.return_value = history
        with mock.patch.object(fetch_data.yf, "Ticker", return_value=ticker, create=True):
            rec = fetch_data._yahoo_once("^GSPC", "2026-09-04", "14d")
        self.assertEqual(rec, {"value": 102., "chg": 2., "pct": 2., "asof": "2026-09-04"})
        self.assertEqual(ticker.history.call_args.kwargs,
                         {"start": "2026-08-22", "end": "2026-09-07", "interval": "1d"})

    def test_holiday_keeps_actual_observation_date_without_live_quote(self):
        old = {"value": 100., "asof": "2026-09-03", "chg": 1., "pct": 1.01}
        with mock.patch.object(fetch_data, "_yahoo_once", return_value=old), \
                mock.patch.object(fetch_data, "_yahoo_range", return_value=None), \
                mock.patch.object(fetch_data, "_closed_quote", return_value=None), \
                mock.patch.object(fetch_data.yf, "Ticker", create=True) as ticker:
            rec = fetch_data.yahoo("^GSPC", "2026-09-04")
        self.assertEqual(rec, old)
        ticker.assert_not_called()

    def test_labor_day_is_us_only(self):
        for symbol in ("^GSPC", "^NDX", "^TNX", "^TYX"):
            self.assertEqual(expected_close(symbol, "2026-09-07"), "2026-09-04")
        for symbol in ("^KS11", "^KQ11", "000001.SS", "^STOXX50E", "ktb3y"):
            self.assertEqual(expected_close(symbol, "2026-09-07"), "2026-09-07")
        self.assertEqual(expected_close("^GSPC", "2026-09-08"), "2026-09-08")

    def test_range_retry_recovers_monday_but_excludes_tuesday(self):
        history = pd.DataFrame({"Close": [100., 102., 999.]},
                               index=pd.to_datetime(["2026-09-04", "2026-09-07", "2026-09-08"]))
        ticker = mock.Mock()
        ticker.history.return_value = history
        with mock.patch.object(fetch_data, "_yahoo_once", return_value={"asof": "2026-09-04"}), \
                mock.patch.object(fetch_data.yf, "Ticker", return_value=ticker, create=True):
            rec = fetch_data.yahoo("^KS11", "2026-09-07")
        self.assertEqual((rec["asof"], rec["value"], rec["chg"]), ("2026-09-07", 102., 2.))
        self.assertEqual(ticker.history.call_args.kwargs, {"period": "1mo", "interval": "1d"})

    def test_us_holiday_does_not_retry_a_complete_friday_bar(self):
        with mock.patch.object(fetch_data, "_yahoo_once", return_value={"asof": "2026-09-04"}), \
                mock.patch.object(fetch_data, "_yahoo_range") as retry:
            fetch_data.yahoo("^GSPC", "2026-09-07")
        retry.assert_not_called()

    def test_dated_quote_requires_matching_session_and_completed_close(self):
        previous = {"value": 100., "asof": "2026-09-04"}
        ticker = mock.Mock()
        for timestamp, accepted in (("2026-09-07T15:40:00+09:00", True),
                                    ("2026-09-07T14:00:00+09:00", False),
                                    ("2026-09-08T15:40:00+09:00", False),
                                    (None, False)):
            ticker.get_history_metadata.return_value = {
                "regularMarketTime": dt.datetime.fromisoformat(timestamp) if timestamp else None,
                "exchangeTimezoneName": "Asia/Seoul", "regularMarketPrice": 102.,
                "chartPreviousClose": 999.,
            }
            with self.subTest(timestamp=timestamp), \
                    mock.patch.object(fetch_data.yf, "Ticker", return_value=ticker, create=True):
                result = fetch_data._closed_quote("^KS11", "2026-09-07", previous)
            self.assertEqual(result is not None, accepted)
            if accepted:
                self.assertEqual((result["asof"], result["value"], result["chg"]),
                                 ("2026-09-07", 102., 2.))

    def test_old_observation_is_stale_only_in_open_markets(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "data.json"
            with mock.patch.object(fetch_data, "yahoo", side_effect=lambda *a: {
                    "asof": "2026-09-04", "value": 100., "chg": 1., "pct": 1.}), \
                    mock.patch.object(fetch_data, "fred", return_value=None), \
                    mock.patch.object(fetch_data, "ecos", return_value=None), \
                    mock.patch.object(sys, "argv", ["fetch_data.py", "--date", "2026-09-08", "--out", str(path)]), \
                    redirect_stdout(io.StringIO()):
                fetch_data.main()
            data = json.loads(path.read_text(encoding="utf-8"))
            stale = {item["key"] for item in data["stale"]}
            self.assertTrue({"kospi", "shcomp", "sx5e"} <= stale)
            self.assertFalse({"sp500", "ndx", "ust10y", "ust30y"} & stale)

    def test_fred_enforces_cutoff_even_if_response_contains_newer_rows(self):
        response = mock.Mock()
        response.json.return_value = {"observations": [
            {"date": "2026-09-07", "value": "9.9"},
            {"date": "2026-09-04", "value": "4.1"},
            {"date": "2026-09-03", "value": "4.0"}]}
        with mock.patch.dict(os.environ, {"FRED_API_KEY": "test"}), \
                mock.patch.object(fetch_data.requests, "get", return_value=response, create=True) as get:
            rec = fetch_data.fred("DGS10", "2026-09-04")
        self.assertEqual(get.call_args.kwargs["params"]["observation_end"], "2026-09-04")
        self.assertEqual((rec["asof"], rec["value"], rec["chg"]), ("2026-09-04", 4.1, 10.))

    def test_ecos_enforces_cutoff_and_keeps_actual_observation_date(self):
        rows = [{"TIME": "20260907", "DATA_VALUE": "9.9"},
                {"TIME": "20260904", "DATA_VALUE": "3.9"},
                {"TIME": "20260903", "DATA_VALUE": "3.8"}]
        with mock.patch.dict(os.environ, {"ECOS_API_KEY": "test"}), \
                mock.patch.object(fetch_data, "_ecos_combo", None), \
                mock.patch.object(fetch_data, "_ecos_get", return_value=(rows, None)) as get, \
                redirect_stdout(io.StringIO()):
            rec = fetch_data.ecos("ktb3y", "2026-09-04")
        self.assertIn("/20260805/20260904/", get.call_args.args[0])
        self.assertEqual((rec["asof"], rec["value"], rec["chg"]), ("2026-09-04", 3.9, 10.))

    def test_daily_collection_uses_same_bound_in_morning_and_evening(self):
        for hour in (7, 23):
            with self.subTest(hour=hour), tempfile.TemporaryDirectory() as temp:
                now = dt.datetime(2026, 9, 7, hour, tzinfo=fetch_data.KST)
                out = Path(temp) / "data.json"
                with mock.patch.object(fetch_data.dt, "datetime") as clock, \
                        mock.patch.object(fetch_data, "yahoo", return_value=None) as yahoo, \
                        mock.patch.object(fetch_data, "fred", return_value=None) as fred, \
                        mock.patch.object(fetch_data, "ecos", return_value=None) as ecos, \
                        mock.patch.object(sys, "argv", ["fetch_data.py", "--out", str(out)]), \
                        redirect_stdout(io.StringIO()):
                    clock.now.return_value = now
                    fetch_data.main()
                data = json.loads(out.read_text(encoding="utf-8"))
                self.assertEqual(data["briefing_date"], "2026-09-07")
                self.assertEqual(data["cutoff"], "2026-09-04")
                for provider in (yahoo, fred, ecos):
                    self.assertTrue(provider.called)
                    self.assertTrue(all(call.args[1] == "2026-09-04" for call in provider.call_args_list))

    def test_past_edition_uses_explicit_date(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "data.json"
            with mock.patch.object(fetch_data, "yahoo", return_value=None), \
                    mock.patch.object(fetch_data, "fred", return_value=None), \
                    mock.patch.object(fetch_data, "ecos", return_value=None), \
                    mock.patch.object(sys, "argv", ["fetch_data.py", "--date", "2026-09-07", "--out", str(out)]), \
                    redirect_stdout(io.StringIO()):
                fetch_data.main()
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual((data["briefing_date"], data["cutoff"]), ("2026-09-07", "2026-09-04"))

    def test_generation_guard_rejects_future_values_without_relabeling(self):
        day = dt.date(2026, 9, 7)
        for data in ({"cutoff": "2026-09-07"},
                     {"cutoff": "2026-09-04", "series": {"sp500": {"asof": "2026-09-07"}}},
                     {"series": {"ktb3y": {"value": 3.9}}}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_daily_dates(data, day)
        validate_daily_dates({"cutoff": "2026-09-04", "series": {"sp500": {"asof": "2026-09-03"}}}, day)
