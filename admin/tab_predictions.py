"""Tab 2: 예측 검토·관리 — draft → 게시 → 방영 후 정답 입력"""
import streamlit as st

VERDICT_KR = {'pending': '⏳ 미판정', 'resolved': '✅ 판정완료',
              'correct': '✅ 정답', 'incorrect': '❌ 오답'}
STATUS_KR = {'draft': '검토 대기', 'published': '게시됨', 'expired': '거부됨'}
# 필터 라벨 → status 값
_FILTERS = {'검토 대기': 'draft', '게시됨': 'published', '거부됨': 'expired', '전체': '전체'}


def _count(db, status: str) -> int:
    try:
        return db.table('predictions').select('id', count='exact').eq(
            'status', status).execute().count or 0
    except Exception:
        return 0


def render(db):
    st.subheader('예측 검토·관리')

    if st.button('🔁 지금 pending 재검증', help='방영 끝난 회차의 미판정 예측을 AI로 즉시 재검증'):
        with st.spinner('pending 예측 재검증 중...'):
            try:
                from orchestrator import resolve_pending_sweep
                resolve_pending_sweep()
                st.toast('재검증 완료 — 결과를 확인하세요')
                st.rerun()
            except Exception as e:
                st.error(f'재검증 실패: {e}')

    # ── 상태별 개수 + 필터 ──────────────────────────────
    n_draft = _count(db, 'draft')
    n_pub = _count(db, 'published')
    n_exp = _count(db, 'expired')
    label_map = {
        f'검토 대기 ({n_draft})': 'draft',
        f'게시됨 ({n_pub})': 'published',
        f'거부됨 ({n_exp})': 'expired',
        '전체': '전체',
    }
    labels = list(label_map.keys())
    sel_label = st.segmented_control('상태 필터', labels, default=labels[0],
                                     key='pred_filter') or labels[0]
    filter_status = label_map[sel_label]
    prog_filter = st.text_input('프로그램 검색', key='pred_prog', placeholder='예: 오십프로')

    q = db.table('predictions').select('*, episodes(program_name, episode_number)')
    if filter_status != '전체':
        q = q.eq('status', filter_status)
    rows = q.order('created_at', desc=True).limit(120).execute().data or []

    if prog_filter:
        rows = [r for r in rows
                if prog_filter in ((r.get('episodes') or {}).get('program_name', '')
                                   or r.get('program_name', ''))]

    if not rows:
        st.info('해당 조건의 예측 없음 — [프로그램] 탭에서 예측 생성 후 여기서 관리하세요')
        return

    st.caption(f'표시 {len(rows)}개')

    for pred in rows:
        ep_info = pred.get('episodes') or {}
        prog = ep_info.get('program_name') or pred.get('program_name', '?')
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
                    mark = ' ✅ **(정답)**' if coid and o.get('id') == coid else ''
                    st.write(f"  {o.get('id')}. {o.get('text')}{mark}")

            st.caption(f"확인 방법: {pred.get('verification_method') or '-'}  ·  "
                       f"난이도: {'⭐' * int(pred.get('difficulty') or 0)}")
            if pred.get('evidence_text'):
                st.caption(f"🤖 판정 근거: {pred.get('evidence_text')}")

            st.divider()
            status = pred.get('status', 'draft')

            # ── draft: 게시 / 거부 ──────────────────────
            if status == 'draft':
                b1, b2 = st.columns(2)
                if b1.button('게시', key=f'pub_{pid}', type='primary', use_container_width=True):
                    db.table('predictions').update({'status': 'published'}).eq('id', pid).execute()
                    st.toast('게시됨 — [게시됨] 탭에서 관리'); st.rerun()
                if b2.button('거부', key=f'rej_{pid}', use_container_width=True):
                    db.table('predictions').update({'status': 'expired'}).eq('id', pid).execute()
                    st.rerun()

            # ── published: 정답 관리 + 게시 취소 ─────────
            elif status == 'published':
                if pred.get('verdict') == 'pending':
                    ids = ['(미정)'] + [o.get('id') for o in opts]
                    def _fmt(x, _opts=opts):
                        if x == '(미정)':
                            return '정답 선택'
                        t = next((o.get('text') for o in _opts if o.get('id') == x), '')
                        return f'{x}. {t}'
                    c_sel, c_save = st.columns([2, 1])
                    sel = c_sel.selectbox('정답 선택지', ids, format_func=_fmt,
                                          key=f'v_{pid}', label_visibility='collapsed')
                    if c_save.button('정답 저장', key=f'vs_{pid}', use_container_width=True):
                        if sel == '(미정)':
                            st.toast('정답 선택지를 고르세요')
                        else:
                            db.table('predictions').update(
                                {'correct_option_id': sel, 'verdict': 'resolved'}
                            ).eq('id', pid).execute()
                            st.rerun()
                else:
                    # 이미 판정됨 — 재오픈(정답 정정)
                    if st.button('판정 취소(정정)', key=f'reopen_{pid}'):
                        db.table('predictions').update(
                            {'verdict': 'pending', 'correct_option_id': None}
                        ).eq('id', pid).execute()
                        st.rerun()

                if st.button('게시 취소 → 검토 대기', key=f'unpub_{pid}'):
                    db.table('predictions').update({'status': 'draft'}).eq('id', pid).execute()
                    st.toast('게시 취소됨'); st.rerun()

            # ── expired: 복원 ───────────────────────────
            elif status == 'expired':
                if st.button('복원 → 검토 대기', key=f'restore_{pid}'):
                    db.table('predictions').update({'status': 'draft'}).eq('id', pid).execute()
                    st.rerun()
