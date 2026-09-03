# -*- coding: utf-8 -*-
"""
주간 마켓 브리핑 · 2단계 · 헤드라인·인사이트 생성  →  brief_weekly.json

daily의 make_brief.py와 같은 구조입니다. 차이점은 세 가지입니다.
    1. 지난 한 주(월~금) 전체를 요약합니다 — 하루 뉴스가 아니라 주간 흐름.
    2. 이번 주(월~금) 일정을 요일별로 정리합니다.
    3. '이주의 투자교육'은 LLM이 새로 쓰지 않고, 미리 검증된 원고 목록에서
       주차(ISO week)에 따라 고정 로테이션으로 선택합니다 — 매주 다른 금융
       주장을 새로 생성하게 하면 사실 오류 위험이 커지므로, 안전한 방식을
       택했습니다.

최신성 검증 (반드시 확인)
    · 헤드라인 3건의 출처 날짜가 '지난주(월~금)' 범위 안에 있는지 확인합니다.
    · 이번주 체크포인트의 날짜가 '이번주(월~금)' 범위 안에 있는지 확인합니다.
    · 범위를 벗어나면 brief_issues 에 기록되고, render_weekly.py가 화면에
      경고를 표시할 수 있도록 report에 전달됩니다.

사용법
    python make_brief_weekly.py --data data_weekly.json --out brief_weekly.json
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time

import requests
from requests.exceptions import RequestException
from pipeline_utils import atomic_write_json, has_disallowed_markup, md_date_in_range

KST = dt.timezone(dt.timedelta(hours=9))
API = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"

# ── 이주의 투자교육 : 검증된 원고 로테이션 (LLM이 매번 새로 쓰지 않음) ──
EDU_LESSONS = [
    {"title": "복리는 '기간'에서 나옵니다",
     "body": "연 6% 수익률이라도 10년이면 원금의 1.8배, 20년이면 3.2배가 됩니다. "
             "퇴직연금의 진짜 무기는 높은 수익률이 아니라 <b>긴 가입 기간</b>입니다."},
    {"title": "분산투자는 손실을 없애는 게 아니라 나누는 것입니다",
     "body": "여러 자산에 나눠 담아도 전체가 오르기만 하지는 않습니다. 다만 "
             "<b>한 자산의 큰 하락이 전체를 흔들지 않게</b> 하는 것이 분산의 역할입니다."},
    {"title": "타이밍보다 시간이 중요한 이유",
     "body": "시장의 저점을 맞히는 것은 전문가에게도 어렵습니다. 대신 <b>오래 시장에 머무는 것</b>은 "
             "누구나 할 수 있고, 장기 데이터상 그 효과가 더 꾸준했습니다."},
    {"title": "정기 납입은 가격이 아니라 습관을 사는 것입니다",
     "body": "매달 같은 금액을 넣으면 비쌀 때는 적게, 쌀 때는 많이 사게 됩니다. "
             "<b>납입을 멈추지 않는 것</b> 자체가 하나의 전략입니다."},
    {"title": "리밸런싱은 예측이 아니라 원칙입니다",
     "body": "오른 자산을 팔고 내린 자산을 채우는 리밸런싱은 시장을 맞히려는 행동이 아닙니다. "
             "<b>처음 정한 비중으로 되돌리는 것</b>일 뿐입니다."},
    {"title": "변동성과 손실은 다릅니다",
     "body": "평가액이 오르내리는 것은 변동성이고, 실제로 팔았을 때만 손실이 확정됩니다. "
             "퇴직연금처럼 <b>인출까지 긴 시간이 남은 자산</b>일수록 이 구분이 중요합니다."},
    {"title": "은퇴 시점이 가까워질수록 배분이 바뀌어야 합니다",
     "body": "TDF가 자동으로 하는 일이 이것입니다. 위험자산 비중을 시간이 갈수록 낮춰, "
             "<b>은퇴 직전 급락의 충격</b>을 줄이도록 설계돼 있습니다."},
    {"title": "수수료는 매년 갈아 넣는 비용입니다",
     "body": "연 1%의 보수 차이는 하루로 보면 작지만, 20년이면 최종 자산의 상당 부분을 갉아먹습니다. "
             "<b>수익률만큼 비용도 누적되는 변수</b>입니다."},
    {"title": "버핏이 말한 '역발상'이 필요한 순간",
     "body": "워런 버핏은 \"다른 사람이 두려워할 때 욕심내고, 욕심낼 때 두려워하라\"고 했습니다. "
             "하락장에서 납입을 멈추는 것이야말로 <b>가장 흔한 실수</b>입니다."},
    {"title": "보글이 지수펀드를 만든 이유",
     "body": "뱅가드 창립자 존 보글은 개별 종목을 고르는 대신 시장 전체를 담으라고 조언했습니다. "
             "퇴직연금의 ETF·TDF도 같은 원리로, <b>시장 전체의 성장에 올라타는 방법</b>입니다."},
    {"title": "린치의 질문 — '왜 이 상품에 가입했나요?'",
     "body": "전설적 펀드매니저 피터 린치는 무엇을 보유하고 있는지, 왜 보유하는지 알아야 한다고 강조했습니다. "
             "이유를 스스로 설명할 수 있다면 <b>흔들리지 않을 준비</b>가 된 것입니다."},
    {"title": "그레이엄의 저울 — 시장은 결국 무게를 잽니다",
     "body": "벤저민 그레이엄은 단기 시장을 인기투표에, 장기 시장을 저울에 비유했습니다. "
             "오늘의 등락은 인기투표일 뿐, <b>결국 가치가 무게를 결정</b>합니다."},
    {"title": "멍거가 말한 '기다림의 값'",
     "body": "버크셔 해서웨이의 찰리 멍거는 수익의 상당 부분이 매매가 아니라 "
             "<b>보유하며 기다리는 시간</b>에서 나온다고 말했습니다. 잦은 상품 변경보다 꾸준한 유지가 유리한 이유입니다."},
    {"title": "템플턴이 찾은 '최악의 순간'",
     "body": "존 템플턴은 비관론이 극에 달했을 때가 오히려 매수 적기라고 봤습니다. "
             "정기 납입 방식은 이 타이밍을 <b>고민 없이 자동으로 실행</b>하는 구조입니다."},
    {"title": "코스톨라니 — 돈은 인내심으로 이동합니다",
     "body": "투자자 앙드레 코스톨라니는 돈은 조급한 사람에게서 여유 있는 사람에게로 흘러간다고 했습니다. "
             "퇴직연금처럼 <b>긴 시간이 확보된 자산</b>일수록 이 원리가 유리하게 작동합니다."},
    {"title": "하워드 막스의 위험 관리법",
     "body": "오크트리캐피탈의 하워드 막스는 위험은 우리가 무엇을 모르는지 모를 때 가장 커진다고 말합니다. "
             "자산배분은 <b>모르는 미래에 대비하는 현실적인 방법</b>입니다."},
    {"title": "레이 달리오의 '성배' — 분산투자",
     "body": "브리지워터 창립자 레이 달리오는 서로 다른 자산에 나눠 담는 것을 투자의 '성배'라 불렀습니다. "
             "Core-Satellite 구조도 <b>자산군을 나눠 위험을 낮추는</b> 같은 원리입니다."},
    {"title": "피셔의 조언 — '사고 나서 조용히 앉아 있어라'",
     "body": "성장주 투자의 대가 필립 피셔는 좋은 자산을 고른 뒤엔 조용히 앉아 있는 것이 가장 어렵다고 했습니다. "
             "계좌를 자주 들여다보지 않는 것도 <b>하나의 전략</b>입니다."},
    {"title": "버핏의 눈덩이 — 젖은 눈과 긴 언덕",
     "body": "워런 버핏은 복리를 눈덩이에 비유하며 젖은 눈과 충분히 긴 언덕이 필요하다고 말했습니다. "
             "퇴직연금에서 <b>긴 언덕(가입 기간)</b>은 이미 주어져 있습니다."},
    {"title": "보글 — '내지 않은 비용이 곧 수익입니다'",
     "body": "존 보글은 투자에서 지불하지 않은 비용이 곧 돌려받는 수익이라고 했습니다. "
             "낮은 보수의 ETF를 활용하는 것도 <b>확실하게 통제할 수 있는 수익률</b>입니다."},
    {"title": "수익률보다 먼저 필요한 질문",
     "body": "\"이번 달 몇 % 벌었나\"보다 중요한 질문은 \"은퇴 시점에 얼마가 필요한가\"입니다. "
             "목표액이 정해지면 <b>중간의 등락에 덜 흔들리게</b> 됩니다."},
    {"title": "퇴직연금이 가진 눈에 안 보이는 혜택",
     "body": "퇴직연금은 운용 중 발생한 수익에 매년 세금을 매기지 않고 인출 시점까지 미룹니다. "
             "이 <b>세금이연 효과</b> 자체가 장기 수익률을 끌어올리는 요인입니다."},
    {"title": "카너먼이 밝힌 '손실은 두 배 아프다'",
     "body": "노벨경제학상 수상자 대니얼 카너먼은 사람이 손실의 고통을 이익의 기쁨보다 "
             "<b>훨씬 크게 느낀다</b>는 것을 실험으로 보였습니다. 하락장에 유독 불안한 건 자연스러운 반응입니다."},
    {"title": "최고의 날 며칠을 놓치면 벌어지는 일",
     "body": "시장의 큰 상승은 며칠 안에 몰려서 나타나는 경우가 많습니다. "
             "그 며칠을 피하려다 <b>오히려 놓치면 장기 수익률이 크게 낮아진다</b>는 연구 결과가 반복적으로 확인됐습니다."},
    {"title": "가장 성과가 좋았던 계좌의 공통점",
     "body": "한 자산운용사 내부 분석에서, 수익률이 가장 좋았던 계좌는 "
             "가입자가 <b>존재 자체를 잊고 있던 계좌</b>였다는 이야기가 자주 회자됩니다. 자동 납입·자동 배분이 유리한 이유입니다."},
    {"title": "현금도 완전히 안전하지는 않습니다",
     "body": "물가상승률이 연 3%라면 현금의 실질 가치는 20년 후 절반 가까이 줄어듭니다. "
             "원리금보장 상품만 고집하는 것도 <b>또 다른 종류의 위험</b>일 수 있습니다."},
    {"title": "수익률을 가르는 건 종목이 아니라 배분입니다",
     "body": "자산배분을 다룬 유명한 연구에 따르면, 장기 수익률 차이의 상당 부분은 "
             "어떤 종목을 골랐는지가 아니라 <b>자산군을 어떻게 나눴는지</b>로 설명됩니다."},
    {"title": "적립식은 가격을 예측하지 않아도 됩니다",
     "body": "매달 같은 금액을 넣으면 오를 때도 내릴 때도 계속 사게 되어 "
             "<b>평균 매입 단가가 자연스럽게 낮아지는 효과</b>가 있습니다. 시장을 맞히려 하지 않아도 되는 이유입니다."},
    {"title": "쌓는 시기와 쓰는 시기는 전략이 다릅니다",
     "body": "자산을 쌓는 동안의 원칙과 은퇴 후 꺼내 쓰는 동안의 원칙은 다릅니다. "
             "인출이 가까워질수록 <b>변동성 관리가 수익률 추구보다 우선</b>됩니다."},
    {"title": "한국에만 담으면 놓치는 것들",
     "body": "코스피는 전 세계 주식시장 시가총액의 일부에 불과합니다. "
             "해외 자산을 함께 담으면 <b>국내 시장에 쏠린 환율·산업 위험</b>을 줄일 수 있습니다."},
]


SYSTEM = """당신은 퇴직연금 담당자를 위한 주간 마켓 브리핑을 쓰는 애널리스트다.
이 브리핑은 매주 월요일 아침, 그날의 데일리 브리핑이 돌기 전에 발행되어
지난 한 주를 정리하고 이번 한 주를 준비하는 역할을 한다.

[사실 규칙 — 위반 금지]
· 시장 수치는 반드시 [수집 데이터]에 있는 값만 쓴다. 없는 수치는 지어내지 않는다.
· 뉴스는 웹검색으로 확인한 것만 쓰고, 매체명과 날짜(M/D)를 반드시 붙인다.
· 헤드라인은 [지난주 기간] 안에 발생한 사건만 다룬다. 그 이전 사건을 재탕하지 않는다.
  다만 그 사건의 '이번 주 후속 전개'가 있다면 그 전개를 새 사실로 다룰 수 있다.
· 체크포인트(이번주 일정)는 [이번주 기간] 안의 날짜만 쓴다. 범위 밖 날짜를 넣지 않는다.
· 확인되지 않은 사실은 "확인 필요"라고 명시한다.

[관점 — 반드시 지킬 것]
· 이 브리핑의 정체성은 '하루 단위 반응'이 아니라 '주 단위, 그리고 그보다 긴 관점'이다.
· 헤드라인과 회고는 하루하루의 사건이 아니라 한 주 전체를 관통한 흐름으로 서술한다.
· 개별 이벤트에 대한 반응보다, 이벤트가 몰려도 원칙(Core-TDF/Satellite-ETF, 정기납입,
  분산투자)이 흔들리지 않았다는 점을 자연스럽게 드러낸다. 설교조로 반복하지 않는다.

[투자 전제 — 매일/매주 동일]
· 글로벌 자산배분 기반. Core = TDF(30~50%), Satellite = ETF(30~70%)

[톤]
· 사실 → 해석 순서. 단정적 전망 금지. 특정 상품 매수 권유 금지.
· 문장은 짧고 담백하게.

[정리 문장 규격]
· 3개, 각 45자 이내. 핵심 어구 1~2개만 <b>로 강조 (문장 전체 강조 금지).
· 설명이 아니라 결론을 쓴다.

[출력]
· 오직 JSON 객체만 출력한다. 마크다운 코드펜스나 설명 문장을 붙이지 않는다.
· body/quotes 안에서는 <b> 태그로만 강조할 수 있다."""

SCHEMA = """{
  "og_description": "링크 미리보기용 2문장 요약. 핵심 수치 포함. 120자 이내",
  "last_week_headlines": [
    {"title": "25자 내외 제목", "body": "2~3문장, 한 주 전체를 관통하는 서술", "source": "매체명 · M/D"}
  ],
  "retrospective": {"title": "회고 소제목", "body": "2~3문장. 원칙이 왜 유효했는지"},
  "checkpoints": [
    {"day": "월|화|수|목|금", "date": "M/D", "text": "일정 내용", "highlight": true 또는 false}
  ],
  "mindset_01": {"title": "이번주 시장 관전 포인트 소제목", "body": "2~3문장"},
  "quotes": ["45자 이내 정리문장 3개, 각 <b> 강조 포함"],
  "next_week_note": "다음 주 이후 참고할 만한 한 줄"
}"""


def summarize(data: dict) -> str:
    lines = [f'지난주: {data["last_week"]["mon"]} ~ {data["last_week"]["fri"]}',
             f'이번주: {data["this_week"]["mon"]} ~ {data["this_week"]["fri"]}', ""]
    for key, r in data["series"].items():
        unit = "%" if r.get("unit") == "price" else "bp"
        value = "-" if r.get("value") is None else f'{r["value"]:,}'
        wow = "-" if r.get("wow_pct") is None else f'{r["wow_pct"]:+.2f}{unit}'
        ytd = "-" if r.get("ytd_pct") is None else f'{r["ytd_pct"]:+.2f}{unit}'
        lines.append(f'  {r.get("label", key)}: {value} (주간 {wow}, '
                     f'YTD {ytd}, {r.get("asof", "-")} 기준)')
    if data.get("missing"):
        lines.append(f'  [미확보] {", ".join(data["missing"])} — 언급하지 말 것')
    if data.get("stale"):
        names = ", ".join(x["label"] for x in data["stale"])
        lines.append(f'  [확인필요/오래됨] {names} — 정확한 최신 수치가 아닐 수 있으니 단정적으로 쓰지 말 것')
    return "\n".join(lines)


def call_api(prompt: str, key: str) -> str:
    body = {"model": MODEL, "max_tokens": 4000, "system": SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}]}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    last_error = "알 수 없는 오류"
    for attempt in range(3):
        try:
            r = requests.post(API, headers=headers, json=body, timeout=180)
            if r.status_code == 200:
                payload = r.json()
                text = "".join(b.get("text", "") for b in payload.get("content", [])
                               if b.get("type") == "text")
                if not text.strip():
                    raise ValueError("API 응답에 텍스트 블록이 없습니다")
                return text
            last_error = f"HTTP {r.status_code}: {r.text[:400]}"
            if r.status_code not in (408, 409, 429) and r.status_code < 500:
                break
        except (RequestException, ValueError) as exc:
            last_error = str(exc)
        if attempt < 2:
            time.sleep(2 ** attempt)
    sys.exit(f"API 호출 실패(3회 이내 재시도): {last_error}")


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        sys.exit("JSON을 찾지 못했습니다:\n" + text[:600])
    return json.loads(text[i:j + 1])


def pick_education(ref: dt.date) -> dict:
    """ISO 주차 기준으로 결정론적 로테이션 — 실행할 때마다 같은 주에는 같은 항목."""
    week_no = ref.isocalendar()[1]
    return EDU_LESSONS[(week_no - 1) % len(EDU_LESSONS)]


def quarter_end_note(ref: dt.date) -> dict:
    """분기 말까지 남은 일수를 계산해 안내 문구를 코드로 확정한다.
    (LLM이 날짜를 잘못 계산할 위험을 원천적으로 없앤다)"""
    q_end_month = ((ref.month - 1) // 3 + 1) * 3
    if q_end_month == 12:
        q_end = dt.date(ref.year, 12, 31)
    else:
        q_end = dt.date(ref.year, q_end_month + 1, 1) - dt.timedelta(days=1)
    d = (q_end - ref).days
    if d <= 30:
        body = (f"{q_end.month}월 {q_end.day}일 분기 마감이 {d}일 앞으로 다가왔습니다. "
                "Core-Satellite 비중이 목표 범위(±5%p)를 벗어난 고객이 있다면, "
                "이번 주 상담에서 리밸런싱을 함께 짚어보시기 좋습니다.")
        title = "분기 리밸런싱을 점검할 시점입니다"
    else:
        body = (f"다음 분기 마감({q_end.month}월 {q_end.day}일)까지는 아직 여유가 있습니다. "
                "다만 이번 주 큰 변동이 있었다면 목표 비중 이탈 여부를 가볍게 확인해두면 좋습니다.")
        title = "리밸런싱은 캘린더가 아니라 이탈 폭으로 판단합니다"
    return {"title": title, "body": body, "days_left": d, "quarter_end": q_end.isoformat()}


def validate(brief: dict, last_week: dict, this_week: dict) -> list:
    issues = []
    hl = brief.get("last_week_headlines", [])
    if len(hl) != 3:
        issues.append("지난주 헤드라인이 3건이 아님")
    lm, lf = dt.date.fromisoformat(last_week["mon"]), dt.date.fromisoformat(last_week["fri"])
    for i, h in enumerate(hl, 1):
        m = re.search(r"(\d{1,2})/(\d{1,2})", h.get("source", ""))
        if not m:
            issues.append(f"헤드라인 {i} 출처에 날짜(M/D) 없음")
            continue
        if md_date_in_range(h.get("source", ""), lm, lf) is None:
            issues.append(f"헤드라인 {i} 날짜({m.group(0)})가 지난주 범위({lm}~{lf}) 밖")
        if has_disallowed_markup(h.get("body", "")):
            issues.append(f"헤드라인 {i} 본문에 허용되지 않은 HTML 태그가 있음")

    tm, tf = dt.date.fromisoformat(this_week["mon"]), dt.date.fromisoformat(this_week["fri"])
    checkpoints = brief.get("checkpoints", [])
    if not checkpoints:
        issues.append("이번주 체크포인트가 없음")
    day_names = ["월", "화", "수", "목", "금", "토", "일"]
    for c in checkpoints:
        m = re.search(r"(\d{1,2})/(\d{1,2})", c.get("date", ""))
        if not m:
            issues.append(f"체크포인트 '{c.get('text','')[:20]}' 날짜 형식 없음")
            continue
        d = md_date_in_range(c.get("date", ""), tm, tf)
        if d is None:
            issues.append(f"체크포인트 날짜({m.group(0)})가 이번주 범위({tm}~{tf}) 밖")
        elif c.get("day") != day_names[d.weekday()]:
            issues.append(f"체크포인트 {m.group(0)}의 요일({c.get('day', '-')})이 실제 요일과 다름")
        if has_disallowed_markup(c.get("text", "")):
            issues.append(f"체크포인트 {m.group(0)}에 허용되지 않은 HTML 태그가 있음")

    quotes = brief.get("quotes", [])
    if len(quotes) != 3:
        issues.append("정리문장이 3줄이 아님")
    for i, q in enumerate(quotes, 1):
        plain = re.sub(r"<[^>]+>", "", q)
        if len(plain) > 45:
            issues.append(f"정리문장 {i}이 {len(plain)}자로 김 (45자 제한)")
        if "<b>" not in q:
            issues.append(f"정리문장 {i}에 <b> 강조 없음")
        if has_disallowed_markup(q):
            issues.append(f"정리문장 {i}에 허용되지 않은 HTML 태그가 있음")
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data_weekly.json")
    ap.add_argument("--out", default="brief_weekly.json")
    args = ap.parse_args()

    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY 환경변수가 필요합니다.")

    with open(args.data, encoding="utf-8") as fp:
        data = json.load(fp)
    ref = dt.date.fromisoformat(data["this_week"]["mon"])

    prompt = f"""[지난주 기간] {data['last_week']['mon']} ~ {data['last_week']['fri']}
[이번주 기간] {data['this_week']['mon']} ~ {data['this_week']['fri']}

[수집 데이터]
{summarize(data)}

[할 일]
1. 웹검색으로 [지난주 기간] 동안의 글로벌 금융시장 흐름을 확인하고, 퇴직연금
   자산배분에 영향이 있었던 사건 3가지를 고른다. 하루 뉴스가 아니라 "그 주에
   어떤 흐름이 있었는지"로 서술한다.
2. retrospective: 그 주의 이벤트들에도 불구하고 왜 원칙을 유지하는 것이 유효했는지
   위 [수집 데이터]의 실제 주간 등락 수치를 근거로 짧게 회고한다.
3. checkpoints: [이번주 기간] 월~금 요일별로 예정된 주요 지표·이벤트를 웹검색으로
   확인해 정리한다. 특히 중요한 날은 highlight를 true로 표시한다.
4. mindset_01: 이번 주 시장에서 특히 주목해야 할 변수 하나를 짚는다.
5. 정리문장 3개를 규격에 맞게 쓴다.
6. next_week_note: 다음 주 이후 참고할 만한 이벤트를 한 줄로.

아래 스키마의 JSON만 출력한다.
{SCHEMA}"""

    print("[생성] Claude API 호출 (웹검색 포함)...")
    brief = parse_json(call_api(prompt, key))

    # 이 두 항목은 LLM이 아니라 코드가 확정한다 (교육 콘텐츠 안정성 · 날짜 계산 정확성)
    brief["education"] = pick_education(ref)
    brief["rebalance_note"] = quarter_end_note(ref)

    issues = validate(brief, data["last_week"], data["this_week"])
    brief["_issues"] = issues

    atomic_write_json(args.out, brief)

    print(f"  · 지난주 헤드라인 {len(brief.get('last_week_headlines', []))}건 "
          f"· 체크포인트 {len(brief.get('checkpoints', []))}건")
    print(f"  · 이주의 투자교육: {brief['education']['title']}")
    print(f"  · 리밸런싱 안내: D-{brief['rebalance_note']['days_left']}")
    if issues:
        print("  ! 점검 사항:")
        for i in issues:
            print(f"    - {i}")
    else:
        print("  · 검증 통과")
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
