import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw


spec = importlib.util.spec_from_file_location(
    "daily_thumbnail", Path(__file__).resolve().parents[1] / "make_og.py")
thumbnail = importlib.util.module_from_spec(spec)
spec.loader.exec_module(thumbnail)


class ThumbnailTests(unittest.TestCase):
    def test_build_without_html_or_screenshot_tool(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "og.png"
            with mock.patch.object(thumbnail.subprocess, "check_output", side_effect=OSError):
                thumbnail.build("missing.html", output, "2026년 9월 23일 아침",
                                [("코스피", "7,017.91", "▲0.15%", "up")] * 4,
                                "오늘의 <b>시장 요약</b>")
            with Image.open(output) as image:
                self.assertEqual(image.size, (1200, 630))
                image.verify()

    def test_long_summary_stays_within_two_lines(self):
        draw = ImageDraw.Draw(Image.new("RGB", (1200, 630)))
        font = thumbnail.font(36)
        lines = thumbnail.wrap("시장동향과투자심리" * 50, font, 1060, draw, limit=2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[-1].endswith("…"))
        self.assertTrue(all(draw.textlength(line, font=font) <= 1060 for line in lines))
