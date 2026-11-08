"""Decide whether an automatic morning run still needs to publish (KST)."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def should_run(kind, now, docs):
    now = now.astimezone(KST)
    weekly = kind == "weekly"
    start = dt.time(6) if weekly else dt.time(7, 20)
    if now.weekday() >= 5 or (weekly and now.weekday() != 0):
        return False, "예약 대상 요일이 아닙니다"
    if not start <= now.time() < dt.time(12):
        return False, "아침 실행 시간대 밖입니다"
    slug = ("weekly-" if weekly else "") + now.date().isoformat()
    report = docs / ("report_weekly.json" if weekly else "report.json")
    try:
        published = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        published = {}
    required = [docs / f"{slug}.html"]
    if weekly:
        required += [docs / "weekly.html"]
    else:
        required += [docs / "daily.html", docs / f"og-{slug}.png"]
    if published.get("slug") == slug and all(p.is_file() and p.stat().st_size for p in required):
        return False, f"{slug} 이미 배포됨 — 중복 생성·알림 생략"
    return True, f"{slug} 미배포 — 생성 필요"


def should_recover(now, docs):
    """One scheduled 08:00 recovery of today's degraded edition only."""
    now = now.astimezone(KST)
    if now.weekday() >= 5 or not dt.time(8) <= now.time() < dt.time(12):
        return False, "복구 실행 시간대 밖입니다"
    try:
        report = json.loads((docs / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "당일 상태 리포트 없음"
    if report.get("slug") != now.date().isoformat():
        return False, "당일 브리핑 아님"
    try:
        claim = json.loads((docs.parent / ".state/news-recovery.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        claim = {}
    if claim.get("date") == now.date().isoformat():
        return False, "오늘 뉴스 복구 이미 시도됨"
    if report.get("news_recovery_attempted"):
        return False, "오늘 뉴스 복구 이미 실행됨"
    state = report.get("news_status")
    # Migration: older reports record news generation issues without a status.
    degraded = state in ("partial", "failed") or (state is None and bool(report.get("brief_issues")))
    return degraded, "뉴스 복구 실행" if degraded else "뉴스 정상 — 복구 생략"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("daily", "weekly"), required=True)
    parser.add_argument("--docs", type=Path, default=Path("docs"))
    args = parser.parse_args()
    automatic = os.getenv("GITHUB_EVENT_NAME") == "schedule" or os.getenv("AUTOMATIC", "").lower() == "true"
    now = dt.datetime.now(KST)
    recovery = args.kind == "daily" and os.getenv("NEWS_RECOVERY", "").lower() == "true"
    run, reason = (should_recover(now, args.docs) if recovery else
                   should_run(args.kind, now, args.docs) if automatic else (True, "수동 실행"))
    if recovery and run:
        claim = args.docs.parent / ".state/news-recovery.json"
        claim.parent.mkdir(parents=True, exist_ok=True)
        claim.write_text(json.dumps({"date": now.date().isoformat(),
                                    "run_id": os.getenv("GITHUB_RUN_ID", "manual")}), encoding="utf-8")
    message = f"{now.isoformat()} | {reason}"
    print(message)
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"run={str(run).lower()}\n")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(message + "\n")


if __name__ == "__main__":
    main()
