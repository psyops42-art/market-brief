# -*- coding: utf-8 -*-
"""
카카오 리프레시 토큰 최초 발급 (처음 한 번, 이후 만료 시에만)

Google Colab 이나 PC(Python 3)에서 실행합니다. GitHub Actions 에서 돌리는 스크립트가 아닙니다.
    1) 아래 REST_API_KEY 를 채우고 실행
    2) 출력된 주소를 휴대폰/PC 브라우저에서 열고 카카오 로그인 → '동의하고 계속하기'
    3) 이동된 페이지 주소창의 전체 URL(…?code=xxxx)을 복사해 입력칸에 붙여넣기
    4) 출력된 리프레시 토큰을 GitHub > Settings > Secrets and variables > Actions 의
       KAKAO_REFRESH_TOKEN 에 저장 (이 대화나 다른 곳에 붙여넣지 마세요)
"""
import json
import urllib.parse
import urllib.request

REST_API_KEY = "여기에_REST_API_키"
CLIENT_SECRET = ""          # 앱에서 Client Secret 을 켠 경우에만 입력
REDIRECT_URI = "https://psyops42-art.github.io/market-brief/"   # 카카오 로그인 > Redirect URI 에 똑같이 등록

auth = ("https://kauth.kakao.com/oauth/authorize?" + urllib.parse.urlencode(
    {"client_id": REST_API_KEY, "redirect_uri": REDIRECT_URI, "response_type": "code", "scope": "talk_message"}))
print("① 이 주소를 열고 로그인·동의하세요:\n", auth, "\n")
got = input("② 이동된 페이지의 전체 주소를 붙여넣으세요: ").strip()
code = urllib.parse.parse_qs(urllib.parse.urlparse(got).query).get("code", [got])[0]

data = {"grant_type": "authorization_code", "client_id": REST_API_KEY,
        "redirect_uri": REDIRECT_URI, "code": code}
if CLIENT_SECRET:
    data["client_secret"] = CLIENT_SECRET
req = urllib.request.Request("https://kauth.kakao.com/oauth/token",
                             data=urllib.parse.urlencode(data).encode(), method="POST")
tok = json.load(urllib.request.urlopen(req))
days = int(tok.get("refresh_token_expires_in", 0)) // 86400
print("\n③ 발급 완료 (유효기간 약", days, "일)")
print("KAKAO_REFRESH_TOKEN =", tok["refresh_token"])

# 확인용: 나에게 테스트 메시지 1건
msg = {"object_type": "text", "text": "주간 마켓 브리핑 카톡 연결 테스트입니다.",
       "link": {"web_url": REDIRECT_URI, "mobile_web_url": REDIRECT_URI}}
req = urllib.request.Request("https://kapi.kakao.com/v2/api/talk/memo/default/send",
                             data=urllib.parse.urlencode({"template_object": json.dumps(msg)}).encode(),
                             headers={"Authorization": "Bearer " + tok["access_token"]}, method="POST")
print("④ 테스트 메시지:", json.load(urllib.request.urlopen(req)))
