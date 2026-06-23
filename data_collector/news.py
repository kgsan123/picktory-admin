"""
Naver News API collector — 24h window summary.
Usage: python -m data_collector.news --program "선재 업고 튀어" --aired "2024-05-20T21:10:00+09:00"
"""
import argparse
import json
import os
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()
KST = ZoneInfo('Asia/Seoul')
NAVER_NEWS_URL = 'https://openapi.naver.com/v1/search/news.json'
MAX_SUMMARY_CHARS = 800


def _naver_headers() -> dict:
    return {
        'X-Naver-Client-Id': os.environ.get('NAVER_CLIENT_ID', ''),
        'X-Naver-Client-Secret': os.environ.get('NAVER_CLIENT_SECRET', ''),
    }


def _strip_html(text: str) -> str:
    return re.sub(r'<[^>]+>', '', text).strip()


def fetch_news(program_name: str, aired_at: datetime) -> dict:
    """
    Returns:
        {'news_summary': str, 'article_count': int, 'collected_at': datetime}
    Collects up to 100 articles, filters to 24h window, summarises titles.
    """
    cutoff = aired_at - timedelta(hours=2)
    deadline = aired_at + timedelta(hours=24)
    articles: list[str] = []

    try:
        for start in range(1, 101, 20):
            params = {
                'query': program_name,
                'display': 20,
                'start': start,
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
            if not items:
                break

            for item in items:
                pub_raw = item.get('pubDate', '')
                try:
                    pub_dt = parsedate_to_datetime(pub_raw).astimezone(KST)
                except Exception:
                    continue
                if not (cutoff <= pub_dt <= deadline):
                    continue
                title = _strip_html(item.get('title', ''))
                desc = _strip_html(item.get('description', ''))
                if title:
                    articles.append(f"{title}: {desc[:100]}" if desc else title)

    except Exception as e:
        return {
            'news_summary': '',
            'article_count': 0,
            'collected_at': datetime.now(KST),
            'error': str(e),
        }

    summary = ' / '.join(articles)[:MAX_SUMMARY_CHARS]
    return {
        'news_summary': summary,
        'article_count': len(articles),
        'collected_at': datetime.now(KST),
        'source': 'naver_news',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--program', required=True)
    parser.add_argument('--aired', required=True)
    args = parser.parse_args()
    aired_at = datetime.fromisoformat(args.aired)
    result = fetch_news(args.program, aired_at)
    print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
