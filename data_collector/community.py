"""
커뮤니티 실시간 반응 수집기 — 더쿠 + 에펨코리아.
reaction_score를 더 정확하게 측정하기 위해 reactions.py와 병행 사용.

Usage: python -m data_collector.community --program "선재 업고 튀어" --aired "2024-05-20T21:10:00+09:00"
"""
import argparse
import json
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

KST = ZoneInfo('Asia/Seoul')
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
THEQOO_SEARCH = 'https://theqoo.net/index.php?mid=hot&act=IS&is_keyword={keyword}'
FMK_SEARCH = 'https://www.fmkorea.com/index.php?mid=best&act=IS&is_keyword={keyword}'


def _get(url: str, delay: float = 2.0) -> str | None:
    time.sleep(delay)
    try:
        resp = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=10)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


def _count_posts_theqoo(program_name: str, aired_at: datetime) -> int:
    """더쿠 인기글 중 프로그램 언급 게시물 수."""
    from urllib.parse import quote
    url = THEQOO_SEARCH.format(keyword=quote(program_name))
    html = _get(url)
    if not html:
        return 0

    soup = BeautifulSoup(html, 'html.parser')
    cutoff = aired_at - timedelta(hours=2)
    deadline = aired_at + timedelta(hours=24)
    count = 0

    for row in soup.select('li.li_br_line, .bd_lst_wrp li'):
        text = row.get_text()
        if program_name not in text:
            continue
        # 날짜 파싱 시도
        date_el = row.select_one('.date, time')
        if date_el:
            try:
                dt = datetime.fromisoformat(
                    date_el.get('datetime') or date_el.text.strip()
                ).replace(tzinfo=KST)
                if cutoff <= dt <= deadline:
                    count += 1
                    continue
            except Exception:
                pass
        # 날짜 파싱 실패해도 키워드 일치면 카운트
        count += 1

    return count


def _count_posts_fmk(program_name: str, aired_at: datetime) -> int:
    """에펨코리아 베스트 중 프로그램 언급 게시물 수."""
    from urllib.parse import quote
    url = FMK_SEARCH.format(keyword=quote(program_name))
    html = _get(url, delay=2.0)
    if not html:
        return 0

    soup = BeautifulSoup(html, 'html.parser')
    count = 0
    for row in soup.select('li.li_br_line, .bd_lst_wrp li'):
        if program_name in row.get_text():
            count += 1
    return count


def fetch_community_reactions(program_name: str, aired_at: datetime) -> dict:
    """
    Returns:
        {
            'theqoo_count': int,
            'fmk_count': int,
            'community_score': float,  # 0.0–10.0
            'collected_at': datetime,
        }
    community_score = theqoo(0-5) + fmk(0-5)
    """
    theqoo = _count_posts_theqoo(program_name, aired_at)
    fmk = _count_posts_fmk(program_name, aired_at)

    theqoo_score = min(5.0, theqoo / 3)
    fmk_score = min(5.0, fmk / 4)
    total = round(theqoo_score + fmk_score, 2)

    return {
        'theqoo_count': theqoo,
        'fmk_count': fmk,
        'community_score': total,
        'collected_at': datetime.now(KST),
        'source': 'theqoo+fmkorea',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--program', required=True)
    parser.add_argument('--aired', required=True)
    args = parser.parse_args()
    aired_at = datetime.fromisoformat(args.aired)
    result = fetch_community_reactions(args.program, aired_at)
    print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
