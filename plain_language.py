# -*- coding: utf-8 -*-
"""쉬운 말 다듬기 — 데일리·주간 공통 (헤드라인·투자마인드).

독자는 투자 초보인 40~50대 퇴직연금 가입자다. 1차 생성 프롬프트에 규칙을 넣어도
모델이 길거나 어려운 문장을 낼 때가 있어, 생성 직후 한 번 더 점검한다.

    1. readability_issues(): 글자 수 상한 초과, 풀어 쓰지 않은 전문용어를 찾는다.
    2. simplify(): 문제가 있으면 웹검색 없이 해당 문구만 쉬운 말로 다시 쓰게 한다.
       · 새 문장에 원문에 없던 숫자가 생기면 그 항목은 버린다 (사실 보호).
       · <b> 외 태그, 빈 문자열, 상한보다 크게 긴 문장도 버린다.
       · API 오류·형식 오류면 원문을 그대로 둔다 — 다듬기 실패가 배포를 막지 않는다.
    출처·날짜·URL 필드는 절대 건드리지 않는다.
"""

from __future__ import annotations

import json
import re
import time

import requests
from requests.exceptions import RequestException

from pipeline_utils import has_disallowed_markup

API = "https://api.anthropic.com/v1/messages"

# (필드 경로, 글자 수 상한) — 상한은 <b> 태그를 뺀 글자 수
TITLE_MAX, BODY_MAX, MIND_MAX = 25, 90, 110

# 괄호 설명 없이 쓰면 초보 독자가 막히는 용어
JARGON = ("스프레드", "컨센서스", "탈동조화", "디커플링", "자본지출", "캐펙스", "capex",
          "베이시스포인트", "bp", "듀레이션", "밸류에이션", "리스크오프", "리스크온",
          "숏커버링", "테이퍼링", "피벗", "모멘텀", "펀더멘털", "익스포저", "헤지")

SYSTEM = """당신은 금융 기사를 투자 초보 40~50대가 읽기 쉽게 고치는 편집자다.
· 의미와 사실은 그대로 두고 표현만 쉽게 바꾼다. 새 사실·전망·숫자를 추가하지 않는다.
· 원문에 있는 숫자만 쓸 수 있다. 숫자는 꼭 필요한 1~2개만 남긴다.
· 전문용어는 쉬운 말로 바꾸거나 괄호로 한 번 풀어 쓴다.
· 한 문장에는 한 가지 내용만. "~입니다/~습니다" 존댓말.
· 강조는 <b> 태그만 쓸 수 있다.
· 오직 JSON 객체만 출력한다."""

_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_TAG = re.compile(r"<[^>]+>")


def plain_len(text) -> int:
    return len(_TAG.sub("", str(text or "")).strip())


def _numbers(text) -> set:
    return {n.replace(",", "") for n in _NUM.findall(_TAG.sub("", str(text or "")))}


def editable_fields(brief: dict, kind: str) -> dict:
    """{경로: (현재 문구, 상한)} — 쉬운 말 다듬기 대상만."""
    out = {}

    def add(path, item, key, limit):
        if isinstance(item, dict) and isinstance(item.get(key), str) and item[key].strip():
            out[path] = (item[key], limit)

    if kind == "daily":
        for i, h in enumerate(brief.get("headlines") or [], 1):
            add(f"headlines.{i}.title", h, "title", TITLE_MAX)
            add(f"headlines.{i}.body", h, "body", BODY_MAX)
        cp = brief.get("checkpoint")
        if not (isinstance(cp, dict) and cp.get("status") == "unavailable"):
            add("checkpoint.title", cp, "title", TITLE_MAX)
            add("checkpoint.body", cp, "body", BODY_MAX)
        for i, m in enumerate(brief.get("mindset") or [], 1):
            add(f"mindset.{i}.title", m, "title", TITLE_MAX)
            add(f"mindset.{i}.body", m, "body", MIND_MAX)
    else:
        for i, h in enumerate(brief.get("last_week_headlines") or [], 1):
            add(f"last_week_headlines.{i}.title", h, "title", TITLE_MAX)
            add(f"last_week_headlines.{i}.body", h, "body", BODY_MAX)
        for key in ("retrospective", "mindset_01"):
            add(f"{key}.title", brief.get(key), "title", TITLE_MAX)
            add(f"{key}.body", brief.get(key), "body", MIND_MAX)
    return out


def _set(brief: dict, path: str, value: str) -> None:
    parts = path.split(".")
    node = brief[parts[0]]
    if len(parts) == 3:
        node = node[int(parts[1]) - 1]
    node[parts[-1]] = value


def _jargon(text: str) -> list:
    plain = _TAG.sub("", text)
    found = []
    for word in JARGON:
        for m in re.finditer(re.escape(word), plain, re.I):
            if word == "bp" and re.search(r"[A-Za-z]", plain[max(0, m.start() - 1):m.start()]):
                continue
            after = plain[m.end():m.end() + 2]
            if not after.startswith("("):          # 바로 뒤에 괄호 설명이 없으면 문제
                found.append(word)
                break
    return found


def readability_issues(brief: dict, kind: str) -> list:
    issues = []
    for path, (text, limit) in editable_fields(brief, kind).items():
        n = plain_len(text)
        if n > limit:
            issues.append(f"{path}: {n}자로 김 (권장 {limit}자 이내)")
        words = _jargon(text)
        if words:
            issues.append(f"{path}: 전문용어 '{', '.join(words)}' 풀이 없음")
    return issues


def _call(prompt: str, key: str, model: str) -> str:
    body = {"model": model, "max_tokens": 2500, "system": SYSTEM,
            "messages": [{"role": "user", "content": prompt}]}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    last = "알 수 없는 오류"
    for attempt in range(2):
        try:
            r = requests.post(API, headers=headers, json=body, timeout=90)
            if r.status_code == 200:
                return "".join(b.get("text", "") for b in r.json().get("content", [])
                               if b.get("type") == "text")
            last = f"HTTP {r.status_code}"
        except (RequestException, ValueError) as exc:
            last = str(exc)
        time.sleep(2)
    raise RuntimeError(last)


def _parse(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("JSON 없음")
    data = json.loads(text[i:j + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON 객체 아님")
    return data


def simplify(brief: dict, kind: str, key: str, model: str, caller=None) -> list:
    """문제 있는 문구만 쉬운 말로 다시 쓴다. 반환값: 로그용 메모 목록. brief는 제자리 수정."""
    fields = editable_fields(brief, kind)
    flagged = sorted({i.split(":")[0] for i in readability_issues(brief, kind)})
    if not flagged:
        return []
    # 같은 카드의 제목·본문을 함께 보내야 문맥이 유지된다.
    cards = {p.rsplit(".", 1)[0] for p in flagged}
    targets = {p: fields[p] for p in fields if p.rsplit(".", 1)[0] in cards}
    request = {p: {"text": t, "max_chars": lim} for p, (t, lim) in targets.items()}
    prompt = ("아래 각 문구를 쉬운 말로 다시 써라. 키는 그대로 두고 값은 고친 문자열만 넣는다.\n"
              "max_chars는 <b> 태그를 뺀 최대 글자 수다. 제목은 제목만 읽어도 이해되게 쓴다.\n"
              "본문은 제목을 보충하는 요약이다. 원문에 없는 숫자·사실을 넣지 않는다.\n\n"
              + json.dumps(request, ensure_ascii=False, indent=1)
              + '\n\n출력 형식: {"경로": "고친 문구", ...}')
    try:
        rewritten = _parse((caller or _call)(prompt, key, model))
    except Exception as exc:                       # 다듬기 실패는 배포를 막지 않는다
        return [f"쉬운 말 다듬기 생략: {type(exc).__name__}: {exc}"]

    notes = []
    for path, (orig, limit) in targets.items():
        new = rewritten.get(path)
        if not isinstance(new, str) or not new.strip() or new.strip() == orig:
            continue
        new = new.strip()
        if has_disallowed_markup(new):
            notes.append(f"{path}: 허용되지 않은 태그 — 원문 유지")
        elif not _numbers(new) <= _numbers(orig):
            notes.append(f"{path}: 원문에 없는 숫자 {sorted(_numbers(new) - _numbers(orig))} — 원문 유지")
        elif plain_len(new) > max(limit * 1.2, plain_len(orig)):
            notes.append(f"{path}: 다듬은 문장이 더 김 — 원문 유지")
        else:
            _set(brief, path, new)
            notes.append(f"{path}: 쉬운 말로 다듬음 ({plain_len(orig)}→{plain_len(new)}자)")
    return notes
