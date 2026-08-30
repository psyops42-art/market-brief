# -*- coding: utf-8 -*-
"""
3단계 · 렌더링  →  대시보드 HTML + OG 썸네일 PNG

  python render.py --data data.json --brief brief.json --base https://psyops42-art.github.io/market-brief

산출물
    out/YYYY-MM-DD.html      모바일 대시보드
    out/og-YYYY-MM-DD.png    1200x630 카톡 썸네일

레이아웃은 template.html 을 그대로 쓰며, 토큰 자리만 채웁니다.
디자인을 바꾸고 싶으면 이 파일이 아니라 template.html 을 고치세요.
"""

import argparse
import datetime as dt
import html
import json
import os
import subprocess

import make_og

KST = dt.timezone(dt.timedelta(hours=9))
WD = ["월", "화", "수", "목", "금", "토", "일"]


# ─────────────────────────────── 표시 헬퍼

def cls(v):
    """국내 관행 : 상승 적색 / 하락 청색 / 보합 회색"""
    if v is None:
        return "fl"
    return "up" if v > 0 else ("dn" if v < 0 else "fl")


def arrow(v):
    if v is None:
        return "－"
    return "▲" if v > 0 else ("▼" if v < 0 else "－")


def num(v, nd=2):
    return "-" if v is None else f"{v:,.{nd}f}"


def row(rec, sub):
    """지표 한 줄. rec 가 없으면 '확인필요'로 표기 (임의 추정 금지)"""
    if not rec:
        return ('        <div class="row">\n'
                f'          <div class="c1"><div class="nm">{sub}</div>'
                '<div class="sub">데이터 미확보</div></div>\n'
                '          <div class="c2 fl">확인필요</div><div class="c3 fl">－</div>\n'
                '        </div>')
    badge = rec.get("badge") or ""
    bcls = f"bd {badge}" if badge else "bd"
    blab = {"kr": "국내", "us": "미국"}.get(badge, "")
    btag = f'<span class="{bcls}">{blab}</span>' if blab else ""
    if rec.get("unit") == "bp":
        val, chg = f'{rec["value"]:.3f}%', f'{arrow(rec["chg"])} {abs(rec["chg"]):.1f}bp'
        c = cls(rec["chg"])
    else:
        val = num(rec["value"])
        c = cls(rec["pct"])
        chg = f'{arrow(rec["pct"])} {abs(rec["chg"]):,.2f}<br>{rec["pct"]:+.2f}%'
    return ('        <div class="row">\n'
            f'          <div class="c1"><div class="nm">{btag}{html.escape(rec["label"])}</div>'
            f'<div class="sub">{html.escape(sub)}</div></div>\n'
            f'          <div class="c2">{val}</div><div class="c3 {c}">{chg}</div>\n'
            '        </div>')


def fmt_date(iso):
    d = dt.date.fromisoformat(iso)
    return f'{d.month}/{d.day}'


# ─────────────────────────────── 블록 생성

def build_news(brief):
    out = []
    for i, n in enumerate(brief["headlines"][:3], 1):
        out.append('      <div class="news">\n'
                   f'        <div class="h"><em>{"①②③"[i-1]}</em>{html.escape(n["title"])}</div>\n'
                   f'        <div class="d">{n["body"]}</div>\n'
                   f'        <div class="s">{html.escape(n["source"])}</div>\n'
                   '      </div>')
    c = brief["checkpoint"]
    out.append('      <div class="news kr">\n'
               f'        <div class="h">{html.escape(c["title"])}</div>\n'
               f'        <div class="d">{c["body"]}</div>\n'
               f'        <div class="s">{html.escape(c["source"])}</div>\n'
               '      </div>')
    return "\n".join(out)


def build_mindset(brief):
    out = []
    for i, m in enumerate(brief["mindset"][:3], 1):
        badge = ""
        if i == 2:      # Core-Satellite 배지는 항상 2번에 고정
            badge = ('\n        <div class="cs">\n'
                     '          <div class="box core"><div class="l">핵심자산 Core</div>'
                     '<div class="v">TDF 30~50%</div></div>\n'
                     '          <div class="plus">+</div>\n'
                     '          <div class="box sat"><div class="l">위성 Satellite</div>'
                     '<div class="v">ETF 30~70%</div></div>\n'
                     '        </div>')
        out.append('      <div class="mind">\n'
                   f'        <div class="n">MINDSET 0{i}</div>\n'
                   f'        <div class="t">{html.escape(m["title"])}</div>\n'
                   f'        <div class="b">{m["body"]}</div>{badge}\n'
                   '      </div>')
    return "\n".join(out)


def build_quotes(brief):
    return "\n".join(f'        <p>{q}</p>' for q in brief["quotes"][:3])


# ─────────────────────────────── 메인

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data.json")
    ap.add_argument("--brief", default="brief.json")
    ap.add_argument("--template", default="template.html")
    ap.add_argument("--base", default="https://psyops42-art.github.io/market-brief")
    ap.add_argument("--out", default="out")
    args = ap.parse_args()

    data = json.load(open(args.data, encoding="utf-8"))
    brief = json.load(open(args.brief, encoding="utf-8"))
    S = data["series"]
    os.makedirs(args.out, exist_ok=True)

    today = dt.datetime.now(KST).date()
    slug = today.isoformat()
    date_line = (f'{today.year}년 {today.month}월 {today.day}일 ({WD[today.weekday()]}) 아침 · '
                 f'美 {fmt_date(S["sp500"]["asof"]) if "sp500" in S else "-"} 뉴욕 마감 · '
                 f'韓 {fmt_date(S["kospi"]["asof"]) if "kospi" in S else "-"} 마감 기준')
    title = f'글로벌 마켓 브리핑 | {today.year}년 {today.month}월 {today.day}일 ({WD[today.weekday()]}) 아침'

    def sub(key, extra=""):
        r = S.get(key)
        return (f'{fmt_date(r["asof"])} 종가' + (f' · {extra}' if extra else "")) if r else ""

    kosdaq = S.get("kosdaq")
    dow = S.get("dow")
    equity = "\n".join([
        row(S.get("sp500"), sub("sp500", f'다우 {num(dow["value"])}({dow["pct"]:+.2f}%)' if dow else "")),
        row(S.get("ndx"), sub("ndx", f'나스닥 종합 {num(S["nasdaq_comp"]["value"])}' if "nasdaq_comp" in S else "")),
        row(S.get("kospi"), sub("kospi", f'코스닥 {num(kosdaq["value"])}({kosdaq["pct"]:+.2f}%)' if kosdaq else "")),
    ])
    rates = "\n".join([row(S.get(k), sub(k)) for k in ("ktb3y", "ktb10y", "ust3y", "ust10y")])
    brent = S.get("brent")
    fx = "\n".join([
        row(S.get("dxy"), sub("dxy")),
        row(S.get("usdkrw"), sub("usdkrw")),
        row(S.get("gold"), sub("gold")),
        row(S.get("wti"), sub("wti", f'브렌트 ${num(brent["value"])}' if brent else "")),
    ])

    miss = data.get("missing") or []
    foot = ('        ※ 지수·환율·원자재는 Yahoo Finance, 미국채는 FRED, 국고채는 한국은행 ECOS '
            '자료를 자동 수집한 값입니다. 항목별 기준일은 각 행에 표기했습니다.<br>\n')
    if miss:
        foot += f'        ※ 자동 수집에 실패한 항목({len(miss)}건)은 "확인필요"로 표기했습니다. 발송 전 확인하세요.<br>\n'
    foot += '        ※ 금·유가는 선물 기준이며 현물과 차이가 있을 수 있습니다.<br>\n'

    tpl = open(args.template, encoding="utf-8").read()
    out_html = (tpl
                .replace("{{TITLE}}", html.escape(title))
                .replace("{{OG_DESC}}", html.escape(brief["og_description"]))
                .replace("{{OG_URL}}", f"{args.base}/{slug}.html")
                .replace("{{OG_IMAGE}}", f"{args.base}/og-{slug}.png")
                .replace("{{DATE_LINE}}", date_line)
                .replace("{{NEWS}}", build_news(brief))
                .replace("{{MINDSET}}", build_mindset(brief))
                .replace("{{QUOTES}}", build_quotes(brief))
                .replace("{{TBL_EQUITY}}", equity)
                .replace("{{TBL_RATES}}", rates)
                .replace("{{TBL_FX}}", fx)
                .replace("{{ONELINE_MARKET}}", brief["oneline_market"])
                .replace("{{ONELINE_PENSION}}", brief["oneline_pension"])
                .replace("{{NEXT}}", html.escape(brief["next_events"]))
                .replace("{{FOOTNOTE}}", foot))
    path = os.path.join(args.out, f"{slug}.html")
    open(path, "w", encoding="utf-8").write(out_html)
    print(f"  · 대시보드 → {path}")

    # ── OG 썸네일 : 대시보드 실제 화면을 캡처해 합성 ──
    kpi_spec = [("코스피", "kospi"), ("S&P 500", "sp500"), ("국고채 3년", "ktb3y"), ("국제금", "gold")]
    kpis = []
    for label, key in kpi_spec:
        rec = S.get(key)
        if not rec:
            kpis.append((label, "확인필요", "－", "fl"))
        elif rec.get("unit") == "bp":
            kpis.append((label, f'{rec["value"]:.3f}%',
                         f'{arrow(rec["chg"])} {abs(rec["chg"]):.1f}bp', cls(rec["chg"])))
        else:
            kpis.append((label, num(rec["value"]),
                         f'{arrow(rec["pct"])} {abs(rec["pct"]):.2f}%', cls(rec["pct"])))

    png = os.path.join(args.out, f"og-{slug}.png")
    make_og.build(path, png, date_line, kpis, brief["oneline_market"], tmpdir=args.out)
    print(f"  · OG 썸네일 → {png}")
    if miss:
        print(f"  ! 미확보 {len(miss)}건: {', '.join(miss)}")


if __name__ == "__main__":
    main()
