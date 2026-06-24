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
    """한자·일본어·제어문자·HTML 엔티티 제거, 한글/영문/숫자/기본 기호만 유지."""
    text = re.sub(r'&\w+;', ' ', text)
    text = re.sub(r'<[^>]+>', '', text)
    # CJK 한자·일본어 명시적 제거 (한글 범위 제외)
    text = re.sub(r'[一-鿿㐀-䶿　-〿＀-￯぀-ヿ]', '', text)
    # 한글·영문·숫자·기본 기호만 허용
    text = re.sub(r'[^가-힣ㄱ-ㆎa-zA-Z0-9\s.,·!?()\[\]%\'\"\-/]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def _extract_names(snippets: list[str]) -> list[str]:
    """컨텍스트 스니펫에서 출연자 이름 추출.
    나는 SOLO 스타일 'X기 이름' 패턴에 집중. 오탐 최소화.
    """
    seen: set[str] = set()
    result: list[str] = []

    PARTICLES = re.compile(r'(와|과|이|가|은|는|을|를|의|에게|에서|에|씨|야|아)$')

    for text in snippets:
        # 패턴 1: "25기 영자", "20기 영식" (나는 SOLO 시리즈) — 조사 제거
        for m in re.finditer(r'(\d+기)\s*([가-힣]{2,4})', text):
            name_part = PARTICLES.sub('', m.group(2))
            if len(name_part) < 2:
                continue
            label = f'{m.group(1)} {name_part}'
            if label not in seen:
                seen.add(label)
                result.append(label)
        # 패턴 2: "영자씨", "영식이" — 호칭이 붙은 이름 (2~3자)
        for m in re.finditer(r'([가-힣]{2,3})(씨|이(?=[가-힣\s])|야|아(?=[가-힣\s]))', text):
            name = m.group(1)
            if name not in seen and not _is_common_word(name):
                seen.add(name)
                result.append(name)

    return result


def _is_common_word(word: str) -> bool:
    COMMON = {
        '이번', '지금', '오늘', '내일', '다음', '마음', '사랑', '관계', '상황',
        '선택', '미션', '결과', '발표', '출연', '방송', '프로그램', '이상', '이하',
        '그것', '이것', '저것', '무엇', '어떤', '어디', '우리', '그들', '그녀',
        '남자', '여자', '남성', '여성', '솔로', '커플', '게스트', '출연자',
        '진행', '진심', '진짜', '정말', '사실', '생각', '느낌', '눈물',
        '대화', '이야기', '질문', '대답', '고백', '키스', '데이트',
    }
    return word in COMMON


@dataclass
class EpisodeContext:
    news_snippets: list[str] = field(default_factory=list)
    community_posts: list[str] = field(default_factory=list)
    chart_snippets: list[str] = field(default_factory=list)   # 음악방송 전용
    cast_names: list[str] = field(default_factory=list)       # 컨텍스트에서 추출한 이름
    sources_used: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        parts = []
        # 이름 목록을 맨 앞에 — AI가 이것만 사용하도록
        if self.cast_names:
            parts.append(f'[이번 회차 등장인물] {", ".join(self.cast_names)}')
            parts.append('※ 위 이름 외 다른 이름은 절대 사용 금지')
        if self.news_snippets:
            parts.append('[뉴스 기사]')
            parts.extend(f'- {s}' for s in self.news_snippets[:5])
        if self.community_posts:
            parts.append('[커뮤니티 반응]')
            parts.extend(f'- {s}' for s in self.community_posts[:5])
        return '\n'.join(parts)

    def to_chart_text(self) -> str:
        if not self.chart_snippets:
            return ''
        return '[이번 주 1위 후보 동향]\n' + '\n'.join(f'- {s}' for s in self.chart_snippets[:4])

    def is_empty(self) -> bool:
        return not self.news_snippets and not self.community_posts and not self.chart_snippets


def _google_rss(query: str, ctx: EpisodeContext, label: str,
                is_community: bool = False, program_name: str = '') -> None:
    try:
        feed = feedparser.parse(
            f'{GOOGLE_RSS}?q={requests.utils.quote(query)}&hl=ko&gl=KR&ceid=KR:ko'
        )
        results = []
        # 커뮤니티 포스트는 프로그램명 포함 여부로 관련성 필터
        keywords = [w for w in re.split(r'\s+', program_name) if len(w) >= 2] if program_name else []
        for entry in feed.entries[:8]:
            title = re.sub(r'\s*-\s*[^-]{1,30}$', '', entry.get('title', '')).strip()
            title = _clean(title)
            summary = _clean(entry.get('summary', ''))[:80]
            if not title:
                continue
            text = title if not summary or summary[:30] in title else f'{title}. {summary}'
            # 커뮤니티 포스트: 관련 키워드 없으면 skip
            if is_community and keywords:
                combined = (title + summary).replace(' ', '')
                if not any(kw.replace(' ', '') in combined for kw in keywords):
                    continue
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

    # 더쿠 (Google 인덱싱) — 관련 없는 포스트 필터링
    _google_rss(f'{program_name} site:theqoo.net', ctx, 'theqoo',
                is_community=True, program_name=program_name)

    # DC인사이드
    _dcinside(program_name, ctx)

    # 음악 방송 전용: 차트 동향
    if category == 'music':
        _google_rss_chart(f'{program_name} 1위 예측', ctx, 'chart_prediction')
        if not ctx.chart_snippets:
            _google_rss_chart('이번주 음악방송 1위 후보', ctx, 'chart_general')

    # 모든 텍스트에서 출연자 이름 추출 (AI가 이 이름만 사용하도록)
    all_text = ctx.news_snippets + ctx.community_posts
    ctx.cast_names = _extract_names(all_text)

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
