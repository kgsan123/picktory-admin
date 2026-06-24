"""
PICKTORY 관리자 페이지
PIN 입력으로 접근, 예측 폴 생성/검토/검증 결과 확인
"""
import os
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
KST = ZoneInfo('Asia/Seoul')

# Streamlit Cloud secrets 우선, 없으면 .env
def _secret(key, default=''):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)

ADMIN_PIN = _secret('ADMIN_PIN', '1234')

st.set_page_config(page_title='PICKTORY Admin', page_icon='🎯', layout='wide')


# ── 인증 ──────────────────────────────────────────────
def check_auth():
    if st.session_state.get('authenticated'):
        return True
    st.title('🎯 PICKTORY Admin')
    pin = st.text_input('PIN 입력', type='password')
    if st.button('접속'):
        if pin == ADMIN_PIN:
            st.session_state['authenticated'] = True
            st.rerun()
        else:
            st.error('PIN이 올바르지 않습니다.')
    return False


# ── Supabase 클라이언트 ────────────────────────────────
@st.cache_resource
def get_db():
    return create_client(
        _secret('SUPABASE_URL'),
        _secret('SUPABASE_KEY'),
    )


# ── 유틸 ──────────────────────────────────────────────
STATUS_KR = {
    'draft': '검토 대기',
    'published': '게시됨',
    'expired': '만료',
}
VERDICT_KR = {
    'pending': '⏳ 미판정',
    'correct': '✅ 정답',
    'incorrect': '❌ 오답',
}
PIPELINE_KR = {
    'detected': '감지됨',
    'collected': '수집 완료',
    'verified': '검증 완료',
    'generated': '예측 생성 완료',
}


def fmt_aired(val):
    if not val:
        return '-'
    try:
        return datetime.fromisoformat(val).astimezone(KST).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return val


# ── 탭 1: 에피소드 현황 ──────────────────────────────
def tab_episodes(db):
    st.subheader('에피소드 현황')
    rows = db.table('episodes').select('*').order('aired_at', desc=True).limit(30).execute().data

    if not rows:
        st.info('등록된 에피소드가 없습니다.')
        return

    for ep in rows:
        status = ep.get('pipeline_status', 'detected')
        label = PIPELINE_KR.get(status, status)
        color = {'detected': '🔵', 'collected': '🟡', 'verified': '🟠', 'generated': '🟢'}.get(status, '⚪')
        with st.expander(f"{color} {ep.get('program_name')} {ep.get('episode_number', '')}회 — {fmt_aired(ep.get('aired_at'))}  [{label}]"):
            c1, c2, c3 = st.columns(3)
            c1.metric('시청률', f"{ep.get('ratings_percent', '-')}%")
            c2.metric('반응 점수', f"{ep.get('reaction_score', '-')}/10")
            c3.metric('상태', label)
            if ep.get('news_summary'):
                st.caption('뉴스 요약')
                st.write(ep['news_summary'][:300] + '...' if len(ep.get('news_summary','')) > 300 else ep['news_summary'])


# ── 탭 2: 예측 폴 관리 ──────────────────────────────
def tab_predictions(db):
    st.subheader('예측 폴 관리')

    col1, col2 = st.columns([2, 1])
    with col1:
        filter_status = st.selectbox('상태 필터', ['전체', 'draft', 'published', 'expired'])
    with col2:
        program_filter = st.text_input('프로그램 검색', placeholder='예: 선재 업고 튀어')

    query = db.table('predictions').select('*, episodes(program_name, episode_number, aired_at)')
    if filter_status != '전체':
        query = query.eq('status', filter_status)
    rows = query.order('created_at', desc=True).limit(50).execute().data

    if program_filter:
        rows = [r for r in rows if program_filter in (r.get('episodes') or {}).get('program_name', '')]

    if not rows:
        st.info('예측이 없습니다.')
        return

    st.caption(f'총 {len(rows)}개')

    for pred in rows:
        ep_info = pred.get('episodes') or {}
        prog = ep_info.get('program_name', '?')
        ep_num = ep_info.get('episode_number', '?')
        verdict = VERDICT_KR.get(pred.get('verdict', 'pending'), '⏳')
        status = STATUS_KR.get(pred.get('status', 'draft'), pred.get('status'))
        fun = '⭐' * int(pred.get('fun_score') or 0)

        with st.expander(f"[{status}] {prog} {ep_num}회 — {pred.get('title', '')}  {verdict}  {fun}"):
            st.write(f"**질문:** {pred.get('content', '')}")

            options = pred.get('options') or []
            if options:
                st.write('**선택지:**')
                for opt in options:
                    st.write(f"  {opt.get('id')}. {opt.get('text')}  ({int(opt.get('odds', 0)*100)}%)")

            c1, c2, c3 = st.columns(3)
            c1.write(f"난이도: {'⭐'*int(pred.get('difficulty') or 0)}")
            c2.write(f"검증: {pred.get('evidence_text') or '-'}")
            c3.write(f"확인 방법: {pred.get('verification_method') or '-'}")

            st.divider()
            bc1, bc2, bc3, bc4 = st.columns(4)
            pid = pred['id']

            if bc1.button('✅ 승인', key=f'pub_{pid}'):
                db.table('predictions').update({'status': 'published'}).eq('id', pid).execute()
                st.success('게시됨')
                st.rerun()

            if bc2.button('❌ 거부', key=f'rej_{pid}'):
                db.table('predictions').update({'status': 'expired'}).eq('id', pid).execute()
                st.warning('거부됨')
                st.rerun()

            if pred.get('verdict') == 'pending':
                verdict_choice = bc3.selectbox('판정', ['pending', 'correct', 'incorrect'],
                                                key=f'v_{pid}', label_visibility='collapsed')
                if bc4.button('저장', key=f'vs_{pid}'):
                    db.table('predictions').update({'verdict': verdict_choice}).eq('id', pid).execute()
                    st.rerun()


# ── 탭 3: 검증 결과 통계 ────────────────────────────
def tab_stats(db):
    st.subheader('검증 결과 통계')

    preds = db.table('predictions').select('verdict, fun_score, difficulty, status, episodes(program_name)').execute().data

    if not preds:
        st.info('데이터 없음')
        return

    total = len(preds)
    correct = sum(1 for p in preds if p.get('verdict') == 'correct')
    incorrect = sum(1 for p in preds if p.get('verdict') == 'incorrect')
    pending = sum(1 for p in preds if p.get('verdict') == 'pending')
    published = sum(1 for p in preds if p.get('status') == 'published')
    fun_scores = [p['fun_score'] for p in preds if p.get('fun_score')]
    avg_fun = round(sum(fun_scores) / len(fun_scores), 2) if fun_scores else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric('전체 예측', total)
    c2.metric('게시됨', published)
    c3.metric('정답', correct)
    c4.metric('오답', incorrect)
    c5.metric('평균 재미점수', f'{avg_fun}/5')

    # 프로그램별 통계
    from collections import Counter
    prog_counter = Counter(
        (p.get('episodes') or {}).get('program_name', '?') for p in preds
    )
    st.divider()
    st.write('**프로그램별 예측 수**')
    for prog, cnt in prog_counter.most_common(10):
        st.write(f"- {prog}: {cnt}개")


SHOWS_PATH = Path(__file__).parent / 'shows.json'
DISCOVERED_PATH = Path(__file__).parent / 'discovered_shows.json'
CATEGORIES = ['romance', 'survival', 'drama', 'variety', 'music']
DAYS_OPTIONS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
DAY_KR = {'Mon':'월','Tue':'화','Wed':'수','Thu':'목','Fri':'금','Sat':'토','Sun':'일'}


def _load_json(path: Path) -> list:
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []


def _save_json(path: Path, data: list):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


# ── 탭 4: 발견된 프로그램 ─────────────────────────────
def tab_discovery():
    st.subheader('발견된 프로그램')
    st.caption('YouTube/OTT 자동 발견 → 승인하면 추적 시작')

    if st.button('지금 스캔 실행'):
        with st.spinner('스캔 중... (1-2분 소요)'):
            try:
                from data_collector.show_discovery import discover_shows
                found = discover_shows()
                st.success(f'{len(found)}개 신규 발견')
                st.rerun()
            except Exception as e:
                st.error(f'스캔 실패: {e}')

    discovered = _load_json(DISCOVERED_PATH)
    pending = [d for d in discovered if d.get('status') == 'pending']

    if not pending:
        st.info('검토 대기 중인 프로그램 없음')
        approved = [d for d in discovered if d.get('status') == 'approved']
        rejected = [d for d in discovered if d.get('status') == 'rejected']
        if approved or rejected:
            st.caption(f'승인됨: {len(approved)}개 / 거부됨: {len(rejected)}개')
        return

    st.caption(f'검토 대기: {len(pending)}개')

    for show in pending:
        name = show['name']
        src = show.get('source', '?')
        clips = show.get('clip_count_7d', 0)
        ch = show.get('channel', '?')

        src_badge = {'youtube': '📺 YouTube', 'netflix': '🎬 Netflix', 'tving': '📱 Tving'}.get(
            src.split('_')[0], f'🔍 {src}')

        with st.expander(f"{src_badge}  **{name}** — {ch}  (클립 {clips}개/7일)"):
            col1, col2 = st.columns(2)
            cat = col1.selectbox(
                '카테고리', CATEGORIES,
                index=CATEGORIES.index(show.get('category', 'variety')) if show.get('category') in CATEGORIES else 3,
                key=f'cat_{name}',
            )
            ep = col2.number_input('현재 회차', min_value=1, value=int(show.get('current_episode') or 1), key=f'ep_{name}')

            col3, col4 = st.columns(2)
            air_days = col3.multiselect('방영 요일', DAYS_OPTIONS, default=show.get('air_days', []), key=f'days_{name}')
            air_time = col4.text_input('방영 시각 (HH:MM)', value=show.get('air_time_kst', ''), key=f'time_{name}')

            if show.get('season'):
                st.caption(f'시즌: {show["season"]}')

            b1, b2 = st.columns(2)
            if b1.button('추가', key=f'add_{name}', type='primary'):
                shows = _load_json(SHOWS_PATH)
                if not any(s['name'] == name for s in shows):
                    shows.append({
                        'name': name,
                        'category': cat,
                        'channel': ch,
                        'air_days': air_days,
                        'air_time_kst': air_time,
                        'current_episode': int(ep),
                        'season': show.get('season'),
                        'source': src,
                        'ended': False,
                    })
                    _save_json(SHOWS_PATH, shows)

                # 상태 업데이트
                for d in discovered:
                    if d['name'] == name:
                        d['status'] = 'approved'
                _save_json(DISCOVERED_PATH, discovered)
                st.success(f'{name} 추가됨')
                st.rerun()

            if b2.button('제외', key=f'rej_{name}'):
                for d in discovered:
                    if d['name'] == name:
                        d['status'] = 'rejected'
                _save_json(DISCOVERED_PATH, discovered)
                st.warning(f'{name} 제외됨 (재발견 안 됨)')
                st.rerun()


# ── 메인 ─────────────────────────────────────────────
def main():
    if not check_auth():
        return

    db = get_db()
    st.title('🎯 PICKTORY Admin')
    st.caption(f"마지막 접속: {datetime.now(KST).strftime('%Y-%m-%d %H:%M')}")

    tab1, tab2, tab3, tab4 = st.tabs(['📺 에피소드', '🗳️ 예측 폴', '📊 통계', '🔍 발견된 프로그램'])
    with tab1:
        tab_episodes(db)
    with tab2:
        tab_predictions(db)
    with tab3:
        tab_stats(db)
    with tab4:
        tab_discovery()


if __name__ == '__main__':
    main()
