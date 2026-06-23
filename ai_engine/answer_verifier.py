"""
AI 정답 검증기 — 과거 예측을 에피소드 데이터로 판정.
Groq (llama-3.3-70b-versatile) 사용.

Usage: python -m ai_engine.answer_verifier --episode_id <uuid>
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

PROMPT_PATH = Path(__file__).parent / 'prompts' / 'verifier_v1.txt'
MODEL = 'llama-3.3-70b-versatile'
CONFIDENCE_THRESHOLD = 0.70


def _load_prompt() -> tuple[str, str]:
    """Returns (system_prompt, user_template)."""
    text = PROMPT_PATH.read_text(encoding='utf-8')
    parts = text.split('[USER]')
    system = parts[0].split('[SYSTEM]')[1].strip()
    user_template = parts[1].strip()
    return system, user_template


def _parse_json_response(text: str) -> dict | None:
    """JSON 블록 추출 — 마크다운 코드블록 포함 처리."""
    text = text.strip()
    match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if match:
        text = match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def verify_predictions(
    episode: dict,
    predictions: list[dict],
) -> list[dict]:
    """
    Args:
        episode: {program_name, episode_number, aired_at, ratings_percent,
                  reaction_score, news_summary}
        predictions: [{id, title, content, options}]

    Returns:
        list of {prediction_id, verdict, confidence, evidence}
        실패 시 모두 pending 처리.
    """
    if not predictions:
        return []

    system_prompt, user_template = _load_prompt()

    predictions_json = json.dumps(
        [{'prediction_id': p['id'], 'title': p.get('title', ''), 'content': p.get('content', '')}
         for p in predictions],
        ensure_ascii=False, indent=2
    )

    user_msg = user_template.format(
        program_name=episode.get('program_name', ''),
        episode_number=episode.get('episode_number', '?'),
        aired_at=episode.get('aired_at', ''),
        ratings_percent=episode.get('ratings_percent') or 'N/A',
        reaction_score=episode.get('reaction_score') or 'N/A',
        news_summary=episode.get('news_summary') or '데이터 없음',
        predictions_json=predictions_json,
    )

    client = Groq(api_key=os.environ.get('GROQ_API_KEY'))

    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_msg},
                ],
                temperature=0.1,
                max_tokens=1024,
            )
            raw = resp.choices[0].message.content
            parsed = _parse_json_response(raw)
            if parsed and 'results' in parsed:
                results = parsed['results']
                # confidence 기준 미달은 pending 강제
                for r in results:
                    if r.get('confidence', 0) < CONFIDENCE_THRESHOLD:
                        r['verdict'] = 'pending'
                return results
        except Exception as e:
            log.warning(f"Groq API 시도 {attempt+1} 실패: {e}")
            if attempt == 0:
                time.sleep(3)

    # 2회 실패 → 전체 pending
    log.error("AI 검증 2회 실패, 전체 pending 처리")
    return [
        {'prediction_id': p['id'], 'verdict': 'pending',
         'confidence': 0.0, 'evidence': 'AI 응답 실패'}
        for p in predictions
    ]


def verify_episode(episode_id: str) -> list[dict]:
    """
    DB에서 에피소드 + predictions 조회 후 판정, 결과를 DB에 upsert.
    Returns list of verdict dicts.
    """
    from db import get_client
    client = get_client()

    ep = client.table('episodes').select('*').eq('id', episode_id).single().execute().data
    preds = (client.table('predictions')
             .select('*')
             .eq('episode_id', episode_id)
             .eq('verdict', 'pending')
             .execute().data)

    if not preds:
        log.info(f"판정할 예측 없음: episode_id={episode_id}")
        return []

    results = verify_predictions(ep, preds)

    for r in results:
        client.table('predictions').update({
            'verdict': r['verdict'],
            'confidence': r.get('confidence'),
            'evidence_text': r.get('evidence'),
        }).eq('id', r['prediction_id']).execute()

    # pipeline_status 업데이트
    client.table('episodes').update(
        {'pipeline_status': 'verified'}
    ).eq('id', episode_id).execute()

    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--episode_id', required=True)
    args = parser.parse_args()
    results = verify_episode(args.episode_id)
    print(json.dumps(results, ensure_ascii=False, indent=2))
