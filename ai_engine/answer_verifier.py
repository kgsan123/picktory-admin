"""
AI 정답 검증기 — 방금 방영된 회차의 실제 데이터로 과거 예측을 판정.
Groq (llama-3.3-70b-versatile) 사용.

흐름:
  1. episode_id(방영된 회차 N) 조회
  2. target_episode_number == N 인 pending 예측 조회 (= 직전 회차에 생성된 예측)
  3. 회차 N의 방영 후 fresh 데이터 재수집 (나무위키 줄거리 + 뉴스)
  4. AI에 선택지(options) 포함해 판정 → correct_option_id 반환
  5. verdict = "최고 배당(유력 후보)이 실제로 맞았는지" + correct_option_id 저장

Usage: python -m ai_engine.answer_verifier --episode_id <uuid>
"""
import argparse
import json
import logging
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

load_dotenv()
log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / 'prompts' / 'verifier_v2.txt'
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
    """JSON 블록 추출 — 마크다운 코드블록 + 잘린 JSON 복구 처리."""
    text = text.strip()
    match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if match:
        text = match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 잘린 JSON 복구: 앞에서부터 파싱 가능한 객체만 추출
    salvaged = _salvage_results(text)
    return {'results': salvaged} if salvaged else None


def _salvage_results(text: str) -> list[dict]:
    """잘린 응답에서 results 배열의 완성된 객체들만 raw_decode로 누적 추출."""
    start = text.find('[')
    if start < 0:
        return []
    decoder = json.JSONDecoder()
    results = []
    i = text.find('{', start)
    while i >= 0:
        try:
            obj, end = decoder.raw_decode(text, i)
            if isinstance(obj, dict) and 'prediction_id' in obj:
                results.append(obj)
            i = text.find('{', end)
        except json.JSONDecodeError:
            break
    return results


def _reconcile(predictions: list[dict], results: list[dict]) -> list[dict]:
    """
    AI 응답을 입력 예측과 대조해 완전한 판정 리스트 재구성.
    - 누락된 예측 → pending (AI 미응답)
    - 환각 prediction_id → drop + 경고
    - correct_option_id 유효성 검증 (실제 options id 집합에 있어야 함)
    - confidence < 임계값 또는 미확정 → pending
    - 실제 일어난 선택지(correct_option_id)가 확정되면 → resolved
      (확률을 쓰지 않으므로 '유력 후보 적중' 개념 없음. 정답 선택지만 기록)
    """
    by_id = {r.get('prediction_id'): r for r in results}
    input_ids = {p['id'] for p in predictions}

    for hid in set(by_id) - input_ids:
        log.warning(f'환각 prediction_id 무시: {hid}')

    reconciled = []
    for p in predictions:
        pid = p['id']
        options = p.get('options') or []
        valid_ids = {o.get('id') for o in options}
        r = by_id.get(pid)

        if not r:
            reconciled.append({
                'prediction_id': pid, 'verdict': 'pending',
                'correct_option_id': None, 'confidence': 0.0,
                'evidence': 'AI 미응답(누락)',
            })
            continue

        coid = r.get('correct_option_id')
        conf = r.get('confidence', 0) or 0
        evidence = r.get('evidence', '')

        # 유효하지 않은 선택지 id 또는 저신뢰 → pending
        if coid not in valid_ids or conf < CONFIDENCE_THRESHOLD:
            note = '[저신뢰 강등] ' if (coid in valid_ids and conf < CONFIDENCE_THRESHOLD) else ''
            reconciled.append({
                'prediction_id': pid, 'verdict': 'pending',
                'correct_option_id': coid if coid in valid_ids else None,
                'confidence': conf, 'evidence': note + (evidence or '판정 불가'),
            })
            continue

        # 정답 선택지 확정 → resolved (확률 미사용이라 correct/incorrect 구분 없음)
        reconciled.append({
            'prediction_id': pid, 'verdict': 'resolved',
            'correct_option_id': coid, 'confidence': conf, 'evidence': evidence,
        })

    return reconciled


def verify_predictions(episode: dict, predictions: list[dict]) -> list[dict]:
    """
    Args:
        episode: {program_name, episode_number, aired_at, ratings_percent, news_summary}
        predictions: [{id, title, content, options}]
    Returns:
        list of {prediction_id, verdict, correct_option_id, confidence, evidence}
    """
    if not predictions:
        return []

    system_prompt, user_template = _load_prompt()

    # 선택지(options)를 반드시 포함 — AI가 실제 일어난 선택지를 식별하도록
    predictions_json = json.dumps([
        {
            'prediction_id': p['id'],
            'content': p.get('content', ''),
            'options': [{'id': o.get('id'), 'text': o.get('text')}
                        for o in (p.get('options') or [])],
        }
        for p in predictions
    ], ensure_ascii=False, indent=2)

    user_msg = user_template.format(
        program_name=episode.get('program_name', ''),
        episode_number=episode.get('episode_number', '?'),
        aired_at=episode.get('aired_at', ''),
        ratings_percent=episode.get('ratings_percent') or 'N/A',
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
                max_tokens=4096,  # 6개 예측 + 한국어 evidence 잘림 방지
            )
            raw = resp.choices[0].message.content
            parsed = _parse_json_response(raw)
            if parsed and 'results' in parsed:
                return _reconcile(predictions, parsed['results'])
            # 전체 파싱 실패만 재시도 (부분 누락은 _reconcile이 pending 처리)
            log.warning(f'JSON 파싱 실패 (시도 {attempt+1})')
        except Exception as e:
            log.warning(f"Groq API 시도 {attempt+1} 실패: {e}")
        if attempt == 0:
            time.sleep(3)

    log.error("AI 검증 2회 실패, 전체 pending 처리")
    return _reconcile(predictions, [])


def _fetch_fresh_context(episode: dict) -> str:
    """
    방영된 회차의 fresh 데이터 재수집 (나무위키 줄거리 + 방영 후 뉴스).
    충분한 신호가 없으면 빈 문자열 반환 → 검증 보류.
    stale news_summary로 폴백하지 않음 (결과-전 데이터로 오판 방지).
    """
    try:
        from data_collector.context_fetcher import fetch_episode_context
        from datetime import datetime as _dt
        program = episode.get('program_name', '')
        ep_num = episode.get('episode_number') or 1
        category = episode.get('category', 'drama')

        aired_at_dt = None
        raw = episode.get('aired_at')
        if raw:
            try:
                aired_at_dt = _dt.fromisoformat(str(raw).replace('Z', '+00:00'))
            except Exception:
                pass

        # show_notes 조회
        show_notes = ''
        try:
            from db import get_client
            row = get_client().table('shows').select('notes').eq('name', program).maybe_single().execute()
            if row and row.data:
                show_notes = (row.data.get('notes') or '').strip()
        except Exception:
            pass

        ctx = fetch_episode_context(program, ep_num, category=category,
                                    show_notes=show_notes, aired_at=aired_at_dt)
        if not ctx.has_sufficient_signal():
            log.info(f'fresh 신호 부족 → 검증 보류 ({program} {ep_num}회)')
            return ''
        return ctx.to_prompt_text()
    except Exception as e:
        log.warning(f'fresh 컨텍스트 재수집 실패: {e}')
        return ''


def verify_episode(episode_id: str) -> list[dict]:
    """
    방영된 회차 N을 대상으로, 직전 회차에 생성된 예측(target_episode_number == N)을 판정.
    결과를 predictions 테이블에 저장.
    """
    from db import get_client
    client = get_client()

    ep = client.table('episodes').select('*').eq('id', episode_id).single().execute().data
    if not ep:
        log.error(f'에피소드 미발견: {episode_id}')
        return []

    program = ep.get('program_name', '')
    n = ep.get('episode_number')

    # 이 회차(N)를 대상으로 한 pending 예측 조회 (직전 run에서 생성됨)
    # 'finale'(최종화 마감) 예측은 다음 회차에 판정하지 않음 — 시즌 끝에 별도 처리/수동 판정
    preds = (client.table('predictions')
             .select('*')
             .eq('program_name', program)
             .eq('target_episode_number', n)
             .eq('verdict', 'pending')
             .neq('resolution_horizon', 'finale')
             .execute().data) or []

    if not preds:
        log.info(f"판정할 예측 없음: {program} {n}회 (target_episode_number={n})")
        return []

    # 방영 후 fresh 데이터 재수집 (없으면 검증 보류 — 다음 윈도우로 미룸)
    fresh = _fetch_fresh_context(ep)
    if not fresh:
        log.info(f"{program} {n}회: fresh 데이터 부족, 검증 보류 (pending 유지)")
        return []

    ep_for_verify = dict(ep)
    ep_for_verify['news_summary'] = fresh

    results = verify_predictions(ep_for_verify, preds)

    for r in results:
        update = {
            'verdict': r['verdict'],
            'confidence': r.get('confidence'),
            'evidence_text': r.get('evidence'),
        }
        if r.get('correct_option_id') is not None:
            update['correct_option_id'] = r['correct_option_id']
        try:
            client.table('predictions').update(update).eq('id', r['prediction_id']).execute()
        except Exception as e:
            if 'correct_option_id' in str(e):  # 마이그레이션 007 미적용
                update.pop('correct_option_id', None)
                client.table('predictions').update(update).eq('id', r['prediction_id']).execute()
            else:
                raise

    client.table('episodes').update(
        {'pipeline_status': 'verified'}
    ).eq('id', episode_id).execute()

    resolved = sum(1 for r in results if r['verdict'] != 'pending')
    log.info(f"{program} {n}회 검증: {resolved}/{len(results)} 판정 완료")
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--episode_id', required=True)
    args = parser.parse_args()
    results = verify_episode(args.episode_id)
    print(json.dumps(results, ensure_ascii=False, indent=2))
