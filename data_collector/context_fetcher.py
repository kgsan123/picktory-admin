"""
직전 회차 컨텍스트 자동 수집 — Naver News API.
예측 생성 직전에 호출. 실패해도 '' 반환하며 파이프라인을 막지 않음.
"""
import os
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()
KST = ZoneInfo('Asia/Seoul')
NAVER_URL = 'https://openapi.naver.com/v1/search/news.json'


def _headers() -> dict:
    client_id = os.environ.get('NAVER_CLIENT_ID', '')
    client_secret = os.environ.get('NAVER_CLIENT_SECRET', '')
    if not client_id:
        try:
            import streamlit as st
            client_id = st.secrets.get('NAVER_CLIENT_ID', '')
            client_secret = st.secrets.get('NAVER_CLIENT_SECRET', '')
        except Exception:
            pass
    return {
        'X-Naver-Client-Id': client_id,
        'X-Naver-Client-Secret': client_secret,
    }


def fetch_episode_context(program_name: str, episode_num: int, timeout: int = 8) -> str:
    """
    Naver News에서 해당 회차 방송 내용 기사를 검색해 요약 반환.
    최근 7일 이내 기사만 사용. API 키 없거나 실패하면 '' 반환.
    """
    headers = _headers()
    if not headers.get('X-Naver-Client-Id'):
        return ''

    cutoff = datetime.now(KST) - timedelta(days=7)
    queries = [f'{program_name} {episode_num}회', f'{program_name} {episode_num}화']
    articles: list[str] = []

    for query in queries:
        if articles:
            break
        try:
            resp = requests.get(
                NAVER_URL,
                headers=headers,
                params={'query': query, 'display': 10, 'sort': 'date'},
                timeout=timeout,
            )
            resp.raise_for_status()
            for item in resp.json().get('items', []):
                pub_raw = item.get('pubDate', '')
                try:
                    pub_dt = parsedate_to_datetime(pub_raw).astimezone(KST)
                except Exception:
                    continue
                if pub_dt < cutoff:
                    continue
                title = re.sub(r'<[^>]+>', '', item.get('title', ''))
                desc = re.sub(r'<[^>]+>', '', item.get('description', ''))
                if title:
                    articles.append(f'{title}. {desc[:80]}' if desc else title)
        except Exception:
            continue

    return '\n'.join(articles[:5])[:600]
