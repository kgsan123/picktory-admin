"""
출연자 명단 자동 추천.
나무위키(등장인물/출연진) + 뉴스에서 텍스트를 모아 LLM으로 실명만 추출.

100% 정확도는 운영자 확인 단계에서 보장한다:
이 모듈은 "후보"를 제안할 뿐이고, 예측 생성은 운영자가 [설정 저장]으로
확정한 shows.cast_names만 사용한다. 자동 추출분은 저장 전까지 쓰이지 않는다.

Usage: python -m data_collector.cast_suggest --program "나는 SOLO"
"""
import argparse
import json
import logging
import os
import re

from dotenv import load_dotenv
from groq import Groq

load_dotenv()
log = logging.getLogger(__name__)
MODEL = 'llama-3.3-70b-versatile'

# 한국 실명: 한글 2~4자
_HANGUL_NAME = re.compile(r'^[가-힣]{2,4}$')
# 출연자가 아닌 흔한 오탐
_NOT_NAME = {
    '출연자', '참가자', '등장인물', '진행자', '제작진', '스태프', '게스트',
    '프로그램', '시청자', '아나운서', '리포터', '내레이션', '본방송',
}


def _gather_text(program_name: str) -> str:
    """나무위키 등장인물/출연진 + 뉴스 텍스트 수집."""
    chunks = []
    # 나무위키 — 등장인물/출연진 페이지 우선 (cast 밀도 높음)
    try:
        from data_collector.episode_summary import _fetch_namu_page
        from bs4 import BeautifulSoup
        for title in (f'{program_name}/출연진', f'{program_name}/등장인물', program_name):
            html = _fetch_namu_page(title)
            if not html:
                continue
            txt = BeautifulSoup(html, 'html.parser').get_text(separator=' ', strip=True)
            if txt and len(txt) > 200:
                chunks.append(f'[나무위키:{title}]\n{txt[:4500]}')
                break
    except Exception as e:
        log.debug(f'namu 수집 실패: {e}')
    # 뉴스 — 출연자 관련 기사 제목
    try:
        from data_collector.context_fetcher import _google_rss, EpisodeContext
        ctx = EpisodeContext()
        _google_rss(f'{program_name} 출연자', ctx, 'cast_news',
                    program_name=program_name, require_program_name=True)
        if ctx.news_snippets:
            chunks.append('[뉴스]\n' + ' / '.join(ctx.news_snippets[:8]))
    except Exception as e:
        log.debug(f'뉴스 수집 실패: {e}')
    return '\n\n'.join(chunks)[:6000]


def _llm_extract(program_name: str, text: str) -> list[str]:
    """모은 텍스트에서 현재 시즌 출연자 실명만 추출. 실패 시 예외 전파."""
    key = os.environ.get('GROQ_API_KEY', '')
    if not key:
        raise RuntimeError('GROQ_API_KEY 미설정')
    system = (
        '아래 자료에서 해당 프로그램의 현재 시즌 "출연자/참가자 실명"만 추출하라.\n'
        '- 자료에 실제로 등장하는 이름만. 추측·창작 금지.\n'
        '- 진행자(MC)·제작진·다른 프로그램 인물·일반명사 제외.\n'
        '- 한국 실명(한글 2~4자)만.\n'
        'JSON 배열만 출력: ["이름1","이름2"]'
    )
    user = f'프로그램: {program_name}\n\n자료:\n{text}'
    resp = Groq(api_key=key).chat.completions.create(
        model=MODEL,
        messages=[{'role': 'system', 'content': system},
                  {'role': 'user', 'content': user}],
        temperature=0.0, max_tokens=512,
    )
    raw = resp.choices[0].message.content
    m = re.search(r'\[[\s\S]*\]', raw)
    if not m:
        return []
    try:
        names = json.loads(m.group())
    except json.JSONDecodeError:
        return []
    out = []
    for n in names:
        n = str(n).strip()
        if _HANGUL_NAME.match(n) and n not in _NOT_NAME and n not in out:
            out.append(n)
    return out


def suggest_cast(program_name: str, category: str = 'variety') -> list[str]:
    """출연자 후보 명단 추천. 빈 리스트면 자료 부족."""
    text = _gather_text(program_name)
    if not text.strip():
        return []
    return _llm_extract(program_name, text)


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument('--program', required=True)
    args = p.parse_args()
    names = suggest_cast(args.program)
    sys.stdout.buffer.write(json.dumps(names, ensure_ascii=False, indent=2).encode('utf-8'))
    print()
