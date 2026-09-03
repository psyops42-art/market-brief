# -*- coding: utf-8 -*-
"""파이프라인 전 단계에서 공유하는 작은 안전성 헬퍼."""

from __future__ import annotations

import html
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any


_B_TAG = re.compile(r"&lt;(/?)b&gt;", re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]*>")


def safe_rich_text(value: Any) -> str:
    """외부 생성 문구에서 정확한 ``<b>`` 태그만 허용한다.

    웹검색 결과와 LLM 출력은 신뢰할 수 없는 입력이다. 먼저 모든 HTML을
    이스케이프한 뒤 속성 없는 b 태그만 복원해 스크립트/이벤트 속성 주입을 막는다.
    """
    escaped = html.escape(str(value or ""), quote=True)
    return _B_TAG.sub(lambda m: f"<{m.group(1).lower()}b>", escaped)


def safe_text(value: Any) -> str:
    """HTML 문맥에 넣을 일반 텍스트를 이스케이프한다."""
    return html.escape(str(value or ""), quote=True)


def strip_markup(value: Any) -> str:
    """아카이브/이미지용 평문을 만든다."""
    return html.unescape(_ANY_TAG.sub("", str(value or ""))).strip()


def has_disallowed_markup(value: Any) -> bool:
    """속성 없는 b 태그 외의 HTML 태그가 포함됐는지 검사한다."""
    text = re.sub(r"</?b>", "", str(value or ""), flags=re.IGNORECASE)
    return bool(_ANY_TAG.search(text))


def md_date_in_range(value: Any, start: date, end: date) -> date | None:
    """문자열의 M/D를 연말 경계를 포함한 기간 안의 날짜로 해석한다."""
    match = re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)", str(value or ""))
    if not match:
        return None
    month, day = map(int, match.groups())
    for year in range(start.year, end.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if start <= candidate <= end:
            return candidate
    return None


def atomic_write_text(path: str | os.PathLike[str], text: str) -> None:
    """중간 실패 때 반쪽짜리 산출물이 남지 않도록 원자적으로 저장한다."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fp:
            fp.write(text)
        os.replace(temp_path, target)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: str | os.PathLike[str], value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
