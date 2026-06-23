"""
Community reaction collector.
Sources: Naver Blog API (official) + DC인사이드 드라마 갤러리 (BeautifulSoup).
reaction_score = blog_score(0-5) + dc_score(0-5) → range 0.0–10.0

Usage: python -m data_collector.reactions --program "선재 업고 튀어" --aired "2024-05-20T21:10:00+09:00"
"""
import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()
KST = ZoneInfo('Asia/Seoul')
NAVER_BLOG_URL = 'https://openapi.naver.com/v1/search/blog.json'
DC_DRAMA_URL = 'https://gall.dcinside.com/board/lists/'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'


def _naver_headers() -> dict:
    return {
        'X-Naver-Client-Id': os.environ.get('NAVER_CLIENT_ID', ''),
        'X-Naver-Client-Secret': os.environ.get('NAVER_CLIENT_SECRET', ''),
    }


def _blog_score(program_name: str, aired_at: datetime) -> float:
    """Naver Blog search count → 0.0–5.0"""
    try:
        params = {'query': program_name, 'display': 100, 'sort': 'date'}
        resp = requests.get(NAVER_BLOG_URL, headers=_naver_headers(), params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        total = int(data.get('total', 0))
        return min(5.0, total / 200)
    except Exception:
        return 0.0


def _dc_score(program_name: str, aired_at: datetime) -> float:
    """DC인사이드 드라마 갤러리 24h 게시물 수 → 0.0–5.0"""
    cutoff = aired_at - timedelta(hours=2)
    deadline = aired_at + timedelta(hours=24)
    count = 0
    try:
        time.sleep(2)
        params = {
            'id': 'drama',
            'list_num': 50,
            's_type': 'search_subject_memo',
            's_keyword': program_name,
        }
        resp = requests.get(
            DC_DRAMA_URL,
            params=params,
            headers={'User-Agent': USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        rows = soup.select('tr.ub-content')
        for row in rows:
            date_el = row.select_one('td.gall_date')
            if not date_el:
                continue
            date_str = date_el.get('title') or date_el.text.strip()
            try:
                post_dt = datetime.fromisoformat(date_str).replace(tzinfo=KST)
            except Exception:
                continue
            if cutoff <= post_dt <= deadline:
                count += 1
    except Exception:
        pass
    return min(5.0, count / 4)


def fetch_reactions(program_name: str, aired_at: datetime) -> dict:
    """
    Returns:
        {'reaction_score': float, 'collected_at': datetime, 'source': str}
    """
    blog = _blog_score(program_name, aired_at)
    dc = _dc_score(program_name, aired_at)
    score = round(blog + dc, 2)

    return {
        'reaction_score': score,
        'blog_score': round(blog, 2),
        'dc_score': round(dc, 2),
        'collected_at': datetime.now(KST),
        'source': 'naver_blog+dcinside',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--program', required=True)
    parser.add_argument('--aired', required=True)
    args = parser.parse_args()
    aired_at = datetime.fromisoformat(args.aired)
    result = fetch_reactions(args.program, aired_at)
    print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
