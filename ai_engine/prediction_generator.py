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
    """생성된 예측에서 한자·일본어 제거 + 확률(odds) 제거 (확률은 쓰지 않음)."""
    for field in ('title', 'content', 'verification_method'):
        if field in p:
            p[field] = _CJK_RE.sub('', p[field]).strip()
    if 'options' in p:
        for opt in p['options']:
            if 'text' in opt:
                opt['text'] = _CJK_RE.sub('', opt['text']).strip()
            opt.pop('odds', None)  # 확률 미사용 — 모델이 내보내도 제거
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
        pass
    # 잘림·일부 깨짐 복구: 파싱 가능한 예측 객체만 누적 추출
    salvaged = _salvage_predictions(text)
    return salvaged or None


def _salvage_predictions(text: str) -> list[dict]:
    """깨진/잘린 응답에서 완성된 예측 객체(content+options 보유)만 raw_decode로 추출."""
    decoder = json.JSONDecoder()
    out = []
    i = text.find('{', text.find('"predictions"') if '"predictions"' in text else 0)
    while i >= 0:
        try:
            obj, end = decoder.raw_decode(text, i)
            if isinstance(obj, dict) and 'content' in obj and 'options' in obj:
                out.append(obj)
            i = text.find('{', end)
        except json.JSONDecodeError:
            i = text.find('{', i + 1)  # 이 위치는 깨짐 → 다음 후보로
    return out


_VAGUE_VERIFY = ['방영 후 확인', '해당 회차에서 확인', '추후 확인', '나중에 확인']

# verification_method에 최소 하나는 있어야 하는 "장면" 어휘 (판별 가능성 양성검사)
# 5종 프롬프트의 검증 예시 어휘를 전수 포함 — 정상 출력을 거르지 않도록
_SCENE_KW = [
    '발표', '호명', '장면', '공개', '등장', '투표', '순위', '결과', '클립',
    '방송 종료', '방영 직후', '1위', '엔딩', '확인', '선택', '대결', '탈락', '우승',
]

# 의미상 YES/NO와 동일한 이진 반의어 쌍
_BINARY_PAIRS = [
    {'진입', '미진입'}, {'성공', '실패'}, {'달성', '미달성'},
    {'있음', '없음'}, {'맞다', '아니다'}, {'예', '아니오'},
    {'남자', '여자'}, {'통과', '탈락'}, {'승', '패'},
]

# 선택지 텍스트에 들어있으면 안 되는 플레이스홀더
_PLACEHOLDER_TERMS = [
    'A 출연자', 'B 출연자', 'A 아이돌', 'B 아이돌',
    'A팀', 'B팀', '주요 커플', '기타 아티스트', '기타 팀', '기타 출연자',
]

# 구체적 이름이 아닌 일반 명사 선택지 (실제 인물·곡명이어야 함)
# "선택지에 없음/아무도 없음" 같은 예외 선택지는 허용
_GENERIC_OPTIONS = {
    '남자', '여자', '남성', '여성', '남솔로', '여솔로',
    '진입', '미진입', '성공', '실패', '달성', '미달성',
    '있음', '통과', '탈락',
}

# grounding 면제 선택지 (실제 이름이 아닌 정당한 예외 선택지)
_EXEMPT_OPTS = {
    '없음', '선택지에 없음', '아무도 없음', '아무도 선택 안 함', '무승부',
    '전원 생존', '탈락 없음', '벌칙 없음', '그 외 아티스트', '그 외', '기권', '제3의 팀이 가져감',
}

def _build_allowed_names(cast_names: list[str]) -> set[str]:
    """cast_names에서 'N기 ' 접두 제거한 순수 이름 집합."""
    names = set()
    for c in cast_names or []:
        pure = re.sub(r'^\d+기\s*', '', c).strip()
        if len(pure) >= 2:
            names.add(pure)
    return names


def _is_grounded(opt_text: str, allowed: set[str]) -> bool:
    """선택지 텍스트가 허용 이름 중 하나를 포함하면 grounded."""
    t = opt_text.strip()
    if t in _EXEMPT_OPTS:
        return True
    return any(name in t for name in allowed)


def _apply_filters(predictions: list[dict], allowed_names: set[str] | None = None) -> list[dict]:
    """품질 필터 적용. 통과한 예측만 반환.

    allowed_names: 신뢰 가능한 출연자 이름 집합(3개 이상일 때만 grounding 적용).
    비예외 선택지의 절반 초과가 이 이름들과 매칭 안 되면 환각으로 보고 제거.
    """
    grounding_on = bool(allowed_names) and len(allowed_names) >= 3
    kept = []
    for p in predictions:
        fun = p.get('fun_score', 0)
        diff = p.get('difficulty', 3)
        verify = p.get('verification_method', '').strip()
        options = p.get('options', [])
        content = p.get('content', '')
        opt_texts = {o.get('text', '').strip() for o in options}

        if fun < 3:
            log.debug(f"필터 제거 (fun_score={fun}): {p.get('title')}")
            continue

        if not verify or len(verify) < 20:
            log.debug(f"필터 제거 (verification 너무 짧음): {p.get('title')}")
            continue
        if any(v in verify for v in _VAGUE_VERIFY):
            log.debug(f"필터 제거 (verification 모호): {p.get('title')}")
            continue
        # 장면 양성검사 — 구체적 판별 장면 어휘가 하나도 없으면 모호한 검증
        if not any(k in verify for k in _SCENE_KW):
            log.debug(f"필터 제거 (verification 장면 키워드 없음): {p.get('title')}")
            continue

        if not options or len(options) < 3:
            log.debug(f"필터 제거 (선택지 2개 이하): {p.get('title')}")
            continue

        # YES/NO id 형식 거부
        opt_ids = {o.get('id', '').upper() for o in options}
        if opt_ids == {'YES', 'NO'}:
            log.debug(f"필터 제거 (YES/NO id): {p.get('title')}")
            continue

        # 의미상 이진 선택지 거부 (정확히 2개 텍스트이고 반의어 쌍인 경우)
        if len(opt_texts) == 2 and any(opt_texts == pair for pair in _BINARY_PAIRS):
            log.debug(f"필터 제거 (이진 반의어 선택지): {p.get('title')}")
            continue

        # 플레이스홀더 금지 (content + 선택지 텍스트 모두 검사)
        all_text = content + ' ' + ' '.join(opt_texts)
        if any(t in all_text for t in _PLACEHOLDER_TERMS):
            log.debug(f"필터 제거 (플레이스홀더): {p.get('title')}")
            continue

        # 일반 명사 선택지 거부 (실제 이름·곡명 아닌 '남자/진입' 등)
        generic_hits = [t for t in opt_texts if t in _GENERIC_OPTIONS]
        if generic_hits:
            log.debug(f"필터 제거 (일반명사 선택지 {generic_hits}): {p.get('title')}")
            continue

        # 환각 grounding 검사 — 신뢰 이름 집합이 있을 때만
        if grounding_on:
            non_exempt = [t for t in opt_texts if t not in _EXEMPT_OPTS]
            if non_exempt:
                ungrounded = [t for t in non_exempt if not _is_grounded(t, allowed_names)]
                if len(ungrounded) > len(non_exempt) / 2:
                    log.debug(f"필터 제거 (환각 선택지 {ungrounded}): {p.get('title')}")
                    continue

        kept.append(p)
    return kept


def _content_tokens(content: str) -> set:
    """content를 정규화해 토큰 집합으로. 조사·짧은 토큰 제거."""
    cleaned = re.sub(r'[^가-힣a-zA-Z0-9\s]', ' ', content)
    return {t for t in cleaned.split() if len(t) >= 2}


def _dedupe(predictions: list[dict], threshold: float = 0.7) -> list[dict]:
    """거의 동일한 질문만 제거 (Jaccard 유사도). 서로 다른 각도의 예측은 유지.

    음악의 '1위' 처럼 같은 키워드를 공유해도 질문이 다르면(이번 회차 1위 vs 컴백 1위
    vs A·B 대결) 보존. 사실상 같은 질문이 중복될 때만 제거.
    """
    kept: list[dict] = []
    kept_tokens: list[set] = []
    for p in predictions:
        toks = _content_tokens(p.get('content', ''))
        is_dup = False
        for prev in kept_tokens:
            if not toks or not prev:
                continue
            jaccard = len(toks & prev) / len(toks | prev)
            if jaccard >= threshold:
                is_dup = True
                break
        if is_dup:
            log.debug(f"중복 제거 (유사 질문): {p.get('title')}")
            continue
        kept.append(p)
        kept_tokens.append(toks)
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
    allowed_names = _build_allowed_names(context.get('cast_names', []))

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
            filtered = _dedupe(_apply_filters(predictions, allowed_names))[:6]  # 필터 → 중복제거 → 최대 6개
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

    raise RuntimeError(classify_generation_error(last_error))


def classify_generation_error(text: str) -> str:
    """raw 실패 메시지를 운영자용 한국어 사유로 분류."""
    raw = str(text)
    t = raw.lower()
    if any(k in t for k in ('rate_limit', '429', 'tokens per day', 'tpd', 'rate limit')):
        return 'AI 일일 토큰 한도 초과 — 한도 회복 후(수십 분~) 재시도하거나 Groq 티어 업그레이드 필요'
    if 'groq_api_key' in t:
        return 'GROQ_API_KEY 미설정 — 환경변수 확인 필요'
    if 'json' in t or '파싱' in raw:
        return 'AI 응답 형식 오류(JSON 파싱 실패) — 다시 시도하세요'
    if '필터 후' in raw or '최소 4개' in raw:
        return '생성된 예측이 품질 기준(선택지·검증·차등배당)을 통과 못함 — 컨텍스트 보강 후 재시도'
    if 'timeout' in t or 'connection' in t:
        return 'AI 서버 응답 지연/연결 실패 — 다시 시도하세요'
    return f'생성 오류: {raw[:160]}'


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
    context_sufficient = True  # 운영자 입력이 있으면 무조건 통과
    chart_text = ''
    auto_trailer = ''
    cast_names: list[str] = []
    if operator_summary:
        auto_text = operator_summary
    else:
        try:
            from data_collector.context_fetcher import fetch_episode_context
            category = ep.get('category', 'drama')
            # aired_at 파싱 (KST datetime)
            from datetime import datetime as _dt
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
            context_sufficient = ep_ctx.has_sufficient_signal()
            cast_names = ep_ctx.cast_names
            auto_text = ep_ctx.to_prompt_text() or ep.get('news_summary') or ''
            chart_text = ep_ctx.to_chart_text()
            # 자동 수집 예고를 operator 미입력시 fallback으로 사용
            auto_trailer = '\n'.join(ep_ctx.trailer_snippets) if ep_ctx.trailer_snippets else ''
            if auto_text:
                client.table('episodes').update(
                    {'news_summary': auto_text[:2500]}  # 줄거리 사실 앵커 보존
                ).eq('id', episode_id).execute()
            if ep_ctx.errors:
                log.debug(f'context_fetcher 부분 실패: {ep_ctx.errors}')
        except Exception as e:
            log.warning(f'context_fetcher 실패 (무시): {e}')
            auto_text = ep.get('news_summary') or ''
            chart_text = ''
            auto_trailer = ''
            context_sufficient = bool(auto_text)  # DB 기존 데이터라도 있으면 시도

    # ── rank8: 빈약 컨텍스트 게이트 ──────────────────────────────
    # 운영자 입력도 없고 자동 수집 신호도 부족하면 → 환각만 나올 생성을 건너뜀
    if not context_sufficient:
        log.warning(f'컨텍스트 부족 → 생성 건너뜀 (운영자 입력 필요): {program_name} {ep_num}회')
        try:
            client.table('episodes').update(
                {'pipeline_status': 'context_insufficient'}
            ).eq('id', episode_id).execute()
        except Exception:
            pass
        return []

    # trailer_hints: 운영자 직접 입력 > 자동 수집 예고
    trailer_hints = (extra_context or {}).get('trailer_hints', '').strip()
    if not trailer_hints:
        trailer_hints = auto_trailer

    context: dict = {
        'episode_summary': auto_text,
        'trailer_hints': trailer_hints,
        'news_summary': auto_text,
        'chart_context': chart_text,
        'cast_names': cast_names,
    }

    predictions = generate_predictions(ep, context)
    if not predictions:
        return []

    # 예측 대상 회차 = 방영된 회차 + 1 (검증 시점에 이 키로 조회)
    target_ep = ep_num + 1

    rows = []
    for p in predictions:
        closing = p.get('closing', 'next')
        if closing not in ('next', 'finale'):
            closing = 'next'
        rows.append({
            'episode_id': episode_id,
            'program_name': program_name,
            'target_episode_number': target_ep,
            'category': p.get('category', ep.get('category', 'drama')),
            'title': p.get('title', ''),
            'content': p.get('content', ''),
            'options': p.get('options', []),
            'difficulty': p.get('difficulty', 3),
            'fun_score': p.get('fun_score', 3),
            'verification_method': p.get('verification_method', ''),
            'resolution_horizon': closing,
            'prompt_version': p.get('prompt_version', PROMPT_VERSION),
            'status': 'draft',
            'verdict': 'pending',
            'created_at': datetime.now(KST).isoformat(),
        })

    if rows:
        _insert_predictions(client, rows)
        client.table('episodes').update(
            {'pipeline_status': 'generated'}
        ).eq('id', episode_id).execute()

    log.info(f'예측 {len(rows)}개 저장 (episode_id={episode_id})')
    return rows


# 마이그레이션 미적용 시 없을 수 있는 컬럼 (006/008)
_OPTIONAL_COLS = ['program_name', 'target_episode_number', 'resolution_horizon']

def _insert_predictions(client, rows: list[dict]) -> None:
    """예측 insert — 신규 컬럼이 DB에 없으면(마이그레이션 미적용) 제거 후 재시도."""
    try:
        client.table('predictions').insert(rows).execute()
    except Exception as e:
        if any(col in str(e) for col in _OPTIONAL_COLS):
            log.warning('신규 컬럼 미적용(마이그레이션 006/008 필요) — 해당 컬럼 제외하고 저장')
            stripped = [{k: v for k, v in r.items() if k not in _OPTIONAL_COLS} for r in rows]
            client.table('predictions').insert(stripped).execute()
        else:
            raise


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
