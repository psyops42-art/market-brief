# -*- coding: utf-8 -*-
"""
주간 마켓 브리핑 · 3단계 · 렌더링  →  대시보드 HTML + OG 썸네일

    python render_weekly.py --data data_weekly.json --brief brief_weekly.json \
        --base https://user.github.io/repo --out out

산출물
    out/YYYY-MM-DD.html      주간 대시보드 (지난주 월요일 날짜로 파일명 표기)
    out/og-YYYY-MM-DD.png    1200x630 카톡 썸네일
    out/report_weekly.json   검증 리포트 (daily의 report.json과 같은 역할)

최신성 검증 — 이 렌더러가 최종 판정을 내립니다
    · 시장지표: data_weekly.json의 'stale'/'delayed' 를 그대로 화면에 반영합니다.
    · 주간 리뷰/인사이트: brief_weekly.json의 '_issues'(날짜범위 위반 등)를 그대로
      반영하고, 있으면 각주에 안내를 추가합니다.
    → daily와 동일하게, "화면에 실제로 표시된 내용"이 검증의 유일한 기준입니다.
      집계 단계에서 놓친 문제가 있어도 화면 자체에서 다시 걸러냅니다.
"""

import argparse
import datetime as dt
import html
import json
import os
import re

from pipeline_utils import atomic_write_json, atomic_write_text, safe_rich_text, safe_text, strip_markup

KST = dt.timezone(dt.timedelta(hours=9))
WD_KR = ["월", "화", "수", "목", "금", "토", "일"]


# ─────────────────────────────── 표시 헬퍼

def fmt_date(iso):
    try:
        d = dt.date.fromisoformat(str(iso))
        return f"{d.month}/{d.day}"
    except (TypeError, ValueError):
        return "-"


def cls(v):
    if v is None:
        return "fl"
    return "up" if v > 0 else ("dn" if v < 0 else "fl")


def arrow(v):
    if v is None:
        return "－"
    return "▲" if v > 0 else ("▼" if v < 0 else "－")


def num(v, nd=2):
    return "-" if v is None else f"{v:,.{nd}f}"


def pct_txt(v, nd=2):
    return "-" if v is None else f"{v:+.{nd}f}%"


def bp_txt(v):
    return "-" if v is None else f"{v:+.1f}bp"


# ─────────────────────────────── 표 행 렌더링

BADGE_LABEL = {"kr": "국내", "us": "미국", "cn": "중국", "eu": "유럽",
               "cr": "가상자산", "fx": "환율", "cm": "원자재"}


def row_html(key, r, stale_set, delayed_set, value_fmt=lambda v: num(v)):
    badge = r.get("badge", "")
    label = BADGE_LABEL.get(badge, "")
    badge_html = f'<span class="bd {badge}">{label}</span>' if label else ""
    warn = ""
    if key in stale_set:
        warn = '<div class="sub" style="color:#c0392b">⚠ 확인 필요 — 최신 데이터 아님</div>'
    elif key in delayed_set:
        warn = '<div class="sub">해외지수 특성상 1일 지연 · 정상 범위</div>'
    unit = r.get("unit")
    value = r.get("value")
    val = ("-" if value is None else f'{value:.3f}%') if unit == "bp" else value_fmt(value)
    wow = bp_txt(r.get("wow_pct")) if unit == "bp" else pct_txt(r.get("wow_pct"))
    ytd = bp_txt(r.get("ytd_pct")) if unit == "bp" else pct_txt(r.get("ytd_pct"))
    return (
        '        <div class="row">\n'
        f'          <div class="c1"><div class="nm">{badge_html}{safe_text(r.get("label", key))}</div>\n'
        f'            <div class="trend {r.get("trend_color", "fl")}">{safe_text(r.get("trend", "－"))}</div>{warn}</div>\n'
        f'          <div class="c2">{val}</div>'
        f'<div class="c3 {cls(r.get("wow_pct"))}">{wow}</div>'
        f'<div class="c4 {cls(r.get("ytd_pct"))}">{ytd}</div>\n'
        '        </div>'
    )


KEY_LABEL = {"sp500": "S&P 500", "ndx": "나스닥 100", "kospi": "코스피",
             "shcomp": "상해종합", "sx5e": "유로스톡스 50",
             "ktb3y": "국고채 3년", "ktb10y": "국고채 10년",
             "ust10y": "미국채 10년", "ust30y": "미국채 30년",
             "usdkrw": "달러/원", "gold": "국제금", "wti": "유가 WTI", "btc": "비트코인"}


def build_table(series, keys, stale_set, delayed_set):
    rows = [row_html(k, series[k], stale_set, delayed_set) for k in keys if k in series]
    missing = [k for k in keys if k not in series]
    for k in missing:
        rows.append(
            '        <div class="row">\n'
            f'          <div class="c1"><div class="nm">{KEY_LABEL.get(k, k)}</div>'
            '<div class="sub" style="color:#c0392b">데이터 미확보 — 수동 확인 필요</div></div>\n'
            '          <div class="c2 fl">확인필요</div><div class="c3 fl">－</div><div class="c4 fl">－</div>\n'
            '        </div>')
    return "\n".join(rows)


# ─────────────────────────────── 블록 생성

def build_news(brief):
    icons = "①②③"
    out = []
    for i, n in enumerate(brief.get("last_week_headlines", [])[:3], 1):
        out.append('      <div class="news">\n'
                   f'        <div class="h"><em>{icons[i-1]}</em>{html.escape(n.get("title",""))}</div>\n'
                   f'        <div class="d">{safe_rich_text(n.get("body"))}</div>\n'
                   f'        <div class="s">{html.escape(n.get("source",""))}</div>\n'
                   '      </div>')
    return "\n".join(out)


def build_mvp(series):
    # 금리(bp)는 '수익률'이 아니라서 등락률(%) 자산과 같은 잣대로 비교할 수 없다.
    # (금리 상승 = 채권가격 하락이라 방향 해석도 반대) 가격형 자산만 비교 대상으로 삼는다.
    priced = [r for r in series.values() if r.get("wow_pct") is not None and r.get("unit") == "price"]
    if not priced:
        return ('      <div class="mvp"><div class="c best"><div class="lb">이번 주 최고</div>'
                '<div class="nm2">확인필요</div><div class="vv">－</div></div>'
                '<div class="c worst"><div class="lb">이번 주 최저</div>'
                '<div class="nm2">확인필요</div><div class="vv">－</div></div></div>')
    best = max(priced, key=lambda r: r["wow_pct"])
    worst = min(priced, key=lambda r: r["wow_pct"])
    return ('      <div class="mvp">\n'
            '        <div class="c best"><div class="lb">이번 주 최고</div>'
            f'<div class="nm2">{html.escape(best["label"])}</div>'
            f'<div class="vv">{pct_txt(best["wow_pct"]) if best["unit"]=="price" else bp_txt(best["wow_pct"])}</div></div>\n'
            '        <div class="c worst"><div class="lb">이번 주 최저</div>'
            f'<div class="nm2">{html.escape(worst["label"])}</div>'
            f'<div class="vv">{pct_txt(worst["wow_pct"]) if worst["unit"]=="price" else bp_txt(worst["wow_pct"])}</div></div>\n'
            '      </div>')


def build_calendar(brief):
    rows = []
    for c in brief.get("checkpoints", []):
        wd_class = "wd today" if c.get("highlight") else "wd"
        text = safe_text(c.get("text", ""))
        event_date = safe_text(c.get("date", ""))
        if c.get("highlight"):
            text = f'<b>{event_date}</b> {text}'
        else:
            text = f'{event_date} {text}'
        rows.append(f'        <div class="d"><div class="{wd_class}">{html.escape(c.get("day",""))}</div>'
                   f'<div class="ev">{text}</div></div>')
    if not rows:
        rows.append('        <div class="d"><div class="wd">-</div><div class="ev">확인필요 — 일정 미생성</div></div>')
    return '      <div class="cal">\n' + "\n".join(rows) + '\n      </div>'


# ─────────────────────────────── 메인

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data_weekly.json")
    ap.add_argument("--brief", default="brief_weekly.json")
    ap.add_argument("--template", default="template_weekly.html")
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", default="out")
    args = ap.parse_args()
    args.base = args.base.rstrip("/")

    with open(args.data, encoding="utf-8") as fp:
        data = json.load(fp)
    with open(args.brief, encoding="utf-8") as fp:
        brief = json.load(fp)
    S = data["series"]
    os.makedirs(args.out, exist_ok=True)

    lw, tw = data["last_week"], data["this_week"]
    lm, lf = dt.date.fromisoformat(lw["mon"]), dt.date.fromisoformat(lw["fri"])
    tm, tf = dt.date.fromisoformat(tw["mon"]), dt.date.fromisoformat(tw["fri"])
    # 주간은 daily와 같은 날(월요일)에 발행되므로 파일명이 충돌한다.
    # 접두어로 분리해 docs/ 안에서 서로 덮어쓰지 않게 한다.
    slug = f"weekly-{tm.isoformat()}"

    date_line = (f'{lm.month}월 {lm.day}일~{lf.day}일 정리 · '
                 f'{tm.month}월 {tm.day}일({WD_KR[tm.weekday()]}) 아침')
    prev_fri = lm - dt.timedelta(days=3)
    asof_equity = f'{prev_fri.month}/{prev_fri.day} 종가 → {lf.month}/{lf.day} 종가 기준 · YTD는 연초 대비'
    asof_rates = f'{prev_fri.month}/{prev_fri.day} 대비 · bp(basis point)'
    asof_fx = f'{prev_fri.month}/{prev_fri.day} 대비'
    title = f'주간 마켓 브리핑 | {lm.month}월 {lm.day}일~{lf.day}일 정리'

    stale_keys = [x["key"] for x in data.get("stale", []) if x.get("key")]
    delayed_keys = [x["key"] for x in data.get("delayed", []) if x.get("key")]
    stale_set = set(stale_keys)
    delayed_set = set(delayed_keys)

    equity = build_table(S, ["sp500", "ndx", "kospi", "shcomp", "sx5e"], stale_set, delayed_set)
    rates = build_table(S, ["ktb3y", "ktb10y", "ust10y", "ust30y"], stale_set, delayed_set)
    fx = build_table(S, ["usdkrw", "gold", "wti", "btc"], stale_set, delayed_set)

    news = build_news(brief)
    mvp = build_mvp(S)
    calendar = build_calendar(brief)

    retro = brief.get("retrospective", {"title": "확인필요", "body": "회고 데이터가 생성되지 않았습니다."})
    retro_card = ('      <div class="mind">\n        <div class="n">RETROSPECTIVE</div>\n'
                 f'        <div class="t">{html.escape(retro.get("title",""))}</div>\n'
                 f'        <div class="b">{safe_rich_text(retro.get("body"))}</div>\n      </div>')

    m01 = brief.get("mindset_01", {"title": "확인필요", "body": "생성되지 않았습니다."})
    mindset01 = ('      <div class="mind">\n        <div class="n">MINDSET 01</div>\n'
                f'        <div class="t">{html.escape(m01.get("title",""))}</div>\n'
                f'        <div class="b">{safe_rich_text(m01.get("body"))}</div>\n      </div>')

    edu = brief.get("education", {"title": "", "body": ""})
    rebal = brief.get("rebalance_note", {"title": "", "body": ""})

    quotes = brief.get("quotes", [])
    quotes_html = "\n".join(f'        <p>{safe_rich_text(q)}</p>' for q in quotes[:3]) or '        <p>확인필요</p>'

    miss = data.get("missing") or []
    footnote_lines = [
        '        ※ 주간 등락은 그 전주 금요일 종가 대비 이번 주 금요일(또는 직전 거래일) 종가 기준이며, '
        '국채는 그 전주 대비 금리 변동폭(bp)입니다.<br>',
        '        ※ 최근 4주 흐름은 매주 금요일 종가를 비교한 방향이며(왼쪽이 4주 전), '
        '상승 우세면 적색, 하락 우세면 청색으로 표시합니다.<br>',
        '        ※ YTD(연초 대비)는 올해 첫 거래일 종가 대비 등락률입니다.<br>',
    ]
    if delayed_set:
        names = ", ".join(S[k]["label"] for k in delayed_keys if k in S)
        footnote_lines.append(f'        ※ {safe_text(names)}은 휴장 또는 데이터 제공사의 반영 시차로 '
                              '직전 거래일 값이 사용됐습니다.<br>')
    if stale_set:
        names = ", ".join(S[k]["label"] for k in stale_keys if k in S)
        footnote_lines.append(f'        ※ {safe_text(names)}은 정상 지연 범위를 넘어선 값입니다. 발송 전 확인이 필요합니다.<br>')
    if miss:
        names = ", ".join(KEY_LABEL.get(k, k) for k in miss)
        footnote_lines.append(f'        ※ 수집 실패 {len(miss)}건: {names} — "확인필요"로 표기했습니다.<br>')
    brief_issues = brief.get("_issues", [])
    if brief_issues:
        footnote_lines.append(f'        ※ 생성 내용 자체 점검에서 {len(brief_issues)}건이 발견됐습니다. '
                              '발송 전 헤드라인·일정 날짜를 확인해 주세요.<br>')
    footnote = "\n".join(footnote_lines)

    with open(args.template, encoding="utf-8") as fp:
        tpl = fp.read()
    out_html = (tpl
                .replace("{{TITLE}}", html.escape(title))
                .replace("{{OG_DESC}}", safe_text(brief.get("og_description", "")))
                .replace("{{OG_URL}}", safe_text(f"{args.base}/{slug}.html"))
                .replace("{{OG_IMAGE}}", safe_text(f"{args.base}/og-{slug}.png"))
                .replace("{{DATE_LINE}}", date_line)
                .replace("{{LAST_WEEK_NEWS}}", news)
                .replace("{{MVP_CARD}}", mvp)
                .replace("{{RETRO_CARD}}", retro_card)
                .replace("{{CALENDAR}}", calendar)
                .replace("{{MINDSET_01}}", mindset01)
                .replace("{{REBAL_TITLE}}", html.escape(rebal.get("title", "")))
                .replace("{{REBAL_BODY}}", safe_rich_text(rebal.get("body", "")))
                .replace("{{EDU_TITLE}}", html.escape(edu.get("title", "")))
                .replace("{{EDU_BODY}}", safe_rich_text(edu.get("body", "")))
                .replace("{{QUOTES}}", quotes_html)
                .replace("{{ASOF_EQUITY}}", asof_equity)
                .replace("{{ASOF_RATES}}", asof_rates)
                .replace("{{ASOF_FX}}", asof_fx)
                .replace("{{TBL_EQUITY}}", equity)
                .replace("{{TBL_RATES}}", rates)
                .replace("{{TBL_FX}}", fx)
                .replace("{{FOOTNOTE}}", footnote))
    if re.search(r"\{\{[A-Z0-9_]+\}\}", out_html):
        raise ValueError("치환되지 않은 템플릿 토큰이 남아 있습니다")

    path = os.path.join(args.out, f"{slug}.html")
    atomic_write_text(path, out_html)
    print(f"  · 대시보드 → {path}")

    # ── OG 썸네일 : 1·3페이지(리뷰+지표) 실제 화면을 캡처해 합성 ──
    import make_og_weekly
    png = os.path.join(args.out, f"og-{slug}.png")
    kpi = []
    for key, label in (("kospi", "코스피"), ("sp500", "S&P 500"), ("ktb3y", "국고채 3년"), ("gold", "국제금")):
        r = S.get(key)
        if r:
            value = r.get("value")
            v = ("-" if value is None else f'{value:.3f}%') if r.get("unit") == "bp" else num(value)
            c = bp_txt(r.get("wow_pct")) if r.get("unit") == "bp" else pct_txt(r.get("wow_pct"))
            kpi.append((label, v, c, cls(r.get("wow_pct"))))
        else:
            kpi.append((label, "확인필요", "－", "fl"))
    make_og_weekly.build(path, png, date_line, kpi,
                         strip_markup(brief.get("retrospective", {}).get("body", "")), tmpdir=args.out)
    print(f"  · OG 썸네일 → {png}")

    # ── 검증 리포트 ──
    unresolved = [KEY_LABEL.get(k, k) for k in
                  ["sp500", "ndx", "kospi", "shcomp", "sx5e",
                   "ktb3y", "ktb10y", "ust10y", "ust30y",
                   "usdkrw", "gold", "wti", "btc"] if k not in S]
    report = {
        "slug": slug, "last_week": lw, "this_week": tw,
        "unresolved": unresolved,
        "stale": [f'{S[k]["label"]}({fmt_date(S[k]["asof"])})' for k in stale_keys if k in S],
        "delayed": [f'{S[k]["label"]}({fmt_date(S[k]["asof"])})' for k in delayed_keys if k in S],
        "brief_issues": brief_issues,
        "collected": sorted(S.keys()),
    }
    atomic_write_json(os.path.join(args.out, "report_weekly.json"), report)

    ok = not unresolved and not stale_set and not brief_issues
    print(f"\n[검증 요약] {'정상' if ok else '확인 필요'}")
    if unresolved:
        print(f"  ! 데이터 미확보: {', '.join(unresolved)}")
    if stale_set:
        print(f"  ! 확인필요(지표): {', '.join(report['stale'])}")
    if delayed_set:
        print(f"  · 정상 지연(지표): {', '.join(report['delayed'])}")
    if brief_issues:
        print(f"  ! 확인필요(내용): {len(brief_issues)}건")
        for i in brief_issues:
            print(f"      - {i}")


if __name__ == "__main__":
    main()
