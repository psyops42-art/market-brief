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
    dax = S.get("dax")
    equity = "\n".join([
        row(S.get("sp500"), sub("sp500", f'다우 {num(dow["value"])}({dow["pct"]:+.2f}%)' if dow else ""), "S&P 500"),
        row(S.get("ndx"), sub("ndx", f'나스닥 종합 {num(S["nasdaq_comp"]["value"])}' if "nasdaq_comp" in S else ""), "나스닥 100"),
        row(S.get("kospi"), sub("kospi", f'코스닥 {num(kosdaq["value"])}({kosdaq["pct"]:+.2f}%)' if kosdaq else ""), "코스피"),
        row(S.get("shcomp"), sub("shcomp"), "상해종합"),
        row(S.get("sx5e"), sub("sx5e", f'DAX {num(dax["value"])}({dax["pct"]:+.2f}%)' if dax else ""), "유로스톡스 50"),
    ])
    RATE_NAMES = {"ktb3y": "국고채 3년", "ktb10y": "국고채 10년",
                  "ust3y": "미국채 3년", "ust10y": "미국채 10년"}
    rates = "\n".join([row(S.get(k), sub(k), RATE_NAMES[k]) for k in RATE_NAMES])
    brent = S.get("brent")
    fx = "\n".join([
        row(S.get("dxy"), sub("dxy"), "달러인덱스"),
        row(S.get("usdkrw"), sub("usdkrw"), "달러/원"),
        row(S.get("gold"), sub("gold"), "국제금"),
        row(S.get("wti"), sub("wti", f'브렌트 ${num(brent["value"])}' if brent else ""), "유가 WTI"),
        row(S.get("btc"), sub("btc", "24시간 거래"), "비트코인"),
    ])

    foot = ('        ※ 지수·환율·원자재는 Yahoo Finance, 미국채는 FRED, 국고채는 한국은행 ECOS '
            '자료를 자동 수집한 값입니다. 항목별 기준일은 각 행에 표기했습니다.<br>\n')
    foot += ('        ※ 금·유가는 선물 기준이며 현물과 차이가 있을 수 있습니다. '
             '비트코인은 24시간 거래되어 주식시장 마감 시점과 기준이 다릅니다.<br>\n')

    if UNRESOLVED:
        foot += ('        ※ 자동 수집에 실패해 "확인필요"로 표기된 항목 '
                 f'{len(UNRESOLVED)}건: {", ".join(UNRESOLVED)}. 발송 전 직접 확인하세요.<br>\n')

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

    report = {"slug": slug, "unresolved": UNRESOLVED,
              "collected": sorted(S.keys()), "brief_issues": brief.get("_issues", [])}
    with open(os.path.join(args.out, "report.json"), "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    if UNRESOLVED:
        print(f"  ! 화면에 '확인필요'로 표기된 항목 {len(UNRESOLVED)}건: {', '.join(UNRESOLVED)}")
    else:
        print("  · 12개 지표 모두 정상 표기")


if __name__ == "__main__":
    main()
