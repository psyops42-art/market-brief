# -*- coding: utf-8 -*-
"""
주간 마켓 브리핑 OG 썸네일 — daily의 make_og.py를 3탭 구조에 맞게 조정.
1페이지(주간 리뷰)와 3페이지(주간 시장지표)를 캡처해 카드에 배치합니다.
render_weekly.py가 호출합니다. 단독 실행도 가능합니다.
"""

import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
GRAD = [(175, 162, 230), (149, 182, 222), (95, 211, 176)]
UP, DN, FL = (224, 59, 50), (38, 104, 196), (120, 129, 138)


def font(size, bold=False):
    path = f"/usr/share/fonts/opentype/noto/NotoSansCJK-{'Bold' if bold else 'Regular'}.ttc"
    if os.path.exists(path):
        return ImageFont.truetype(path, size)
    try:
        q = "Noto Sans CJK KR:bold" if bold else "Noto Sans CJK KR"
        return ImageFont.truetype(
            subprocess.check_output(["fc-match", "-f", "%{file}", q]).decode().strip(), size)
    except Exception:                                             # noqa: BLE001
        return ImageFont.load_default()


def shoot(html_path, page, out_png, width=390):
    src = html_path
    if page != 1:
        with open(html_path, encoding="utf-8") as fp:
            s = fp.read()
        s = (s.replace('<section class="pg on" id="p1">', '<section class="pg" id="p1">')
              .replace(f'<section class="pg" id="p{page}">', f'<section class="pg on" id="p{page}">')
              .replace("\ngo(1);", ""))
        fd, src = tempfile.mkstemp(suffix=".html")
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(s)
    try:
        if os.path.exists(out_png):
            os.remove(out_png)
        result = subprocess.run(["wkhtmltoimage", "--enable-local-file-access",
                                 "--width", str(width), "--quality", "92",
                                 "--javascript-delay", "600", os.path.abspath(src), os.path.abspath(out_png)],
                                capture_output=True, text=True, timeout=90)
        if result.returncode or not os.path.exists(out_png) or os.path.getsize(out_png) == 0:
            raise RuntimeError(
                f"캡처 실패({result.returncode}): {(result.stderr or result.stdout)[:300]}")
    finally:
        if page != 1 and os.path.exists(src):
            os.unlink(src)


def gradient():
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


def phone(img, target_h):
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


def wrap(text, f, max_w, d, limit=3):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if d.textlength(trial, font=f) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
            if len(lines) == limit:
                break
    if cur and len(lines) < limit:
        lines.append(cur)
    return lines


def build(html_path, out_png, date_line, kpis, retro_text, tmpdir=None):
    owned_tmp = tmpdir is None
    tmpdir = tmpdir or tempfile.mkdtemp()
    p1, p3 = os.path.join(tmpdir, "_wp1.png"), os.path.join(tmpdir, "_wp3.png")
    shoot(html_path, 1, p1)
    shoot(html_path, 3, p3)

    bg = gradient()
    im1 = Image.open(p1).convert("RGB").crop((0, 0, 390, min(760, Image.open(p1).size[1])))
    im3 = Image.open(p3).convert("RGB")
    im3 = im3.crop((0, 150, 390, min(910, im3.size[1])))
    f1, m1 = phone(im1, 470)
    f3, m3 = phone(im3, 470)
    paste_shadow(bg, f1, m1, (622, 80))
    paste_shadow(bg, f3, m3, (900, 80))

    d = ImageDraw.Draw(bg)
    d.text((56, 62), "퇴직연금 · 위클리 마켓", font=font(22), fill=(255, 255, 255))
    d.text((56, 96), "주간 마켓 브리핑", font=font(58, True), fill=(255, 255, 255))

    fd = font(20)
    for size in (20, 19, 18, 17):
        fd = font(size)
        if d.textlength(date_line, font=fd) <= 545:
            break
    d.text((56, 176), date_line, font=fd, fill=(255, 255, 255))

    x0, y0, bw, bh, gap = 56, 238, 131, 100, 8
    for i, (label, value, chg, direction) in enumerate(kpis[:4]):
        x = x0 + i * (bw + gap)
        mk = Image.new("L", (bw, bh), 0)
        ImageDraw.Draw(mk).rounded_rectangle([0, 0, bw - 1, bh - 1], radius=13, fill=255)
        bg.paste(Image.new("RGB", (bw, bh), (255, 255, 255)), (x, y0), mk)
        d.text((x + 12, y0 + 12), label, font=font(17), fill=(120, 129, 138))
        d.text((x + 12, y0 + 37), value, font=font(26, True), fill=(29, 34, 38))
        d.text((x + 12, y0 + 71), chg, font=font(17), fill={"up": UP, "dn": DN}.get(direction, FL))

    bx, by, bw2, bh2 = 56, 362, 520, 150
    mk = Image.new("L", (bw2, bh2), 0)
    ImageDraw.Draw(mk).rounded_rectangle([0, 0, bw2 - 1, bh2 - 1], radius=15, fill=255)
    bg.paste(Image.new("RGB", (bw2, bh2), (255, 255, 255)), (bx, by), mk)
    d.text((bx + 18, by + 16), "지난주 회고", font=font(19, True), fill=(107, 87, 196))
    fb = font(19)
    plain = retro_text.replace("<b>", "").replace("</b>", "") or "이번 주 시장을 정리했습니다."
    for i, line in enumerate(wrap(plain, fb, bw2 - 36, d, limit=3)):
        d.text((bx + 18, by + 48 + i * 28), line, font=fb, fill=(51, 56, 61))

    d.text((56, 556), "WEEKLY BRIEF", font=font(20, True), fill=(255, 255, 255))
    d.text((222, 558), "주간 리뷰 · 이번주 인사이트 · 시장지표 · 작성 PHILIP",
           font=font(17), fill=(255, 255, 255))

    bg.save(out_png)
    with Image.open(out_png) as rendered:
        rendered.convert("RGB").quantize(colors=220, method=2).save(out_png, optimize=True)
    for f_ in (p1, p3):
        if os.path.exists(f_):
            os.remove(f_)
    if owned_tmp:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return out_png


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("사용법: python make_og_weekly.py <html> <out.png>")
    build(sys.argv[1], sys.argv[2], "8월 24일~28일 정리 · 8월 31일(월) 아침",
          [("코스피", "-", "-", "fl")] * 4, "이번 주 시장을 정리했습니다.")
    print("OG →", sys.argv[2])
