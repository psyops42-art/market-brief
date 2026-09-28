# -*- coding: utf-8 -*-
"""주간 OG 썸네일: 일간과 같은 전체 폭 레이아웃, 주간 지표·회고 표시."""
import sys


def build(html_path, out_png, date_line, kpis, retro_text, tmpdir=None):
    from make_og import build as build_summary
    return build_summary(
        html_path, out_png, date_line, kpis,
        retro_text or "이번 주 시장을 정리했습니다.", tmpdir,
        eyebrow="퇴직연금 · 위클리 마켓", title="주간 마켓 브리핑",
        summary_label="지난주 회고", footer_label="WEEKLY BRIEF · 전주 대비")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("사용법: python make_og_weekly.py <html> <out.png>")
    build(sys.argv[1], sys.argv[2], "8월 24일~28일 정리 · 8월 31일(월) 아침",
          [("코스피", "-", "-", "fl")] * 4, "이번 주 시장을 정리했습니다.")
    print("OG →", sys.argv[2])
