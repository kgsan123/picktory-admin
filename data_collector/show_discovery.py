"""
프로그램 자동 발견 메인 오케스트레이터.
YouTube 채널 스캔 + OTT 랭킹 → discovered_shows.json에 저장.
관리자 페이지에서 검토 후 승인 시 shows.json에 추가.

실행: python -m data_collector.show_discovery
"""
import json
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

SHOWS_PATH = os.path.join(os.path.dirname(__file__), '..', 'shows.json')
DISCOVERED_PATH = os.path.join(os.path.dirname(__file__), '..', 'discovered_shows.json')
MIN_CLIP_COUNT = 3  # 현재 방영중 판단 기준


def _load_discovered() -> list[dict]:
    if not os.path.exists(DISCOVERED_PATH):
        return []
    with open(DISCOVERED_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _load_shows() -> list[dict]:
    if not os.path.exists(SHOWS_PATH):
        return []
    with open(SHOWS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


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


def _merge_signals(yt: list, ott: list) -> list[dict]:
    """
    YouTube + OTT 결과 통합. 동일 프로그램이 여러 소스에서 발견 시 신뢰도 상승.
    shows.json에 이미 있는 프로그램, discovered_shows에서 거부된 프로그램 제외.
    """
    existing_shows = {s['name'] for s in _load_shows()}
    discovered = _load_discovered()
    rejected = {d['name'] for d in discovered if d.get('status') == 'rejected'}

    merged: dict[str, dict] = {}
    for show in yt + ott:
        name = show['name']
        if name in existing_shows or name in rejected:
            continue
        if name not in merged:
            merged[name] = show.copy()
            merged[name]['clip_count_7d'] = show.get('clip_count_7d', 0)
        else:
            merged[name]['clip_count_7d'] += show.get('clip_count_7d', 0)
            # OTT 소스가 있으면 channel 정보로 보완
            if show.get('channel') not in ('', None):
                merged[name].setdefault('channel', show['channel'])

    return list(merged.values())


def save_discovered(shows: list[dict]) -> None:
    """discovered_shows.json 업데이트. 기존 pending/rejected 유지."""
    existing = _load_discovered()
    existing_map = {d['name']: d for d in existing}

    now = datetime.now(KST).isoformat()
    for show in shows:
        name = show['name']
        if name in existing_map:
            # clip_count와 최신화 정보만 업데이트
            existing_map[name]['clip_count_7d'] = show.get('clip_count_7d', 0)
            existing_map[name]['discovered_at'] = now
            if show.get('latest_episode') and not existing_map[name].get('current_episode'):
                existing_map[name]['current_episode'] = show['latest_episode']
        else:
            existing_map[name] = {
                'name': name,
                'season': show.get('season'),
                'channel': show.get('channel', ''),
                'category': show.get('category', 'variety'),
                'air_days': [],
                'air_time_kst': '',
                'current_episode': show.get('latest_episode') or show.get('current_episode', 1),
                'source': show.get('source', ''),
                'clip_count_7d': show.get('clip_count_7d', 0),
                'discovered_at': now,
                'status': 'pending',
            }

    updated = list(existing_map.values())
    with open(DISCOVERED_PATH, 'w', encoding='utf-8') as f:
        json.dump(updated, f, ensure_ascii=False, indent=2)
    log.info(f'discovered_shows.json 저장: {len(updated)}개 (신규 {len(shows)}개 처리)')


def discover_shows() -> list[dict]:
    """전체 발견 파이프라인 실행."""
    from data_collector.show_discovery_yt import scan_yt_channels
    from data_collector.show_discovery_ott import scan_netflix_kr, scan_tving

    log.info('YouTube 채널 스캔 중...')
    yt = scan_yt_channels(days=7)

    log.info('OTT 랭킹 스캔 중...')
    nf = scan_netflix_kr()
    tv = scan_tving()

    merged = _merge_signals(yt, nf + tv)
    log.info(f'통합 후 신규 발견: {len(merged)}개')

    # 회차 번호 보완
    for show in merged:
        if not show.get('current_episode') or show['current_episode'] < 1:
            show['current_episode'] = _get_episode_num(show['name'])

    save_discovered(merged)
    return merged


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    results = discover_shows()
    print(f'\n발견된 신규 프로그램: {len(results)}개')
    for s in results:
        ep = s.get('current_episode', '?')
        clips = s.get('clip_count_7d', 0)
        print(f"  [{s.get('channel','?')}] {s['name']} (최신 {ep}회, 클립 {clips}개/7일)")
