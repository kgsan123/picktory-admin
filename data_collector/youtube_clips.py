"""
YouTube 클립/예고편/선공개 수집기.
- 방영 후 클립 조회수 → 장면별 화제성
- 예고편/선공개 → 다음 회차 힌트 (예측 검증 + 생성에 모두 활용)

Usage: python -m data_collector.youtube_clips --program "선재 업고 튀어" --episode 16
"""
import argparse
import json
import os
import sys
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from googleapiclient.discovery import build

load_dotenv()
KST = ZoneInfo('Asia/Seoul')
MAX_RESULTS = 10


def _yt_client():
    return build('youtube', 'v3', developerKey=os.environ.get('YOUTUBE_API_KEY', ''))


def _parse_duration_to_sec(iso: str) -> int:
    """PT1H2M3S → 3723"""
    h = int(m[0]) if (m := re.findall(r'(\d+)H', iso)) else 0
    mi = int(m[0]) if (m := re.findall(r'(\d+)M', iso)) else 0
    s = int(m[0]) if (m := re.findall(r'(\d+)S', iso)) else 0
    return h * 3600 + mi * 60 + s


def _classify(title: str) -> str:
    """영상 유형 분류."""
    t = title.lower()
    if any(k in t for k in ['예고', '티저', 'teaser', 'preview', 'next week']):
        return 'trailer'
    if any(k in t for k in ['선공개', '미리보기', 'exclusive', 'clip']):
        return 'prerelease'
    if any(k in t for k in ['하이라이트', 'highlight', '명장면']):
        return 'highlight'
    return 'clip'


def fetch_youtube_clips(
    program_name: str,
    episode_number: int,
    aired_at: datetime | None = None,
) -> dict:
    """
    Returns:
        {
            'clips': [
                {
                    'video_id': str,
                    'title': str,
                    'type': 'trailer|prerelease|highlight|clip',
                    'view_count': int,
                    'like_count': int,
                    'published_at': str,
                    'duration_sec': int,
                }
            ],
            'trailer_hints': str,   # 예고편/선공개 제목 요약 (예측 컨텍스트용)
            'top_clip_views': int,  # 가장 많이 본 클립 조회수
            'collected_at': datetime,
        }
    """
    try:
        yt = _yt_client()

        # 검색 기간: 방영일 기준 ±3일
        if aired_at:
            published_after = (aired_at - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
            published_before = (aired_at + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M:%SZ')
        else:
            published_after = None
            published_before = None

        query = f'{program_name} {episode_number}회'
        search_params = dict(
            part='snippet',
            q=query,
            type='video',
            maxResults=MAX_RESULTS,
            order='viewCount',
            relevanceLanguage='ko',
            regionCode='KR',
        )
        if published_after:
            search_params['publishedAfter'] = published_after
            search_params['publishedBefore'] = published_before

        search_resp = yt.search().list(**search_params).execute()
        video_ids = [item['id']['videoId'] for item in search_resp.get('items', [])]

        if not video_ids:
            return _empty_result()

        # 상세 정보 (조회수, 좋아요, 길이)
        details_resp = yt.videos().list(
            part='statistics,contentDetails,snippet',
            id=','.join(video_ids),
        ).execute()

        clips = []
        for item in details_resp.get('items', []):
            stats = item.get('statistics', {})
            snippet = item.get('snippet', {})
            duration = _parse_duration_to_sec(
                item.get('contentDetails', {}).get('duration', 'PT0S')
            )
            # 10초 미만 쇼츠/광고 제외
            if duration < 10:
                continue

            title = snippet.get('title', '')
            clips.append({
                'video_id': item['id'],
                'title': title,
                'type': _classify(title),
                'view_count': int(stats.get('viewCount', 0)),
                'like_count': int(stats.get('likeCount', 0)),
                'published_at': snippet.get('publishedAt', ''),
                'duration_sec': duration,
            })

        clips.sort(key=lambda x: x['view_count'], reverse=True)

        # 예고편/선공개 제목 요약 → AI 컨텍스트에 넣을 힌트
        hints = [c['title'] for c in clips if c['type'] in ('trailer', 'prerelease')]
        trailer_hints = ' | '.join(hints[:5]) if hints else ''
        top_views = clips[0]['view_count'] if clips else 0

        return {
            'clips': clips,
            'trailer_hints': trailer_hints,
            'top_clip_views': top_views,
            'collected_at': datetime.now(KST),
            'source': 'youtube_data_api_v3',
        }

    except Exception as e:
        return {**_empty_result(), 'error': str(e)}


def _empty_result() -> dict:
    return {
        'clips': [],
        'trailer_hints': '',
        'top_clip_views': 0,
        'collected_at': datetime.now(KST),
        'source': 'youtube_data_api_v3',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--program', required=True)
    parser.add_argument('--episode', type=int, required=True)
    parser.add_argument('--aired', default=None)
    args = parser.parse_args()
    aired_at = datetime.fromisoformat(args.aired).astimezone(KST) if args.aired else None
    result = fetch_youtube_clips(args.program, args.episode, aired_at)
    sys.stdout.buffer.write(json.dumps(result, default=str, ensure_ascii=False, indent=2).encode('utf-8'))
    print()
