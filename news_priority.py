"""Check coverage of large overnight moves without prescribing a company/event."""
import datetime as dt
import re
from zoneinfo import ZoneInfo

US_EQUITIES = re.compile(
    r"뉴욕|월가|나스닥|미국.{0,12}(?:증시|주식)|S\s*&\s*P|다우|"
    r"\b(?:Wall Street|Nasdaq|Dow|U\.?S\.? (?:stocks?|equities|markets?))\b", re.I)
UP = re.compile(r"급등|폭등|반등|상승|올랐|올라|뛰|\b(?:surge\w*|jump\w*|rall\w*|rebound\w*|gain\w*|higher)\b", re.I)
DOWN = re.compile(r"급락|폭락|하락|떨어|내렸|내려|\b(?:plung\w*|slump\w*|tumb\w*|fell|fall\w*|drop\w*|lower)\b", re.I)
EXCLUDE = re.compile(r"렌터카|전시회|박람회|축제|교통사고|\b(?:rental car|exhibition|festival)\b", re.I)
CORE = re.compile(r"증시|금리|국채|환율|물가|연준|유가|금값|코스피|나스닥|"
                  r"\b(?:stocks?|bonds?|yields?|rates?|inflation|Fed|oil|gold|Nasdaq|earnings)\b", re.I)


def requirements_for(data):
    """Only current, non-stale closing observations may trigger the guard."""
    stale = {x['key'] for x in data.get('stale', [])}
    signals = []
    for key, threshold in (('sp500', 2.0), ('ndx', 3.0), ('nasdaq_comp', 3.0)):
        rec = data.get('series', {}).get(key) or {}
        change = rec.get('pct')
        if (key not in stale and isinstance(change, (int, float))
                and rec.get('asof') == data.get('cutoff') and abs(change) >= threshold):
            signals.append((abs(change), key, change))
    if not signals:
        return []
    _, key, change = max(signals)
    return [{'market': 'us_equities', 'key': key, 'pct': change,
             'direction': 'up' if change > 0 else 'down', 'asof': data['cutoff']}]


def requirement_prompt(requirements):
    if not requirements:
        return ''
    r = requirements[0]
    move = '급등' if r['direction'] == 'up' else '급락'
    return (f"\n[필수 시장 핵심 뉴스]\n미국 증시 {r['asof']} 종가에서 {r['key']}가 "
            f"{r['pct']:+.2f}% 변동했습니다. 미국 증시 {move}와 관련된 최신 마감 보도를 "
            "별도로 검색하고, 원문으로 확인한 핵심 사건을 첫 헤드라인으로 다루세요. "
            "수치만 보고 원인을 추측하지 마세요. 토큰 주식·개별 금융상품 소개로 대신하지 마세요. "
            "다른 자산의 뉴스는 나머지 두 건에 배치하세요.\n")


def matches(item, requirement, evidence=None):
    if not isinstance(item, dict):
        return False
    session_start = dt.datetime.combine(dt.date.fromisoformat(requirement['asof']),
                                        dt.time(), ZoneInfo('America/New_York'))
    sources = item.get('sources') or [item]
    fresh = False
    for source in sources:
        if not isinstance(source, dict):
            continue
        try:
            published = dt.datetime.fromisoformat(source.get('published_at', ''))
            fresh |= published >= session_start
        except (TypeError, ValueError):
            continue
    if not fresh:
        return False
    text = ' '.join(str(item.get(k) or '') for k in ('title', 'body', 'summary'))
    direction = UP if requirement['direction'] == 'up' else DOWN
    if not direction.search(text):
        return False
    if US_EQUITIES.search(text):
        return True
    # A short Korean headline may name only the company; its verified source
    # must explicitly connect that story to the same market move.
    for source in item.get('sources', []):
        if not isinstance(source, dict):
            continue
        original = (evidence or {}).get(source.get('url'), {})
        source_text = str(original.get('title', '')) + ' ' + str(original.get('summary', ''))
        if US_EQUITIES.search(source_text) and direction.search(source_text):
            return True
    return False


def coverage_issues(brief, requirements, evidence=None):
    if not requirements:
        return []
    for item in brief.get('headlines', [])[:3]:
        if isinstance(item, dict) and item.get('status') != 'unavailable' and matches(item, requirements[0], evidence):
            return []
    return ['주요 시장 뉴스: 미국 증시의 큰 변동을 설명하는 최신 마감 보도가 누락됨']


def rank_key(candidate, requirements):
    text = str(candidate.get('title', '')) + ' ' + str(candidate.get('summary', ''))
    if EXCLUDE.search(text):
        return (0, 0)
    covered = int(bool(requirements and matches(candidate, requirements[0])))
    return (covered, len(CORE.findall(str(candidate.get('title', '')))))


def prioritize(items, evidence, requirements, start, end):
    """Promote a verified driver, or report the gap; never invent a driver."""
    if not requirements:
        return items
    if not coverage_issues({'headlines': [x for x in items[:3] if x]}, requirements, evidence):
        return sorted(items[:3], key=lambda x: bool(matches(x, requirements[0], evidence)), reverse=True) + [items[3]]
    candidates = []
    for candidate in evidence.values():
        try:
            published = dt.datetime.fromisoformat(candidate['published_at'])
            if (start <= published <= end and rank_key(candidate, requirements)[0]
                    and str(candidate['url']).startswith('https://')):
                candidates.append((published, candidate))
        except (KeyError, TypeError, ValueError):
            continue
    if candidates:
        _, chosen = max(candidates, key=lambda x: x[0])
        card = {'title': chosen['title'], 'body': chosen.get('summary') or chosen['title'],
                'sources': [{k: chosen[k] for k in ('name', 'url', 'published_at')}]}
        # Keep the global card count and domestic slot; discard duplicate URLs.
        remaining = [x for x in items[:3] if x and not any(
            s.get('url') == chosen['url'] for s in x.get('sources', []))]
        global_cards = ([card] + remaining)[:3]
        global_cards += [None] * (3 - len(global_cards))
        return global_cards + [items[3]]
    return items
