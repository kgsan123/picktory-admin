"""
에피소드 컨텍스트 수집 — 예측 생성 및 정답 검증에 사용.
소스: Google News RSS + 더쿠 + DC인사이드
텍스트 스니펫을 반환 — 숫자 점수 아님.

Usage: python -m data_collector.context_fetcher --program "무한도전" --episode 400
"""
import argparse
import json
import re
import time
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
TIMEOUT = 8


@dataclass
class EpisodeContext:
    news_snippets: list[str] = field(default_factory=list)   # 뉴스 기사 제목+요약
    community_posts: list[str] = field(default_factory=list) # 커뮤니티 인기글 제목
    sources_used: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        """AI 프롬프트에 넣을 텍스트로 변환."""
        parts = []
        if self.news_snippets:
            parts.append('[뉴스 기사]')
            parts.extend(f'- {s}' for s in self.news_snippets[:5])
        if self.community_posts:
            parts.append('[커뮤니티 반응]')
            parts.extend(f'- {s}' for s in self.community_posts[:8])
        return '\n'.join(parts) if parts else ''

    def is_empty(self) -> bool:
        return not self.news_snippets and not self.community_posts


def _fetch_google_news(query: str, ctx: EpisodeContext) -> None:
    """Google News RSS — 무료, API 키 불필요."""
    try:
        url = 'https://news.google.com/rss/search'
        params = {'q': query, 'hl': 'ko', 'gl': 'KR', 'ceid': 'KR:ko'}
        resp = requests.get(url, params=params, timeout=TIMEOUT,
                            headers={'User-Agent': UA})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'xml')
        for item in soup.find_all('item')[:8]:
            title = item.find('title')
            desc = item.find('description')
            t = title.text.strip() if title else ''
            d = BeautifulSoup(desc.text, 'html.parser').get_text()[:100].strip() if desc else ''
            if t:
                ctx.news_snippets.append(f'{t}. {d}' if d else t)
        if ctx.news_snippets:
            ctx.sources_used.append('google_news')
    except Exception as e:
        ctx.errors.append(f'google_news: {e}')


def _fetch_theqoo(query: str, ctx: EpisodeContext) -> None:
    """더쿠 실시간 인기글 검색."""
    try:
        time.sleep(1)
        url = 'https://theqoo.net/index.php'
        params = {
            'mid': 'hot',
            'search_target': 'title_content',
            'search_keyword': query,
        }
        resp = requests.get(url, params=params, timeout=TIMEOUT,
                            headers={'User-Agent': UA})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        for row in soup.select('.bd_lst .title a')[:10]:
            text = row.get_text(strip=True)
            # 광고/공지 제외
            if text and len(text) > 5 and '공지' not in text:
                ctx.community_posts.append(f'[더쿠] {text}')
        if any('[더쿠]' in p for p in ctx.community_posts):
            ctx.sources_used.append('theqoo')
    except Exception as e:
        ctx.errors.append(f'theqoo: {e}')


def _fetch_dcinside(query: str, ctx: EpisodeContext) -> None:
    """DC인사이드 — 드라마/연예 갤러리 검색."""
    try:
        time.sleep(2)
        url = 'https://gall.dcinside.com/board/lists/'
        # 드라마 갤러리 먼저, 없으면 연예갤
        for gall_id in ('drama', 'entertainer'):
            params = {
                'id': gall_id,
                'list_num': 30,
                's_type': 'search_subject_memo',
                's_keyword': query,
            }
            resp = requests.get(url, params=params, timeout=TIMEOUT,
                                headers={'User-Agent': UA})
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, 'html.parser')
            rows = soup.select('tr.ub-content')
            found = []
            for row in rows:
                title_el = row.select_one('.gall_tit a')
                if not title_el:
                    continue
                text = title_el.get_text(strip=True)
                if text and len(text) > 5:
                    found.append(f'[DC] {text}')
            ctx.community_posts.extend(found[:6])
            if found:
                ctx.sources_used.append(f'dcinside_{gall_id}')
                break
    except Exception as e:
        ctx.errors.append(f'dcinside: {e}')


def fetch_episode_context(program_name: str, episode_num: int) -> EpisodeContext:
    """
    방송 직후 컨텍스트 수집. 모든 소스 병렬 시도, 일부 실패해도 진행.

    Returns:
        EpisodeContext (news_snippets + community_posts + metadata)
    """
    ctx = EpisodeContext()

    # 검색 쿼리 — 회차 포함 버전 + 프로그램명만 버전
    ep_query = f'{program_name} {episode_num}회'
    name_query = program_name

    _fetch_google_news(ep_query, ctx)

    # 뉴스가 없으면 프로그램명으로도 시도
    if not ctx.news_snippets:
        _fetch_google_news(name_query, ctx)

    _fetch_theqoo(name_query, ctx)
    _fetch_dcinside(name_query, ctx)

    return ctx


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--program', required=True)
    parser.add_argument('--episode', type=int, required=True)
    args = parser.parse_args()

    result = fetch_episode_context(args.program, args.episode)
    print(json.dumps({
        'news_snippets': result.news_snippets,
        'community_posts': result.community_posts,
        'sources_used': result.sources_used,
        'errors': result.errors,
        'prompt_text': result.to_prompt_text(),
    }, ensure_ascii=False, indent=2))
