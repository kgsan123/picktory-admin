"""
에피소드 감지기
Naver News에서 시청률 기사를 찾아 새 에피소드 방영 확인 후 DB 기록
"""
import os
import re
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()
KST = ZoneInfo('Asia/Seoul')
log = logging.getLogger(__name__)


def _search_naver_news(query: str) -> list:
    client_id = os.environ.get('NAVER_CLIENT_ID', '')
    client_secret = os.environ.get('NAVER_CLIENT_SECRET', '')
    if not client_id:
        return []
    resp = requests.get(
        'https://openapi.naver.com/v1/search/news.json',
        headers={
            'X-Naver-Client-Id': client_id,
            'X-Naver-Client-Secret': client_secret,
        },
        params={'query': query, 'display': 10, 'sort': 'date'},
        timeout=10,
    )
    if resp.status_code != 200:
        return []
    return resp.json().get('items', [])


def _episode_confirmed(items: list, episode_num: int) -> bool:
    patterns = [f'{episode_num}회', f'제{episode_num}화', f'EP{episode_num}', f'{episode_num}화']
    for item in items:
        text = item.get('title', '') + item.get('description', '')
        if any(p in text for p in patterns):
            return True
    return False


def detect_new_episode(show: dict) -> str | None:
    """
    새 에피소드 방영 확인 후 DB upsert.
    Returns: episode_id or None
    """
    from db import get_client

    program = show['name']
    episode_num = show.get('current_episode', 1)
    category = show.get('category', 'drama')
    channel = show.get('channel', '')

    db = get_client()

    # 이미 DB에 존재하면 기존 ID 반환
    existing = (db.table('episodes')
                .select('id')
                .eq('program_name', program)
                .eq('episode_number', episode_num)
                .execute().data)
    if existing:
        log.info(f'{program} {episode_num}회 이미 DB에 존재')
        return existing[0]['id']

    # Naver News로 방영 확인
    items = _search_naver_news(f'{program} {episode_num}회 시청률')
    if not _episode_confirmed(items, episode_num):
        log.warning(f'{program} {episode_num}회 시청률 기사 미발견')
        return None

    log.info(f'{program} {episode_num}회 방영 확인')
    result = db.table('episodes').insert({
        'program_name': program,
        'episode_number': episode_num,
        'category': category,
        'channel': channel,
        'aired_at': datetime.now(KST).isoformat(),
        'pipeline_status': 'detected',
    }).execute()

    if not result.data:
        log.error(f'DB insert 실패: {program} {episode_num}회')
        return None

    episode_id = result.data[0]['id']
    _increment_episode_in_db(program, episode_num)
    return episode_id


def _increment_episode_in_db(program_name: str, episode_num: int):
    """Supabase shows 테이블의 current_episode를 +1 업데이트"""
    from db import get_client
    try:
        db = get_client()
        db.table('shows').update(
            {'current_episode': episode_num + 1}
        ).eq('name', program_name).execute()
        log.info(f'{program_name} current_episode → {episode_num + 1}')
    except Exception as e:
        log.warning(f'shows 업데이트 실패: {e}')


def get_shows_to_check() -> list[dict]:
    """Supabase에서 추적 중인 프로그램 목록 반환 (오케스트레이터용)"""
    from db import get_client
    db = get_client()
    return db.table('shows').select('*').eq('ended', False).execute().data or []


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)
    shows = get_shows_to_check()
    if not shows:
        print('추적 중인 프로그램 없음')
        sys.exit(0)
    show = shows[0]
    print(f'감지 시작: {show["name"]} {show["current_episode"]}회')
    episode_id = detect_new_episode(show)
    print(f'결과: {episode_id}')
