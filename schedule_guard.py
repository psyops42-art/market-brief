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
    if not weekly:
        required += [docs / "daily.html", docs / f"og-{slug}.png"]
    if published.get("slug") == slug and all(p.is_file() and p.stat().st_size for p in required):
        return False, f"{slug} 이미 배포됨 — 중복 생성·알림 생략"
    return True, f"{slug} 미배포 — 생성 필요"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("daily", "weekly"), required=True)
    parser.add_argument("--docs", type=Path, default=Path("docs"))
    args = parser.parse_args()
    automatic = os.getenv("GITHUB_EVENT_NAME") == "schedule" or os.getenv("AUTOMATIC", "").lower() == "true"
    now = dt.datetime.now(KST)
    run, reason = should_run(args.kind, now, args.docs) if automatic else (True, "수동 실행")
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
