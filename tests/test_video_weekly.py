import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import kakao_send  # noqa: E402
import video_weekly as v  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def _has_font():
    try:
        v.F(20)
        return True
    except Exception:  # noqa: BLE001
        return False


# 글꼴은 '렌더링 도구 설치' 단계에서 준비되므로, 테스트가 먼저 돌 때 글꼴이 없으면 그리기 테스트만 건너뛴다
needs_font = unittest.skipUnless(_has_font(), "한글 글꼴 없음")


def load():
    data = json.loads((FIX / "weekly_sample_data.json").read_text(encoding="utf-8"))
    brief = json.loads((FIX / "weekly_sample_brief.json").read_text(encoding="utf-8"))
    return data, brief


class SpeakableTests(unittest.TestCase):
    def test_reads_numbers_and_names_naturally(self):
        self.assertEqual(v.speakable("S&P 500은 <b>+3.25%</b> 올라"), "에스앤피 오백은 3.25% 올라")
        self.assertEqual(v.speakable("나스닥이 -2% 빠지는 동안"), "나스닥이 마이너스 2% 빠지는 동안")
        self.assertEqual(v.speakable("연준은 10/28 금리를"), "연준은 10월 28일 금리를")
        self.assertEqual(v.speakable("선거 + 고용"), "선거 그리고 고용")
        self.assertEqual(v.speakable("고용보고서(비농업) 발표"), "고용보고서 발표")
        self.assertEqual(v.speakable("TDF 30~50%"), "TDF 30에서 50%")

    def test_first_sentence(self):
        self.assertEqual(v.first_sentence("첫 문장입니다. 둘째 문장입니다."), "첫 문장입니다.")


@needs_font
class SceneTests(unittest.TestCase):
    def test_narration_uses_only_dashboard_text_and_data(self):
        data, brief = load()
        scenes = v.build_scenes(data, brief)
        self.assertEqual(len(scenes), 10)
        text = " ".join(s.narration for s in scenes)
        for h in brief["last_week_headlines"]:
            self.assertIn(h["title"], text)
        self.assertIn("코스피, 3.25% 상승", text)        # 실제 주간 최고
        self.assertIn("비트코인, 4.78% 하락", text)      # 실제 주간 최저

    def test_length_budget_trims_supporting_sentences(self):
        data, brief = load()
        scenes = v.build_scenes(data, brief)
        est = sum(max(s.base_dur, v.LEAD + len(v.speakable(s.narration)) / v.CHARS_PER_SEC + v.TAIL)
                  for s in scenes)
        self.assertLessEqual(est, v.TARGET_SEC + 8)

    def test_related_indicator_requires_data(self):
        data, _ = load()
        self.assertEqual(v.related_key("비트코인 급락", data["series"]), "btc")
        self.assertIsNone(v.related_key("영국 중앙은행 금리 결정", data["series"]))
        self.assertIsNone(v.related_key("비트코인 급락", {}))

    def test_missing_sections_do_not_crash(self):
        data, _ = load()
        scenes = v.build_scenes(data, {})
        self.assertGreaterEqual(len(scenes), 4)

    def test_frame_renders(self):
        data, brief = load()
        scenes = v.build_scenes(data, brief)
        im = scenes[4].frame(3.0, 10, 70)
        self.assertEqual(im.size, (v.W, v.H))


class PruneTests(unittest.TestCase):
    def test_keeps_latest_weeks(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(1, 11):
                for ext in ("mp4", "jpg"):
                    Path(d, f"weekly-2026-01-{i:02d}.{ext}").write_bytes(b"x")
            removed = v.prune(d, keep=8)
            self.assertEqual(len(removed), 4)
            self.assertFalse(Path(d, "weekly-2026-01-01.mp4").exists())
            self.assertTrue(Path(d, "weekly-2026-01-10.mp4").exists())


class KakaoTemplateTests(unittest.TestCase):
    def test_video_button_only_when_video_exists(self):
        _, brief = load()
        rep = {"slug": "weekly-2026-11-02", "page": "weekly.html",
               "last_week": {"mon": "2026-10-26", "fri": "2026-10-30"}, "video": {"ok": True}}
        t = kakao_send.build_template(rep, brief, "https://x.io/b", 3)
        self.assertEqual([b["title"] for b in t["buttons"]], ["1분 영상 보기", "대시보드 보기"])
        self.assertTrue(t["buttons"][0]["link"]["web_url"].endswith("weekly.html#video"))
        self.assertEqual(t["content"]["title"], "주간 마켓 브리핑 | 10월 26일~30일 정리")
        rep["video"] = {"ok": False}
        t = kakao_send.build_template(rep, brief, "https://x.io/b", 3)
        self.assertEqual([b["title"] for b in t["buttons"]], ["대시보드 보기"])


if __name__ == "__main__":
    unittest.main()
