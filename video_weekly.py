# -*- coding: utf-8 -*-
"""
주간 마켓 브리핑 · 3-1단계 · 1분 브리핑 영상  →  out/video/weekly-YYYY-MM-DD.mp4

    python video_weekly.py --data data_weekly.json --brief brief_weekly.json --out out

대시보드와 같은 data_weekly.json / brief_weekly.json 만 읽어서 만든다.
    · 화면: Pillow로 장면을 그리고 ffmpeg로 장면별 mp4를 만든 뒤 이어 붙인다 (세로 1080x1920).
    · 내레이션: LLM이 새 문장을 쓰지 않는다. 이미 검증된 대시보드 문장(헤드라인 제목·회고 등)과
      실제 수치로 원고를 '조립'한 뒤 Google Cloud TTS로 읽는다 → 화면과 말이 항상 일치한다.
    · 배경음악: numpy로 매번 합성(저작권 문제 없음). 말할 때는 자동으로 작아진다(덕킹).

실패해도 대시보드 배포를 막지 않는다.
    · GOOGLE_TTS_API_KEY 가 없거나 TTS가 실패하면 → 음성 없이 배경음악 버전으로 만든다.
    · 영상 자체가 실패하면 → out/video_report.json 에 원인을 남기고 종료코드 0으로 끝낸다.
"""

import argparse
import base64
import datetime as dt
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave

from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H, FPS = 1080, 1920, 30
WD_KR = ["월", "화", "수", "목", "금", "토", "일"]
HERE = os.path.dirname(os.path.abspath(__file__))

TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
TTS_VOICES = ["ko-KR-Chirp3-HD-Kore", "ko-KR-Neural2-A"]
TTS_RATE = 1.1
LEAD, TAIL = 0.45, 0.55          # 장면 시작 후 말 시작까지 / 말 끝난 뒤 여유
TARGET_SEC = 72                  # 원고가 이보다 길면 보조 문장부터 뺀다
CHARS_PER_SEC = 7.0              # 1.1배속 Chirp3 실측 근사치


# ═══════════════════════════════ 글꼴

_NOTO = "/usr/share/fonts/opentype/noto/NotoSansCJK-{}.ttc"
_NANUM = os.path.join(HERE, "assets", "fonts", "NanumGothic-{}.ttf")
_FALLBACK = {"Black": ["Black", "Bold"], "Bold": ["Bold"], "Medium": ["Medium", "Regular"],
             "Regular": ["Regular"]}
_fc = {}


def F(size, w="Bold"):
    k = (size, w)
    if k in _fc:
        return _fc[k]
    for cand in _FALLBACK[w]:
        p = _NOTO.format(cand)
        if os.path.exists(p):
            _fc[k] = ImageFont.truetype(p, size, index=1)      # index 1 = KR
            return _fc[k]
    nan = "Bold" if w in ("Black", "Bold") else "Regular"
    p = _NANUM.format(nan)
    if os.path.exists(p):
        _fc[k] = ImageFont.truetype(p, size)
        return _fc[k]
    raise FileNotFoundError("한글 글꼴이 없습니다 (scripts/install-render-tools.sh 실행 필요)")


# ═══════════════════════════════ 색상 (대시보드와 같은 계열)

INK = (29, 34, 38); SUB = (90, 99, 107); MUTE = (152, 161, 168)
PURPLE = (124, 107, 208); MINT = (63, 185, 154); BLUEG = (143, 187, 221)
UP = (224, 59, 50); DOWN = (38, 104, 196); WHITE = (255, 255, 255)
BORDER = (227, 231, 227); AMBER = (214, 154, 62); GREY = (152, 161, 168)


def ease(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def lerp(a, b, t):
    return a + (b - a) * t


def grad(w, h, stops, horiz=True):
    """stops: [(pos, rgb), ...] 다중 정지점 선형 그라데이션"""
    g = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(g)
    n = w if horiz else h
    for i in range(n):
        t = i / max(1, n - 1)
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if p0 <= t <= p1:
                k = (t - p0) / max(1e-6, p1 - p0)
                c = tuple(int(lerp(c0[j], c1[j], k)) for j in range(3)) + (255,)
                break
        if horiz:
            d.line([(i, 0), (i, h)], fill=c)
        else:
            d.line([(0, i), (w, i)], fill=c)
    return g


BRAND = [(0, (169, 155, 224)), (0.55, (143, 187, 221)), (1, (99, 211, 178))]


# ═══════════════════════════════ 텍스트

def plain(text):
    return html.unescape(re.sub(r"<[^>]+>", "", str(text or ""))).strip()


def _tokens(text, color, hl):
    """'<b>강조</b>' 를 색이 다른 토큰으로 나누고, 줄바꿈 단위(어절)로 쪼갠다."""
    out = []
    for i, part in enumerate(re.split(r"</?b>", str(text or ""))):
        part = html.unescape(part)
        c = hl if (i % 2 == 1 and hl) else color
        for tok in re.findall(r"\n|[^\S\n]+|\S+", part):
            out.append((tok, c))
    return out


def text_block(text, size, w="Bold", color=INK, max_w=940, hl=None, spacing=1.32, align="left",
               max_lines=None):
    f = F(size, w)
    lines, cur, cur_w = [], [], 0.0
    for tok, c in _tokens(text, color, hl):
        if tok == "\n":
            if cur:
                lines.append((cur, cur_w))
            cur, cur_w = [], 0.0
            continue
        tw = f.getlength(tok)
        if tok.isspace():
            if cur:
                cur.append((tok, c)); cur_w += tw
            continue
        if cur_w + tw > max_w and cur:
            while cur and cur[-1][0].isspace():
                cur_w -= f.getlength(cur.pop()[0])
            lines.append((cur, cur_w)); cur, cur_w = [], 0.0
        if tw > max_w:                       # 한 어절이 너무 길면 글자 단위로 자른다
            for ch in tok:
                cw = f.getlength(ch)
                if cur_w + cw > max_w and cur:
                    lines.append((cur, cur_w)); cur, cur_w = [], 0.0
                cur.append((ch, c)); cur_w += cw
            continue
        cur.append((tok, c)); cur_w += tw
    if cur:
        lines.append((cur, cur_w))
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last, lw = lines[-1]
        lines[-1] = (last + [("…", last[-1][1])], lw + f.getlength("…"))
    if not lines:
        lines = [([(" ", color)], 1)]
    lh = int(size * spacing)
    mw = int(max(lw for _, lw in lines)) + 6
    img = Image.new("RGBA", (mw, lh * (len(lines) - 1) + int(size * 1.35)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, (runs, lw) in enumerate(lines):
        x = 0 if align == "left" else (mw - lw) / 2
        for tok, c in runs:
            d.text((x, i * lh), tok, font=f, fill=c)
            x += f.getlength(tok)
    return img


def pill(text, size, bg, fg=WHITE, w="Bold", padx=24, pady=9):
    f = F(size, w)
    tw = int(f.getlength(text))
    img = Image.new("RGBA", (tw + padx * 2, int(size * 1.3) + pady * 2), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, img.width - 1, img.height - 1], img.height // 2, fill=bg)
    ImageDraw.Draw(img).text((padx, pady - int(size * 0.06)), text, font=f, fill=fg)
    return img


PAD = 30


def card(w, h, fill=WHITE, r=36, border=BORDER, shadow=True):
    img = Image.new("RGBA", (w + PAD * 2, h + PAD * 2), (0, 0, 0, 0))
    if shadow:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([PAD, PAD + 10, PAD + w, PAD + h + 10], r, fill=(40, 60, 80, 34))
        img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(14)))
    if isinstance(fill, Image.Image):
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], r, fill=255)
        img.paste(fill.resize((w, h)), (PAD, PAD), m)
    else:
        ImageDraw.Draw(img).rounded_rectangle([PAD, PAD, PAD + w, PAD + h], r, fill=fill,
                                              outline=border, width=3 if border else 0)
    return img


def put(base, spr, x, y):
    base.alpha_composite(spr, (int(x + PAD), int(y + PAD)))
    return base


def fade(spr, k):
    if k >= 1:
        return spr
    s = spr.copy()
    s.putalpha(s.getchannel("A").point(lambda v: int(v * k)))
    return s


# ═══════════════════════════════ 장면

class Scene:
    def __init__(self, base_dur, bg, narration=""):
        self.dur = base_dur
        self.base_dur = base_dur
        self.bg = bg
        self.narration = narration
        self.narr_short = None
        self.items, self.dyn = [], []

    def add(self, spr, x, y, t, d=0.5, dy=50, dx=0, center=False):
        if center:
            x = (W - spr.width) // 2
        self.items.append((spr, x, y, t, d, dy, dx))
        return spr

    def frame(self, t, g0, total):
        im = self.bg.copy()
        for kind, fn in self.dyn:
            if kind == "pre":
                fn(im, t)
        for spr, x, y, t0, d, dy, dx in self.items:
            if t < t0:
                continue
            k = ease((t - t0) / d)
            a = min(1.0, (t - t0) / (d * 0.7))
            im.alpha_composite(fade(spr, a), (int(x + dx * (1 - k)), int(y + dy * (1 - k))))
        for kind, fn in self.dyn:
            if kind == "post":
                fn(im, t)
        dd = ImageDraw.Draw(im)
        p = min(1.0, (g0 + t) / total)
        dd.rounded_rectangle([60, 1868, W - 60, 1878], 5, fill=(255, 255, 255, 150))
        dd.rounded_rectangle([60, 1868, 60 + int((W - 120) * p), 1878], 5, fill=PURPLE)
        return im


LIGHT = None
HEAD = None


def base_bg(section, tag, foot_date):
    global LIGHT, HEAD
    if LIGHT is None:
        LIGHT = grad(W, H, [(0, (244, 246, 244)), (1, (236, 244, 241))], horiz=False)
        HEAD = grad(W, 300, BRAND)
    bg = LIGHT.copy()
    bg.alpha_composite(HEAD, (0, 0))
    d = ImageDraw.Draw(bg)
    d.text((70, 92), "퇴직연금 · 위클리 마켓", font=F(34, "Medium"), fill=(255, 255, 255, 235))
    d.text((70, 140), section, font=F(76, "Black"), fill=WHITE)
    tg = pill(tag, 32, (255, 255, 255, 70))
    bg.alpha_composite(tg, (W - 70 - tg.width, 96))
    fb = F(34, "Black")
    d.text((70, 1800), "WEEKLY BRIEF", font=fb, fill=(31, 78, 156))
    d.text((70 + fb.getlength("WEEKLY BRIEF") + 16, 1806), "작성 PHILIP", font=F(28, "Medium"), fill=SUB)
    fm = F(28, "Medium")
    d.text((W - 70 - fm.getlength(foot_date), 1806), foot_date, font=fm, fill=MUTE)
    return bg


def counter(s, x, y, t0, d, a, b, fmt, size, color, w="Black", anchor="la"):
    def fn(im, t):
        if t < t0:
            return
        k = ease((t - t0) / d)
        al = min(1, (t - t0) / 0.3)
        layer = Image.new("RGBA", (W, size + 60), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((x, 10), fmt(lerp(a, b, k)), font=F(size, w),
                                   fill=color + (int(255 * al),), anchor=anchor)
        im.alpha_composite(layer, (0, y - 10))
    s.dyn.append(("post", fn))


def chg_color(v):
    return GREY if v is None or v == 0 else (UP if v > 0 else DOWN)


def pct(v):
    if v is None:
        return "－"
    return f'{"▲" if v > 0 else "▼" if v < 0 else "－"} {abs(v):.2f}%'


def bp(v):
    if v is None:
        return "－"
    return f'{"▲" if v > 0 else "▼" if v < 0 else "－"} {abs(v):.1f}bp'


# ═══════════════════════════════ 읽기용 문장 정리

def speakable(text):
    t = plain(text)
    rep = [("S&P 500", "에스앤피 오백"), ("S&P500", "에스앤피 오백"), ("나스닥 100", "나스닥 백"),
           ("유로스톡스 50", "유로스톡스 오십"), ("WTI", "더블유티아이"), ("YTD", "연초 대비"),
           ("—", ", "), ("–", ", "), ("·", ", "),
           (":", ","), ("%p", "%포인트"), ("→", ", ")]
    for a, b in rep:
        t = t.replace(a, b)
    t = re.sub(r"(?<![\w.])\+(?=\d)", "", t)                 # +3.25% → 3.25% (뒤의 '올라'가 방향을 말함)
    t = re.sub(r"(?<![\w.])-(?=\d)", "마이너스 ", t)
    t = re.sub(r"\s*\+\s*", " 그리고 ", t)
    t = re.sub(r"\s*\([^)]*\)", "", t)                        # 괄호 속 보충 설명은 읽지 않는다
    t = re.sub(r"\$\s?([\d,]+(?:\.\d+)?)", r"\1달러", t)
    t = re.sub(r"(?<![\d.])(\d{1,2})/(\d{1,2})(?![\d/])", r"\1월 \2일", t)
    t = re.sub(r"(\d)\s?~\s?(\d)", r"\1에서 \2", t)
    t = re.sub(r"(\d)bp", r"\1 베이시스포인트", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def first_sentence(text):
    t = plain(text)
    m = re.search(r"^(.+?[다요]\.)(\s|$)", t)
    return (m.group(1) if m else t).strip()


def change_word(v, unit="price"):
    if v is None:
        return ""
    if unit == "bp":
        p = abs(v) / 100
        return f"{p:.2f}%포인트 {'올랐습니다' if v > 0 else '내렸습니다'}" if v else "보합이었습니다"
    return f"{abs(v):.2f}% {'상승' if v > 0 else '하락'}" if v else "보합"


RELATED = [(r"비트코인|가상자산|코인", "btc"), (r"나스닥|빅테크|기술주|AI", "ndx"), (r"S&P|뉴욕증시|미국 증시", "sp500"),
           (r"코스피|국내 증시|한국 증시", "kospi"), (r"상해|중국 증시", "shcomp"), (r"유로스톡스|유럽 증시", "sx5e"),
           (r"국제금|금값|금 가격", "gold"), (r"유가|원유|WTI|산유", "wti"), (r"환율|원화|달러/원", "usdkrw"),
           (r"국고채|한국은행|금통위", "ktb3y"), (r"연준|미국채|미 국채|미국 국채", "ust10y")]


def related_key(text, S):
    """헤드라인에 등장한 자산 중 실제 주간 수치가 있는 첫 번째 지표"""
    for pat, key in RELATED:
        if re.search(pat, text) and S.get(key, {}).get("wow_pct") is not None:
            return key
    return None


def layout_top(heights, gaps, top=360, bottom=1740):
    """블록 묶음을 본문 영역 세로 가운데(살짝 위쪽)에 놓는 시작 y"""
    total = sum(heights) + sum(gaps)
    return int(top + max(0, (bottom - top - total) * 0.38))


# ═══════════════════════════════ 장면 구성 (data + brief → 장면 목록)

def build_scenes(data, brief):
    S = data["series"]
    lw, tw = data["last_week"], data["this_week"]
    lm, lf = dt.date.fromisoformat(lw["mon"]), dt.date.fromisoformat(lw["fri"])
    tm = dt.date.fromisoformat(tw["mon"])
    foot = f"{tm.year}.{tm.month}.{tm.day} ({WD_KR[tm.weekday()]})"
    period = f"{lm.month}월 {lm.day}일~{lf.day}일 정리"
    scenes = []

    # ── 0. 인트로
    bg = grad(W, H, [(0, (176, 162, 232)), (0.5, (143, 187, 221)), (1, (99, 211, 178))], horiz=False)
    s = Scene(4.0, bg, f"{tm.month}월 {tm.day}일 월요일, 주간 마켓 브리핑입니다.")
    circles = [(180, 380, 260, 0.6), (900, 640, 200, 1.0), (760, 1500, 330, 0.5), (200, 1600, 160, 0.9)]
    sprites = {}

    def deco(im, t):
        for cx, cy, r, sp in circles:
            if r not in sprites:
                ov = Image.new("RGBA", (r * 2, r * 2), (0, 0, 0, 0))
                ImageDraw.Draw(ov).ellipse([0, 0, r * 2 - 1, r * 2 - 1], fill=(255, 255, 255, 34))
                sprites[r] = ov
            yy = cy + math.sin(t * sp * 1.6) * 40
            im.alpha_composite(sprites[r], (int(cx - r), int(yy - r)))
    s.dyn.append(("pre", deco))
    s.add(pill("퇴직연금 · 위클리 마켓", 38, (255, 255, 255, 60)), 0, 640, 0.1, center=True)
    s.add(text_block("주간\n마켓 브리핑", 150, "Black", WHITE, 760, align="center",
                     spacing=1.15), 0, 760, 0.3, dy=80, center=True)
    s.add(text_block(period, 52, "Bold", WHITE, 960, align="center"), 0, 1160, 0.75, center=True)
    s.add(text_block(f"{tm.month}월 {tm.day}일({WD_KR[tm.weekday()]}) 아침", 38, "Medium",
                     (255, 255, 255), 960, align="center"), 0, 1240, 0.95, center=True)
    s.add(text_block("WEEKLY BRIEF  작성 PHILIP", 34, "Bold", WHITE, 960, align="center"),
          0, 1760, 1.2, center=True)
    scenes.append(s)

    # ── 1~3. 지난주 헤드라인 (+ 제목·본문에 나온 자산의 실제 주간 등락)
    heads = brief.get("last_week_headlines", [])[:3]
    for i, n in enumerate(heads, 1):
        s = Scene(6.0, base_bg("지난주 한눈에", f"{i} / {len(heads)}", foot),
                  f"{plain(n.get('title'))}. {first_sentence(n.get('body'))}")
        s.narr_short = f"{plain(n.get('title'))}."
        tag = pill(f"HEADLINE {i:02d}", 34, MINT)
        title = text_block(n.get("title", ""), 96, "Black", INK, 940, spacing=1.22, max_lines=3)
        body = text_block(n.get("body", ""), 54, "Medium", INK, 840, spacing=1.55, max_lines=7)
        c = card(940, body.height + 170)
        put(c, body, 50, 50)
        put(c, text_block(n.get("source", ""), 34, "Medium", MUTE, 840), 50, body.height + 92)
        key = related_key(plain(n.get("title")) + " " + plain(n.get("body")), S)
        blocks = [(tag, 70, 0.15, 30), (title, 70, 0.3, 50), (c, 40, 1.0, 50)]
        gaps = [24, 50, 40]
        rc = None
        if key:
            r = S[key]
            rc = card(940, 250, fill=(250, 251, 250))
            put(rc, text_block("관련 지표 · 지난주 등락", 34, "Medium", SUB, 600), 50, 36)
            put(rc, text_block(r["label"], 60, "Black", INK, 520), 50, 104)
            blocks.append((rc, 40, 1.6, 50))
        hs = [b[0].height for b in blocks]
        y = layout_top(hs, gaps)
        for (spr, x, t0, dy), h, g in zip(blocks, hs, gaps + [0]):
            s.add(spr, x, y, t0, dy=dy)
            if spr is rc:
                r = S[key]; v = r["wow_pct"]
                unit = r.get("unit")
                fmt = ((lambda z, v=v: f'{"▲" if v > 0 else "▼" if v < 0 else ""} {abs(z):.1f}bp') if unit == "bp"
                       else (lambda z, v=v: f'{"▲" if v > 0 else "▼" if v < 0 else ""} {abs(z):.2f}%'))
                counter(s, W - 90 - 10, y + PAD + 100, 1.9, 1.2, 0, v, fmt, 90, chg_color(v), anchor="ra")
            y += h + g
        scenes.append(s)

    # ── 4. 지난주 성적표 (최고/최저 + 주요 지수)
    priced = [(k, r) for k, r in S.items() if r.get("unit") == "price" and r.get("wow_pct") is not None]
    narr = ""
    s = Scene(7.0, base_bg("지난주 성적표", "주간 등락", foot), "")
    if priced:
        bk, best = max(priced, key=lambda kr: kr[1]["wow_pct"])
        wk, worst = min(priced, key=lambda kr: kr[1]["wow_pct"])
        narr = (f"주간 성적 1위는 {best['label']}, {change_word(best['wow_pct'])}. "
                f"최하위는 {worst['label']}, {change_word(worst['wow_pct'])}.")
        for j, (lab, r, col, x) in enumerate((("주간 최고", best, (226, 84, 76), 40),
                                              ("주간 최저", worst, (64, 118, 196), 530))):
            fill = grad(450, 330, [(0, col), (1, tuple(max(0, c - 22) for c in col))])
            c = card(450, 330, fill=fill, border=None)
            put(c, text_block(lab, 34, "Bold", (255, 255, 255), 400), 40, 34)
            put(c, text_block(r["label"], 50, "Black", WHITE, 400), 40, 96)
            s.add(c, x, 360, 0.3 + j * 0.3)
            v = r["wow_pct"]
            counter(s, x + PAD + 40, 360 + PAD + 200, 0.6 + j * 0.3, 1.2, 0, v,
                    lambda z, v=v: f'{"▲" if v > 0 else "▼" if v < 0 else ""} {abs(z):.2f}%', 76, WHITE)
    y = 780
    s.add(text_block("주요 지수 · 전주 대비", 36, "Medium", SUB, 900), 70, y, 1.2, dy=20)
    y += 70
    for j, k in enumerate(["sp500", "ndx", "kospi", "shcomp", "sx5e"]):
        r = S.get(k)
        if not r:
            continue
        t0 = 1.4 + j * 0.25
        c = card(940, 150)
        put(c, text_block(r["label"], 46, "Black", INK, 500), 40, 22)
        put(c, text_block(f'{r["value"]:,.2f}' if r.get("value") is not None else "－", 34, "Bold", SUB, 400),
            40, 88)
        s.add(c, 40, y, t0, dx=120, dy=0)
        v = r.get("wow_pct")

        def bar(im, t, yy=y, v=v, t0=t0):
            if t < t0 + 0.3 or v is None:
                return
            k2 = ease((t - t0 - 0.3) / 1.0)
            d = ImageDraw.Draw(im)
            bx, by, bw = 420, yy + PAD + 110, 260
            d.rounded_rectangle([bx, by, bx + bw, by + 16], 8, fill=(236, 238, 242))
            L = int(bw * min(1, abs(v) / 5) * k2)
            if L > 2:
                d.rounded_rectangle([bx, by, bx + max(L, 16), by + 16], 8, fill=chg_color(v))
            d.text((W - 90, yy + PAD + 34), pct(v * k2) if v else "－", font=F(54, "Black"),
                   fill=chg_color(v), anchor="ra")
        s.dyn.append(("post", bar))
        y += 172
    s.narration = narr
    scenes.append(s)

    # ── 5. 금리 · 환율 · 원자재
    s = Scene(6.0, base_bg("금리 · 환율", "전주 대비", foot), "")
    bits = []
    for j, k in enumerate(["ktb3y", "ktb10y", "ust10y", "ust30y"]):
        r = S.get(k)
        if not r:
            continue
        x = 40 + (j % 2) * 490; yy = 370 + (j // 2) * 330
        c = card(450, 290)
        put(c, text_block(r["label"], 42, "Black", INK, 400), 36, 30)
        put(c, text_block(f'{r["value"]:.3f}%' if r.get("value") is not None else "－", 66, "Black", INK, 400),
            36, 96)
        put(c, pill(bp(r.get("wow_pct")), 34, chg_color(r.get("wow_pct"))), 36, 196)
        s.add(c, x, yy, 0.3 + j * 0.25, dy=60)
    k3 = S.get("ktb3y") or {}
    if k3.get("wow_pct"):
        bits.append(f"국고채 3년 금리는 {abs(k3['wow_pct']) / 100:.2f}%포인트 "
                    f"{'올랐' if k3['wow_pct'] > 0 else '내렸'}")
    y = 1060
    for j, k in enumerate(["usdkrw", "gold", "wti", "btc"]):
        r = S.get(k)
        if not r:
            continue
        c = card(940, 120)
        put(c, text_block(r["label"], 42, "Black", INK, 420), 40, 30)
        val = f'{r["value"]:,.2f}' if r.get("value") is not None else "－"
        vs = text_block(val, 40, "Bold", SUB, 300)
        put(c, vs, 470 - vs.width // 2 + 40, 32)
        cs = text_block(pct(r.get("wow_pct")), 42, "Black", chg_color(r.get("wow_pct")), 300)
        put(c, cs, 940 - 40 - cs.width, 30)
        s.add(c, 40, y, 1.3 + j * 0.25, dx=120, dy=0)
        y += 142
    u = S.get("usdkrw") or {}
    if u.get("wow_pct"):
        bits.append(f"원 달러 환율은 {abs(u['wow_pct']):.2f}% {'올랐' if u['wow_pct'] > 0 else '내렸'}")
    s.narration = ("고, ".join(bits) + "습니다.") if bits else "지난주 금리와 환율 흐름입니다."
    scenes.append(s)

    # ── 6. 지난주 회고
    retro = brief.get("retrospective") or {}
    if retro.get("title"):
        s = Scene(6.0, base_bg("투자마인드 회고", "RETRO", foot),
                  f"{plain(retro.get('title'))}. {first_sentence(retro.get('body'))}")
        s.narr_short = f"{plain(retro.get('title'))}."
        tg = pill("RETROSPECTIVE", 34, MINT)
        tt = text_block(retro.get("title", ""), 92, "Black", INK, 940, spacing=1.22, max_lines=3)
        body = text_block(retro.get("body", ""), 56, "Medium", INK, 800, spacing=1.6, hl=PURPLE, max_lines=8)
        c = card(940, body.height + 150, fill=(246, 243, 253), border=(205, 193, 241))
        put(c, text_block("“", 110, "Black", (156, 136, 223), 80), 40, 0)
        put(c, body, 70, 90)
        y = layout_top([tg.height, tt.height, c.height], [24, 50])
        s.add(tg, 70, y, 0.15, dy=30); y += tg.height + 24
        s.add(tt, 70, y, 0.3); y += tt.height + 50
        s.add(c, 40, y, 1.0)
        scenes.append(s)

    # ── 7. 이번주 체크포인트
    cps = brief.get("checkpoints", [])
    if cps:
        items = list(cps)
        if len(items) > 4:
            order = sorted(range(len(items)), key=lambda i: (not items[i].get("highlight"), i))[:4]
            items = [items[i] for i in sorted(order)]
        keys = [c for c in items if c.get("highlight")][:2] or items[:2]
        narr = "이번 주 핵심 일정입니다. " + " ".join(
            f"{c.get('date', '')} {plain(c.get('text'))}." for c in keys)
        s = Scene(6.0, base_bg("이번주 체크포인트", "THIS WEEK", foot), narr)
        y = 370
        for j, c0 in enumerate(items):
            hl = bool(c0.get("highlight"))
            et = text_block(c0.get("text", ""), 48, "Black", INK, 640, spacing=1.3, max_lines=2)
            ew = text_block(c0.get("why", ""), 34, "Medium", SUB, 640, spacing=1.45, max_lines=2)
            h = max(190, et.height + ew.height + 70)
            c = card(940, h, fill=(248, 246, 254) if hl else WHITE)
            chip = (grad(150, 150, [(0, (169, 155, 224)), (1, (143, 187, 221))]) if hl
                    else Image.new("RGBA", (150, 150), (238, 241, 238, 255)))
            m = Image.new("L", (150, 150), 0)
            ImageDraw.Draw(m).rounded_rectangle([0, 0, 149, 149], 26, fill=255)
            ch = Image.new("RGBA", (150, 150), (0, 0, 0, 0)); ch.paste(chip, (0, 0), m)
            cd = ImageDraw.Draw(ch)
            cd.text((75, 52), str(c0.get("day", "")), font=F(54, "Black"),
                    fill=WHITE if hl else SUB, anchor="mm")
            cd.text((75, 108), str(c0.get("date", "")), font=F(32, "Bold"),
                    fill=WHITE if hl else MUTE, anchor="mm")
            put(c, ch, 30, (h - 150) // 2)
            tx = 220
            if hl:
                kp = pill("KEY", 26, PURPLE, padx=14, pady=4)
                put(c, kp, tx, 30)
                put(c, et, tx + kp.width + 12, 22)
            else:
                put(c, et, tx, 22)
            put(c, ew, tx, et.height + 36)
            s.add(c, 40, y, 0.3 + j * 0.4, dx=-100, dy=0)
            y += h + 26
        scenes.append(s)

    # ── 8. 이번주 투자마인드 + 핵심/위성
    m1 = brief.get("mindset_01") or {}
    s = Scene(7.0, base_bg("이번주 투자마인드", "MINDSET", foot),
              (f"{plain(m1.get('title'))}. {first_sentence(m1.get('body'))} " if m1.get("title") else "")
              + "TDF는 핵심, ETF는 위성. 원래 계획을 지키세요.")
    s.narr_short = ((f"{plain(m1.get('title'))}. " if m1.get("title") else "")
                    + "TDF는 핵심, ETF는 위성. 원래 계획을 지키세요.")
    y = 370
    if m1.get("title"):
        tt = text_block(m1.get("title", ""), 56, "Black", INK, 840, max_lines=2)
        bb = text_block(m1.get("body", ""), 40, "Medium", SUB, 840, spacing=1.5, hl=INK, max_lines=6)
        c = card(940, tt.height + bb.height + 150)
        put(c, text_block("MINDSET 01", 30, "Black", MINT, 400), 50, 36)
        put(c, tt, 50, 84)
        put(c, bb, 50, 84 + tt.height + 24)
        s.add(c, 40, y, 0.2)
        y += tt.height + bb.height + 190
    c = card(940, 330)
    put(c, text_block("핵심은 지키고, 위성은 유연하게", 46, "Black", INK, 860), 50, 36)
    g1 = card(400, 170, fill=grad(400, 170, [(0, (96, 214, 176)), (1, (70, 190, 150))]), border=None, shadow=False)
    put(g1, text_block("핵심자산 Core", 32, "Bold", WHITE, 360), 30, 22)
    put(g1, text_block("TDF 30~50%", 52, "Black", WHITE, 360), 30, 72)
    g2 = card(400, 170, fill=grad(400, 170, [(0, (150, 168, 226)), (1, (124, 140, 214))]), border=None, shadow=False)
    put(g2, text_block("위성 Satellite", 32, "Bold", WHITE, 360), 30, 22)
    put(g2, text_block("ETF 30~70%", 52, "Black", WHITE, 360), 30, 72)
    put(c, g1, 18, 110); put(c, g2, 488, 110)
    put(c, text_block("+", 60, "Black", MUTE, 60), 450, 150)
    s.add(c, 40, min(y, 1360), 1.6)
    scenes.append(s)

    # ── 9. 정리 문장 + 마무리
    quotes = brief.get("quotes", [])[:3]
    s = Scene(5.5, base_bg("오늘의 정리", "SUMMARY", foot),
              (plain(quotes[0]) + " " if quotes else "") + "이번 주도 차분한 장기투자 되세요.")
    y = 380
    for j, q in enumerate(quotes):
        qb = text_block(q, 48, "Bold", INK, 800, spacing=1.45, hl=PURPLE, max_lines=3)
        c = card(940, qb.height + 80, fill=(246, 243, 253), border=(205, 193, 241))
        put(c, text_block("“", 80, "Black", (156, 136, 223), 60), 30, 10)
        put(c, qb, 100, 40)
        s.add(c, 40, y, 0.2 + j * 0.45)
        y += qb.height + 120
    s.add(text_block("이번 주도 차분한\n장기투자 되세요", 80, "Black", PURPLE, 760,
                     align="center", spacing=1.25), 0, max(y + 40, 1150), 1.8, center=True)
    s.add(text_block("본 자료는 시장 정보 제공 목적이며, 특정 상품의 매매를 권유하지 않습니다. "
                     "실적배당형 상품은 운용실적에 따라 원금손실이 발생할 수 있습니다.",
                     26, "Regular", MUTE, 900, spacing=1.5, align="center"), 0, 1620, 2.4, dy=10, center=True)
    scenes.append(s)

    # 원고가 너무 길면 헤드라인 본문 문장부터 덜어낸다
    def est(sc):
        return sum(max(x.base_dur, LEAD + len(speakable(x.narration)) / CHARS_PER_SEC + TAIL) for x in sc)
    # 줄이는 순서: 투자마인드 → 회고 → 헤드라인 3 → 2 → 1 (뒤에 나오는 보조 문장부터)
    order = [x for x in reversed(scenes) if getattr(x, "narr_short", None)]
    order.sort(key=lambda x: 0 if x in scenes[1:1 + len(heads)] else -1)
    for sc in order:
        if est(scenes) <= TARGET_SEC:
            break
        sc.narration = sc.narr_short
    return scenes


# ═══════════════════════════════ 음성 (Google Cloud TTS)

def tts(text, key, voice, out_path):
    import requests
    body = {"input": {"text": text},
            "voice": {"languageCode": "ko-KR", "name": voice},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": TTS_RATE, "sampleRateHertz": 44100}}
    last = ""
    for attempt in range(3):
        try:
            r = requests.post(TTS_URL, params={"key": key}, json=body, timeout=60)
            if r.status_code == 200:
                with open(out_path, "wb") as fp:
                    fp.write(base64.b64decode(r.json()["audioContent"]))
                return
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code < 500 and r.status_code != 429:
                break
        except Exception as exc:                                  # noqa: BLE001
            last = str(exc)
        time.sleep(2 ** attempt)
    raise RuntimeError(last)


def synth_all(scenes, key, workdir):
    """모든 장면 음성 생성. 목소리 하나가 실패하면 다음 목소리로 처음부터 다시."""
    last = ""
    for voice in TTS_VOICES:
        try:
            paths = []
            for i, sc in enumerate(scenes):
                p = os.path.join(workdir, f"voice_{i:02d}.mp3")
                tts(speakable(sc.narration), key, voice, p)
                paths.append(p)
            return voice, paths
        except Exception as exc:                                  # noqa: BLE001
            last = f"{voice}: {exc}"
            print(f"  ! TTS 실패 → 다음 목소리 시도 ({last})")
    raise RuntimeError(last)


def media_dur(p):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "csv=p=0", p]))


# ═══════════════════════════════ 배경음악 (numpy 합성)

def make_music(durs, path, sr=44100):
    import numpy as np
    total = float(sum(durs))
    starts = np.cumsum([0] + list(durs[:-1]))
    N = int(sr * total)
    L = np.zeros(N); R = np.zeros(N)
    rng = np.random.default_rng(7)
    beat = 60 / 112; bar_ = beat * 4

    def hz(m): return 440 * 2 ** ((m - 69) / 12)

    def add(sig, t, pan=0.0, g=1.0):
        i = int(t * sr)
        if i >= N or i < 0:
            return
        j = min(N, i + len(sig))
        s = sig[:j - i] * g
        L[i:j] += s * (1 - pan) ** 0.5 * 0.9
        R[i:j] += s * (1 + pan) ** 0.5 * 0.9

    chords = [[57, 60, 64, 69], [53, 57, 60, 65], [48, 55, 60, 64], [55, 59, 62, 67]]
    roots = [45, 41, 48, 43]

    def ar(d): return np.arange(int(d * sr)) / sr

    def pad(notes, d):
        t = ar(d)
        s = sum(np.sin(2 * np.pi * hz(n) * t * (1 + e)) for n in notes for e in (-0.002, 0.002)) / 8
        s += 0.3 * sum(np.sin(2 * np.pi * hz(n) * 2 * t) for n in notes) / 4
        return s * np.minimum(1, t / 0.4) * np.minimum(1, (d - t) / 0.4)

    def pluck(n, d=0.45):
        t = ar(d); f = hz(n)
        s = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) + 0.12 * np.sin(6 * np.pi * f * t)
        return s * np.exp(-t * 9) * np.minimum(1, t / 0.004)

    def bass(n, d):
        t = ar(d)
        return (np.sin(2 * np.pi * hz(n) * t) + 0.2 * np.sin(4 * np.pi * hz(n) * t)) * np.exp(-t * 2.5) \
            * np.minimum(1, t / 0.01)

    def kick():
        t = ar(0.35)
        return np.sin(2 * np.pi * np.cumsum(50 + 110 * np.exp(-t * 30)) / sr) * np.exp(-t * 9)

    def noise(d, decay, hp=True):
        n = rng.standard_normal(int(d * sr))
        if hp:
            n = np.diff(n, prepend=0)
        return n * np.exp(-ar(d)[:len(n)] * decay)

    def clap():
        n = noise(0.25, 18, hp=False)
        return (n - np.convolve(n, np.ones(8) / 8, "same")) * 0.8

    def whoosh(d=0.55):
        n = rng.standard_normal(int(d * sr)); t = ar(d)[:len(n)]
        a = np.clip(0.02 + 0.25 * np.sin(np.pi * t / d), 0, 1)
        out = np.zeros_like(n); y = 0.0
        for i in range(len(n)):
            y += a[i] * (n[i] - y); out[i] = y
        out *= np.sin(np.pi * t / d) ** 2
        return out / (np.abs(out).max() or 1)

    intro = durs[0] - 0.2
    for b in range(int(total / bar_) + 1):
        t0 = b * bar_; ci = b % 4
        add(pad(chords[ci], bar_ + 0.3), t0, 0, 0.16)
        seq = chords[ci] + [chords[ci][2] + 12, chords[ci][1] + 12, chords[ci][3], chords[ci][1]]
        for k in range(8):
            add(pluck(seq[k] + 12), t0 + k * beat / 2, pan=(-0.4 if k % 2 else 0.4), g=0.11)
        if t0 >= intro:
            for k in range(4):
                add(bass(roots[ci], beat * 0.95), t0 + k * beat, 0, 0.32)
                if k in (0, 2):
                    add(kick(), t0 + k * beat, 0, 0.55)
                else:
                    add(clap(), t0 + k * beat, 0, 0.22)
            for k in range(8):
                add(noise(0.06, 70), t0 + k * beat / 2, 0.2, 0.07 if k % 2 else 0.04)
            if b % 2 == 1:
                add(kick(), t0 + 3.5 * beat, 0, 0.35)
    for st in starts[1:]:
        add(whoosh(), st - 0.3, 0, 0.18)
    add(pad([57, 60, 64, 69, 72], 3.0), total - 3.2, 0, 0.12)
    mix = np.stack([L, R], 1)
    t = np.arange(N) / sr
    mix *= (np.minimum(1, t / 1.0) * np.minimum(1, (total - t) / 2.5))[:, None]
    mix = np.tanh(mix * 1.4) / np.tanh(1.4)
    mix *= 0.89 / (np.abs(mix).max() or 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((mix * 32767).astype(np.int16).tobytes())


# ═══════════════════════════════ 렌더링

def render_scene(sc, path, g0, total):
    n = int(round(sc.dur * FPS))
    tmp = path[:-4] + ".tmp.mp4"
    p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "23",
                          "-preset", "veryfast", "-pix_fmt", "yuv420p", tmp], stdin=subprocess.PIPE)
    for k in range(n):
        p.stdin.write(sc.frame(k / FPS, g0, total).convert("RGB").tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError(f"ffmpeg 장면 인코딩 실패: {path}")
    os.replace(tmp, path)


def _render_range(args):
    data, brief, idxs, durs, workdir = args
    scenes = build_scenes(data, brief)
    total = sum(durs)
    for i in idxs:
        scenes[i].dur = durs[i]
        render_scene(scenes[i], os.path.join(workdir, f"s{i:02d}.mp4"), sum(durs[:i]), total)


def make_video(data, brief, out_dir, slug, tts_key=None, jobs=None):
    os.makedirs(out_dir, exist_ok=True)
    work = tempfile.mkdtemp(prefix="wvid_")
    report = {"ok": False, "slug": slug, "voice": None, "tts_error": "", "error": ""}
    try:
        scenes = build_scenes(data, brief)
        voice_paths = None
        if tts_key:
            try:
                report["voice"], voice_paths = synth_all(scenes, tts_key, work)
            except Exception as exc:                              # noqa: BLE001
                report["tts_error"] = str(exc)[:300]
                print(f"  ! 음성 생성 실패 — 배경음악만 넣습니다: {report['tts_error']}")
        else:
            report["tts_error"] = "GOOGLE_TTS_API_KEY 없음"
            print("  · GOOGLE_TTS_API_KEY 가 없어 배경음악 버전으로 만듭니다.")

        vd = [media_dur(p) for p in voice_paths] if voice_paths else [0] * len(scenes)
        durs = [round(max(sc.base_dur, (LEAD + v + TAIL) if v else 0) * FPS) / FPS
                for sc, v in zip(scenes, vd)]
        total = sum(durs)
        report["duration"] = round(total, 1)
        report["narration"] = [speakable(sc.narration) for sc in scenes]
        print(f"  · 장면 {len(scenes)}개 · 총 {total:.1f}초 · 목소리 {report['voice'] or '없음'}")

        # 장면 렌더 (CPU 수만큼 나눠서)
        from concurrent.futures import ProcessPoolExecutor
        jobs = jobs or max(1, min(4, os.cpu_count() or 1))
        groups = [list(range(k, len(scenes), jobs)) for k in range(jobs)]
        with ProcessPoolExecutor(jobs) as ex:
            list(ex.map(_render_range, [(data, brief, g, durs, work) for g in groups if g]))

        with open(os.path.join(work, "list.txt"), "w") as fp:
            fp.write("".join(f"file 's{i:02d}.mp4'\n" for i in range(len(scenes))))
        vpath = os.path.join(work, "v.mp4")
        subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                               "-i", os.path.join(work, "list.txt"), "-c", "copy", vpath])
        mpath = os.path.join(work, "music.wav")
        make_music(durs, mpath)

        final = os.path.join(out_dir, f"{slug}.mp4")
        tmp_final = final[:-4] + ".tmp.mp4"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", vpath, "-i", mpath]
        if voice_paths:
            filt, t = [], 0.0
            for i, p in enumerate(voice_paths):
                cmd += ["-i", p]
                ms = int((t + LEAD) * 1000)
                filt.append(f"[{i + 2}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={ms}|{ms}[v{i}]")
                t += durs[i]
            n = len(voice_paths)
            filt.append("".join(f"[v{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0,volume=1.6,"
                        f"apad=whole_dur={total:.3f},asplit[vo][sc]")
            filt.append("[1:a]volume=-6dB[bgm]")
            filt.append("[bgm][sc]sidechaincompress=threshold=0.02:ratio=6:attack=20:release=400[duck]")
            filt.append("[duck][vo]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95[a]")
            cmd += ["-filter_complex", ";".join(filt), "-map", "0:v", "-map", "[a]"]
        else:
            cmd += ["-map", "0:v", "-map", "1:a", "-af", "volume=-4dB"]
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", tmp_final]
        subprocess.check_call(cmd)
        os.replace(tmp_final, final)

        # 포스터(재생 전 표시 이미지): 첫 장면 2초 지점
        poster = os.path.join(out_dir, f"{slug}.jpg")
        subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-ss", "2.2", "-i", final,
                               "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "4", poster])
        report.update(ok=True, file=os.path.basename(final), poster=os.path.basename(poster),
                      size_mb=round(os.path.getsize(final) / 1e6, 2))
        print(f"  · 영상 → {final} ({report['size_mb']}MB)")
    except Exception as exc:                                      # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"[:400]
        print(f"  ! 영상 생성 실패: {report['error']}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return report


def add_play_badge(png_path, label="1분 영상 브리핑"):
    """카톡 미리보기(OG) 썸네일 오른쪽 위 빈 공간에 '▶ 1분 영상' 배지를 얹는다."""
    im = Image.open(png_path).convert("RGBA")
    f = F(34, "Black")
    tw = int(f.getlength(label))
    bw, bh = 96 + tw + 34, 84
    x, y = im.width - 50 - bw, 96
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([x, y + 6, x + bw, y + bh + 6], bh // 2, fill=(40, 40, 90, 70))
    layer = Image.alpha_composite(layer, sh.filter(ImageFilter.GaussianBlur(8)))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([x, y, x + bw, y + bh], bh // 2, fill=WHITE)
    cx, cy, r = x + 44, y + bh // 2, 30
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=PURPLE)
    d.polygon([(cx - 9, cy - 15), (cx - 9, cy + 15), (cx + 16, cy)], fill=WHITE)
    d.text((x + 88, cy), label, font=f, fill=PURPLE, anchor="lm")
    Image.alpha_composite(im, layer).convert("RGB").save(png_path)


def prune(video_dir, keep=8):
    """최근 keep주치 영상만 남긴다 (파일명 날짜순)."""
    if not os.path.isdir(video_dir):
        return []
    slugs = sorted({f.rsplit(".", 1)[0] for f in os.listdir(video_dir)
                    if re.match(r"weekly-\d{4}-\d{2}-\d{2}\.(mp4|jpg)$", f)})
    removed = []
    for s in slugs[:-keep] if len(slugs) > keep else []:
        for ext in ("mp4", "jpg"):
            p = os.path.join(video_dir, f"{s}.{ext}")
            if os.path.exists(p):
                os.remove(p); removed.append(os.path.basename(p))
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data_weekly.json")
    ap.add_argument("--brief", default="brief_weekly.json")
    ap.add_argument("--out", default="out")
    ap.add_argument("--still", type=float, nargs=2, metavar=("SCENE", "SEC"),
                    help="미리보기: 장면 번호와 시각의 한 장면만 JPG로 저장")
    args = ap.parse_args()
    with open(args.data, encoding="utf-8") as fp:
        data = json.load(fp)
    with open(args.brief, encoding="utf-8") as fp:
        brief = json.load(fp)
    slug = f"weekly-{data['this_week']['mon']}"
    if args.still:
        sc = build_scenes(data, brief)
        i, t = int(args.still[0]), args.still[1]
        total = sum(x.dur for x in sc)
        os.makedirs(args.out, exist_ok=True)
        p = os.path.join(args.out, f"still_{i:02d}.jpg")
        sc[i].frame(t, sum(x.dur for x in sc[:i]), total).convert("RGB").save(p, quality=88)
        print(p, "| 원고:", speakable(sc[i].narration))
        return
    print("[영상] 주간 1분 브리핑 생성")
    rep = make_video(data, brief, os.path.join(args.out, "video"), slug,
                     tts_key=os.getenv("GOOGLE_TTS_API_KEY"))
    with open(os.path.join(args.out, "video_report.json"), "w", encoding="utf-8") as fp:
        json.dump(rep, fp, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
