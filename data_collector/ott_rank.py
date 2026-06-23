"""
OTT 랭킹 수집기.
- Netflix: 공식 주간 TOP10 CSV (https://www.netflix.com/tudum/top10)
- Tving / Coupang: Playwright JS 렌더링

Usage: python -m data_collector.ott_rank --program "선재 업고 튀어"
"""
import argparse
import csv
import io
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo('Asia/Seoul')
NETFLIX_CSV_URL = 'https://top10.netflix.com/all-weeks-tv.tsv'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'


def _netflix_rank(program_name: str) -> int | None:
    """Download Netflix weekly TOP10 TSV and search for program name."""
    try:
        resp = requests.get(
            NETFLIX_CSV_URL,
            headers={'User-Agent': USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text), delimiter='\t')
        kw = program_name.lower()
        for row in reader:
            title = row.get('show_title', '').lower()
            if kw in title or title in kw:
                rank = row.get('weekly_rank')
                return int(rank) if rank else None
    except Exception:
        pass
    return None


def _playwright_rank(url: str, program_name: str, rank_selector: str) -> int | None:
    """Generic Playwright rank scraper. Returns None if playwright unavailable."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, timeout=20000)
            page.wait_for_load_state('networkidle', timeout=15000)
            items = page.query_selector_all(rank_selector)
            for i, el in enumerate(items, start=1):
                if program_name in (el.inner_text() or ''):
                    browser.close()
                    return i
            browser.close()
    except Exception:
        pass
    return None


def _tving_rank(program_name: str) -> int | None:
    return _playwright_rank(
        'https://www.tving.com/ranking/content',
        program_name,
        '.ranking-item .title',
    )


def _coupang_rank(program_name: str) -> int | None:
    return _playwright_rank(
        'https://www.coupangplay.com/browse/ranking',
        program_name,
        '.content-item .content-title',
    )


def fetch_ott_rank(program_name: str) -> dict:
    """
    Returns:
        {
            'netflix_rank': int|None,
            'tving_rank': int|None,
            'coupang_rank': int|None,
            'collected_at': datetime,
        }
    All ranks are soft-fail — None if unavailable.
    """
    netflix = _netflix_rank(program_name)
    tving = _tving_rank(program_name)
    coupang = _coupang_rank(program_name)

    return {
        'netflix_rank': netflix,
        'tving_rank': tving,
        'coupang_rank': coupang,
        'collected_at': datetime.now(KST),
        'source': 'netflix_csv+playwright',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--program', required=True)
    args = parser.parse_args()
    result = fetch_ott_rank(args.program)
    print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
