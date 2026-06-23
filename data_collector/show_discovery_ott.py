"""
OTT 랭킹에서 현재 방영중 한국 콘텐츠 발견.
Netflix TOP10 TSV (KR 필터) + Tving 랭킹 Playwright.
"""
import csv
import io
import re
import sys
import logging

csv.field_size_limit(min(sys.maxsize, 2147483647))

import requests

log = logging.getLogger(__name__)
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
NETFLIX_TSV = 'https://top10.netflix.com/all-weeks-tv.tsv'

RERUN_KEYWORDS = ['재방', '총집편', '다시보기', '클래식']


def _is_rerun(title: str) -> bool:
    return any(k in title for k in RERUN_KEYWORDS)


def _has_korean(text: str) -> bool:
    return bool(re.search(r'[가-힣]', text))


def _infer_category(title: str) -> str:
    if any(k in title for k in ['연애', '솔로', '커플', '결혼', '하트']):
        return 'romance'
    if any(k in title for k in ['서바이벌', '경쟁', '피지컬', '배틀']):
        return 'survival'
    return 'variety'


def scan_netflix_kr() -> list[dict]:
    """
    Netflix TOP10 TSV에서 한국어 타이틀만 추출.
    한국어 문자 포함 여부로 한국 콘텐츠 판별.
    Returns: [{name, channel, category, clip_count_7d(=rank score), source}]
    """
    try:
        resp = requests.get(
            NETFLIX_TSV, headers={'User-Agent': USER_AGENT}, timeout=15
        )
        resp.raise_for_status()
    except Exception as e:
        log.warning(f'Netflix TSV 다운로드 실패: {e}')
        return []

    try:
        reader = csv.DictReader(io.StringIO(resp.text), delimiter='\t')
        rows = list(reader)
    except Exception as e:
        log.warning(f'Netflix TSV 파싱 실패: {e}')
        return []

    # 가장 최신 주차 데이터만 사용
    weeks = sorted({r.get('week', '') for r in rows if r.get('week')}, reverse=True)
    latest_week = weeks[0] if weeks else None

    seen: dict[str, dict] = {}
    for row in rows:
        if latest_week and row.get('week') != latest_week:
            continue

        title = row.get('show_title', '') or row.get('season_title', '')
        if not title or not _has_korean(title):
            continue
        if _is_rerun(title):
            continue

        rank_str = row.get('weekly_rank', '')
        try:
            rank = int(rank_str)
        except (ValueError, TypeError):
            continue

        if rank > 10:
            continue

        if title not in seen:
            seen[title] = {
                'name': title,
                'channel': 'Netflix',
                'category': _infer_category(title),
                'clip_count_7d': 11 - rank,  # rank 1 = 10, rank 10 = 1
                'source': 'netflix_top10',
                'latest_episode': None,
                'season': None,
            }

    log.info(f'Netflix KR: {len(seen)}개 발견 (주차: {latest_week})')
    return list(seen.values())


def scan_tving() -> list[dict]:
    """
    Tving 랭킹 페이지에서 현재 인기 한국 콘텐츠 추출.
    Returns: [{name, channel, category, clip_count_7d, source}]
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning('playwright 미설치 — Tving 스캔 건너뜀')
        return []

    results = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto('https://www.tving.com/ranking/content', timeout=20000)
            page.wait_for_load_state('networkidle', timeout=15000)

            items = page.query_selector_all('.ranking-item .title, [class*="ranking"] [class*="title"]')
            for i, el in enumerate(items[:20], 1):
                title = (el.inner_text() or '').strip()
                if not title or not _has_korean(title):
                    continue
                if _is_rerun(title):
                    continue
                results.append({
                    'name': title,
                    'channel': 'Tving',
                    'category': _infer_category(title),
                    'clip_count_7d': 21 - i,
                    'source': 'tving_ranking',
                    'latest_episode': None,
                    'season': None,
                })
            browser.close()
    except Exception as e:
        log.warning(f'Tving 스캔 실패: {e}')

    log.info(f'Tving: {len(results)}개 발견')
    return results


if __name__ == '__main__':
    import json
    logging.basicConfig(level=logging.INFO)
    nf = scan_netflix_kr()
    tv = scan_tving()
    print(json.dumps({'netflix': nf, 'tving': tv}, ensure_ascii=False, indent=2, default=str))
