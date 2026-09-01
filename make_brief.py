# -*- coding: utf-8 -*-
"""
2단계 · 헤드라인·코멘트 생성  →  brief.json

수집된 시장데이터(data.json)를 근거로, Claude API가 웹검색을 돌려
글로벌 헤드라인 3건 + 국내 체크포인트 + 투자마인드 3개 + 정리문장 3줄을 만듭니다.

환경변수
    ANTHROPIC_API_KEY

사용법
    python make_brief.py --data data.json --out brief.json
"""

import argparse
import datetime as dt
import json
import os
import re
import sys

import requests

KST = dt.timezone(dt.timedelta(hours=9))
WD = ["월", "화", "수", "목", "금", "토", "일"]
API = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"

SYSTEM = """당신은 퇴직연금 담당자를 위한 데일리 마켓 브리핑을 쓰는 애널리스트다.

[사실 규칙 — 위반 금지]
· 시장 수치는 반드시 아래 [수집 데이터]에 있는 값만 쓴다. 없는 수치는 지어내지 않는다.
· 뉴스는 웹검색으로 확인한 것만 쓰고, 매체명과 날짜(M/D)를 반드시 붙인다.
· 날짜가 확인되지 않는 기사는 사용하지 않는다. 예외 없다.
· 확인되지 않은 사실은 "확인 필요"라고 명시한다. 추측을 사실처럼 쓰지 않는다.

[투자 전제 — 매일 동일]
· 글로벌 자산배분 기반. Core = TDF(30~50%), Satellite = ETF(30~70%)
· 이 전략은 시장 국면과 무관하게 일관되게 유지한다는 것이 메시지의 축이다.

[톤]
· 사실 → 해석 순서. 해석이 사실보다 앞서지 않는다.
· 단정적 전망 금지. "~할 것이다" 대신 "~할 가능성이 있다 / ~로 해석된다".
· 특정 종목·상품의 매수 권유 표현 금지.
· 문장은 짧고 담백하게. 과장된 수식어를 쓰지 않는다.

[출력]
· 오직 JSON 객체만 출력한다. 마크다운 코드펜스나 설명 문장을 붙이지 않는다.
· body/quotes 안에서는 <b> 태그로만 강조할 수 있다. 다른 HTML 태그는 쓰지 않는다."""

SCHEMA = """{
  "og_description": "링크 미리보기용 2문장 요약. 핵심 수치 포함. 120자 이내",
  "headlines": [
    {"title": "25자 내외 제목", "body": "2~3문장 설명", "source": "매체명 · M/D"}
  ],
  "checkpoint": {"title": "국내 이슈 또는 주간 정리 제목", "body": "2~3문장", "source": "매체명 · M/D"},
  "mindset": [
    {"title": "소제목", "body": "2~3문장"}
  ],
  "quotes": ["담당자가 고객에게 그대로 읽어줄 수 있는 짧은 문장 3개. 각 45자 이내. 핵심 어구 1~2개를 <b>로 강조"],
  "oneline_market": "오늘 시장을 한 문장으로",
  "oneline_pension": "퇴직연금 관점 한 문장",
  "next_events": "다음 체크포인트를 · 로 구분해 한 줄"
}"""


def summarize(data: dict) -> str:
    lines = []
    for key, r in data["series"].items():
        if r.get("unit") == "bp":
            lines.append(f'  {r["label"]}: {r["value"]}% ({r["chg"]:+.1f}bp, {r["asof"]} 기준)')
        else:
            lines.append(f'  {r.get("label", key)}: {r["value"]:,} ({r["pct"]:+.2f}%, {r["asof"]} 기준)')
    if data.get("missing"):
        lines.append(f'  [미확보] {", ".join(data["missing"])} — 이 항목의 수치는 언급하지 말 것')
    return "\n".join(lines)


def call_api(prompt: str, key: str) -> str:
    body = {
        "model": MODEL, "max_tokens": 4000, "system": SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
    }
    r = requests.post(API, headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                    "content-type": "application/json"},
                      json=body, timeout=180)
    if r.status_code != 200:
        sys.exit(f"API 오류 {r.status_code}: {r.text[:400]}")
    return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        sys.exit("JSON을 찾지 못했습니다:\n" + text[:600])
    return json.loads(text[i:j + 1])


def validate(b: dict) -> list:
    """발송 전 자체 점검 — 문제를 리스트로 반환"""
    issues = []
    if len(b.get("headlines", [])) != 3:
        issues.append("헤드라인이 3건이 아님")
    for i, h in enumerate(b.get("headlines", []), 1):
        if not re.search(r"\d{1,2}/\d{1,2}", h.get("source", "")):
            issues.append(f"헤드라인 {i}의 출처에 날짜(M/D)가 없음")
    if len(b.get("mindset", [])) != 3:
        issues.append("MINDSET이 3개가 아님")
    quotes = b.get("quotes", [])
    if len(quotes) != 3:
        issues.append("정리문장이 3줄이 아님")
    for i, q in enumerate(quotes, 1):
        plain = re.sub(r"<[^>]+>", "", q)
        if len(plain) > 55:
            issues.append(f"정리문장 {i}이 {len(plain)}자로 김 (45자 권장)")
        if "<b>" not in q:
            issues.append(f"정리문장 {i}에 <b> 강조가 없음")
    for k in ("og_description", "oneline_market", "oneline_pension", "next_events", "checkpoint"):
        if not b.get(k):
            issues.append(f"{k} 누락")
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data.json")
    ap.add_argument("--out", default="brief.json")
    args = ap.parse_args()

    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY 환경변수가 필요합니다.")

    data = json.load(open(args.data, encoding="utf-8"))
    today = dt.datetime.now(KST).date()

    prompt = f"""오늘은 {today.year}년 {today.month}월 {today.day}일 ({WD[today.weekday()]})이다.
아래는 오늘 아침 자동 수집된 시장 데이터다.

[수집 데이터]  (기준일: {data.get("cutoff", "확인 필요")})
{summarize(data)}

[최신성 — 반드시 지킬 것]
· 수집 데이터의 기준일(asof)이 곧 이 브리핑의 시점이다. 그보다 오래된 뉴스로 헤드라인을
  채우지 않는다. 기준일 당일 또는 직전 영업일에 발생한 사건을 우선한다.
· 이미 며칠 지난 이벤트(지난주 금통위, 지난주 잭슨홀 등)를 헤드라인으로 다시 올리지 않는다.
  다만 그 사건의 '새로운 후속 전개'가 있으면 그 전개를 다룬다.
· MINDSET도 오늘 수집된 수치의 움직임을 근거로 새로 쓴다. 어제와 같은 문장을 반복하지 않는다.
· [미확인] 표시가 있는 항목의 수치는 서술에 사용하지 않는다.

[할 일]
0. 오늘이 월요일이면 직전 거래일은 지난 금요일이다. 주말 사이 나온 뉴스도 함께 확인하고,
   기준시점 표기는 수집 데이터의 asof 날짜를 따른다.
1. 웹검색으로 직전 거래일의 글로벌 금융시장 뉴스를 확인하고, 퇴직연금 자산배분
   (주식·금리·환율·원자재)에 영향이 있는 것으로 3건을 고른다. 세 건이 서로 다른
   축을 다루도록 배분한다. 위 수집 데이터의 움직임을 설명해 주는 뉴스를 우선한다.
2. 국내 이슈(금통위·국고채·코스피 수급·퇴직연금 제도) 1건을 checkpoint로 넣는다.
   마땅한 국내 뉴스가 없으면 제목을 "투자 코멘트"로 바꾸고 글로벌 이슈가 국내
   퇴직연금 자산배분에 주는 시사점을 쓴다. 칸을 비우지 않는다.
3. MINDSET 3개를 쓴다. 2번은 반드시 Core(TDF)-Satellite(ETF) 역할 분담을 다룬다.
   1번은 그날의 시장 국면을 자산배분 언어로 해석하고, 3번은 장기투자 원칙을 다룬다.
4. 정리문장 3줄을 쓴다. 상담 현장에서 그대로 읽는 문장이므로 아래를 지킨다.
   - 한 문장당 45자 이내. 두 문장으로 늘이지 않는다.
   - 각 문장의 핵심 어구 1~2개를 <b>태그로 감싼다. 문장 전체를 감싸지 않는다.
   - 설명이 아니라 결론을 쓴다. "~입니다"로 끝나는 단정적 조언 형태.
   - 예시 형식: 오늘 오른 자산을 쫓기보다 <b>비중을 원래대로</b> 돌리는 것이 먼저입니다.
5. 오늘의 한 줄(시장/연금)과 다음 체크포인트를 작성한다.

아래 스키마의 JSON만 출력한다.
{SCHEMA}"""

    print("[생성] Claude API 호출 (웹검색 포함)...")
    brief = parse_json(call_api(prompt, key))

    issues = validate(brief)
    brief["_issues"] = issues
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(brief, fp, ensure_ascii=False, indent=2)

    print(f"  · 헤드라인 {len(brief.get('headlines', []))}건 / MINDSET {len(brief.get('mindset', []))}개")
    for h in brief.get("headlines", []):
        print(f"    - {h['title'][:40]}  ({h['source']})")
    if issues:
        print("  ! 점검 사항:")
        for i in issues:
            print(f"    - {i}")
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
