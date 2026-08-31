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
    "sp500":  ("^GSPC",     "S&P 500",               "us", 2),
    "ndx":    ("^NDX",      "나스닥 100",             "us", 2),
    "kospi":  ("^KS11",     "코스피",                 "kr", 2),
    "shcomp": ("000001.SS", "상해종합",               "cn", 2),
    "sx5e":   ("^STOXX50E", "유로스톡스 50",          "eu", 2),
    "dxy":    ("DX-Y.NYB",  "달러인덱스 (pt)",        "fx", 2),
    "usdkrw": ("KRW=X",     "달러/원 (USD/KRW)",      "fx", 2),
    "gold":   ("GC=F",      "국제금 ($/oz, 선물)",    "cm", 2),
    "wti":    ("CL=F",      "유가 WTI ($/bbl, 선물)", "cm", 2),
    "btc":    ("BTC-USD",   "비트코인 (BTC/USD)",     "cr", 2),
}
EXTRA = {"kosdaq": "^KQ11", "dow": "^DJI", "nasdaq_comp": "^IXIC",
         "brent": "BZ=F", "vix": "^VIX", "dax": "^GDAXI"}


def yahoo(symbol: str, cutoff: str = None) -> dict | None:
    """최근 2영업일 종가로 값·등락 계산.

    cutoff(YYYY-MM-DD)를 주면 그 날짜 이후 데이터는 버린다.
    환율·원자재는 주말·휴장일에도 호가가 잡혀 주식보다 최신 날짜가 나오는데,
    그대로 두면 헤더의 '8/28 마감 기준' 표기와 행별 기준일이 어긋난다.
    """
    try:
        hist = yf.Ticker(symbol).history(period="14d", interval="1d")
        hist = hist.dropna(subset=["Close"])
        if cutoff:
            hist = hist[hist.index.strftime("%Y-%m-%d") <= cutoff]
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


# ── 한국은행 ECOS ──────────────────────────────────────────────
# 통계표 코드와 주기 표기가 자료마다 다릅니다(817Y002/060Y001, D/DD).
# 하나를 추정해 고정하지 않고 후보를 순서대로 시도한 뒤,
# 성공한 조합을 기억해 두 번째 호출부터는 바로 사용합니다.
ECOS_CANDIDATES = [("817Y002", "D"), ("817Y002", "DD"),
                   ("060Y001", "D"), ("060Y001", "DD")]
ECOS_ITEMS = {"ktb3y": ("국고채(3년)", "010200000"),
              "ktb10y": ("국고채(10년)", "010210000")}
_ecos_combo = None          # 성공한 (stat, cycle)
_ecos_items = {}            # 자동 탐색으로 찾은 항목코드


def _ecos_get(url):
    """(rows, 에러메시지) 반환"""
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        j = r.json()
    except Exception as exc:                                    # noqa: BLE001
        return None, f"요청 실패: {exc}"
    if "RESULT" in j:                                           # ECOS 오류 응답
        return None, f'{j["RESULT"].get("CODE")} {j["RESULT"].get("MESSAGE")}'
    for root in ("StatisticSearch", "StatisticItemList"):
        if root in j:
            return j[root].get("row", []), None
    return None, f"예상치 못한 응답: {str(j)[:120]}"


def ecos_discover(key, stat):
    """통계표의 항목 목록에서 국고채 3년/10년 코드를 이름으로 찾는다"""
    rows, err = _ecos_get(f"https://ecos.bok.or.kr/api/StatisticItemList/{key}/json/kr/1/200/{stat}")
    if err:
        return {}, err
    found = {}
    for row in rows or []:
        name = (row.get("ITEM_NAME") or "").replace(" ", "")
        code = row.get("ITEM_CODE")
        if "국고채" in name and "3년" in name and "13년" not in name:
            found.setdefault("ktb3y", code)
        if "국고채" in name and "10년" in name:
            found.setdefault("ktb10y", code)
    return found, None


def ecos(key_name: str) -> dict | None:
    """key_name: 'ktb3y' | 'ktb10y'"""
    global _ecos_combo
    key = os.getenv("ECOS_API_KEY")
    if not key:
        print("  ! ECOS_API_KEY 없음 — 국고채 건너뜀")
        return None

    end = dt.datetime.now(KST).strftime("%Y%m%d")
    start = (dt.datetime.now(KST) - dt.timedelta(days=30)).strftime("%Y%m%d")
    combos = [_ecos_combo] if _ecos_combo else ECOS_CANDIDATES

    for stat, cycle in combos:
        item = _ecos_items.get(key_name) or ECOS_ITEMS[key_name][1]
        url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/200/"
               f"{stat}/{cycle}/{start}/{end}/{item}")
        rows, err = _ecos_get(url)

        # 항목코드가 틀렸을 수 있으니 이름으로 한 번 더 탐색
        if not rows and key_name not in _ecos_items:
            found, derr = ecos_discover(key, stat)
            if found:
                _ecos_items.update(found)
                print(f"    (항목코드 자동탐색: {found})")
                if found.get(key_name) and found[key_name] != item:
                    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/200/"
                           f"{stat}/{cycle}/{start}/{end}/{found[key_name]}")
                    rows, err = _ecos_get(url)

        if not rows:
            if _ecos_combo is None:
                print(f"    · {stat}/{cycle} 실패 — {err or '데이터 없음'}")
            continue

        rows = [x for x in rows if x.get("DATA_VALUE")]
        if len(rows) < 2:
            continue
        rows.sort(key=lambda x: x["TIME"])
        last, prev = float(rows[-1]["DATA_VALUE"]), float(rows[-2]["DATA_VALUE"])
        d = rows[-1]["TIME"]
        if _ecos_combo is None:
            _ecos_combo = (stat, cycle)
            print(f"    · 조회 성공 조합: 통계표 {stat} / 주기 {cycle}")
        return {"value": round(last, 3), "chg": round((last - prev) * 100, 1),
                "pct": None, "asof": f"{d[:4]}-{d[4:6]}-{d[6:]}", "unit": "bp"}

    print(f"  ! ECOS {ECOS_ITEMS[key_name][0]} 조회 실패 — 모든 조합에서 데이터 없음")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", dest="show", action="store_true")
    ap.add_argument("--out", default="data.json")
    args = ap.parse_args()

    out = {"generated_at": dt.datetime.now(KST).isoformat(timespec="seconds"), "series": {}, "missing": []}

    print("[1/3] Yahoo Finance")

    # 기준일 확정 : 주식시장(미국·한국)의 마지막 거래일 중 늦은 쪽
    anchors = [yahoo("^GSPC"), yahoo("^KS11")]
    cutoff = max([a["asof"] for a in anchors if a], default=None)
    print(f"  · 기준일 : {cutoff or '확정 실패'} (이후 데이터는 제외)")

    for key, (sym, label, badge, _) in TICKERS.items():
        rec = yahoo(sym, cutoff)
        if rec:
            rec.update(label=label, badge=badge, symbol=sym)
            out["series"][key] = rec
            print(f"  · {label:<22} {rec['value']:>12,} ({rec['pct']:+.2f}%)  {rec['asof']}")
        else:
            out["missing"].append(key)
    for key, sym in EXTRA.items():
        rec = yahoo(sym, cutoff)
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
    for key in ("ktb3y", "ktb10y"):
        rec = ecos(key)
        if rec:
            rec.update(label={"ktb3y": "국고채 3년", "ktb10y": "국고채 10년"}[key], badge="kr")
            out["series"][key] = rec
            print(f"  · {rec['label']:<22} {rec['value']:>12}%  ({rec['chg']:+.1f}bp)  {rec['asof']}")
        else:
            out["missing"].append(key)

    if cutoff:
        stale = [r["label"] for r in out["series"].values()
                 if r.get("label") and r["asof"] < cutoff]
        if stale:
            print(f"  · 기준일({cutoff})보다 이전 값: {', '.join(stale)} — 각 행에 기준일이 표기됩니다")
        out["cutoff"] = cutoff

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
