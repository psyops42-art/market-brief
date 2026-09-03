# -*- coding: utf-8 -*-
"""
5단계 · 알림 — 오늘 링크를 나에게 보내기

두 가지 경로를 지원하며, 설정된 것만 동작합니다.

  ① GitHub 이슈 생성  (설정 불필요)
     저장소에 이슈가 하나 만들어지고, GitHub이 알림 메일을 보냅니다.
     휴대폰에 GitHub 앱이 있으면 푸시 알림도 옵니다.
     → 워크플로에서 GH_TOKEN 이 자동으로 주어지므로 별도 준비가 없습니다.

  ② 이메일 직접 발송  (Secrets 3개 필요)
     MAIL_USER  보내는 Gmail 주소
     MAIL_PASS  Gmail 앱 비밀번호 (일반 비밀번호 아님)
     MAIL_TO    받을 주소 (쉼표로 여러 개 가능)
     → 세 개가 모두 없으면 조용히 건너뜁니다.

사용법
    python notify.py --report out/report.json --base https://... --run 42
    python notify.py ... --dry-run      # 보내지 않고 내용만 출력
"""

import argparse
import json
import os
import smtplib
import ssl
import subprocess
import sys
from email.message import EmailMessage


def load_report(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


def compose(rep, base, run, label="마켓 브리핑"):
    slug = rep.get("slug", "")
    unresolved = rep.get("unresolved", [])
    issues = rep.get("brief_issues", [])
    stale = rep.get("stale", [])
    delayed = rep.get("delayed", [])
    link = f"{base}/{slug}.html"

    needs_check = bool(unresolved or issues or stale)
    status = "확인 필요" if needs_check else "정상"
    subject = f"[{label}] {slug} 아침" + ("  ※확인필요" if needs_check else "")

    lines = [
        f"브리핑이 준비됐습니다.  ({status})",
        "",
        "▼ 카톡에 붙여넣을 링크",
        link,
        "",
        f"아카이브 : {base}/",
    ]
    if unresolved:
        lines += ["", "■ 화면에 '확인필요'로 표기된 항목 — 발송 전 직접 확인하세요",
                  *[f"  · {x}" for x in unresolved]]
    if stale:
        lines += ["", "■ 기준일보다 오래된 값 (허용범위 초과)", *[f"  · {x}" for x in stale]]
    if issues:
        lines += ["", "■ 생성 내용 자체 점검", *[f"  · {x}" for x in issues]]
    if delayed:
        lines += ["", "■ 해외지수 정상 지연 (참고용 · 조치 불필요)", *[f"  · {x}" for x in delayed]]
    if not needs_check:
        lines += ["", f"지표 {len(rep.get('collected', []))}종 모두 정상 수집됐습니다."]
    lines += ["", f"— 자동 생성 (run #{run})"]
    return subject, "\n".join(lines), link


def make_issue(subject, body):
    """gh CLI 로 이슈 생성. GitHub이 알림 메일을 대신 보내줍니다."""
    if not os.getenv("GH_TOKEN") and not os.getenv("GITHUB_TOKEN"):
        print("  · GH_TOKEN 없음 — 이슈 생성 건너뜀")
        return
    try:
        r = subprocess.run(["gh", "issue", "create", "--title", subject, "--body", body],
                           capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        print("  ! gh CLI가 없어 이슈 생성을 건너뜁니다")
        return
    except subprocess.TimeoutExpired:
        print("  ! 이슈 생성이 60초 안에 끝나지 않아 중단했습니다")
        return
    if r.returncode:
        print(f"  ! 이슈 생성 실패: {(r.stderr or r.stdout).strip()[:200]}")
    else:
        print(f"  · 이슈 생성 완료 → {r.stdout.strip()}")


def send_mail(subject, body):
    user, pw, to = (os.getenv("MAIL_USER"), os.getenv("MAIL_PASS"), os.getenv("MAIL_TO"))
    if not (user and pw and to):
        print("  · 메일 설정 없음 — 발송 건너뜀 (MAIL_USER/MAIL_PASS/MAIL_TO)")
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(body)
    host = os.getenv("MAIL_HOST", "smtp.gmail.com")
    port = int(os.getenv("MAIL_PORT", "465"))
    try:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as s:
            s.login(user, pw)
            s.send_message(msg)
        print(f"  · 메일 발송 완료 → {to}")
    except Exception as exc:                                    # noqa: BLE001
        print(f"  ! 메일 발송 실패: {exc}")
        print("    (Gmail은 '앱 비밀번호'가 필요합니다. 일반 비밀번호로는 로그인되지 않습니다)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="out/report.json")
    ap.add_argument("--base", required=True)
    ap.add_argument("--run", default="")
    ap.add_argument("--label", default="마켓 브리핑", help="메일 제목 머리표 (예: 주간 마켓 브리핑)")
    ap.add_argument("--no-issue", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rep = load_report(args.report)
    if not rep.get("slug"):
        sys.exit("report.json 을 읽지 못했습니다 — 렌더 단계가 실패했을 수 있습니다.")

    subject, body, link = compose(rep, args.base.rstrip("/"), args.run, args.label)
    if args.dry_run:
        print(f"제목: {subject}\n{'-' * 50}\n{body}")
        return

    print("[알림]")
    if not args.no_issue:
        make_issue(subject, body)
    send_mail(subject, body)
    print(f"  · 공유 링크 : {link}")


if __name__ == "__main__":
    main()
