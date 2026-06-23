"""
YouTube 방송사 공식 채널 최근 업로드 스캔으로 현재 방영중 예능/드라마 발견.
7일 내 클립 3개 이상 = 현재 방영중으로 판단.
재방송 키워드 필터로 오래된 콘텐츠 제외.
"""
import json
import re
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)
KST = ZoneInfo('Asia/Seoul')

# 채널 검색 쿼리 → (방송채널명, 카테고리 힌트)
# 예능 채널 우선 (서바이벌/연애 예능이 메인 타겟)
YT_CHANNEL_QUERIES = {
    'MBC 예능':           ('MBC',   'variety'),
    'SBS 예능':           ('SBS',   'variety'),
    'KBS 예능':           ('KBS2',  'variety'),
    'JTBC Entertainment': ('JTBC',  'variety'),
    'tvN 드라마':          ('tvN',   'drama'),
    'Mnet':               ('Mnet',  'survival'),
    'M2':                 ('Mnet',  'survival'),   # CJ ENM 퍼포먼스 채널 (Mnet 보조 신호)
    'ENA Channel':        ('ENA',   'variety'),
    'MBC Drama':          ('MBC',   'drama'),
    'SBS Drama':          ('SBS',   'drama'),
    'JTBC Drama':         ('JTBC',  'drama'),
}

_CACHE_FILE = os.path.join(os.path.dirname(__file__), '..', '.yt_channel_cache.json')
_channel_id_cache: dict[str, str] = {}


def _load_channel_cache():
    global _channel_id_cache
    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE, 'r', encoding='utf-8') as f:
                _channel_id_cache = json.load(f)
        except Exception:
            _channel_id_cache = {}


def _save_channel_cache():
    try:
        with open(_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(_channel_id_cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

RERUN_KEYWORDS = [
    '재방', '재방송', '다시보기', '재편집', '총집편',
    '(재)', '클래식', '명장면 베스트', '베스트클립',
    '오늘의 명장면', '옛날', '레전드',
]

# 프로그램명으로 쓰일 수 없는 일반 단어 — 채널 내 공통 영상 제목에서 걸러냄
GENERIC_NAMES = {
    '선공개', '하이라이트', '스페셜', '메이킹', '예고', '예고편',
    '비하인드', '종합', '모아보기', '티저', '클립', '풀버전', '전편',
    'SUB', 'ENG', 'BEHIND', '하이라이트 모음',
}

# 에피소드 마커 패턴
EP_PATTERN = re.compile(
    r'(\d+)\s*(?:회|화)\b'
    r'|\bEP\.?\s*(\d+)'
    r'|(최종화|마지막화|파이널)',
    re.IGNORECASE
)
BRACKET_SHOW = re.compile(r'^\[([^\]]+)\]')  # [프로그램명] 패턴
PIPE_SHOW = re.compile(r'\|\s*([^|]{2,20}?)\s+(\d+)회\s*\|')  # | 프로그램명 N회 | 패턴 (JTBC)
SEASON_PATTERN = re.compile(r'(\d+)\s*기\b')
BROADCAST_PREFIX = re.compile(r'^(?:MBC|SBS|KBS\d?|JTBC|tvN|OCN|채널A|Mnet|ENA)\s+')
SUFFIX_JUNK = re.compile(r'[\s\-|·•·:_]+$')


def _yt_client():
    from googleapiclient.discovery import build
    return build('youtube', 'v3', developerKey=os.environ.get('YOUTUBE_API_KEY', ''))


def _is_rerun(title: str) -> bool:
    return any(kw in title for kw in RERUN_KEYWORDS)


def _is_old_content(title: str) -> bool:
    """제목에 2년 이상 지난 연도 명시 시 제외."""
    current_year = datetime.now(KST).year
    years = re.findall(r'(20\d{2})년', title)
    return any(int(y) < current_year - 1 for y in years)


def _extract_show_info(title: str) -> dict | None:
    """
    제목에서 프로그램명, 회차, 시즌 추출.
    우선순위:
    1. [프로그램명] 대괄호 (MBC/SBS/KBS 공식 예능 채널 형식)
    2. 프로그램명 N회 패턴 (JTBC, tvN 드라마 형식)
    """
    if title.startswith('[다시보기]') or title.startswith('[SUB]'):
        return None

    # 1) [프로그램명] 대괄호 패턴
    bracket = BRACKET_SHOW.match(title)
    if bracket:
        name = bracket.group(1).strip()
        name = BROADCAST_PREFIX.sub('', name).strip()
        name = SUFFIX_JUNK.sub('', name).strip()
        if len(name) >= 2:
            rest = title[bracket.end():]
            ep_match = EP_PATTERN.search(rest)
            season_match = SEASON_PATTERN.search(rest)
            episode = None
            if ep_match:
                groups = ep_match.groups()
                episode = next((int(g) for g in groups[:2] if g and g.isdigit()), None)
            season_num = int(season_match.group(1)) if season_match else None
            return {
                'name': name,
                'episode': episode,
                'season': f'{season_num}기' if season_num else None,
            }

    # 2) | 프로그램명 N회 | 파이프 패턴 (JTBC 클립 형식)
    pipe = PIPE_SHOW.search(title)
    if pipe:
        name = pipe.group(1).strip()
        if len(name) >= 2:
            return {'name': name, 'episode': int(pipe.group(2)), 'season': None}

    # 3) 프로그램명 N회 패턴
    ep_match = EP_PATTERN.search(title)
    season_match = SEASON_PATTERN.search(title)

    markers = []
    if ep_match:
        groups = ep_match.groups()
        num_val = next((int(g) for g in groups[:2] if g and g.isdigit()), None)
        markers.append((ep_match.start(), 'ep', num_val))
    if season_match:
        markers.append((season_match.start(), 'season', int(season_match.group(1))))

    if not markers:
        return None

    markers.sort(key=lambda x: x[0])
    cut_pos = markers[0][0]
    name = BROADCAST_PREFIX.sub('', title[:cut_pos]).strip()
    name = SUFFIX_JUNK.sub('', name).strip()

    if len(name) < 2:
        return None

    episode = next((v for _, t, v in markers if t == 'ep' and isinstance(v, int)), None)
    season_num = next((v for _, t, v in markers if t == 'season'), None)

    return {
        'name': name,
        'episode': episode,
        'season': f'{season_num}기' if season_num else None,
    }


def _infer_category(show_name: str, channel_hint: str) -> str:
    n = show_name
    nu = n.upper()
    if any(k in n for k in ['서바이벌', '배틀', '경쟁', '피지컬', '흑백', '아이돌', '오디션', '파이터']):
        return 'survival'
    if any(k in n for k in ['연애', '솔로', '환승', '커플', '결혼', '하트', '시그널']):
        return 'romance'
    if any(k in nu for k in ['SOLO', 'HEART', 'LOVE']):
        return 'romance'
    if channel_hint == 'drama':
        return 'drama'
    return 'variety'


def _resolve_channel_id(yt, query: str) -> str | None:
    """채널명으로 YouTube 채널 ID 조회 (디스크 캐시 활용 — 쿼터 절약)."""
    if query in _channel_id_cache:
        return _channel_id_cache[query]
    try:
        resp = yt.search().list(
            part='snippet',
            q=query,
            type='channel',
            maxResults=1,
            relevanceLanguage='ko',
            regionCode='KR',
        ).execute()
        items = resp.get('items', [])
        if items:
            ch_id = items[0]['id']['channelId']
            _channel_id_cache[query] = ch_id
            _save_channel_cache()  # 즉시 저장 (다음 실행 시 재조회 불필요)
            log.info(f'{query} → 채널 ID: {ch_id}')
            return ch_id
    except Exception as e:
        log.warning(f'{query} 채널 ID 조회 실패: {e}')
    return None


def _scan_channel(yt, channel_id: str, channel_name: str,
                  broadcast: str, category_hint: str, days: int,
                  min_clips: int = 3) -> list[dict]:
    """채널의 최근 업로드를 스캔해 현재 방영중 프로그램 목록 반환."""
    published_after = (datetime.now(KST) - timedelta(days=days)).isoformat()

    try:
        resp = yt.search().list(
            part='snippet',
            channelId=channel_id,
            publishedAfter=published_after,
            type='video',
            maxResults=50,
            order='date',
        ).execute()
    except Exception as e:
        log.warning(f'{channel_name} 스캔 실패: {e}')
        return []

    counts: dict[str, dict] = {}
    for item in resp.get('items', []):
        title = item['snippet']['title']
        if _is_rerun(title) or _is_old_content(title):
            continue
        info = _extract_show_info(title)
        if not info:
            continue
        name = info['name'].lstrip('#').strip()  # '#피크타임' → '피크타임'
        if name in GENERIC_NAMES or len(name) < 2:
            continue
        if name not in counts:
            counts[name] = {
                'name': name,
                'channel': broadcast,
                'season': info.get('season'),
                'latest_episode': info.get('episode') or 0,
                'clip_count_7d': 0,
                'category': _infer_category(name, category_hint),
                'source': f'youtube_{channel_name}',
            }
        counts[name]['clip_count_7d'] += 1
        ep = info.get('episode') or 0
        if ep > counts[name]['latest_episode']:
            counts[name]['latest_episode'] = ep

    return [v for v in counts.values() if v['clip_count_7d'] >= min_clips]


def scan_yt_channels(days: int = 7) -> list[dict]:
    """
    등록된 모든 방송사 YouTube 채널 스캔.
    채널명 검색으로 채널 ID를 동적으로 조회한 후 최근 업로드 분석.
    Returns: 현재 방영중으로 추정되는 프로그램 목록
    """
    if not os.environ.get('YOUTUBE_API_KEY'):
        log.warning('YOUTUBE_API_KEY 없음 — YouTube 스캔 건너뜀')
        return []

    _load_channel_cache()  # 디스크 캐시 로드 (쿼터 절약)
    yt = _yt_client()
    results = []

    for ch_query, (broadcast, cat_hint) in YT_CHANNEL_QUERIES.items():
        ch_id = _resolve_channel_id(yt, ch_query)
        if not ch_id:
            log.warning(f'{ch_query} 채널 ID 조회 실패, 건너뜀')
            continue
        # Mnet/M2는 쇼당 클립이 적어 기준 완화
        min_clips = 2 if broadcast == 'Mnet' else 3
        found = _scan_channel(yt, ch_id, ch_query, broadcast, cat_hint, days, min_clips)
        log.info(f'{ch_query}: {len(found)}개 발견')
        results.extend(found)

    # 프로그램명 기준 중복 제거 (여러 채널에서 같은 프로그램 발견 시 clip_count 합산)
    merged: dict[str, dict] = {}
    for show in results:
        key = show['name']
        if key not in merged:
            merged[key] = show.copy()
        else:
            merged[key]['clip_count_7d'] += show['clip_count_7d']
            if show['latest_episode'] > merged[key]['latest_episode']:
                merged[key]['latest_episode'] = show['latest_episode']

    return list(merged.values())


if __name__ == '__main__':
    import json
    logging.basicConfig(level=logging.INFO)
    shows = scan_yt_channels(days=7)
    print(f'\n발견 결과: {len(shows)}개')
    print(json.dumps(shows, ensure_ascii=False, indent=2, default=str))
