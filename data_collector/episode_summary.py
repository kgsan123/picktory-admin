"""
나무위키 회차별 내용 요약 수집기.
방영 후 수 시간 내에 올라오는 회차 정리 텍스트를 파싱.

Usage: python -m data_collector.episode_summary --program "선재 업고 튀어" --episode 16
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

KST = ZoneInfo('Asia/Seoul')
NAMU_BASE = 'https://namu.wiki'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
MAX_SUMMARY_CHARS = 1500


def _fetch_namu_page(title: str) -> str | None:
    """나무위키 페이지 HTML 반환. 실패 시 None."""
    url = f"{NAMU_BASE}/w/{quote(title)}"
    try:
        time.sleep(1)
        resp = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=10)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


# 줄거리가 아닌 네비게이션/메타 라인을 가리키는 신호
_NAV_MARKERS = ('OST', 'Part', '방영 목록', '전체 보기', '등장인물', '음반', '발매',
                '시청률', '편성', '바로가기', '관련 문서', '둘러보기')
# 유튜브 클립·홍보 캡션을 가리키는 신호 (줄거리 아님)
_CLIP_MARKERS = ('미방', '클립', '예고', '하이라이트', '메이킹', 'Exclusive', 'Interview',
                 'Viki', 'Global', 'Official', '구독', '좋아요', '풀버전', '선공개')
_EMOJI_RE = re.compile(
    r'[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF★☆♥♡▶]')


def _is_junk_line(ln: str) -> bool:
    """목차·OST·클립캡션·이모지 등 줄거리가 아닌 라인."""
    if len(ln) <= 6:
        return True
    if re.fullmatch(r'(제\s*)?\d+\s*[회화]', ln):
        return True
    if _EMOJI_RE.search(ln):
        return True
    if any(m in ln for m in _NAV_MARKERS) or any(m in ln for m in _CLIP_MARKERS):
        return True
    # 영문 비중이 높으면 홍보/외부 캡션
    ascii_letters = sum(c.isascii() and c.isalpha() for c in ln)
    if ascii_letters > len(ln) * 0.4:
        return True
    return False


def _looks_like_plot(text: str) -> bool:
    """추출 텍스트가 실제 줄거리 산문인지 검증. 노이즈는 강하게 거부."""
    lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
    if not lines:
        return False
    junk = sum(1 for ln in lines if _is_junk_line(ln))
    # 노이즈가 라인의 40%를 넘으면 줄거리 아님
    if junk / len(lines) > 0.4:
        return False
    # 깨끗한 산문 문장(종결 어미)이 충분히 있는지
    prose = sum(1 for ln in lines
                if not _is_junk_line(ln) and len(ln) > 15
                and re.search(r'(다|었다|았다|된다|한다|왔다|간다)[.!?]?$', ln))
    return prose >= 3


def _extract_episode_section(html: str, episode_number: int) -> str:
    """HTML에서 해당 회차 섹션 텍스트 추출. 줄거리 산문만 반환."""
    soup = BeautifulSoup(html, 'html.parser')

    # 나무위키 본문 영역
    content = soup.find('div', {'class': re.compile(r'wiki-paragraph|namu-content')})
    if not content:
        content = soup.find('article') or soup.find('main') or soup.body

    if not content:
        return ''

    full_text = content.get_text(separator='\n', strip=True)

    # 회차 번호 패턴으로 섹션 찾기 — 목차(앞부분) 매칭을 피해 모든 후보 검토
    patterns = [
        rf'제\s*{episode_number}\s*화',
        rf'{episode_number}화',
        rf'EP\.?\s*{episode_number}',
        rf'{episode_number}회',
    ]
    combined = '|'.join(patterns)
    next_ep_pattern = rf'({episode_number + 1}화|제\s*{episode_number+1}\s*화|{episode_number+1}회)'

    for match in re.finditer(combined, full_text):
        start = match.start()
        next_match = re.search(next_ep_pattern, full_text[start + 10:])
        end = start + 10 + next_match.start() if next_match else start + 2000
        section = full_text[start:end].strip()[:MAX_SUMMARY_CHARS]
        if _looks_like_plot(section):
            return section

    return ''


def fetch_episode_summary(program_name: str, episode_number: int) -> dict:
    """
    Returns:
        {'episode_summary': str, 'source': str, 'collected_at': datetime}
    """
    # 후보 타이틀 순서대로 시도
    candidates = [
        f"{program_name}",
        f"{program_name}/에피소드",
        f"{program_name}/등장인물",
    ]

    for title in candidates:
        html = _fetch_namu_page(title)
        if not html:
            continue
        section = _extract_episode_section(html, episode_number)
        if section and len(section) > 50:
            return {
                'episode_summary': section,
                'source': f'namu_wiki:{title}',
                'collected_at': datetime.now(KST),
            }

    return {
        'episode_summary': '',
        'source': 'namu_wiki:not_found',
        'collected_at': datetime.now(KST),
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--program', required=True)
    parser.add_argument('--episode', type=int, required=True)
    args = parser.parse_args()
    result = fetch_episode_summary(args.program, args.episode)
    sys.stdout.buffer.write(json.dumps(result, default=str, ensure_ascii=False, indent=2).encode('utf-8'))
    print()
