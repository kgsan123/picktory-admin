"""
에피소드 컨텍스트 수집 — 예측 생성 및 정답 검증에 사용.
소스: Google News RSS (뉴스 + 더쿠/커뮤니티 글) + DC인사이드
텍스트 스니펫 반환 — 숫자 점수 아님.

Usage: python -m data_collector.context_fetcher --program "무한도전" --episode 400
"""
import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field

import feedparser
import requests
from bs4 import BeautifulSoup

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
TIMEOUT = 8
GOOGLE_RSS = 'https://news.google.com/rss/search'


@dataclass
class EpisodeContext:
    news_snippets: list[str] = field(default_factory=list)
    community_posts: list[str] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        parts = []
        if self.news_snippets:
            parts.append('[뉴스 기사]')
            parts.extend(f'- {s}' for s in self.news_snippets[:5])
        if self.community_posts:
            parts.append('[커뮤니티 반응]')
            parts.extend(f'- {s}' for s in self.community_posts[:6])
        return '\n'.join(parts)

    def is_empty(self) -> bool:
        return not self.news_snippets and not self.community_posts


def _google_rss(query: str, ctx: EpisodeContext, label: str, is_community: bool = False) -> None:
    """Google News RSS — feedparser 사용."""
    try:
        feed = feedparser.parse(
            f'{GOOGLE_RSS}?q={requests.utils.quote(query)}&hl=ko&gl=KR&ceid=KR:ko'
        )
        results = []
        for entry in feed.entries[:8]:
            title = re.sub(r'\s*-\s*[^-]+$', '', entry.get('title', '')).strip()  # 출처 제거
            summary = re.sub(r'<[^>]+>', '', entry.get('summary', ''))
            summary = re.sub(r'&\w+;', ' ', summary).strip()[:80]  # HTML 엔티티 제거
            if not title:
                continue
            # 요약이 제목과 거의 같으면 제목만 사용
            text = title if not summary or summary[:30] in title else f'{title}. {summary}'
            results.append(text)

        if results:
            if is_community:
                ctx.community_posts.extend(results[:5])
            else:
                ctx.news_snippets.extend(results[:5])
            ctx.sources_used.append(label)
    except Exception as e:
        ctx.errors.append(f'{label}: {e}')


def _dcinside(query: str, ctx: EpisodeContext) -> None:
    """DC인사이드 갤러리 — 드라마/연예/kpop 검색."""
    try:
        time.sleep(1)
        url = 'https://gall.dcinside.com/board/lists/'
        # 카테고리별 갤러리 순차 시도
        for gall_id in ('drama', 'kpop', 'entertainer'):
            params = {
                'id': gall_id,
                'list_num': 30,
                's_type': 'search_subject_memo',
                's_keyword': query,
            }
            resp = requests.get(url, params=params,
                                headers={'User-Agent': UA}, timeout=TIMEOUT)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, 'html.parser')
            found = []
            for row in soup.select('tr.ub-content'):
                title_el = row.select_one('.gall_tit a')
                if not title_el:
                    continue
                text = title_el.get_text(strip=True)
                if text and len(text) > 5 and '공지' not in text:
                    found.append(f'[DC] {text}')
            if found:
                ctx.community_posts.extend(found[:5])
                ctx.sources_used.append(f'dc_{gall_id}')
                break
    except Exception as e:
        ctx.errors.append(f'dcinside: {e}')


def fetch_episode_context(program_name: str, episode_num: int) -> EpisodeContext:
    """
    방송 직후 컨텍스트 수집.
    - Google News RSS: 뉴스 기사 (회차 명시 쿼리 → 프로그램명 쿼리)
    - Google News RSS (더쿠): site:theqoo.net 쿼리
    - DC인사이드: 갤러리 검색

    Returns:
        EpisodeContext (뉴스 + 커뮤니티 텍스트)
    """
    ctx = EpisodeContext()

    # 뉴스: 회차 명시 쿼리 우선
    _google_rss(f'{program_name} {episode_num}회', ctx, 'google_news_ep')
    if not ctx.news_snippets:
        _google_rss(program_name, ctx, 'google_news')

    # 더쿠: Google로 theqoo.net 내 글 검색
    _google_rss(f'{program_name} site:theqoo.net', ctx, 'theqoo', is_community=True)

    # DC인사이드
    _dcinside(program_name, ctx)

    return ctx


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--program', required=True)
    parser.add_argument('--episode', type=int, required=True)
    args = parser.parse_args()

    result = fetch_episode_context(args.program, args.episode)
    out = {
        'news_snippets': result.news_snippets,
        'community_posts': result.community_posts,
        'sources_used': result.sources_used,
        'errors': result.errors,
        'prompt_text': result.to_prompt_text(),
    }
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False, indent=2).encode('utf-8'))
    print()
