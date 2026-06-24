"""
에피소드 컨텍스트 수집 — 예측 생성 및 정답 검증에 사용.
소스: Google News RSS (뉴스 + 더쿠/커뮤니티) + DC인사이드
카테고리별 추가 수집: music → 차트 동향

Usage: python -m data_collector.context_fetcher --program "나는 SOLO" --episode 25 --category romance
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


def _clean(text: str) -> str:
    """한자·제어문자·HTML 엔티티 제거, 한글/영문/숫자/기본 기호만 유지."""
    text = re.sub(r'&\w+;', ' ', text)          # HTML 엔티티
    text = re.sub(r'<[^>]+>', '', text)          # HTML 태그
    text = re.sub(r'[^가-힣ㄱ-ㆎ\w\s.,·!?()\[\]%\'\"\-/]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


@dataclass
class EpisodeContext:
    news_snippets: list[str] = field(default_factory=list)
    community_posts: list[str] = field(default_factory=list)
    chart_snippets: list[str] = field(default_factory=list)   # 음악방송 전용
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

    def to_chart_text(self) -> str:
        if not self.chart_snippets:
            return ''
        return '[이번 주 1위 후보 동향]\n' + '\n'.join(f'- {s}' for s in self.chart_snippets[:4])

    def is_empty(self) -> bool:
        return not self.news_snippets and not self.community_posts and not self.chart_snippets


def _google_rss(query: str, ctx: EpisodeContext, label: str, is_community: bool = False) -> None:
    try:
        feed = feedparser.parse(
            f'{GOOGLE_RSS}?q={requests.utils.quote(query)}&hl=ko&gl=KR&ceid=KR:ko'
        )
        results = []
        for entry in feed.entries[:8]:
            # 출처 제거: "기사 제목 - 출처명" → "기사 제목"
            title = re.sub(r'\s*-\s*[^-]{1,30}$', '', entry.get('title', '')).strip()
            title = _clean(title)
            summary = _clean(entry.get('summary', ''))[:80]
            if not title:
                continue
            text = title if not summary or summary[:30] in title else f'{title}. {summary}'
            results.append(text)

        if results:
            target = ctx.community_posts if is_community else ctx.news_snippets
            target.extend(results[:5])
            ctx.sources_used.append(label)
    except Exception as e:
        ctx.errors.append(f'{label}: {e}')


def _google_rss_chart(query: str, ctx: EpisodeContext, label: str) -> None:
    """차트 동향 전용 — chart_snippets에 저장."""
    try:
        feed = feedparser.parse(
            f'{GOOGLE_RSS}?q={requests.utils.quote(query)}&hl=ko&gl=KR&ceid=KR:ko'
        )
        for entry in feed.entries[:5]:
            title = re.sub(r'\s*-\s*[^-]{1,30}$', '', entry.get('title', '')).strip()
            title = _clean(title)
            if title:
                ctx.chart_snippets.append(title)
        if ctx.chart_snippets:
            ctx.sources_used.append(label)
    except Exception as e:
        ctx.errors.append(f'{label}: {e}')


def _dcinside(query: str, ctx: EpisodeContext) -> None:
    try:
        time.sleep(1)
        url = 'https://gall.dcinside.com/board/lists/'
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
                text = _clean(title_el.get_text(strip=True))
                if text and len(text) > 5 and '공지' not in text:
                    found.append(f'[DC] {text}')
            if found:
                ctx.community_posts.extend(found[:5])
                ctx.sources_used.append(f'dc_{gall_id}')
                break
    except Exception as e:
        ctx.errors.append(f'dcinside: {e}')


def fetch_episode_context(program_name: str, episode_num: int, category: str = 'drama') -> EpisodeContext:
    """
    카테고리별 컨텍스트 수집.
    - 공통: Google News RSS (회차 + 프로그램명) + 더쿠 (site:theqoo.net) + DC인사이드
    - music 추가: 차트 동향 뉴스

    Returns:
        EpisodeContext
    """
    ctx = EpisodeContext()

    # 공통: 회차 명시 뉴스 → 프로그램명 뉴스
    _google_rss(f'{program_name} {episode_num}회', ctx, 'news_ep')
    if not ctx.news_snippets:
        _google_rss(program_name, ctx, 'news_name')

    # 더쿠 (Google 인덱싱)
    _google_rss(f'{program_name} site:theqoo.net', ctx, 'theqoo', is_community=True)

    # DC인사이드
    _dcinside(program_name, ctx)

    # 음악 방송 전용: 차트 동향
    if category == 'music':
        _google_rss_chart(f'{program_name} 1위 예측', ctx, 'chart_prediction')
        if not ctx.chart_snippets:
            _google_rss_chart('이번주 음악방송 1위 후보', ctx, 'chart_general')

    return ctx


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--program', required=True)
    parser.add_argument('--episode', type=int, required=True)
    parser.add_argument('--category', default='drama')
    args = parser.parse_args()

    result = fetch_episode_context(args.program, args.episode, args.category)
    out = {
        'news_snippets': result.news_snippets,
        'community_posts': result.community_posts,
        'chart_snippets': result.chart_snippets,
        'sources_used': result.sources_used,
        'errors': result.errors,
        'prompt_text': result.to_prompt_text(),
        'chart_text': result.to_chart_text(),
    }
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False, indent=2).encode('utf-8'))
    print()
