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
import re

import make_og
from pipeline_utils import atomic_write_json, atomic_write_text, safe_rich_text, safe_text, strip_markup

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


UNRESOLVED = []          # 화면에 '확인필요'로 렌더된 항목 (최종 판정 기준)


def row(rec, sub, name=""):
    """지표 한 줄. rec 가 없으면 '확인필요'로 표기 (임의 추정 금지)"""
    if not rec:
        UNRESOLVED.append(name or sub or "이름 미상")
        return ('        <div class="row">\n'
                f'          <div class="c1"><div class="nm">{name or "데이터 미확보"}</div>'
                '<div class="sub">자동 수집 실패 · 수동 확인 필요</div></div>\n'
                '          <div class="c2 fl">확인필요</div><div class="c3 fl">－</div>\n'
                '        </div>')
    badge = rec.get("badge") or ""
    bcls = f"bd {badge}" if badge else "bd"
    blab = {"kr": "국내", "us": "미국", "cn": "중국", "eu": "유럽",
            "cr": "가상자산", "fx": "환율", "cm": "원자재"}.get(badge, "")
    btag = f'<span class="{bcls}">{blab}</span>' if blab else ""
    if rec.get("unit") == "bp":
        value, change = rec.get("value"), rec.get("chg")
        val = "-" if value is None else f"{value:.3f}%"
        chg = "－" if change is None else f"{arrow(change)} {abs(change):.1f}bp"
        c = cls(change)
    else:
        value, change, pct = rec.get("value"), rec.get("chg"), rec.get("pct")
        val = num(value)
        c = cls(pct)
        absolute = "-" if change is None else f"{abs(change):,.2f}"
        percent = "-" if pct is None else f"{pct:+.2f}%"
        chg = f"{arrow(pct)} {absolute}<br>{percent}"
    return ('        <div class="row">\n'
            f'          <div class="c1"><div class="nm">{btag}{safe_text(rec.get("label", name))}</div>'
            f'<div class="sub">{html.escape(sub)}</div></div>\n'
            f'          <div class="c2">{val}</div><div class="c3 {c}">{chg}</div>\n'
            '        </div>')


def fmt_date(iso):
    try:
        d = dt.date.fromisoformat(str(iso))
        return f'{d.month}/{d.day}'
    except (TypeError, ValueError):
        return "-"


# ─────────────────────────────── 블록 생성

def build_news(brief):
    out = []
    headlines = brief.get("headlines") if isinstance(brief.get("headlines"), list) else []
    for i, n in enumerate((x for x in headlines[:3] if isinstance(x, dict)), 1):
        out.append('      <div class="news">\n'
                   f'        <div class="h"><em>{"①②③"[i-1]}</em>{safe_text(n.get("title"))}</div>\n'
                   f'        <div class="d">{safe_rich_text(n.get("body"))}</div>\n'
                   f'        <div class="s">{safe_text(n.get("source"))}</div>\n'
                   '      </div>')
    if not out:
        out.append('      <div class="news"><div class="h">확인필요</div>'
                   '<div class="d">헤드라인이 생성되지 않았습니다.</div></div>')
    c = brief.get("checkpoint") if isinstance(brief.get("checkpoint"), dict) else {}
    out.append('      <div class="news kr">\n'
               f'        <div class="h">{safe_text(c.get("title") or "확인필요")}</div>\n'
               f'        <div class="d">{safe_rich_text(c.get("body") or "국내 체크포인트가 생성되지 않았습니다.")}</div>\n'
               f'        <div class="s">{safe_text(c.get("source"))}</div>\n'
               '      </div>')
    return "\n".join(out)


def build_mindset(brief):
    out = []
    mindset = brief.get("mindset") if isinstance(brief.get("mindset"), list) else []
    for i, m in enumerate((x for x in mindset[:3] if isinstance(x, dict)), 1):
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
                   f'        <div class="t">{safe_text(m.get("title"))}</div>\n'
                   f'        <div class="b">{safe_rich_text(m.get("body"))}</div>{badge}\n'
                   '      </div>')
    return "\n".join(out) or ('      <div class="mind"><div class="n">MINDSET</div>'
                               '<div class="t">확인필요</div><div class="b">생성되지 않았습니다.</div></div>')


def build_quotes(brief):
    quotes = brief.get("quotes") if isinstance(brief.get("quotes"), list) else []
    return "\n".join(f'        <p>{safe_rich_text(q)}</p>' for q in quotes[:3]) or '        <p>확인필요</p>'


# ─────────────────────────────── 메인

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data.json")
    ap.add_argument("--brief", default="brief.json")
    ap.add_argument("--template", default="template.html")
    ap.add_argument("--base", default="https://psyops42-art.github.io/market-brief")
    ap.add_argument("--out", default="out")
    args = ap.parse_args()

    with open(args.data, encoding="utf-8") as fp:
        data = json.load(fp)
    with open(args.brief, encoding="utf-8") as fp:
        brief = json.load(fp)
    S = data["series"]
    os.makedirs(args.out, exist_ok=True)
    UNRESOLVED.clear()
    args.base = args.base.rstrip("/")

    try:
        today = dt.datetime.fromisoformat(data["generated_at"]).astimezone(KST).date()
    except (KeyError, TypeError, ValueError):
        today = dt.datetime.now(KST).date()
    slug = today.isoformat()
    date_line = (f'{today.year}년 {today.month}월 {today.day}일 ({WD[today.weekday()]}) 아침 · '
                 f'美 {fmt_date(S["sp500"]["asof"]) if "sp500" in S else "-"} 뉴욕 마감 · '
                 f'韓 {fmt_date(S["kospi"]["asof"]) if "kospi" in S else "-"} 마감 기준')
    title = f'글로벌 마켓 브리핑 | {today.year}년 {today.month}월 {today.day}일 ({WD[today.weekday()]}) 아침'

    cutoff = data.get("cutoff")
    STALE = {x["key"]: x for x in data.get("stale", [])}
    DELAYED = {x["key"]: x for x in data.get("delayed", [])}

    def sub(key, extra=""):
        r = S.get(key)
        if not r:
            return ""
        out = f'{fmt_date(r["asof"])} 종가'
        if key in STALE:
            out += f' · ⚠ 최신 아님(기준일 {fmt_date(cutoff)})'
        elif key in DELAYED:
            out += ' · 해외지수 특성상 1일 지연'
        if extra:
            out += f' · {extra}'
        return out

    kosdaq = S.get("kosdaq")
    dow = S.get("dow")
    dax = S.get("dax")
    equity = "\n".join([
        row(S.get("sp500"), sub("sp500", f'다우 {num(dow.get("value"))}({num(dow.get("pct"))}%)' if dow else ""), "S&P 500"),
        row(S.get("ndx"), sub("ndx", f'나스닥 종합 {num(S["nasdaq_comp"]["value"])}' if "nasdaq_comp" in S else ""), "나스닥 100"),
        row(S.get("kospi"), sub("kospi", f'코스닥 {num(kosdaq.get("value"))}({num(kosdaq.get("pct"))}%)' if kosdaq else ""), "코스피"),
        row(S.get("shcomp"), sub("shcomp"), "상해종합"),
        row(S.get("sx5e"), sub("sx5e", f'DAX {num(dax.get("value"))}({num(dax.get("pct"))}%)' if dax else ""), "유로스톡스 50"),
    ])
    RATE_NAMES = {"ktb3y": "국고채 3년", "ktb10y": "국고채 10년",
                  "ust10y": "미국채 10년", "ust30y": "미국채 30년"}
    rates = "\n".join([row(S.get(k), sub(k), RATE_NAMES[k]) for k in RATE_NAMES])
    brent = S.get("brent")
    fx = "\n".join([
        row(S.get("dxy"), sub("dxy"), "달러인덱스"),
        row(S.get("usdkrw"), sub("usdkrw"), "달러/원"),
        row(S.get("gold"), sub("gold"), "국제금"),
        row(S.get("wti"), sub("wti", f'브렌트 ${num(brent["value"])}' if brent else ""), "유가 WTI"),
        row(S.get("btc"), sub("btc", "24시간 거래"), "비트코인"),
    ])

    foot = ('        ※ 지수·환율·원자재와 미국채는 Yahoo Finance(미국채는 FRED 대체), '
            '국고채는 한국은행 ECOS 자료를 자동 수집했습니다. 항목별 기준일은 각 행에 표기했습니다.<br>\n')
    foot += ('        ※ 금·유가는 선물 기준이며 현물과 차이가 있을 수 있습니다. '
             '비트코인은 24시간 거래되어 주식시장 마감 시점과 기준이 다릅니다.<br>\n')

    if DELAYED:
        names = ", ".join(x["label"] for x in DELAYED.values())
        foot += (f'        ※ {names}은 데이터 제공사(Yahoo Finance)가 해외지수 종가를 '
                 '통상 1거래일 늦게 반영합니다. 오류가 아닌 정상적인 지연입니다.<br>\n')
    if STALE:
        names = ", ".join(x["label"] for x in STALE.values())
        foot += (f'        ※ 기준일({fmt_date(cutoff)})보다 오래된 값 {len(STALE)}건: {names}. '
                 '통상적인 지연 범위를 넘어섰으므로 발송 전 확인이 필요합니다.<br>\n')

    if UNRESOLVED:
        foot += ('        ※ 자동 수집에 실패해 "확인필요"로 표기된 항목 '
                 f'{len(UNRESOLVED)}건: {", ".join(UNRESOLVED)}. 발송 전 직접 확인하세요.<br>\n')

    with open(args.template, encoding="utf-8") as fp:
        tpl = fp.read()
    out_html = (tpl
                .replace("{{TITLE}}", html.escape(title))
                .replace("{{OG_DESC}}", safe_text(brief.get("og_description")))
                .replace("{{OG_URL}}", safe_text(f"{args.base}/{slug}.html"))
                .replace("{{OG_IMAGE}}", safe_text(f"{args.base}/og-{slug}.png"))
                .replace("{{DATE_LINE}}", date_line)
                .replace("{{NEWS}}", build_news(brief))
                .replace("{{MINDSET}}", build_mindset(brief))
                .replace("{{QUOTES}}", build_quotes(brief))
                .replace("{{TBL_EQUITY}}", equity)
                .replace("{{TBL_RATES}}", rates)
                .replace("{{TBL_FX}}", fx)
                .replace("{{ONELINE_MARKET}}", safe_rich_text(brief.get("oneline_market") or "확인필요"))
                .replace("{{ONELINE_PENSION}}", safe_rich_text(brief.get("oneline_pension") or "확인필요"))
                .replace("{{NEXT}}", safe_text(brief.get("next_events") or "확인필요"))
                .replace("{{FOOTNOTE}}", foot))
    if re.search(r"\{\{[A-Z0-9_]+\}\}", out_html):
        raise ValueError("치환되지 않은 템플릿 토큰이 남아 있습니다")
    path = os.path.join(args.out, f"{slug}.html")
    atomic_write_text(path, out_html)
    print(f"  · 대시보드 → {path}")

    # ── OG 썸네일 : 대시보드 실제 화면을 캡처해 합성 ──
    kpi_spec = [("코스피", "kospi"), ("S&P 500", "sp500"), ("국고채 3년", "ktb3y"), ("국제금", "gold")]
    kpis = []
    for label, key in kpi_spec:
        rec = S.get(key)
        if not rec:
            kpis.append((label, "확인필요", "－", "fl"))
        elif rec.get("unit") == "bp":
            value, change = rec.get("value"), rec.get("chg")
            shown_value = "-" if value is None else f"{value:.3f}%"
            shown_change = "－" if change is None else f"{arrow(change)} {abs(change):.1f}bp"
            kpis.append((label, shown_value, shown_change, cls(change)))
        else:
            value, pct = rec.get("value"), rec.get("pct")
            shown_pct = "－" if pct is None else f"{arrow(pct)} {abs(pct):.2f}%"
            kpis.append((label, num(value), shown_pct, cls(pct)))

    png = os.path.join(args.out, f"og-{slug}.png")
    make_og.build(path, png, date_line, kpis, strip_markup(brief.get("oneline_market")), tmpdir=args.out)
    print(f"  · OG 썸네일 → {png}")

    report = {"slug": slug, "unresolved": UNRESOLVED,
              "cutoff": cutoff,
              "stale": [f'{x["label"]}({fmt_date(x["asof"])})' for x in STALE.values()],
              "delayed": [f'{x["label"]}({fmt_date(x["asof"])})' for x in DELAYED.values()],
              "collected": sorted(S.keys()), "brief_issues": brief.get("_issues", [])}
    atomic_write_json(os.path.join(args.out, "report.json"), report)
    if UNRESOLVED:
        print(f"  ! '확인필요'로 표기된 항목 {len(UNRESOLVED)}건: {', '.join(UNRESOLVED)}")
    if DELAYED:
        print(f"  · 해외지수 정상 지연 {len(DELAYED)}건: "
              f"{', '.join(x['label'] for x in DELAYED.values())}")
    if STALE:
        print(f"  ! '확인필요'로 표기된 항목 {len(STALE)}건: "
              f"{', '.join(x['label'] for x in STALE.values())}")
    if not UNRESOLVED and not STALE:
        print(f"  · 전 지표가 정상 범위 내 최신 값으로 표기됨")


if __name__ == "__main__":
    main()
