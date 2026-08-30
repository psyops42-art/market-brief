# -*- coding: utf-8 -*-
"""
4단계 · 아카이브 목록(docs/index.html) 생성

    python build_index.py --base https://user.github.io/repo

docs/ 안의 YYYY-MM-DD.html 을 훑어 최신순 목록을 만듭니다.
GitHub Actions와 로컬 양쪽에서 같은 결과가 나오도록 publish.py 와 로직을 공유합니다.
"""

import argparse
import os
import re
from datetime import date

DOCS = "docs"


def read_meta(path):
    html = open(path, encoding="utf-8").read()
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    line = re.search(r'<span class="k">시장</span>(.*?)</p>', html, re.S)
    clean = lambda t: re.sub(r"<[^>]+>", "", t).strip() if t else ""
    return {"title": clean(title.group(1)) if title else os.path.basename(path),
            "line": clean(line.group(1)) if line else ""}


INDEX_TPL = """<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#8FBBDD">
<title>데일리 마켓 브리핑 · 아카이브</title>
<meta property="og:type" content="website">
<meta property="og:locale" content="ko_KR">
<meta property="og:title" content="데일리 마켓 브리핑 · 아카이브">
<meta property="og:description" content="퇴직연금 담당자를 위한 매일 아침 금융시장 브리핑 모음">
<meta property="og:url" content="__BASE__/">
<meta property="og:image" content="__LATEST_OG__">
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{background:#DFE4E1;font-family:'Noto Sans KR','Apple SD Gothic Neo',sans-serif;color:#2b2f33;line-height:1.5}
.app{max-width:520px;margin:0 auto;background:#F4F6F4;min-height:100vh}
.band{background:linear-gradient(100deg,#AFA2E6 0%,#95B6DE 42%,#5FD3B0 100%);
  padding:calc(16px + env(safe-area-inset-top)) 20px 24px 20px}
.band .e{font-size:12px;color:rgba(255,255,255,.88);font-weight:500}
.band h1{font-size:26px;font-weight:900;color:#fff;letter-spacing:-1.2px;margin-top:3px}
.band .d{font-size:12.5px;color:rgba(255,255,255,.94);margin-top:6px}
.sheet{padding:18px 14px calc(24px + env(safe-area-inset-bottom)) 14px}
a.card{display:block;background:#fff;border:1px solid #E3E7E3;border-radius:13px;
  padding:14px 15px;margin-bottom:9px;text-decoration:none;color:inherit}
a.card.new{border:1.5px solid #63D3B2;background:#F4FBF8}
.dt{font-size:11px;font-weight:800;color:#3FB99A;letter-spacing:.5px}
.tt{font-size:15px;font-weight:800;letter-spacing:-.6px;margin-top:4px;color:#1d2226}
.ln{font-size:12.5px;color:#5a636b;margin-top:6px;line-height:1.6;letter-spacing:-.3px}
.go{font-size:11.5px;font-weight:800;color:#6B57C4;margin-top:9px}
.empty{text-align:center;color:#98a1a8;font-size:13px;padding:40px 0}
.foot{margin-top:14px;padding-top:14px;border-top:1px solid #E3E7E3;
  display:flex;justify-content:space-between;align-items:center}
.logo{font-size:14px;font-weight:900;color:#1F4E9C}
.logo span{font-size:10.5px;color:#5a636b;font-weight:700;margin-left:4px}
.cnt{font-size:10.5px;color:#a8b0b6}
</style></head><body><div class="app">
  <div class="band">
    <div class="e">퇴직연금 · 데일리 마켓</div>
    <h1>브리핑 아카이브</h1>
    <div class="d">매일 아침 금융시장 정리 · 최근 업데이트 __UPDATED__</div>
  </div>
  <div class="sheet">
__CARDS__
    <div class="foot">
      <div class="logo">MORNING BRIEF <span>작성 PHILIP</span></div>
      <div class="cnt">총 __COUNT__건</div>
    </div>
  </div>
</div></body></html>
"""


def build_index(cfg: dict):
    docs = os.path.join(cfg["local"], DOCS)
    files = sorted([f for f in os.listdir(docs)
                    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}\.html", f)], reverse=True)
    cards = []
    for i, f in enumerate(files):
        slug = f[:-5]
        meta = read_meta(os.path.join(docs, f))
        cls = "card new" if i == 0 else "card"
        badge = "최신" if i == 0 else slug.replace("-", ".")
        cards.append(
            f'    <a class="{cls}" href="{f}">\n'
            f'      <div class="dt">{badge}</div>\n'
            f'      <div class="tt">{meta["title"]}</div>\n'
            f'      <div class="ln">{meta["line"][:110]}</div>\n'
            f'      <div class="go">브리핑 열기 →</div>\n'
            f'    </a>')
    body = "\n".join(cards) if cards else '    <div class="empty">아직 등록된 브리핑이 없습니다.</div>'
    latest_og = f'{cfg["base"]}/og-{files[0][:-5]}.png' if files else ""
    html = (INDEX_TPL.replace("__CARDS__", body)
                     .replace("__COUNT__", str(len(files)))
                     .replace("__UPDATED__", date.today().strftime("%Y.%m.%d"))
                     .replace("__BASE__", cfg["base"])
                     .replace("__LATEST_OG__", latest_og))
    with open(os.path.join(docs, "index.html"), "w", encoding="utf-8") as fp:
        fp.write(html)
    return len(files)




if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--local", default=".")
    a = ap.parse_args()
    n = build_index({"base": a.base, "local": a.local})
    print(f"아카이브 재생성 : 총 {n}건")
