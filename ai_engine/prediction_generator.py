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
PROMPT_VERSION = 'v1'


def _load_prompt(category: str) -> tuple[str, str]:
    path = PROMPT_DIR / f'generator_{category}_{PROMPT_VERSION}.txt'
    if not path.exists():
        path = PROMPT_DIR / f'generator_drama_{PROMPT_VERSION}.txt'
    text = path.read_text(encoding='utf-8')
    parts = text.split('[USER]')
    system = parts[0].split('[SYSTEM]')[1].strip()
    user_template = parts[1].strip()
    return system, user_template


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


def _apply_filters(predictions: list[dict]) -> list[dict]:
    """품질 필터 적용. 통과한 예측만 반환."""
    kept = []
    for p in predictions:
        fun = p.get('fun_score', 0)
        diff = p.get('difficulty', 3)
        verify = p.get('verification_method', '').strip()
        options = p.get('options', [])

        if fun < 3:
            log.debug(f"필터 제거 (fun_score={fun}): {p.get('title')}")
            continue
        if not verify or len(verify) < 10:
            log.debug(f"필터 제거 (verification_method 없음): {p.get('title')}")
            continue
        if not options or len(options) < 2:
            log.debug(f"필터 제거 (options 부족): {p.get('title')}")
            continue

        # 너무 뻔한 예측 제거 (difficulty=1 + 한 옵션 확률 > 0.85)
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

    user_msg = user_template.format(
        program_name=episode.get('program_name', ''),
        next_episode=next_ep,
        episode_summary=context.get('episode_summary') or '정보 없음',
        trailer_hints=context.get('trailer_hints') or '없음',
        news_summary=context.get('news_summary') or '없음',
        reaction_score=context.get('reaction_score') or 0,
        top_clip_views=f"{context.get('top_clip_views', 0):,}",
    )

    client = Groq(api_key=os.environ.get('GROQ_API_KEY'))

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
                max_tokens=2048,
            )
            raw = resp.choices[0].message.content
            predictions = _parse_predictions(raw)
            if predictions is None:
                log.warning(f"JSON 파싱 실패 (시도 {attempt+1})")
                time.sleep(2)
                continue

            filtered = _apply_filters(predictions)
            if len(filtered) >= 3:
                for p in filtered:
                    p['prompt_version'] = PROMPT_VERSION
                return filtered

            log.info(f"필터 후 {len(filtered)}개 — 최소 3개 미달, 재시도")
            time.sleep(2)

        except Exception as e:
            log.warning(f"Groq API 시도 {attempt+1} 실패: {e}")
            time.sleep(3)

    log.error("예측 생성 2회 실패")
    return []


def generate_episode_predictions(episode_id: str) -> list[dict]:
    """
    DB에서 에피소드 조회 → 예측 생성 → DB 저장.
    데이터 수집은 선택적 — 실패해도 빈 context로 진행.
    Returns saved predictions list.
    """
    from db import get_client

    client = get_client()
    ep = client.table('episodes').select('*').eq('id', episode_id).single().execute().data
    if not ep:
        log.error(f'에피소드 미발견: {episode_id}')
        return []

    ep_num = ep.get('episode_number') or 1

    # DB에 수집된 데이터만 사용 (외부 HTTP 요청 없음)
    context: dict = {
        'episode_summary': '',
        'trailer_hints': '',
        'news_summary': ep.get('news_summary') or '',
        'reaction_score': ep.get('reaction_score') or 0,
        'top_clip_views': 0,
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
