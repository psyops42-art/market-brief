# -*- coding: utf-8 -*-
"""
OG 썸네일 생성 — 대시보드 실제 화면을 캡처해 1200×630 카드로 합성

render.py 가 호출합니다. 단독 실행도 가능합니다.

    python make_og.py out/2026-08-30.html out/og-2026-08-30.png \
        --date-line "2026년 8월 30일 (일) 아침 · 美/韓 8/28 마감 기준"

필요 패키지 : pillow, wkhtmltoimage (wkhtmltopdf 패키지), fonts-noto-cjk
"""

import argparse
import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
GRAD = [(175, 162, 230), (149, 182, 222), (95, 211, 176)]   # 대시보드 헤더와 동일 계열
UP, DN, FL = (224, 59, 50), (38, 104, 196), (120, 129, 138)


def font(size, bold=False):
    for path in (f"/usr/share/fonts/opentype/noto/NotoSansCJK-{'Bold' if bold else 'Regular'}.ttc",):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    try:
        q = "Noto Sans CJK KR:bold" if bold else "Noto Sans CJK KR"
        return ImageFont.truetype(
            subprocess.check_output(["fc-match", "-f", "%{file}", q]).decode().strip(), size)
    except Exception:                                        # noqa: BLE001
        return ImageFont.load_default()


def shoot(html_path: str, page: int, out_png: str, width: int = 390):
    """대시보드를 캡처. page=2 면 2번 탭이 열린 상태로 렌더한다."""
    src = html_path
    if page == 2:
        s = open(html_path, encoding="utf-8").read()
        s = (s.replace('<section class="pg on" id="p1">', '<section class="pg" id="p1">')
              .replace('<section class="pg" id="p2">', '<section class="pg on" id="p2">')
              .replace("\ngo(1);", ""))
        fd, src = tempfile.mkstemp(suffix=".html")
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(s)
    r = subprocess.run(["wkhtmltoimage", "--width", str(width), "--quality", "92",
                        "--javascript-delay", "600", src, out_png],
                       capture_output=True)
    if page == 2:
        os.unlink(src)
    if not os.path.exists(out_png):
        sys.exit(f"캡처 실패: {r.stderr.decode()[:300]}")


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


def phone(img: Image.Image, target_h: int):
    """폰 프레임(흰 테두리 + 라운드)"""
    r = target_h / img.size[1]
    img = img.resize((int(img.size[0] * r), target_h), Image.LANCZOS)
    pad = 6
    card = Image.new("RGB", (img.size[0] + pad * 2, img.size[1] + pad * 2), (255, 255, 255))
    card.paste(img, (pad, pad))
    mask = Image.new("L", card.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, *[v - 1 for v in card.size]], radius=20, fill=255)
    return card, mask


def paste_shadow(base, card, mask, xy):
    sh = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [xy[0] + 5, xy[1] + 8, xy[0] + card.size[0] + 5, xy[1] + card.size[1] + 8],
        radius=20, fill=(20, 40, 60, 70))
    base.paste(Image.alpha_composite(base.convert("RGBA"), sh).convert("RGB"), (0, 0))
    base.paste(card, xy, mask)


def wrap(text: str, f, max_w: int, d: ImageDraw.ImageDraw, limit=3):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if d.textlength(trial, font=f) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = word
            if len(lines) == limit:
                break
    if cur and len(lines) < limit:
        lines.append(cur)
    return lines


def build(html_path, out_png, date_line, kpis, oneline, tmpdir=None):
    tmpdir = tmpdir or tempfile.mkdtemp()
    p1, p2 = os.path.join(tmpdir, "_p1.png"), os.path.join(tmpdir, "_p2.png")
    shoot(html_path, 1, p1)
    shoot(html_path, 2, p2)

    bg = gradient()
    im1 = Image.open(p1).convert("RGB").crop((0, 0, 390, min(760, Image.open(p1).size[1])))
    h2 = Image.open(p2).size[1]
    im2 = Image.open(p2).convert("RGB").crop((0, 150, 390, min(910, h2)))
    f1, m1 = phone(im1, 470)
    f2, m2 = phone(im2, 470)
    paste_shadow(bg, f1, m1, (622, 80))
    paste_shadow(bg, f2, m2, (900, 80))

    d = ImageDraw.Draw(bg)
    d.text((56, 62), "퇴직연금 · 데일리 마켓", font=font(22), fill=(255, 255, 255))
    d.text((56, 96), "글로벌 마켓 브리핑", font=font(60, True), fill=(255, 255, 255))
    fd = font(21)                                   # 폰 이미지에 가리지 않게 자동 축소
    for size in (21, 20, 19, 18, 17):
        fd = font(size)
        if d.textlength(date_line, font=fd) <= 545:
            break
    d.text((56, 178), date_line, font=fd, fill=(255, 255, 255))

    x0, y0, bw, bh, gap = 56, 240, 131, 104, 8
    for i, (label, value, chg, direction) in enumerate(kpis[:4]):
        x = x0 + i * (bw + gap)
        mk = Image.new("L", (bw, bh), 0)
        ImageDraw.Draw(mk).rounded_rectangle([0, 0, bw - 1, bh - 1], radius=13, fill=255)
        bg.paste(Image.new("RGB", (bw, bh), (255, 255, 255)), (x, y0), mk)
        d.text((x + 13, y0 + 13), label, font=font(19), fill=(120, 129, 138))
        d.text((x + 13, y0 + 40), value, font=font(30, True), fill=(29, 34, 38))
        d.text((x + 13, y0 + 76), chg, font=font(19),
               fill={"up": UP, "dn": DN}.get(direction, FL))

    bx, by, bw2, bh2 = 56, 372, 520, 150
    mk = Image.new("L", (bw2, bh2), 0)
    ImageDraw.Draw(mk).rounded_rectangle([0, 0, bw2 - 1, bh2 - 1], radius=15, fill=255)
    bg.paste(Image.new("RGB", (bw2, bh2), (255, 255, 255)), (bx, by), mk)
    d.text((bx + 18, by + 16), "오늘의 한 줄", font=font(20, True), fill=(46, 156, 124))
    fb = font(21)
    for i, line in enumerate(wrap(oneline, fb, bw2 - 36, d, limit=3)):
        d.text((bx + 18, by + 50 + i * 31), line, font=fb, fill=(51, 56, 61))

    d.text((56, 556), "MORNING BRIEF", font=font(20, True), fill=(255, 255, 255))
    d.text((232, 558), "헤드라인 3 · 시장지표 12 · 작성 PHILIP", font=font(18), fill=(255, 255, 255))

    bg.save(out_png)
    Image.open(out_png).convert("RGB").quantize(colors=220, method=2).save(out_png, optimize=True)
    for f in (p1, p2):
        os.path.exists(f) and os.remove(f)
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
