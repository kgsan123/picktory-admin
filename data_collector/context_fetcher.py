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
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
TIMEOUT = 8
GOOGLE_RSS = 'https://news.google.com/rss/search'

# 프로그램별 검색 제외 키워드 — 유사 이름 프로그램과 혼용 방지
SHOW_EXCLUDE_TERMS: dict[str, list[str]] = {
    '나는 SOLO': ['사계', '그 후', '그후', '사랑은 계속된다'],
    '나는 솔로': ['사계', '그 후', '그후', '사랑은 계속된다'],
}


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
        # 단, X기 패턴 이름이 이미 충분하면 skip (오탐 줄이기)
        if len(result) < 3:
            for m in re.finditer(r'([가-힣]{2,3})(씨|이(?=[가-힣\s])|야|아(?=[가-힣\s]))', text):
                name = m.group(1)
                if name not in seen and not _is_common_word(name) and len(name) >= 2:
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
    trailer_snippets: list[str] = field(default_factory=list)  # 다음 회차 예고
    chart_snippets: list[str] = field(default_factory=list)    # 음악방송 전용
    cast_names: list[str] = field(default_factory=list)        # 컨텍스트에서 추출한 이름
    episode_summary: str = ''                                   # 나무위키 회차 줄거리(사실)
    show_notes: str = ''                                        # 프로그램 형식 설명
    ep_num: int = 0                                             # 방영된 회차 번호
    sources_used: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        parts = []
        # 프로그램 형식 설명 (있을 때)
        if self.show_notes:
            parts.append(f'[프로그램 형식] {self.show_notes}')
        # 회차 줄거리(사실) — 최상단 앵커. AI가 학습데이터 대신 이 사실에 근거하도록.
        if self.episode_summary:
            parts.append(f'[{self._ep_label()}회 실제 줄거리(사실 — 이 내용에 근거하여 예측 작성)]')
            parts.append(self.episode_summary)
        # 이름 목록 — 있으면 사용, 없으면 "이름 없음" 명시
        if self.cast_names:
            parts.append(f'[이번 회차 등장인물] {", ".join(self.cast_names)}')
            parts.append('※ 위 이름만 사용. 학습 데이터·다른 프로그램 이름 절대 금지.')
        else:
            parts.append('[이번 회차 등장인물] 컨텍스트에서 이름 확인 불가')
            parts.append('※ 이름을 만들어 내지 마세요. 이름 없이 가능한 유형(선택 결과, 순위, 이벤트 발생 여부)으로만 예측 작성.')
        if self.news_snippets:
            parts.append(f'[{self._ep_label()}회 방영 후 뉴스]')
            parts.extend(f'- {s}' for s in self.news_snippets[:5])
        if self.community_posts:
            parts.append(f'[{self._ep_label()}회 커뮤니티 반응]')
            parts.extend(f'- {s}' for s in self.community_posts[:5])
        if self.trailer_snippets:
            parts.append('[다음 회차 예고]')
            parts.extend(f'- {s}' for s in self.trailer_snippets[:3])
        return '\n'.join(parts)

    def _ep_label(self) -> str:
        return str(self.ep_num) if self.ep_num else 'N'

    def to_chart_text(self) -> str:
        if not self.chart_snippets:
            return ''
        return '[이번 주 1위 후보 동향]\n' + '\n'.join(f'- {s}' for s in self.chart_snippets[:4])

    def is_empty(self) -> bool:
        return (not self.news_snippets and not self.community_posts
                and not self.chart_snippets and not self.episode_summary)

    def has_sufficient_signal(self) -> bool:
        """예측 생성에 충분한 신호가 있는지 — 환각 방지 게이트용."""
        if self.episode_summary:
            return True
        return (len(self.news_snippets) + len(self.community_posts)) >= 2


def _entry_datetime(entry) -> datetime | None:
    """feedparser entry의 published 날짜를 UTC datetime으로 반환."""
    tp = entry.get('published_parsed')
    if not tp:
        return None
    try:
        return datetime(*tp[:6], tzinfo=timezone.utc)
    except Exception:
        return None


def _google_rss(query: str, ctx: EpisodeContext, label: str,
                is_community: bool = False, program_name: str = '',
                exclude_terms: list[str] | None = None,
                after_dt: datetime | None = None,
                require_program_name: bool = False) -> None:
    """
    after_dt: 이 시각 이후 발행된 기사만 수집 (방영 후 콘텐츠 필터).
              None이면 날짜 필터 없음.
    """
    try:
        feed = feedparser.parse(
            f'{GOOGLE_RSS}?q={requests.utils.quote(query)}&hl=ko&gl=KR&ceid=KR:ko'
        )
        results = []
        keywords = [w for w in re.split(r'\s+', program_name) if len(w) >= 2] if program_name else []
        excl = [e.lower() for e in (exclude_terms or [])]
        # 스포일러만 제외 — 예고/예상 기사는 방영 후에도 귀추 주목 컨텍스트로 활용
        preview_kw = ['스포'] if after_dt else []

        for entry in feed.entries[:10]:
            # 날짜 필터: 방영 시각 이후 발행된 글만
            if after_dt:
                pub = _entry_datetime(entry)
                if pub and pub < after_dt:
                    continue

            title = re.sub(r'\s*-\s*[^-]{1,30}$', '', entry.get('title', '')).strip()
            title_raw = title
            title = _clean(title)
            summary = _clean(entry.get('summary', ''))[:120]
            if not title:
                continue

            combined_raw = (title_raw + entry.get('summary', '')).lower()
            # 제외 키워드
            if excl and any(e in combined_raw for e in excl):
                continue
            # 방영 전 예고 기사 제외
            if preview_kw and any(kw in combined_raw for kw in preview_kw):
                continue

            text = title if not summary or summary[:30] in title else f'{title}. {summary}'
            # 커뮤니티 또는 require_program_name: 프로그램명 포함 여부 확인
            if (is_community or require_program_name) and keywords:
                combined = (title + summary).replace(' ', '')
                if not any(kw.replace(' ', '') in combined for kw in keywords):
                    continue
            results.append(text)

        if results:
            if label == 'trailer':
                ctx.trailer_snippets.extend(results[:3])
            elif is_community:
                ctx.community_posts.extend(results[:6])
            else:
                ctx.news_snippets.extend(results[:6])
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


def fetch_episode_context(program_name: str, episode_num: int,
                          category: str = 'drama', show_notes: str = '',
                          aired_at: datetime | None = None) -> EpisodeContext:
    """
    방영 후 콘텐츠(후기·반응·시청률)만 수집해 다음 회차 예측에 활용.
    aired_at: 방영 시각 (KST datetime). 이 시각 이후 발행된 콘텐츠만 수집.
    show_notes: DB shows.notes — AI에 전달되는 프로그램 형식 설명.
    """
    ctx = EpisodeContext()
    ctx.ep_num = episode_num
    if show_notes:
        ctx.show_notes = show_notes

    excl = SHOW_EXCLUDE_TERMS.get(program_name, [])
    # aired_at을 UTC로 변환 (feedparser는 UTC 기준)
    after_dt: datetime | None = None
    if aired_at:
        if aired_at.tzinfo is None:
            aired_at = aired_at.replace(tzinfo=KST)
        after_dt = aired_at.astimezone(timezone.utc)

    ep = str(episode_num)

    # ── 1. 방영 후 뉴스 (시청률·후기·결과) ──────────────────────
    # 가장 구체적인 쿼리부터 시도
    for q in [
        f'{program_name} {ep}회 시청률',
        f'{program_name} {ep}회 후기',
        f'{program_name} {ep}회',
        program_name,
    ]:
        _google_rss(q, ctx, f'news_{q[:10]}',
                    program_name=program_name, exclude_terms=excl,
                    after_dt=after_dt, require_program_name=True)
        if len(ctx.news_snippets) >= 4:
            break

    # ── 2. 다음 회차 예고 (trailer_snippets에 저장) ──────────────
    next_ep = str(episode_num + 1)
    _google_rss(f'{program_name} {next_ep}회 예고', ctx, 'trailer',
                program_name=program_name, exclude_terms=excl,
                require_program_name=True)  # 관련 없는 예고 차단

    # ── 3. 커뮤니티 반응 (더쿠 via Google) ──────────────────────
    _google_rss(f'{program_name} {ep}회 site:theqoo.net', ctx, 'theqoo',
                is_community=True, program_name=program_name,
                exclude_terms=excl, after_dt=after_dt)
    if not ctx.community_posts:
        _google_rss(f'{program_name} site:theqoo.net', ctx, 'theqoo_general',
                    is_community=True, program_name=program_name,
                    exclude_terms=excl, after_dt=after_dt)

    # DC인사이드
    _dcinside(program_name, ctx)

    # 음악 방송 전용: 차트 동향
    if category == 'music':
        _google_rss_chart(f'{program_name} 1위 예측', ctx, 'chart_prediction')
        if not ctx.chart_snippets:
            _google_rss_chart('이번주 음악방송 1위 후보', ctx, 'chart_general')

    # ── 4. 나무위키 회차 줄거리 (사실 앵커) ──────────────────────
    # 방영 후 수 시간 내 올라오는 회차 정리. 환각 차단의 핵심 신호.
    try:
        from .episode_summary import fetch_episode_summary
        summary_result = fetch_episode_summary(program_name, episode_num)
        summary_text = (summary_result.get('episode_summary') or '').strip()
        if summary_text:
            ctx.episode_summary = _clean(summary_text)
            ctx.sources_used.append(summary_result.get('source', 'namu_wiki'))
    except Exception as e:
        ctx.errors.append(f'episode_summary 실패: {e}')

    # 모든 텍스트에서 출연자 이름 추출 (AI가 이 이름만 사용하도록)
    all_text = ctx.news_snippets + ctx.community_posts
    if ctx.episode_summary:
        all_text = all_text + [ctx.episode_summary]
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
