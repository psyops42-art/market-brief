# -*- coding: utf-8 -*-
"""
OG 썸네일 생성 — 제목·핵심 지표·시장 요약을 1200×630 전체에 배치

render.py 가 호출합니다. 단독 실행도 가능합니다.

    python make_og.py out/2026-08-30.html out/og-2026-08-30.png \
        --date-line "2026년 8월 30일 (일) 아침 · 美/韓 8/28 마감 기준"

필요 패키지 : pillow, 한글 글꼴 (Linux: fonts-noto-cjk, Windows: 맑은 고딕)
"""

import argparse
import os
import subprocess

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
GRAD = [(175, 162, 230), (149, 182, 222), (95, 211, 176)]   # 대시보드 헤더와 동일 계열
UP, DN, FL = (224, 59, 50), (38, 104, 196), (120, 129, 138)


def font(size, bold=False):
    for path in (f"/usr/share/fonts/opentype/noto/NotoSansCJK-{'Bold' if bold else 'Regular'}.ttc",
                 'C:/Windows/Fonts/malgunbd.ttf' if bold else 'C:/Windows/Fonts/malgun.ttf'):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    try:
        q = "Noto Sans CJK KR:bold" if bold else "Noto Sans CJK KR"
        return ImageFont.truetype(
            subprocess.check_output(["fc-match", "-f", "%{file}", q]).decode().strip(), size)
    except Exception:                                        # noqa: BLE001
        return ImageFont.load_default()


def gradient() -> Image.Image:
    bg = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(bg)
    for x in range(W):
        t = x / (W - 1)
        if t < 0.42:
            k, a, b = t / 0.42, GRAD[0], GRAD[1]
        else:
            k, a, b = (t - 0.42) / 0.58, GRAD[1], GRAD[2]
        d.line([(x, 0), (x, H)], fill=tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3)))
    return bg


def fit_font(text, size, width, draw, bold=False):
    while size > 16 and draw.textlength(text, font=font(size, bold)) > width:
        size -= 1
    return font(size, bold)


def wrap(text, f, max_w, draw, limit=3):
    """Wrap Korean and long unspaced strings by measured width."""
    lines, current = [], ""
    for char in " ".join(str(text).split()):
        if current and draw.textlength(current + char, font=f) > max_w:
            lines.append(current.rstrip())
            current = char.lstrip()
        else:
            current += char
    if current:
        lines.append(current)
    if len(lines) > limit:
        lines = lines[:limit]
        while lines[-1] and draw.textlength(lines[-1] + "…", font=f) > max_w:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines


def build(html_path, out_png, date_line, kpis, oneline, tmpdir=None):
    """Keep the renderer call signature; no dashboard screenshots are needed."""
    import html
    import re
    bg = gradient()
    d = ImageDraw.Draw(bg)
    white = (255, 255, 255)
    d.text((48, 24), "퇴직연금 · 데일리 마켓", font=font(28), fill=white)
    d.text((44, 62), "글로벌 마켓 브리핑", font=font(82, True), fill=white)
    d.text((48, 172), date_line, font=fit_font(date_line, 30, 1104, d), fill=white)

    width, gap, top, height = 264, 16, 236, 146
    for i, (label, value, change, direction) in enumerate(kpis[:4]):
        left = 48 + i * (width + gap)
        d.rounded_rectangle((left, top, left + width, top + height), radius=18, fill=white)
        d.text((left + 18, top + 12), label, font=font(28), fill=(100, 110, 120))
        d.text((left + 18, top + 49), value,
               font=fit_font(value, 46, width - 36, d, True), fill=(29, 34, 38))
        d.text((left + 18, top + 106), change,
               font=fit_font(change, 30, width - 36, d), fill={"up": UP, "dn": DN}.get(direction, FL))

    d.rounded_rectangle((48, 402, 1152, 570), radius=20, fill=white)
    d.text((70, 414), "오늘의 한 줄", font=font(30, True), fill=(46, 156, 124))
    plain = html.unescape(re.sub(r"<[^>]*>", "", str(oneline)))
    f = font(36)
    for i, line in enumerate(wrap(plain, f, 1060, d, limit=2)):
        d.text((70, 462 + i * 47), line, font=f, fill=(51, 56, 61))
    d.text((48, 588), "MORNING BRIEF", font=font(23, True), fill=white)
    footer = "작성 PHILIP"
    d.text((1152 - d.textlength(footer, font=font(23)), 588), footer, font=font(23), fill=white)
    bg.quantize(colors=220, method=2).save(out_png, optimize=True)
    return out_png


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("out")
    ap.add_argument("--date-line", required=True)
    ap.add_argument("--oneline", default="")
    a = ap.parse_args()
    build(a.html, a.out, a.date_line,
          [("코스피", "-", "-", "fl")] * 4, a.oneline or "오늘의 시장 요약")
    print("OG →", a.out)
