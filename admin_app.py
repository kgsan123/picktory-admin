"""
PICKTORY 관리자 페이지
PIN 입력으로 접근, 예측 폴 생성/검토/검증 결과 확인
"""
import os
import json
from datetime import datetime
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


# ── 메인 ─────────────────────────────────────────────
def main():
    if not check_auth():
        return

    db = get_db()
    st.title('🎯 PICKTORY Admin')
    st.caption(f"마지막 접속: {datetime.now(KST).strftime('%Y-%m-%d %H:%M')}")

    tab1, tab2, tab3 = st.tabs(['📺 에피소드', '🗳️ 예측 폴', '📊 통계'])
    with tab1:
        tab_episodes(db)
    with tab2:
        tab_predictions(db)
    with tab3:
        tab_stats(db)


if __name__ == '__main__':
    main()
