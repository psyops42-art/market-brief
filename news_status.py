"""Shared daily news status for the page, preview, report and notifications."""
LABELS = {'normal': '정상', 'partial': '일부 제공', 'failed': '생성 실패'}
NO_NEWS = '오늘은 뉴스 요약 없이 시장지표만 제공합니다'


def news_status(brief):
    headlines = brief.get('headlines') or []
    checkpoint = brief.get('checkpoint') or {}
    has_checkpoint = bool(checkpoint.get('sources')) and checkpoint.get('status') != 'unavailable'
    if not headlines and not has_checkpoint:
        return 'failed'
    if brief.get('_news_status') in ('partial', 'failed') or len(headlines) < 3 or not has_checkpoint:
        return 'partial'
    return 'normal'


def preview_description(brief):
    status = news_status(brief)
    if status == 'failed':
        return NO_NEWS
    if status == 'partial':
        return '뉴스 일부 제공 · 확인된 기사와 기준일별 시장지표를 제공합니다.'
    return brief.get('og_description', '')
