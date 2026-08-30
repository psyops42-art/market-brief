# GitHub Actions 완전 자동화 설계

작성: 2026-08-30 / 저장소 psyops42-art/market-brief / 최초 세팅 약 30분, 이후 무인 운영

---

## 1. 전체 구조

```
매일 22:00 UTC (= 07:00 KST) · 월~금 아침
│
├─ 1단계  fetch_data.py    시장지표 12종 수집        → data.json
│         · Yahoo Finance : 주가·환율·원자재
│         · FRED          : 미국채 3년/10년
│         · 한국은행 ECOS : 국고채 3년/10년
│
├─ 2단계  make_brief.py    헤드라인·코멘트 생성      → brief.json
│         · Claude API + 웹검색 도구
│         · data.json 수치를 근거로만 서술 (수치 창작 금지)
│
├─ 3단계  render.py        템플릿에 주입             → out/
│         · template.html → 2026-08-30.html
│         · make_og.py    → og-2026-08-30.png (1200×630)
│           대시보드 1·2페이지를 실제로 캡처해 폰 프레임으로 합성
│
└─ 4단계  build_index.py + git push
          · docs/ 로 복사, 아카이브 목록 재생성, 커밋·푸시
          · GitHub Pages 가 자동 배포
                    ↓
          https://psyops42-art.github.io/market-brief/2026-08-30.html
                    ↓
          카톡에 붙여넣기 → OG 썸네일 표시
```

---

## 2. 왜 이 데이터 소스인가

| 지표 | 소스 | 이유 |
|---|---|---|
| S&P500 · 나스닥100 · 코스피 | Yahoo Finance (`yfinance`) | 무료, 인증 불필요, 클라우드에서 안정적 |
| 달러인덱스 · 달러/원 · 금 · WTI | Yahoo Finance | 위와 동일 |
| 미국채 3년 · 10년 | FRED (미 세인트루이스 연준) | **공식 통계**, Yahoo에 3년물이 없음 |
| 국고채 3년 · 10년 | 한국은행 ECOS OpenAPI | **공식 통계**, 국내 유일한 공개 API |

**스크래핑을 쓰지 않았습니다.** 네이버·FunETF·ETF CHECK는 이용약관상 무단 수집이
제한되고, GitHub Actions 러너(미국 IP)에서 차단될 가능성도 높습니다.
위 세 곳은 모두 기관이 공개한 무료 API라 약관·안정성 문제가 없습니다.

---

## 3. 필요한 것

**GitHub Secrets 3개** (저장소 → Settings → Secrets and variables → Actions)

| 이름 | 발급처 | 비용 |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com | 종량제 (아래 참조) |
| `FRED_API_KEY` | fredaccount.stlouisfed.org/apikeys | 무료 |
| `ECOS_API_KEY` | ecos.bok.or.kr/api | 무료 |

**비용 추정** — 하루 1회, 웹검색 8회 + 4천 토큰 생성 기준으로 **월 2~4달러** 수준입니다.
GitHub Actions는 Public 저장소면 무료입니다.

---

## 4. 세팅 순서

**1) 저장소 준비**
```
git clone https://github.com/psyops42-art/market-brief.git
cd market-brief
```
아래 파일을 저장소 루트에 놓습니다.
```
fetch_data.py  make_brief.py  render.py  make_og.py  build_index.py
template.html
.github/workflows/daily-brief.yml
```

**2) Secrets 3개 등록** (위 표)

**3) Pages 켜기**
Settings → Pages → Deploy from a branch → `main` / `/docs`
공개 주소: `https://psyops42-art.github.io/market-brief`

**4) 첫 실행**
Actions 탭 → "데일리 마켓 브리핑" → **Run workflow** (수동 실행 버튼)

3~5분 뒤 `docs/` 에 파일이 생기고 Pages가 배포됩니다.

---

## 5. 실패에 대비한 장치

**수치 미확보 시** — 해당 행을 "확인필요"로 표기하고 나머지는 정상 생성합니다.
임의 추정값을 넣지 않습니다. 미확보 목록은 Actions 요약 화면에 표시됩니다.

**자체 점검** — `make_brief.py` 가 생성 결과를 검사합니다.
- 헤드라인 3건인가
- 각 출처에 날짜(M/D)가 있는가
- MINDSET 3개, 정리문장 3줄인가
- 필수 필드가 비지 않았는가

문제가 있으면 Actions 요약에 항목별로 출력됩니다. **배포는 막지 않습니다.**
발송 전에 눈으로 확인하는 것이 전제입니다.

**실행 실패 시** — `data.json`, `brief.json`, `out/` 이 아티팩트로 7일간 보관돼
원인을 추적할 수 있습니다.

**변경 없음 처리** — 휴장일 등으로 내용이 같으면 커밋을 건너뜁니다.

---

## 6. 스케줄 해설

```yaml
- cron: "0 22 * * 0-4"     # UTC 일~목 = KST 월~금
```

| UTC | KST | 반영되는 마감 |
|---|---|---|
| 일 22:00 | **월 07:00** | **지난 금요일** 뉴욕·국내 마감 |
| 월 22:00 | 화 07:00 | 월요일 뉴욕·국내 마감 |
| 목 22:00 | 금 07:00 | 목요일 뉴욕·국내 마감 |

월요일 아침에는 자동으로 직전 영업일(금요일) 종가가 잡힙니다. yfinance·FRED·ECOS
모두 마지막 관측치를 돌려주므로 별도 분기 처리가 필요 없고, 기준일은 각 행에 표기됩니다.

뉴욕 정규장 마감이 05:00 KST(서머타임 기준)이므로 07:00이면 전일 종가가 모두 확정됩니다.
국내 공휴일에는 코스피 값이 전 거래일과 같게 나오므로, 기준일 표기로 구분됩니다.

> GitHub Actions의 cron은 부하에 따라 **5~30분 지연될 수 있습니다.**
> 정시 발송이 중요하면 `0 21 * * 0-4`(06:00 KST)로 앞당기고 여유를 두세요.

---

## 7. 매일 하는 일

**아무것도 안 해도 됩니다.** 07:00에 자동으로 배포됩니다.

다만 첫 2~3주는 아침에 링크를 열어 아래만 확인하시길 권합니다.
- 기준일 표기가 맞는가
- "확인필요"로 빠진 항목이 있는가
- 헤드라인의 출처·날짜가 붙어 있는가

내용을 고치고 싶으면 `docs/2026-08-29.html` 을 직접 수정해 커밋하면 됩니다.

---

## 8. 디자인을 바꾸고 싶을 때

`template.html` / `og_template.html` 만 고치세요. 파이썬 파일은 건드릴 필요가 없습니다.

토큰 자리에 값이 들어갑니다.

| 토큰 | 내용 |
|---|---|
| `{{TITLE}}` `{{DATE_LINE}}` | 제목·기준일 |
| `{{NEWS}}` `{{MINDSET}}` `{{QUOTES}}` | 카드 블록 |
| `{{TBL_EQUITY}}` `{{TBL_RATES}}` `{{TBL_FX}}` | 지표 표 3개 |
| `{{ONELINE_MARKET}}` `{{ONELINE_PENSION}}` `{{NEXT}}` `{{FOOTNOTE}}` | 하단 |
| `{{OG_URL}}` `{{OG_IMAGE}}` `{{OG_DESC}}` | 링크 미리보기 |

썸네일 레이아웃(폰 배치·KPI·색상)은 `make_og.py` 안에 있습니다.

---

## 9. 반드시 짚고 갈 점

**저장소가 Public이면 브리핑이 인터넷에 공개됩니다.** 검색엔진에도 노출될 수 있습니다.
퇴직연금 상담자료를 공개 배포하는 것이 준법감시 기준에 맞는지 먼저 확인하세요.
현재 대시보드에는 회사명이 들어가 있지 않고 하단에 원금손실 고지가 있지만,
그것만으로 대외자료 심의 요건이 충족되는지는 별개입니다.

**자동 생성물을 검토 없이 대외 공유하지 마세요.** 헤드라인은 AI가 웹검색으로 고른
것이라 맥락 오독이나 오래된 기사 선택이 있을 수 있습니다. 자체 점검 장치를 넣었지만
사람의 확인을 대체하지는 못합니다.

**API 키가 저장소 코드에 들어가지 않게 하세요.** 반드시 Secrets로만 넣습니다.
Public 저장소에 키가 커밋되면 즉시 유출됩니다.
