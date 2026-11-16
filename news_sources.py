"""Fetch dated publisher RSS candidates before asking the model to search."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from news_selection import market_score
import html
import re
import requests

FEEDS = (
    # Publisher XML channel titles verified: Finance, Economy, MarketWatch Breaking News.
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_bulletins"),
    ("BBC", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("연합뉴스", "https://www.yna.co.kr/rss/economy.xml"),
)


def parse_feed(xml, name, start, end):
    items = []
    for node in ET.fromstring(xml).findall(".//item"):
        try:
            published = parsedate_to_datetime(node.findtext("pubDate", ""))
            url = node.findtext("link", "").strip()
            title = node.findtext("title", "").strip()
            if (published.utcoffset() is None or not start <= published <= end
                    or urlsplit(url).scheme != "https" or not title):
                continue
            summary = html.unescape(re.sub(r"<[^>]*>", "", node.findtext("description", ""))).strip()
            items.append({"name": name, "url": url, "title": title, "summary": summary[:500],
                          "published_at": published.isoformat()})
        except (TypeError, ValueError):
            continue
    return items


def fetch_news_sources(start, end):
    def fetch(feed):
        name, url = feed
        try:
            response = requests.get(url, timeout=8, headers={"User-Agent": "market-brief/1.0"})
            response.raise_for_status()
            return parse_feed(response.content, name, start, end)
        except Exception as exc:
            print(f"  ! {name} 최신 기사 목록 조회 실패: {type(exc).__name__}")
            return []
    with ThreadPoolExecutor(max_workers=len(FEEDS)) as pool:
        groups = list(pool.map(fetch, FEEDS))
    # Bound prompt size while keeping each publisher represented.
    candidates = [item for group in groups for item in sorted(
        group, key=lambda x: (market_score(x), datetime.fromisoformat(x["published_at"])), reverse=True)[:30]]
    return {item["url"]: item for item in candidates}
