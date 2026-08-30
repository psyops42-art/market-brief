# -*- coding: utf-8 -*-
"""
1단계 · 시장지표 12종 수집  →  data.json

공식·무료 API만 사용합니다. 스크래핑 없음.
  · 글로벌(주식·환율·원자재) : yfinance (Yahoo Finance)
  · 미국 국채 3년/10년        : FRED (미 세인트루이스 연준)  DGS3 / DGS10
  · 국고채 3년/10년           : 한국은행 ECOS OpenAPI        817Y002

환경변수
    FRED_API_KEY   https://fredaccount.stlouisfed.org/apikeys (무료)
    ECOS_API_KEY   https://ecos.bok.or.kr/api (무료)

사용법
    python fetch_data.py                 # data.json 생성
    python fetch_data.py --print         # 결과를 화면에도 출력
"""

import argparse
import datetime as dt
import json
import os
import sys

import requests

try:
    import yfinance as yf
except ImportError:
    sys.exit("pip install yfinance requests")

KST = dt.timezone(dt.timedelta(hours=9))

# 표시 순서 고정 — 대시보드 표와 1:1 대응
TICKERS = {
    "sp500":   ("^GSPC",    "S&P 500",              "us", 2),
    "ndx":     ("^NDX",     "나스닥 100",            "us", 2),
    "kospi":   ("^KS11",    "코스피",                "kr", 2),
    "dxy":     ("DX-Y.NYB", "달러인덱스 (pt)",       "",   2),
    "usdkrw":  ("KRW=X",    "달러/원 (USD/KRW)",     "",   2),
    "gold":    ("GC=F",     "국제금 ($/oz, 선물)",   "",   2),
    "wti":     ("CL=F",     "유가 WTI ($/bbl, 선물)", "",  2),
}
EXTRA = {"kosdaq": "^KQ11", "dow": "^DJI", "nasdaq_comp": "^IXIC", "brent": "BZ=F", "vix": "^VIX"}


def yahoo(symbol: str) -> dict | None:
    """최근 2영업일 종가로 값·등락 계산"""
    try:
        hist = yf.Ticker(symbol).history(period="7d", interval="1d")
        hist = hist.dropna(subset=["Close"])
        if len(hist) < 2:
            return None
        last, prev = hist["Close"].iloc[-1], hist["Close"].iloc[-2]
        return {"value": round(float(last), 2),
                "chg": round(float(last - prev), 2),
                "pct": round(float((last / prev - 1) * 100), 2),
                "asof": hist.index[-1].strftime("%Y-%m-%d")}
    except Exception as exc:                                    # noqa: BLE001
        print(f"  ! {symbol} 실패: {exc}")
        return None


def fred(series: str) -> dict | None:
    """FRED 일별 시계열 → 최근 2개 관측치로 bp 변동 계산"""
    key = os.getenv("FRED_API_KEY")
    if not key:
        print("  ! FRED_API_KEY 없음 — 미국채 건너뜀")
        return None
    try:
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                         params={"series_id": series, "api_key": key, "file_type": "json",
                                 "sort_order": "desc", "limit": 10}, timeout=20)
        r.raise_for_status()
        obs = [o for o in r.json()["observations"] if o["value"] not in (".", "")]
        if len(obs) < 2:
            return None
        last, prev = float(obs[0]["value"]), float(obs[1]["value"])
        return {"value": round(last, 3), "chg": round((last - prev) * 100, 1),
                "pct": None, "asof": obs[0]["date"], "unit": "bp"}
    except Exception as exc:                                    # noqa: BLE001
        print(f"  ! FRED {series} 실패: {exc}")
        return None


def ecos(item: str) -> dict | None:
    """한국은행 ECOS 시장금리 일별 (817Y002). item: 010200000=국고3년 010210000=국고10년"""
    key = os.getenv("ECOS_API_KEY")
    if not key:
        print("  ! ECOS_API_KEY 없음 — 국고채 건너뜀")
        return None
    end = dt.datetime.now(KST).strftime("%Y%m%d")
    start = (dt.datetime.now(KST) - dt.timedelta(days=20)).strftime("%Y%m%d")
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/100/"
           f"817Y002/D/{start}/{end}/{item}")
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        rows = r.json().get("StatisticSearch", {}).get("row", [])
        rows = [x for x in rows if x.get("DATA_VALUE")]
        if len(rows) < 2:
            return None
        last, prev = float(rows[-1]["DATA_VALUE"]), float(rows[-2]["DATA_VALUE"])
        d = rows[-1]["TIME"]
        return {"value": round(last, 3), "chg": round((last - prev) * 100, 1),
                "pct": None, "asof": f"{d[:4]}-{d[4:6]}-{d[6:]}", "unit": "bp"}
    except Exception as exc:                                    # noqa: BLE001
        print(f"  ! ECOS {item} 실패: {exc}")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", dest="show", action="store_true")
    ap.add_argument("--out", default="data.json")
    args = ap.parse_args()

    out = {"generated_at": dt.datetime.now(KST).isoformat(timespec="seconds"), "series": {}, "missing": []}

    print("[1/3] Yahoo Finance")
    for key, (sym, label, badge, _) in TICKERS.items():
        rec = yahoo(sym)
        if rec:
            rec.update(label=label, badge=badge, symbol=sym)
            out["series"][key] = rec
            print(f"  · {label:<22} {rec['value']:>12,} ({rec['pct']:+.2f}%)  {rec['asof']}")
        else:
            out["missing"].append(key)
    for key, sym in EXTRA.items():
        rec = yahoo(sym)
        if rec:
            out["series"][key] = rec

    print("[2/3] FRED · 미국 국채")
    for key, series in (("ust3y", "DGS3"), ("ust10y", "DGS10")):
        rec = fred(series)
        if rec:
            rec.update(label={"ust3y": "미국채 3년", "ust10y": "미국채 10년"}[key], badge="us")
            out["series"][key] = rec
            print(f"  · {rec['label']:<22} {rec['value']:>12}%  ({rec['chg']:+.1f}bp)  {rec['asof']}")
        else:
            out["missing"].append(key)

    print("[3/3] 한국은행 ECOS · 국고채")
    for key, item in (("ktb3y", "010200000"), ("ktb10y", "010210000")):
        rec = ecos(item)
        if rec:
            rec.update(label={"ktb3y": "국고채 3년", "ktb10y": "국고채 10년"}[key], badge="kr")
            out["series"][key] = rec
            print(f"  · {rec['label']:<22} {rec['value']:>12}%  ({rec['chg']:+.1f}bp)  {rec['asof']}")
        else:
            out["missing"].append(key)

    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)

    got, total = len(out["series"]), len(TICKERS) + 4
    print(f"\n수집 완료 : 핵심 {total - len(out['missing'])}/{total}종 → {args.out}")
    if out["missing"]:
        print("  ! 미확보 :", ", ".join(out["missing"]), "— 대시보드에 '확인필요'로 표기됩니다")
    if args.show:
        print(json.dumps(out, ensure_ascii=False, indent=2)[:2000])


if __name__ == "__main__":
    main()
