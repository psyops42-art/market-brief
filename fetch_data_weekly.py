# -*- coding: utf-8 -*-
"""
주간 마켓 브리핑 · 1단계 · 데이터 수집  →  data_weekly.json

daily 파이프라인(fetch_data.py)의 함수를 그대로 재사용합니다.
    · Yahoo 일별 시계열                — 주간/YTD/4주 흐름 계산
    · fred() / ecos()                    — 미국채 3년/10년, 국고채 (일별 최신값)
    · LAG_TOLERANCE                      — 비미국 지수의 정상 지연 허용치
이 파일과 fetch_data.py를 같은 폴더에 두고 실행하세요.

무엇을 계산하는가
    · 주간 등락   : 직전 주 금요일 종가 → 이번 주(지난주) 금요일 종가
    · YTD         : 올해 첫 거래일 종가 → 이번 주(지난주) 금요일 종가
    · 4주 흐름    : 최근 5개 금요일 종가를 비교한 4개의 방향 화살표(왼쪽=4주 전)

최신성 검증 (반드시 확인)
    · '지난주 금요일' 종가가 실제로 그 날짜의 데이터인지 확인합니다.
    · 기대 날짜보다 오래됐으면: 국가별 정상 지연 허용치(LAG_TOLERANCE) 이내면
      'delayed'(정보성), 초과하면 'stale'(확인 필요)로 분류합니다.
    · 이 로직은 daily와 동일한 기준을 공유합니다 — 매일 검증하던 방식을
      '지난주 금요일'이라는 목표 날짜에 그대로 적용하는 것뿐입니다.

사용법
    python fetch_data_weekly.py                 # data_weekly.json 생성
    python fetch_data_weekly.py --ref 2026-08-31 # 기준일(보통 월요일) 지정 테스트용
"""

import argparse
import datetime as dt
import json
import os
import sys

from pipeline_utils import atomic_write_json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import fetch_data as D          # daily 모듈 재사용
except ImportError:
    sys.exit("fetch_data.py 가 같은 폴더에 있어야 합니다.")

try:
    import yfinance as yf
except ImportError:
    sys.exit("pip install yfinance requests")

KST = D.KST

# 주간 지표 구성 — daily의 TICKERS/RATE_TICKERS를 그대로 따르되 표시 그룹만 재배열
EQUITY = [
    ("sp500",  "^GSPC",     "S&P 500",       "us"),
    ("ndx",    "^NDX",      "나스닥 100",     "us"),
    ("kospi",  "^KS11",     "코스피",         "kr"),
    ("shcomp", "000001.SS", "상해종합",       "cn"),
    ("sx5e",   "^STOXX50E", "유로스톡스 50",  "eu"),
]
FX_CM = [
    ("usdkrw", "KRW=X",  "달러/원",           "fx"),
    ("gold",   "GC=F",   "국제금",            "cm"),
    ("wti",    "CL=F",   "유가 WTI",          "cm"),
    ("btc",    "BTC-USD","비트코인",          "cr"),
]
YAHOO_RATES = [
    ("ust10y", "^TNX", "미국채 10년", "us"),
    ("ust30y", "^TYX", "미국채 30년", "us"),
]
ECOS_RATES = [
    ("ktb3y",  "010200000", "국고채 3년",  "kr"),
    ("ktb10y", "010210000", "국고채 10년", "kr"),
]


# ─────────────────────────────── 날짜 계산

def week_bounds(any_day: dt.date):
    """any_day가 속한 주의 (월요일, 금요일)"""
    monday = any_day - dt.timedelta(days=any_day.weekday())
    return monday, monday + dt.timedelta(days=4)


def target_weeks(ref: dt.date):
    """ref(보통 월요일 아침) 기준 → (지난주 월,금), (이번주 월,금)"""
    this_mon, this_fri = week_bounds(ref)
    last_mon, last_fri = week_bounds(ref - dt.timedelta(days=7))
    return (last_mon, last_fri), (this_mon, this_fri)


# ─────────────────────────────── 시계열 헬퍼

def _hist(symbol: str, last_fri: dt.date, ytd_start: dt.date):
    """필요한 기간만 명시적으로 조회한다.

    yfinance는 ``420d`` 같은 임의 period를 버전에 따라 거부한다. 또한 --ref로
    과거 주를 테스트할 때 현재 기준 period를 쓰면 대상 주가 빠질 수 있다.
    """
    start = ytd_start - dt.timedelta(days=40)
    end = last_fri + dt.timedelta(days=1)  # yfinance의 end는 exclusive
    try:
        h = yf.Ticker(symbol).history(start=start.isoformat(), end=end.isoformat(), interval="1d")
        return h.dropna(subset=["Close"])
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {symbol} 시계열 조회 실패: {exc}")
        return None


def _on_or_before(hist, target: dt.date):
    d = hist[hist.index.date <= target]
    if d.empty:
        return None, None
    row = d.iloc[-1]
    return row.name.date(), float(row["Close"])


def _on_or_after(hist, target: dt.date):
    d = hist[hist.index.date >= target]
    if d.empty:
        return None, None
    row = d.iloc[0]
    return row.name.date(), float(row["Close"])


def trend_arrows(hist, last_fri: dt.date, weeks: int = 4):
    """최근 (weeks+1)개 금요일 종가 → weeks개의 방향 화살표. 왼쪽이 가장 오래된 주."""
    points = []
    for i in range(weeks, -1, -1):
        target = last_fri - dt.timedelta(weeks=i)
        _, price = _on_or_before(hist, target)
        points.append(price)
    arrows = []
    for a, b in zip(points, points[1:]):
        if a is None or b is None:
            arrows.append("－")
        elif b > a:
            arrows.append("▲")
        elif b < a:
            arrows.append("▼")
        else:
            arrows.append("－")
    up, dn = arrows.count("▲"), arrows.count("▼")
    color = "up" if up > dn else ("dn" if dn > up else "fl")
    return "".join(arrows), color


def classify_freshness(asof: dt.date, expected: dt.date, key: str):
    """daily와 동일한 기준(LAG_TOLERANCE)으로 'ok' / 'delayed' / 'stale' 판정"""
    gap = (expected - asof).days
    if gap <= 0:
        return "ok", gap
    # 금요일 휴장 시 목요일 종가가 정상적인 직전 거래일이므로 최소 1일은 허용한다.
    tol = max(1, D.LAG_TOLERANCE.get(key, 0))
    return ("delayed" if gap <= tol else "stale"), gap


# ─────────────────────────────── 지표 1건 수집

def collect_price(key, symbol, label, badge, last_mon, last_fri, ytd_start, unit="price"):
    hist = _hist(symbol, last_fri, ytd_start)
    if hist is None or hist.empty:
        return None, ("stale", None)

    asof, close = _on_or_before(hist, last_fri)
    if asof is None:
        return None, ("stale", None)

    status, gap = classify_freshness(asof, last_fri, key)

    # 주간/과거 조회에는 fast_info를 쓰지 않는다. 현재 시세를 과거 금요일 값으로
    # 잘못 라벨링할 수 있기 때문이다. 오래된 값은 delayed/stale로 그대로 공개한다.

    prev_fri = last_mon - dt.timedelta(days=3)              # 그 전주 금요일
    _, prev_close = _on_or_before(hist, prev_fri)
    ytd_date, ytd_close = _on_or_after(hist, ytd_start)
    trend, trend_color = trend_arrows(hist, last_fri)

    wow = round((close / prev_close - 1) * 100, 2) if prev_close else None
    ytd = round((close / ytd_close - 1) * 100, 2) if ytd_close else None

    rec = {"key": key, "label": label, "badge": badge, "unit": unit,
           "value": round(close, 2), "asof": asof.isoformat(),
           "wow_pct": wow, "ytd_pct": ytd,
           "trend": trend, "trend_color": trend_color}
    return rec, (status, gap)


def collect_rate_yahoo(key, symbol, label, badge, last_mon, last_fri, ytd_start):
    rec, flag = collect_price(key, symbol, label, badge, last_mon, last_fri, ytd_start, unit="bp")
    if not rec:
        return None, flag
    # bp 단위 재계산 (% 대신 절대 diff*100)
    hist = _hist(symbol, last_fri, ytd_start)
    if hist is None or hist.empty:
        return None, ("stale", None)
    _, prev_close = _on_or_before(hist, last_mon - dt.timedelta(days=3))
    ytd_date, ytd_close = _on_or_after(hist, ytd_start)
    rec["wow_pct"] = round((rec["value"] - prev_close) * 100, 1) if prev_close else None
    rec["ytd_pct"] = round((rec["value"] - ytd_close) * 100, 1) if ytd_close else None
    return rec, flag


def collect_ecos_rate(key, item, label, badge, last_mon, last_fri, ytd_start):
    api_key = os.getenv("ECOS_API_KEY")
    if not api_key:
        print(f"  ! ECOS_API_KEY 없음 — {label} 건너뜀")
        return None, ("stale", None)
    end = last_fri.strftime("%Y%m%d")
    start = ytd_start.strftime("%Y%m%d")
    combos = [D._ecos_combo] if D._ecos_combo else D.ECOS_CANDIDATES
    for stat, cycle in combos:
        rows, err = D._ecos_get(f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/900/"
                                f"{stat}/{cycle}/{start}/{end}/{item}")
        if not rows:
            continue
        rows = sorted([r for r in rows if r.get("DATA_VALUE")], key=lambda r: r["TIME"])
        if not rows:
            continue
        D._ecos_combo = (stat, cycle)
        series = {f'{r["TIME"][:4]}-{r["TIME"][4:6]}-{r["TIME"][6:]}': float(r["DATA_VALUE"]) for r in rows}
        dates = sorted(series)
        available = [d for d in dates if d <= last_fri.isoformat()]
        if not available:
            continue
        asof = available[-1]
        close = series[asof]
        status, gap = classify_freshness(dt.date.fromisoformat(asof), last_fri, key)

        prev_fri = (last_mon - dt.timedelta(days=3)).isoformat()
        before = [d for d in dates if d <= prev_fri]
        prev_close = series[before[-1]] if before else None

        ytd_iso = ytd_start.isoformat()
        after = [d for d in dates if d >= ytd_iso]
        ytd_close = series[after[0]] if after else None

        # 4주 흐름
        pts = []
        for i in range(4, -1, -1):
            t = (last_fri - dt.timedelta(weeks=i)).isoformat()
            cand = [d for d in dates if d <= t]
            pts.append(series[cand[-1]] if cand else None)
        arrows = []
        for a, b in zip(pts, pts[1:]):
            arrows.append("－" if a is None or b is None or a == b else ("▲" if b > a else "▼"))
        up, dn = arrows.count("▲"), arrows.count("▼")
        trend_color = "up" if up > dn else ("dn" if dn > up else "fl")

        rec = {"key": key, "label": label, "badge": badge, "unit": "bp",
               "value": round(close, 3), "asof": asof,
               "wow_pct": round((close - prev_close) * 100, 1) if prev_close else None,
               "ytd_pct": round((close - ytd_close) * 100, 1) if ytd_close else None,
               "trend": "".join(arrows), "trend_color": trend_color}
        return rec, (status, gap)
    return None, ("stale", None)


# ─────────────────────────────── 메인

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", help="기준일 YYYY-MM-DD (기본: 오늘, KST)")
    ap.add_argument("--out", default="data_weekly.json")
    args = ap.parse_args()

    ref = dt.date.fromisoformat(args.ref) if args.ref else dt.datetime.now(KST).date()
    (last_mon, last_fri), (this_mon, this_fri) = target_weeks(ref)
    ytd_start = dt.date(last_fri.year, 1, 1)

    print(f"[기간] 지난주 {last_mon}~{last_fri}  ·  이번주 {this_mon}~{this_fri}")

    out = {"generated_at": dt.datetime.now(KST).isoformat(timespec="seconds"),
           "last_week": {"mon": last_mon.isoformat(), "fri": last_fri.isoformat()},
           "this_week": {"mon": this_mon.isoformat(), "fri": this_fri.isoformat()},
           "ytd_start": ytd_start.isoformat(),
           "series": {}, "missing": [], "stale": [], "delayed": []}

    def record(key, rec, flag):
        status, gap = flag
        if rec:
            out["series"][key] = rec
            tag = f"[{status}]" if status != "ok" else ""
            wow = "-" if rec.get("wow_pct") is None else f"{rec['wow_pct']:+.2f}"
            ytd = "-" if rec.get("ytd_pct") is None else f"{rec['ytd_pct']:+.2f}"
            print(f"  · {rec['label']:<14} {rec['value']:>12,} "
                  f"주간 {wow} · YTD {ytd}  {rec['asof']} {tag}")
            if status == "stale":
                out["stale"].append({"key": key, "label": rec["label"], "asof": rec["asof"], "gap": gap})
            elif status == "delayed":
                out["delayed"].append({"key": key, "label": rec["label"], "asof": rec["asof"], "gap": gap})
        else:
            out["missing"].append(key)
            print(f"  ! {key} 수집 실패")

    print("[1/4] 주식")
    for key, sym, label, badge in EQUITY:
        record(key, *collect_price(key, sym, label, badge, last_mon, last_fri, ytd_start))

    print("[2/4] 환율·원자재·가상자산")
    for key, sym, label, badge in FX_CM:
        record(key, *collect_price(key, sym, label, badge, last_mon, last_fri, ytd_start))

    print("[3/4] 미국채 (Yahoo)")
    for key, sym, label, badge in YAHOO_RATES:
        record(key, *collect_rate_yahoo(key, sym, label, badge, last_mon, last_fri, ytd_start))

    print("[4/4] 국고채 (ECOS)")
    for key, item, label, badge in ECOS_RATES:
        record(key, *collect_ecos_rate(key, item, label, badge, last_mon, last_fri, ytd_start))

    atomic_write_json(args.out, out)

    total = len(EQUITY) + len(FX_CM) + len(YAHOO_RATES) + len(ECOS_RATES)
    print(f"\n수집 완료 : {len(out['series'])}/{total}종 → {args.out}")
    if out["delayed"]:
        print(f"  · 정상 지연 {len(out['delayed'])}건: {', '.join(x['label'] for x in out['delayed'])}")
    if out["stale"]:
        print(f"  ! 확인 필요 {len(out['stale'])}건: {', '.join(x['label'] for x in out['stale'])}")
    if out["missing"]:
        print(f"  ! 미확보: {', '.join(out['missing'])}")


if __name__ == "__main__":
    main()
