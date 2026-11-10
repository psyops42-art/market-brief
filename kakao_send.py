# -*- coding: utf-8 -*-
"""
6단계 · 카카오톡 '나에게 보내기' — 대시보드 + 1분 영상 카드 1장

    python kakao_send.py --report out/report_weekly.json --brief brief_weekly.json \
        --base https://psyops42-art.github.io/market-brief --run 42 [--dry-run]

내 카톡 '나와의 채팅'으로 썸네일 카드가 옵니다. 내용을 확인한 뒤 고객방·단톡방에 [전달]하면 됩니다.
(카카오 정책상 개인 앱은 다른 사람이나 단톡방으로 직접 보낼 수 없습니다.)

필요한 Secrets
    KAKAO_REST_API_KEY    카카오 디벨로퍼스 > 내 애플리케이션 > 앱 키 > REST API 키
    KAKAO_REFRESH_TOKEN   kakao_auth.py 로 처음 한 번 발급 (약 2개월 유효)
    KAKAO_CLIENT_SECRET   (선택) 앱에서 Client Secret 을 켠 경우에만
    GH_PAT                (선택) 리프레시 토큰이 갱신될 때 Secret 을 자동으로 바꿔 넣는 용도
                          — fine-grained 토큰, 이 저장소의 Secrets: Read and write 권한만

설정이 없으면 조용히 건너뜁니다. 실패해도 배포는 이미 끝난 상태이므로 워크플로를 멈추지 않습니다.
토큰 값은 로그에 절대 출력하지 않습니다.
"""

import argparse
import json
import os
import subprocess
import sys
import time

import requests

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"


def mask(value):
    if value and os.getenv("GITHUB_ACTIONS"):
        print(f"::add-mask::{value}")


def refresh_access_token(rest_key, refresh_token, client_secret=None):
    data = {"grant_type": "refresh_token", "client_id": rest_key, "refresh_token": refresh_token}
    if client_secret:
        data["client_secret"] = client_secret
    r = requests.post(TOKEN_URL, data=data, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"토큰 갱신 실패 HTTP {r.status_code}: {r.text[:200]}")
    j = r.json()
    mask(j.get("access_token")); mask(j.get("refresh_token"))
    return j["access_token"], j.get("refresh_token"), j.get("refresh_token_expires_in")


def rotate_secret(new_refresh):
    """카카오가 새 리프레시 토큰을 주면(만료 1개월 이내) GitHub Secret 을 바꿔 둔다."""
    pat = os.getenv("GH_PAT")
    if not pat:
        return False, "GH_PAT 없음"
    env = dict(os.environ, GH_TOKEN=pat)
    r = subprocess.run(["gh", "secret", "set", "KAKAO_REFRESH_TOKEN", "--body", new_refresh],
                       capture_output=True, text=True, env=env, timeout=60)
    return r.returncode == 0, (r.stderr or "").strip()[:200]


def wait_until_live(urls, timeout=420):
    """GitHub Pages 반영(보통 1~3분)을 기다린다. 카톡이 썸네일을 가져갈 때 404가 나지 않도록."""
    t0 = time.time()
    pending = list(urls)
    while pending and time.time() - t0 < timeout:
        nxt = []
        for u in pending:
            try:
                ok = requests.head(u, timeout=10, allow_redirects=True).status_code == 200
            except requests.RequestException:
                ok = False
            if not ok:
                nxt.append(u)
        pending = nxt
        if pending:
            time.sleep(15)
    return pending


def build_template(rep, brief, base, run):
    slug = rep.get("slug", "")
    page = f"{base}/{rep.get('page') or 'weekly.html'}"
    lw = rep.get("last_week") or {}
    title = "주간 마켓 브리핑"
    if lw.get("mon") and lw.get("fri"):
        m, d1 = int(lw["mon"][5:7]), int(lw["mon"][8:10])
        d2 = int(lw["fri"][8:10])
        title += f" | {m}월 {d1}일~{d2}일 정리"
    desc = str((brief or {}).get("og_description") or "지난주 시장과 이번 주 일정을 정리했습니다.")
    video = rep.get("video") or {}
    if video.get("ok"):
        desc = "▶ 1분 영상 포함 · " + desc
    link = {"web_url": page, "mobile_web_url": page}
    vlink = {"web_url": page + "#video", "mobile_web_url": page + "#video"}
    tpl = {
        "object_type": "feed",
        "content": {
            "title": title[:60],
            "description": desc[:120],
            "image_url": f"{base}/og-{slug}.png?v={run}",
            "image_width": 1200, "image_height": 630,
            "link": link,
        },
        "buttons": ([{"title": "영상 브리핑", "link": vlink}] if video.get("ok") else [])
                   + [{"title": "대시보드 보기", "link": link}],
    }
    return tpl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="out/report_weekly.json")
    ap.add_argument("--brief", default="brief_weekly.json")
    ap.add_argument("--base", required=True)
    ap.add_argument("--run", default="0")
    ap.add_argument("--out", default="out/kakao_report.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-wait", action="store_true")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    status = {"sent": False, "skipped": "", "error": "", "token_rotated": None}

    def finish():
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fp:
            json.dump(status, fp, ensure_ascii=False, indent=2)

    with open(args.report, encoding="utf-8") as fp:
        rep = json.load(fp)
    brief = {}
    if os.path.exists(args.brief):
        with open(args.brief, encoding="utf-8") as fp:
            brief = json.load(fp)
    tpl = build_template(rep, brief, base, args.run)

    if args.dry_run:
        print(json.dumps(tpl, ensure_ascii=False, indent=2))
        return

    rest, refresh = os.getenv("KAKAO_REST_API_KEY"), os.getenv("KAKAO_REFRESH_TOKEN")
    if not (rest and refresh):
        status["skipped"] = "KAKAO_REST_API_KEY / KAKAO_REFRESH_TOKEN 없음"
        print(f"  · 카톡 발송 건너뜀 — {status['skipped']}")
        finish(); return

    print("[카톡] 나에게 보내기")
    try:
        access, new_refresh, _ = refresh_access_token(rest, refresh, os.getenv("KAKAO_CLIENT_SECRET"))
        if new_refresh and new_refresh != refresh:
            ok, why = rotate_secret(new_refresh)
            status["token_rotated"] = "자동 갱신" if ok else f"갱신 필요({why})"
            print(f"  · 리프레시 토큰 {'자동 갱신 완료' if ok else '이 곧 만료됩니다 — kakao_auth.py 로 다시 발급하세요'}")

        if not args.no_wait:
            urls = [tpl["content"]["image_url"].split("?")[0], f"{base}/{rep.get('page') or 'weekly.html'}"]
            if (rep.get("video") or {}).get("ok"):
                urls.append(f"{base}/{rep['video']['file']}")
            left = wait_until_live(urls)
            if left:
                print(f"  ! Pages 반영 대기 시간 초과 — 그래도 보냅니다 (미반영: {', '.join(left)})")

        r = requests.post(MEMO_URL, headers={"Authorization": f"Bearer {access}"},
                          data={"template_object": json.dumps(tpl, ensure_ascii=False)}, timeout=20)
        if r.status_code == 200 and r.json().get("result_code") == 0:
            status["sent"] = True
            print("  · 카톡 '나와의 채팅'으로 발송 완료 — 확인 후 [전달]하세요")
        else:
            raise RuntimeError(f"발송 실패 HTTP {r.status_code}: {r.text[:200]}")
    except Exception as exc:                                     # noqa: BLE001
        status["error"] = str(exc)[:300]
        print(f"  ! {status['error']}")
        if "KOE" in status["error"] or "invalid_grant" in status["error"]:
            print("    → 리프레시 토큰이 만료됐을 수 있습니다. kakao_auth.py 로 다시 발급해 Secret 을 바꿔 주세요.")
    finish()
    if status["error"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
