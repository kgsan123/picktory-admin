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


def _extract_episode_section(html: str, episode_number: int) -> str:
    """HTML에서 해당 회차 섹션 텍스트 추출."""
    soup = BeautifulSoup(html, 'html.parser')

    # 나무위키 본문 영역
    content = soup.find('div', {'class': re.compile(r'wiki-paragraph|namu-content')})
    if not content:
        content = soup.find('article') or soup.find('main') or soup.body

    if not content:
        return ''

    full_text = content.get_text(separator='\n', strip=True)

    # 회차 번호 패턴으로 섹션 찾기
    patterns = [
        rf'{episode_number}화',
        rf'제\s*{episode_number}\s*화',
        rf'EP\.?\s*{episode_number}',
        rf'{episode_number}회',
    ]
    combined = '|'.join(patterns)
    match = re.search(combined, full_text)
    if not match:
        return ''

    start = match.start()
    # 다음 회차 섹션이 시작되기 전까지만 추출
    next_ep_pattern = rf'({episode_number + 1}화|제\s*{episode_number+1}\s*화)'
    next_match = re.search(next_ep_pattern, full_text[start + 10:])
    end = start + 10 + next_match.start() if next_match else start + 2000

    section = full_text[start:end].strip()
    return section[:MAX_SUMMARY_CHARS]


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
