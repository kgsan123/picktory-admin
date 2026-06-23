"""
OTT 랭킹에서 현재 방영중 한국 콘텐츠 발견.
Tving 랭킹 Playwright + Netflix Korea YouTube (show_discovery_yt.py).
"""
import json
import re
import logging

log = logging.getLogger(__name__)
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

RERUN_KEYWORDS = ['재방', '총집편', '다시보기', '클래식']

# LIVE_RANKING에서 허용하는 방송 채널 목록 (화이트리스트 방식)
BROADCAST_CHANNELS = {
    'MBC', 'MBC every1', 'SBS', 'SBS Plus', 'SBS funE',
    'KBS1', 'KBS2', 'KBS Joy', 'KBS Drama',
    'tvN', 'tvN STORY', 'tvN SHOW', 'tvN DRAMA', 'OCN', 'OCN Thrills',
    'JTBC', 'JTBC2', 'JTBC3',
    '채널A', '채널A플러스', 'MBN', 'ENA', 'Mnet', 'E채널',
    'Tving',
}

# 제목 기반 노이즈 필터 — 애니/뉴스/더빙 키워드
TITLE_NOISE_KEYWORDS = ['더빙', '자막판', '자막', '애니', '토론', 'NEWS', '뉴스']

# 일본 애니 시즌 표기: "23기", "24기 (더빙)" 등 — 한국 프로그램은 "시즌N" 사용
_ANIME_SEASON = re.compile(r'\d+기\s*(?:\([^)]+\))?\s*$')
# 장기 방영 임계치 — 이 이상은 수년째 방영 중인 구작 프로그램
MAX_EPISODE_FOR_DISCOVERY = 300

_BRACKET_EP = re.compile(r'^\[?\d+(?:회|화)\]')
_SINCE_YEAR = re.compile(r'\s+since\s+\d{4}', re.IGNORECASE)


def _is_rerun(title: str) -> bool:
    return any(k in title for k in RERUN_KEYWORDS)


def _has_korean(text: str) -> bool:
    return bool(re.search(r'[가-힣]', text))


def _is_valid_broadcast_channel(ch: str) -> bool:
    """방송 채널 화이트리스트 — 재방/옛날 프로그램 전용 채널 제외."""
    return ch in BROADCAST_CHANNELS or ch.startswith('tvN') or ch.startswith('JTBC')


def _is_title_noise(title: str) -> bool:
    if any(k in title for k in TITLE_NOISE_KEYWORDS):
        return True
    if _ANIME_SEASON.search(title):  # "23기", "2기 (자막)" 등 일본 애니 시즌 표기
        return True
    return False


def _clean_title(title: str) -> str:
    """'냉장고를 부탁해 since 2014' → '냉장고를 부탁해'"""
    return _SINCE_YEAR.sub('', title).strip()


def _infer_category(title: str) -> str:
    t = title.upper()
    if any(k in title for k in ['뮤직뱅크', '음악중심', '인기가요', '카운트다운', '쇼챔피언']):
        return 'music'
    if any(k in title for k in ['연애', '솔로', '커플', '결혼', '하트', '시그널']):
        return 'romance'
    if any(k in t for k in ['SOLO', 'HEART', 'LOVE']):
        return 'romance'
    if any(k in title for k in ['서바이벌', '경쟁', '피지컬', '배틀', '파이터', '전설', '오디션', '아이돌']):
        return 'survival'
    return 'variety'


def scan_netflix_kr() -> list[dict]:
    """
    Netflix KR 콘텐츠 발견 — YouTube 기반으로 전환.
    (Netflix TOP10 TSV URL이 2025년 이후 HTML 리디렉션으로 변경되어 사용 불가)
    실제 Netflix 예능은 show_discovery_yt.py의 'Netflix Korea' 채널에서 탐지됨.
    이 함수는 하위 호환성 유지를 위해 빈 리스트를 반환합니다.
    """
    log.info('Netflix: YouTube 기반 탐지로 전환 (show_discovery_yt.py 참고)')
    return []


_EP_SUFFIX = re.compile(r'\s*\d+(?:화|회)\s*$')
_SEASON_IN_TITLE = re.compile(r'\s+시즌\d+$')


def _strip_episode(title: str) -> tuple[str, int | None]:
    """'하트시그널 시즌5 11화' → ('하트시그널 시즌5', 11)"""
    m = _EP_SUFFIX.search(title)
    ep = None
    if m:
        ep_str = re.search(r'\d+', m.group())
        ep = int(ep_str.group()) if ep_str else None
        title = title[:m.start()].strip()
    return title, ep


def scan_tving() -> list[dict]:
    """
    Tving __NEXT_DATA__ JSON에서 현재 방영중 콘텐츠만 추출.

    3가지 소스 사용:
    - VOD_BASIC (화제작): isNewEpisode=True 필터 → 구작/카탈로그 제외
    - VOD_BASIC_RANKING (TOP20): isNewEpisode=True 필터
    - LIVE_RANKING (실시간 인기 LIVE): 에피소드 번호 + 채널명 포함 → 가장 정확
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning('playwright 미설치 — Tving 스캔 건너뜀')
        return []

    results: dict[str, dict] = {}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto('https://www.tving.com/ranking/content', timeout=40000)
            page.wait_for_load_state('domcontentloaded', timeout=30000)
            page.wait_for_timeout(3000)
            html = page.content()
            browser.close()

        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html, re.DOTALL
        )
        if not m:
            log.warning('Tving __NEXT_DATA__ 없음')
            return []

        data = json.loads(m.group(1))
        bands = data['props']['pageProps']['boardMainData']['bands']

        for band in bands:
            bt = band.get('bandType', '')
            items = band.get('items', [])

            # VOD_BASIC (화제작) + VOD_BASIC_RANKING: isNewEpisode=True 인 것만
            if bt in ('VOD_BASIC', 'VOD_BASIC_RANKING'):
                for i, item in enumerate(items[:20], 1):
                    label = item.get('label') or {}
                    if not label.get('isNewEpisode'):
                        continue  # 구작 / 카탈로그 제외
                    title = _clean_title(item.get('title', '').strip())
                    if not title or not _has_korean(title):
                        continue
                    if _is_rerun(title) or _is_title_noise(title):
                        continue
                    key = title
                    if key not in results:
                        results[key] = {
                            'name': title,
                            'channel': 'Tving',
                            'category': _infer_category(title),
                            'clip_count_7d': 21 - i,
                            'source': 'tving_new_episode',
                            'latest_episode': None,
                            'season': None,
                        }

            # LIVE_RANKING: 실시간 인기 — 에피소드 번호 + 채널명 보유
            elif bt == 'LIVE_RANKING':
                for i, item in enumerate(items[:20], 1):
                    raw_title = item.get('title', '').strip()
                    ch_name = item.get('channelName', '') or 'Tving'
                    if not raw_title or not _has_korean(raw_title) or _is_rerun(raw_title):
                        continue
                    if not _is_valid_broadcast_channel(ch_name):
                        continue  # 재방/옛날 프로그램 전용 채널 제외
                    if _BRACKET_EP.match(raw_title):
                        continue  # [199회]로 시작 = 프로그램명 추출 불가
                    show_name, ep = _strip_episode(raw_title)
                    if ep is None:
                        continue  # 에피소드 번호 없음 = 에피소드 내용 제목일 가능성 높음
                    if ep > MAX_EPISODE_FOR_DISCOVERY:
                        continue  # 300회 초과 = 수년째 방영 중인 구작 → 발견 대상 아님
                    show_name = _clean_title(show_name)
                    if not show_name or len(show_name) < 2 or not _has_korean(show_name):
                        continue
                    if _is_title_noise(show_name):
                        continue
                    key = show_name
                    if key not in results:
                        results[key] = {
                            'name': show_name,
                            'channel': ch_name,
                            'category': _infer_category(show_name),
                            'clip_count_7d': 21 - i,
                            'source': 'tving_live',
                            'latest_episode': ep,
                            'season': None,
                        }
                    elif ep and (results[key]['latest_episode'] or 0) < ep:
                        results[key]['latest_episode'] = ep

    except Exception as e:
        log.warning(f'Tving 스캔 실패: {e}')

    out = list(results.values())
    log.info(f'Tving: {len(out)}개 발견 (현재 방영중 필터 적용)')
    return out


if __name__ == '__main__':
    import json
    logging.basicConfig(level=logging.INFO)
    nf = scan_netflix_kr()
    tv = scan_tving()
    print(json.dumps({'netflix': nf, 'tving': tv}, ensure_ascii=False, indent=2, default=str))
