"""Tab 3: 신규 발견 — show_candidates 검토 (Supabase 기반)"""
import streamlit as st

CATEGORIES = ['romance', 'survival', 'drama', 'variety', 'music']
DAYS_OPTIONS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
SRC_BADGE = {
    'youtube': '📺 YT', 'tving': '📱 Tving',
    'tving_live': '📱 Tving', 'tving_new': '📱 Tving',
}


def render(db):
    st.subheader('신규 프로그램 발견')

    if st.button('스캔 실행', help='YouTube + Tving 스캔 (로컬 환경 권장)'):
        with st.spinner('스캔 중... (1~2분)'):
            try:
                from data_collector.show_discovery import discover_shows
                found = discover_shows()
                if found:
                    st.success(f'{len(found)}개 발견 — 아래 목록 갱신됨')
                else:
                    st.info('신규 발견 없음 (YouTube 쿼터 초과 시 내일 재시도)')
                st.rerun()
            except Exception as e:
                st.error(f'스캔 실패: {e}')

    st.caption('로컬 실행: `python -m data_collector.show_discovery`')
    st.divider()

    pending = (db.table('show_candidates').select('*').eq('status', 'pending')
               .order('clip_count_7d', desc=True).execute().data or [])

    counts = db.table('show_candidates').select('status').execute().data or []
    n_approved = sum(1 for c in counts if c['status'] == 'approved')
    n_rejected = sum(1 for c in counts if c['status'] == 'rejected')
    if n_approved or n_rejected:
        st.caption(f'이전 검토: 승인 {n_approved}개 · 거부 {n_rejected}개')

    if not pending:
        st.info('검토 대기 중인 후보 없음')
        return

    st.caption(f'검토 대기: {len(pending)}개')

    for c in pending:
        cid = c['id']
        name = c['name']
        src_key = (c.get('source') or '').split('_')[0]
        badge = SRC_BADGE.get(src_key, '🔍')
        clips = c.get('clip_count_7d', 0)

        with st.expander(f"{badge} **{name}** — {c.get('channel','?')}  (클립 {clips}개/7일)"):
            col1, col2 = st.columns(2)
            cat_idx = CATEGORIES.index(c['category']) if c.get('category') in CATEGORIES else 3
            cat = col1.selectbox('카테고리', CATEGORIES, index=cat_idx, key=f'ccat_{cid}')
            ep = col2.number_input('현재 회차', min_value=1,
                                    value=int(c.get('current_episode') or 1), key=f'cep_{cid}')
            col3, col4 = st.columns(2)
            air_days = col3.multiselect('방영 요일', DAYS_OPTIONS,
                                         default=c.get('air_days') or [], key=f'cdays_{cid}')
            air_time = col4.text_input('방영 시각 (HH:MM)',
                                        value=c.get('air_time_kst') or '', key=f'ctime_{cid}')

            b1, b2 = st.columns(2)
            if b1.button('추적 시작', key=f'cadd_{cid}', type='primary'):
                dup = db.table('shows').select('id').eq('name', name).execute().data
                if not dup:
                    db.table('shows').insert({
                        'name': name,
                        'category': cat,
                        'channel': c.get('channel', ''),
                        'air_days': air_days,
                        'air_time_kst': air_time,
                        'current_episode': int(ep),
                        'source': c.get('source', ''),
                        'ended': False,
                    }).execute()
                db.table('show_candidates').update({'status': 'approved'}).eq('id', cid).execute()
                st.success(f'"{name}" 추적 시작')
                st.rerun()

            if b2.button('제외', key=f'crej_{cid}'):
                db.table('show_candidates').update({'status': 'rejected'}).eq('id', cid).execute()
                st.warning(f'"{name}" 제외됨')
                st.rerun()
