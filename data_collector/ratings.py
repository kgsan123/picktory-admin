"""
Nielsen ratings collector via Naver News API.
Usage: python -m data_collector.ratings --program "선재 업고 튀어" --aired "2024-05-20T21:10:00+09:00"
"""
import argparse
import json
import os
import re
import statistics
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()
KST = ZoneInfo('Asia/Seoul')
NAVER_NEWS_URL = 'https://openapi.naver.com/v1/search/news.json'
RATINGS_PATTERN = re.compile(r'(\d+\.?\d*)\s*%')
KW_PATTERN = re.compile(r'시청률|닐슨|가구')


def _naver_headers() -> dict:
    return {
        'X-Naver-Client-Id': os.environ.get('NAVER_CLIENT_ID', ''),
        'X-Naver-Client-Secret': os.environ.get('NAVER_CLIENT_SECRET', ''),
    }


def fetch_ratings(program_name: str, aired_at: datetime) -> dict:
    """
    Returns:
        {'ratings_percent': float|None, 'collected_at': datetime, 'source': str}
    Never raises — returns None on any failure.
    """
    try:
        params = {
            'query': f'{program_name} 시청률 닐슨',
            'display': 20,
            'sort': 'date',
        }
        resp = requests.get(
            NAVER_NEWS_URL,
            headers=_naver_headers(),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get('items', [])
    except Exception as e:
        return {'ratings_percent': None, 'collected_at': datetime.now(KST), 'error': str(e)}

    ratings: list[float] = []
    for item in items:
        raw = item.get('title', '') + ' ' + item.get('description', '')
        text = re.sub(r'<[^>]+>', '', raw)
        if not KW_PATTERN.search(text):
            continue
        for m in RATINGS_PATTERN.findall(text):
            val = float(m)
            if 0.1 <= val <= 50.0:
                ratings.append(val)

    if not ratings:
        return {'ratings_percent': None, 'collected_at': datetime.now(KST), 'source': 'naver_news'}

    median = statistics.median(ratings)
    filtered = [r for r in ratings if r <= 2 * median]
    result = round(statistics.median(filtered), 2) if filtered else None

    return {
        'ratings_percent': result,
        'collected_at': datetime.now(KST),
        'source': 'naver_news',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--program', required=True)
    parser.add_argument('--aired', required=True, help='ISO 8601 datetime')
    args = parser.parse_args()
    aired_at = datetime.fromisoformat(args.aired)
    result = fetch_ratings(args.program, aired_at)
    print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
