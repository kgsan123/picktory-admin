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


def _median_filtered(ratings: list[float]) -> float | None:
    """중앙값 + 2x 초과 outlier 제거."""
    if not ratings:
        return None
    median = statistics.median(ratings)
    filtered = [r for r in ratings if r <= 2 * median]
    return round(statistics.median(filtered), 2) if filtered else None


def fetch_ratings(program_name: str, aired_at: datetime,
                  episode_num: int | None = None) -> dict:
    """
    Returns:
        {'ratings_percent': float|None, 'collected_at': datetime, 'source': str}
    Never raises — returns None on any failure.
    episode_num: 주어지면 해당 회차를 언급한 기사의 시청률을 우선 사용.
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

    ep_re = re.compile(rf'\b{episode_num}\s*[회화]') if episode_num else None
    ratings: list[float] = []        # 전체
    ep_ratings: list[float] = []     # 해당 회차 언급 기사
    for item in items:
        raw = item.get('title', '') + ' ' + item.get('description', '')
        text = re.sub(r'<[^>]+>', '', raw)
        if not KW_PATTERN.search(text):
            continue
        vals = [float(m) for m in RATINGS_PATTERN.findall(text)
                if 0.1 <= float(m) <= 50.0]
        ratings.extend(vals)
        if ep_re and ep_re.search(text):
            ep_ratings.extend(vals)

    # 회차 매칭 기사가 있으면 그것을 우선, 없으면 전체 median
    result = _median_filtered(ep_ratings) or _median_filtered(ratings)
    source = 'naver_news' + ('_ep_matched' if ep_ratings else '')

    return {
        'ratings_percent': result,
        'collected_at': datetime.now(KST),
        'source': source,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--program', required=True)
    parser.add_argument('--aired', required=True, help='ISO 8601 datetime')
    parser.add_argument('--episode', type=int, default=None)
    args = parser.parse_args()
    aired_at = datetime.fromisoformat(args.aired)
    result = fetch_ratings(args.program, aired_at, args.episode)
    print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
