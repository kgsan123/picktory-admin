"""
AI 예측 생성기 — 다음 회차 예측 문항 자동 생성.
Groq (llama-3.3-70b-versatile) 사용.

Usage: python -m ai_engine.prediction_generator --episode_id <uuid>
"""
import argparse
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from groq import Groq

load_dotenv()
log = logging.getLogger(__name__)
KST = ZoneInfo('Asia/Seoul')

PROMPT_DIR = Path(__file__).parent / 'prompts'
MODEL = 'llama-3.3-70b-versatile'
PROMPT_VERSION = 'v5'


def _load_prompt(category: str) -> tuple[str, str]:
    path = PROMPT_DIR / f'generator_{category}_{PROMPT_VERSION}.txt'
    if not path.exists():
        path = PROMPT_DIR / f'generator_drama_{PROMPT_VERSION}.txt'
    text = path.read_text(encoding='utf-8')
    parts = text.split('[USER]')
    system = parts[0].split('[SYSTEM]')[1].strip()
    user_template = parts[1].strip()
    return system, user_template


_CJK_RE = re.compile(r'[一-鿿㐀-䶿　-〿＀-￯]')

def _clean_prediction(p: dict) -> dict:
    """생성된 예측에서 한자·일본어 제거."""
    for field in ('title', 'content', 'verification_method'):
        if field in p:
            p[field] = _CJK_RE.sub('', p[field]).strip()
    if 'options' in p:
        for opt in p['options']:
            if 'text' in opt:
                opt['text'] = _CJK_RE.sub('', opt['text']).strip()
    return p


def _parse_predictions(text: str) -> list[dict] | None:
    text = text.strip()
    match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if match:
        text = match.group(1).strip()
    try:
        data = json.loads(text)
        return data.get('predictions', [])
    except json.JSONDecodeError:
        return None


_VAGUE_VERIFY = ['방영 후 확인', '해당 회차에서 확인', '추후 확인', '나중에 확인', '확인 가능']

def _apply_filters(predictions: list[dict]) -> list[dict]:
    """품질 필터 적용. 통과한 예측만 반환."""
    kept = []
    for p in predictions:
        fun = p.get('fun_score', 0)
        diff = p.get('difficulty', 3)
        verify = p.get('verification_method', '').strip()
        options = p.get('options', [])
        content = p.get('content', '')

        if fun < 3:
            log.debug(f"필터 제거 (fun_score={fun}): {p.get('title')}")
            continue
        # verification_method 최소 20자 + 모호한 표현 금지
        if not verify or len(verify) < 20:
            log.debug(f"필터 제거 (verification 너무 짧음): {p.get('title')}")
            continue
        if any(v in verify for v in _VAGUE_VERIFY):
            log.debug(f"필터 제거 (verification 모호): {p.get('title')}")
            continue
        if not options or len(options) < 2:
            log.debug(f"필터 제거 (options 부족): {p.get('title')}")
            continue
        # YES/NO 형식 거부 (options가 정확히 YES/NO 두 개인 경우)
        opt_ids = {o.get('id', '').upper() for o in options}
        if opt_ids == {'YES', 'NO'}:
            log.debug(f"필터 제거 (YES/NO 형식): {p.get('title')}")
            continue
        # 플레이스홀더 금지
        placeholder = any(t in content for t in ['A 출연자', 'B 출연자', 'A 아이돌', 'B 아이돌', 'A팀', 'B팀', '주요 커플'])
        if placeholder:
            log.debug(f"필터 제거 (플레이스홀더): {p.get('title')}")
            continue

        if diff == 1:
            max_odds = max(o.get('odds', 0) for o in options)
            if max_odds > 0.85:
                log.debug(f"필터 제거 (너무 뻔함): {p.get('title')}")
                continue

        kept.append(p)
    return kept


def generate_predictions(
    episode: dict,
    context: dict,
    temperature: float = 0.7,
) -> list[dict]:
    """
    Args:
        episode: {program_name, episode_number, category, ...}
        context: {episode_summary, trailer_hints, news_summary,
                  reaction_score, top_clip_views}
        temperature: 기본 0.7, 재시도 시 +0.2

    Returns:
        필터 통과한 predictions 리스트.
    """
    category = episode.get('category', 'drama')
    next_ep = (episode.get('episode_number') or 1) + 1
    system_prompt, user_template = _load_prompt(category)

    # {chart_context}는 music 프롬프트에만 있으므로 없는 필드는 빈 문자열로 처리
    from collections import defaultdict
    fmt = defaultdict(str, {
        'program_name': episode.get('program_name', ''),
        'next_episode': str(next_ep),
        'episode_summary': context.get('episode_summary') or '정보 없음',
        'trailer_hints': context.get('trailer_hints') or '없음',
        'news_summary': context.get('news_summary') or '없음',
        'chart_context': context.get('chart_context') or '',
    })
    user_msg = user_template.format_map(fmt)

    api_key = os.environ.get('GROQ_API_KEY', '')
    if not api_key:
        try:
            import streamlit as st
            api_key = st.secrets.get('GROQ_API_KEY', '')
        except Exception:
            pass
    if not api_key:
        raise RuntimeError('GROQ_API_KEY가 설정되지 않았습니다')

    client = Groq(api_key=api_key)
    last_error = ''

    for attempt in range(2):
        temp = temperature + (0.2 if attempt > 0 else 0)
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_msg},
                ],
                temperature=min(temp, 1.0),
                max_tokens=4096,
            )
            raw = resp.choices[0].message.content
            predictions = _parse_predictions(raw)
            if predictions is None:
                last_error = f'JSON 파싱 실패 (시도 {attempt+1})'
                log.warning(last_error)
                time.sleep(2)
                continue

            predictions = [_clean_prediction(p) for p in predictions]
            filtered = _apply_filters(predictions)[:6]  # 최대 6개
            if len(filtered) >= 4:  # 4개 이상이면 통과 (필터로 일부 탈락 감안)
                for p in filtered:
                    p['prompt_version'] = PROMPT_VERSION
                return filtered

            last_error = f'필터 후 {len(filtered)}개 (최소 4개 필요)'
            log.info(last_error)
            time.sleep(2)

        except Exception as e:
            last_error = str(e)
            log.warning(f'Groq API 시도 {attempt+1} 실패: {e}')
            time.sleep(3)

    raise RuntimeError(f'예측 생성 실패: {last_error}')


def generate_episode_predictions(episode_id: str, extra_context: dict | None = None) -> list[dict]:
    """
    DB에서 에피소드 조회 → 예측 생성 → DB 저장.
    extra_context: 관리자가 직접 입력한 이번 회차 요약/예고 (우선 적용).
    Returns saved predictions list.
    """
    from db import get_client

    client = get_client()
    ep = client.table('episodes').select('*').eq('id', episode_id).single().execute().data
    if not ep:
        log.error(f'에피소드 미발견: {episode_id}')
        return []

    ep_num = ep.get('episode_number') or 1
    program_name = ep.get('program_name', '')

    # shows 테이블에서 프로그램 형식 설명(notes) 조회
    show_notes = ''
    try:
        show_row = client.table('shows').select('notes').eq('name', program_name).maybe_single().execute()
        if show_row and show_row.data:
            show_notes = (show_row.data.get('notes') or '').strip()
    except Exception:
        pass

    # 우선순위: 관리자 입력 > 자동 수집(Google News + 더쿠 + DC) > DB 기존 데이터
    operator_summary = (extra_context or {}).get('episode_summary', '').strip()
    if operator_summary:
        auto_text = operator_summary
    else:
        try:
            from data_collector.context_fetcher import fetch_episode_context
            category = ep.get('category', 'drama')
            # aired_at 파싱 (KST datetime)
            from datetime import datetime as _dt
            from zoneinfo import ZoneInfo as _ZI
            aired_at_raw = ep.get('aired_at')
            aired_at_dt = None
            if aired_at_raw:
                try:
                    aired_at_dt = _dt.fromisoformat(str(aired_at_raw).replace('Z', '+00:00'))
                except Exception:
                    pass

            ep_ctx = fetch_episode_context(
                program_name, ep_num, category=category,
                show_notes=show_notes, aired_at=aired_at_dt,
            )
            auto_text = ep_ctx.to_prompt_text() or ep.get('news_summary') or ''
            chart_text = ep_ctx.to_chart_text()
            # 자동 수집 예고를 operator 미입력시 fallback으로 사용
            auto_trailer = '\n'.join(ep_ctx.trailer_snippets) if ep_ctx.trailer_snippets else ''
            if auto_text:
                client.table('episodes').update(
                    {'news_summary': auto_text[:1000]}
                ).eq('id', episode_id).execute()
            if ep_ctx.errors:
                log.debug(f'context_fetcher 부분 실패: {ep_ctx.errors}')
        except Exception as e:
            log.warning(f'context_fetcher 실패 (무시): {e}')
            auto_text = ep.get('news_summary') or ''
            chart_text = ''
            auto_trailer = ''

    # trailer_hints: 운영자 직접 입력 > 자동 수집 예고
    trailer_hints = (extra_context or {}).get('trailer_hints', '').strip()
    if not trailer_hints:
        trailer_hints = auto_trailer

    context: dict = {
        'episode_summary': auto_text,
        'trailer_hints': trailer_hints,
        'news_summary': auto_text,
        'chart_context': chart_text,
    }

    predictions = generate_predictions(ep, context)
    if not predictions:
        return []

    rows = []
    for p in predictions:
        rows.append({
            'episode_id': episode_id,
            'category': p.get('category', ep.get('category', 'drama')),
            'title': p.get('title', ''),
            'content': p.get('content', ''),
            'options': p.get('options', []),
            'difficulty': p.get('difficulty', 3),
            'fun_score': p.get('fun_score', 3),
            'verification_method': p.get('verification_method', ''),
            'prompt_version': p.get('prompt_version', PROMPT_VERSION),
            'status': 'draft',
            'verdict': 'pending',
            'created_at': datetime.now(KST).isoformat(),
        })

    if rows:
        client.table('predictions').insert(rows).execute()
        client.table('episodes').update(
            {'pipeline_status': 'generated'}
        ).eq('id', episode_id).execute()

    log.info(f'예측 {len(rows)}개 저장 (episode_id={episode_id})')
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--episode_id', required=True)
    args = parser.parse_args()
    import sys
    results = generate_episode_predictions(args.episode_id)
    sys.stdout.buffer.write(
        json.dumps(results, default=str, ensure_ascii=False, indent=2).encode('utf-8')
    )
    print()
