"""Deterministic, conservative fallback selection. No model/network calls."""
import datetime as dt
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

EXCLUDE = re.compile(
    r"전시회|박람회|지역행사|축제|렌터카|렌트카|교통사고|수리비|대여료|살인|절도|화재|"
    r"수출\s*상담|스마트시티|생활\s*판결|내 대출|my mortgage|my pension|\b(?:exhibition|expo|festival|rental car|car rental|"
    r"traffic accident|murder|theft)\b", re.I)
CORE = re.compile(
    r"금리|국채|환율|증시|유가|연준|한은|한국은행|기준금리|인플레이션|소비자물가|"
    r"통화정책|코스피|코스닥|나스닥|관세|고용지표|국내총생산|경상수지|신용위험|"
    r"\b(?:interest rates?|treasur(?:y|ies)|bond yields?|stocks?|equities|inflation|"
    r"federal reserve|fed|ecb|monetary policy|oil|crude|tariffs?|payrolls?|gdp|cpi|"
    r"nasdaq|s&p 500|earnings)\b", re.I)
EVENT = re.compile(
    r"인상|인하|상승|하락|급등|급락|동결|전망|발표|실적|둔화|침체|긴축|완화|"
    r"\b(?:rise|rises|rising|fall|falls|falling|cut|cuts|hike|hikes|rally|rallies|"
    r"slump|surge|outlook|forecast|decision|report|earnings|recession|week ahead)\b", re.I)
GENERAL = re.compile(r"시장|수출|은행|\b(?:markets?|exports?|banks?)\b", re.I)
GLOBAL = {"CNBC", "BBC", "MarketWatch", "Yahoo Finance"}
DOMESTIC = {"연합뉴스"}


def market_score(item):
    title = item.get('title', '')
    if EXCLUDE.search(title + ' ' + item.get('summary', '')):
        return 0
    core = {m.group().lower() for m in CORE.finditer(title)}
    events = {m.group().lower() for m in EVENT.finditer(title)}
    # General words cannot make a candidate eligible, even in combination.
    if not core or (len(core) < 2 and not events):
        return 0
    return len(core) * 4 + min(len(events), 3) * 2 + min(len(GENERAL.findall(title)), 2)


def region(item):
    names = {item.get('name')} | {s.get('name') for s in item.get('sources', [])}
    if names & DOMESTIC:
        return 'domestic'
    if names & GLOBAL:
        return 'global'
    return None


def canonical_url(url):
    p = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith('utm_') and k not in ('ref', 'source')]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/'), urlencode(sorted(query)), ''))


def urls(item):
    return {canonical_url(s['url']) for s in item.get('sources', []) if s.get('url')} | ({canonical_url(item['url'])} if item.get('url') else set())


def title_key(item):
    title = unicodedata.normalize('NFKC', item.get('original_title', item.get('title', ''))).lower()
    title = re.sub(r'^\[[^]]+\]\s*', '', title)
    return re.sub(r'[^a-z0-9가-힣]', '', title)


def duplicate(item, selected):
    key = title_key(item)
    korean = bool(re.search('[가-힣]', key))
    for other in selected:
        if urls(item) & urls(other):
            return True
        other_key = title_key(other)
        if key and other_key and korean == bool(re.search('[가-힣]', other_key)):
            if SequenceMatcher(None, key, other_key).ratio() >= .78:
                return True
    return False


def ranked_candidates(evidence, start, end, target):
    candidates = []
    for item in evidence.values():
        try:
            published = dt.datetime.fromisoformat(item['published_at'])
            if published.utcoffset() is None or not start <= published <= end:
                continue
            if urlsplit(item['url']).scheme != 'https' or not urlsplit(item['url']).netloc:
                continue
            score = market_score(item)
            if region(item) == target and score >= 6:
                candidates.append((score, published, item))
        except (KeyError, TypeError, ValueError):
            continue
    return [x[2] for x in sorted(candidates, key=lambda x: (x[0], x[1]), reverse=True)]


def fallback_card(candidate):
    # No AI translation at the point where AI has already failed. English feeds
    # remain clearly marked original headlines, with no English summary body.
    english = not re.search('[가-힣]', candidate['title'])
    return {'title': candidate['title'], 'body': '' if english else candidate.get('summary', ''),
            'sources': [{k: candidate[k] for k in ('name', 'url', 'published_at')}],
            'original_title': candidate['title'], 'title_only': english or not candidate.get('summary'),
            'fallback': True}


def select_fallback(evidence, start, end, accepted=None):
    accepted = accepted or {}
    selected, headlines = [], []
    for label, card in accepted.items():
        if label.startswith('헤드라인') and not duplicate(card, selected):
            selected.append(card); headlines.append(card)
    checkpoint = accepted.get('국내 체크포인트')
    if checkpoint and duplicate(checkpoint, selected):
        checkpoint = None
    if checkpoint:
        selected.append(checkpoint)
    for candidate in ranked_candidates(evidence, start, end, 'global'):
        if len(headlines) >= 3:
            break
        if not duplicate(candidate, selected):
            card = fallback_card(candidate)
            selected.append(card); headlines.append(card)
    if checkpoint is None:
        for candidate in ranked_candidates(evidence, start, end, 'domestic'):
            if not duplicate(candidate, selected):
                checkpoint = fallback_card(candidate)
                break
    return headlines, checkpoint
