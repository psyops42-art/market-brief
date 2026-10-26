import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import plain_language as P  # noqa: E402

LONG = ("프랑스 국채 금리가 10월 21~22일 5.05%를 돌파하며 2002년 이후 최고치를 기록했다. "
        "독일 분트 대비 스프레드는 175~178bp까지 확대돼 유로존 채무위기 이후 최대 수준에 근접했다.")


def daily():
    return {"headlines": [{"title": "프랑스 OAT 스프레드 확대", "body": LONG, "source": "CNBC · 10/19",
                           "sources": [{"url": "https://x"}]}],
            "checkpoint": {"title": "원화 약세", "body": "짧은 본문입니다.", "source": "연합 · 10/26"},
            "mindset": [{"title": "금리", "body": "쉬운 문장입니다."}]}


class PlainLanguageTest(unittest.TestCase):
    def test_flags_long_text_and_unexplained_jargon(self):
        issues = P.readability_issues(daily(), "daily")
        self.assertTrue(any(i.startswith("headlines.1.body") and "김" in i for i in issues))
        self.assertTrue(any("스프레드" in i for i in issues))
        self.assertFalse(any(i.startswith("checkpoint") for i in issues))

    def test_explained_jargon_is_allowed(self):
        b = {"mindset_01": {"title": "금리 차이", "body": "스프레드(금리 차이)가 커졌습니다."}}
        self.assertEqual(P.readability_issues(b, "weekly"), [])

    def test_rewrite_applied_and_sources_untouched(self):
        b = daily()
        out = {"headlines.1.title": "프랑스 나랏빚 걱정 커져",
               "headlines.1.body": "프랑스 국채 금리(나라 빚의 이자)가 5.05%로 20여 년 만에 가장 높습니다."}
        notes = P.simplify(b, "daily", "k", "m", caller=lambda *a: json.dumps(out, ensure_ascii=False))
        self.assertEqual(b["headlines"][0]["title"], "프랑스 나랏빚 걱정 커져")
        self.assertIn("5.05%", b["headlines"][0]["body"])
        self.assertEqual(b["headlines"][0]["source"], "CNBC · 10/19")
        self.assertTrue(any("다듬음" in n for n in notes))

    def test_new_number_is_rejected(self):
        b = daily()
        out = {"headlines.1.body": "프랑스 국채 금리가 6.2%까지 올랐습니다."}
        P.simplify(b, "daily", "k", "m", caller=lambda *a: json.dumps(out, ensure_ascii=False))
        self.assertEqual(b["headlines"][0]["body"], LONG)

    def test_api_failure_keeps_original(self):
        b = daily()

        def boom(*_):
            raise RuntimeError("HTTP 529")
        notes = P.simplify(b, "daily", "k", "m", caller=boom)
        self.assertEqual(b["headlines"][0]["body"], LONG)
        self.assertIn("생략", notes[0])

    def test_no_call_when_already_plain(self):
        b = {"last_week_headlines": [{"title": "유가 100달러대 유지", "body": "중동 불안으로 유가가 올랐습니다."}]}
        called = []
        self.assertEqual(P.simplify(b, "weekly", "k", "m", caller=lambda *a: called.append(1)), [])
        self.assertEqual(called, [])

    def test_disallowed_markup_rejected(self):
        b = daily()
        out = {"headlines.1.body": "<script>x</script> 금리가 올랐습니다."}
        P.simplify(b, "daily", "k", "m", caller=lambda *a: json.dumps(out))
        self.assertEqual(b["headlines"][0]["body"], LONG)


if __name__ == "__main__":
    unittest.main()
