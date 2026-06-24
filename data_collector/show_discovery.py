"""
프로그램 자동 발견 — YouTube + OTT 스캔 → Supabase show_candidates 저장.
관리자 페이지 [신규 발견] 탭에서 검토 후 shows 테이블로 이동.

실행: python -m data_collector.show_discovery
"""
import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)
KST = ZoneInfo('Asia/Seoul')

MIN_CLIP_COUNT = 3


def _get_episode_num(program_name: str) -> int:
    """Naver News에서 최신 회차 번호 탐색."""
    client_id = os.environ.get('NAVER_CLIENT_ID', '')
    client_secret = os.environ.get('NAVER_CLIENT_SECRET', '')
    if not client_id:
        return 1
    try:
        resp = requests.get(
            'https://openapi.naver.com/v1/search/news.json',
            headers={
                'X-Naver-Client-Id': client_id,
                'X-Naver-Client-Secret': client_secret,
            },
            params={'query': f'{program_name} 회차 시청률', 'display': 5, 'sort': 'date'},
            timeout=8,
        )
        items = resp.json().get('items', []) if resp.status_code == 200 else []
        nums = []
        for item in items:
            text = item.get('title', '') + item.get('description', '')
            found = re.findall(r'(\d+)회', text)
            nums.extend(int(n) for n in found if 1 <= int(n) <= 300)
        return max(nums) if nums else 1
    except Exception:
        return 1


def _merge_signals(yt: list, ott: list, db) -> list[dict]:
    """YouTube + OTT 결과 통합. 이미 추적/거부된 프로그램 제외."""
    existing_names = {
        r['name'] for r in db.table('shows').select('name').execute().data or []
    }
    rejected_names = {
        r['name'] for r in
        db.table('show_candidates').select('name').eq('status', 'rejected').execute().data or []
    }

    merged: dict[str, dict] = {}
    for show in yt + ott:
        name = show['name']
        if name in existing_names or name in rejected_names:
            continue
        if name not in merged:
            merged[name] = show.copy()
        else:
            merged[name]['clip_count_7d'] = (
                merged[name].get('clip_count_7d', 0) + show.get('clip_count_7d', 0)
            )
            if show.get('channel'):
                merged[name].setdefault('channel', show['channel'])
            ep = show.get('latest_episode') or 0
            if ep > (merged[name].get('latest_episode') or 0):
                merged[name]['latest_episode'] = ep

    return list(merged.values())


def save_candidates(shows: list[dict], db) -> None:
    """show_candidates 테이블 upsert. 이미 pending인 항목은 clip_count 갱신."""
    now = datetime.now(KST).isoformat()
    existing = {
        r['name']: r for r in
        db.table('show_candidates').select('*').execute().data or []
    }

    for show in shows:
        name = show['name']
        ep = show.get('latest_episode') or show.get('current_episode') or 1
        rec = {
            'name': name,
            'channel': show.get('channel', ''),
            'category': show.get('category', 'variety'),
            'air_days': [],
            'air_time_kst': '',
            'current_episode': ep,
            'source': show.get('source', ''),
            'clip_count_7d': show.get('clip_count_7d', 0),
            'season': show.get('season'),
            'status': 'pending',
            'discovered_at': now,
        }
        if name in existing:
            db.table('show_candidates').update({
                'clip_count_7d': rec['clip_count_7d'],
                'discovered_at': now,
            }).eq('name', name).execute()
        else:
            db.table('show_candidates').insert(rec).execute()

    log.info(f'show_candidates 저장: {len(shows)}개')


def discover_shows() -> list[dict]:
    """전체 발견 파이프라인 실행. Supabase에 저장."""
    from db import get_client
    from data_collector.show_discovery_yt import scan_yt_channels
    from data_collector.show_discovery_ott import scan_netflix_kr, scan_tving

    db = get_client()

    log.info('YouTube 채널 스캔 중...')
    yt = scan_yt_channels(days=7)

    log.info('OTT 랭킹 스캔 중...')
    nf = scan_netflix_kr()
    tv = scan_tving()

    merged = _merge_signals(yt, nf + tv, db)
    log.info(f'신규 후보: {len(merged)}개')

    # 회차 번호 보완
    for show in merged:
        if not show.get('latest_episode') or show['latest_episode'] < 1:
            show['latest_episode'] = _get_episode_num(show['name'])

    save_candidates(merged, db)
    return merged


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    results = discover_shows()
    print(f'\n발견된 신규 후보: {len(results)}개')
    for s in results:
        print(f"  [{s.get('channel','?')}] {s['name']} "
              f"(최신 {s.get('latest_episode','?')}회, 클립 {s.get('clip_count_7d',0)}개/7일)")
