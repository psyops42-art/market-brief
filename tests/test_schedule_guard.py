import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from schedule_guard import should_run


class ScheduleGuardTests(unittest.TestCase):
    def decide(self, kind, stamp, docs):
        return should_run(kind, dt.datetime.fromisoformat(stamp), docs)[0]

    def test_kst_monday_uses_previous_utc_sunday(self):
        with tempfile.TemporaryDirectory() as root:
            docs = Path(root)
            self.assertFalse(self.decide("daily", "2026-09-27T22:19:00+00:00", docs))
            self.assertTrue(self.decide("daily", "2026-09-27T22:20:00+00:00", docs))
            self.assertFalse(self.decide("daily", "2026-09-28T03:00:00+00:00", docs))
            self.assertFalse(self.decide("daily", "2026-09-25T22:20:00+00:00", docs))

    def test_daily_dedup_requires_current_report_and_files(self):
        with tempfile.TemporaryDirectory() as root:
            docs = Path(root)
            stamp = "2026-09-28T08:35:00+09:00"
            (docs / "report.json").write_text(json.dumps({"slug": "2026-09-28"}))
            self.assertTrue(self.decide("daily", stamp, docs))
            for name in ("daily.html", "2026-09-28.html", "og-2026-09-28.png"):
                (docs / name).write_text("published")
            self.assertFalse(self.decide("daily", stamp, docs))
            (docs / "report.json").write_text(json.dumps({"slug": "2026-09-25"}))
            self.assertTrue(self.decide("daily", stamp, docs))
            (docs / "report.json").write_text("invalid json")
            self.assertTrue(self.decide("daily", stamp, docs))

    def test_weekly_has_separate_report_and_monday_window(self):
        with tempfile.TemporaryDirectory() as root:
            docs = Path(root)
            stamp = "2026-09-28T06:15:00+09:00"
            self.assertTrue(self.decide("weekly", stamp, docs))
            (docs / "report_weekly.json").write_text(json.dumps({"slug": "weekly-2026-09-28"}))
            (docs / "weekly-2026-09-28.html").write_text("published")
            self.assertFalse(self.decide("weekly", stamp, docs))
            self.assertFalse(self.decide("weekly", "2026-09-29T06:15:00+09:00", docs))
            self.assertTrue(self.decide("daily", "2026-09-28T07:20:00+09:00", docs))
