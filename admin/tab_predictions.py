"""Tab 2: 예측 검토 — draft → 게시 → 정답 입력"""
import streamlit as st

VERDICT_KR = {'pending': '⏳ 미판정', 'correct': '✅ 정답', 'incorrect': '❌ 오답'}
STATUS_OPTS = ['draft', 'published', 'expired']
STATUS_KR = {'draft': '검토 대기', 'published': '게시됨', 'expired': '거부됨'}


def render(db):
    st.subheader('예측 검토')
    st.caption('draft → 게시 → 방영 후 정답 입력')

    if st.button('🔁 지금 pending 재검증', help='방영 끝난 회차의 미판정 예측을 AI로 즉시 재검증'):
        with st.spinner('pending 예측 재검증 중...'):
            try:
                from orchestrator import resolve_pending_sweep
                resolve_pending_sweep()
                st.toast('재검증 완료 — 결과를 확인하세요')
                st.rerun()
            except Exception as e:
                st.error(f'재검증 실패: {e}')

    c1, c2 = st.columns([2, 2])
    filter_status = c1.selectbox('상태 필터', ['draft', 'published', '전체', 'expired'],
                                  key='pred_filter')
    prog_filter = c2.text_input('프로그램 검색', key='pred_prog', placeholder='예: 오십프로')

    q = db.table('predictions').select('*, episodes(program_name, episode_number)')
    if filter_status != '전체':
        q = q.eq('status', filter_status)
    rows = q.order('created_at', desc=True).limit(80).execute().data or []

    if prog_filter:
        rows = [r for r in rows
                if prog_filter in (r.get('episodes') or {}).get('program_name', '')]

    if not rows:
        st.info('예측 없음 — [프로그램] 탭에서 예측 생성 후 여기서 검토하세요')
        return

    st.caption(f'총 {len(rows)}개')

    for pred in rows:
        ep_info = pred.get('episodes') or {}
        prog = ep_info.get('program_name', '?')
        ep_num = ep_info.get('episode_number', '?')
        verdict = VERDICT_KR.get(pred.get('verdict', 'pending'), '⏳')
        status_label = STATUS_KR.get(pred.get('status', 'draft'), '?')
        stars = '⭐' * min(int(pred.get('fun_score') or 0), 5)
        pid = pred['id']

        header = f"[{status_label}] **{prog}** {ep_num}회→다음회  {pred.get('title', '')}  {verdict} {stars}"

        with st.expander(header):
            st.write(f"**질문:** {pred.get('content', '')}")

            opts = pred.get('options') or []
            coid = pred.get('correct_option_id')
            if opts:
                st.write('**선택지:**')
                for o in opts:
                    pct = int(o.get('odds', 0) * 100)
                    mark = ' ✅ **(AI 판정 정답)**' if coid and o.get('id') == coid else ''
                    st.write(f"  {o.get('id')}. {o.get('text')}  ({pct}%){mark}")

            st.caption(f"확인 방법: {pred.get('verification_method') or '-'}  ·  "
                       f"난이도: {'⭐' * int(pred.get('difficulty') or 0)}")
            if pred.get('evidence_text'):
                st.caption(f"🤖 판정 근거: {pred.get('evidence_text')}")

            st.divider()
            b1, b2, b3, b4 = st.columns(4)

            if pred.get('status') == 'draft':
                if b1.button('게시', key=f'pub_{pid}', type='primary'):
                    db.table('predictions').update({'status': 'published'}).eq('id', pid).execute()
                    st.rerun()
                if b2.button('거부', key=f'rej_{pid}'):
                    db.table('predictions').update({'status': 'expired'}).eq('id', pid).execute()
                    st.rerun()

            if pred.get('status') == 'published' and pred.get('verdict') == 'pending':
                v = b3.selectbox('정답 입력', ['pending', 'correct', 'incorrect'],
                                  key=f'v_{pid}', label_visibility='collapsed')
                if b4.button('저장', key=f'vs_{pid}'):
                    db.table('predictions').update({'verdict': v}).eq('id', pid).execute()
                    st.rerun()
