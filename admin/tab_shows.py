"""Tab 1: 프로그램 관리 — 예측 생성 버튼 + 컨텍스트 입력"""
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st

KST = ZoneInfo('Asia/Seoul')
CATEGORIES = ['romance', 'survival', 'drama', 'variety', 'music']
DAYS_OPTIONS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
DAY_KR = {'Mon': '월', 'Tue': '화', 'Wed': '수', 'Thu': '목',
           'Fri': '금', 'Sat': '토', 'Sun': '일'}
CAT_EMOJI = {'romance': '💕', 'survival': '⚔️', 'variety': '🎉', 'drama': '🎭', 'music': '🎵'}


def _upsert_episode(db, show: dict, episode_num: int) -> str | None:
    existing = (db.table('episodes')
                .select('id')
                .eq('program_name', show['name'])
                .eq('episode_number', episode_num)
                .execute().data)
    if existing:
        return existing[0]['id']
    result = db.table('episodes').insert({
        'program_name': show['name'],
        'episode_number': episode_num,
        'category': show.get('category', 'variety'),
        'channel': show.get('channel', ''),
        'aired_at': datetime.now(KST).isoformat(),
        'pipeline_status': 'detected',
    }).execute()
    return result.data[0]['id'] if result.data else None


def _run_generate(db, show: dict, episode_num: int, extra_context: dict) -> str:
    episode_id = _upsert_episode(db, show, episode_num)
    if not episode_id:
        return '에피소드 DB 등록 실패'
    try:
        from ai_engine.prediction_generator import generate_episode_predictions
        preds = generate_episode_predictions(episode_id, extra_context=extra_context)
        if preds:
            db.table('shows').update(
                {'current_episode': episode_num + 1}
            ).eq('id', show['id']).execute()
            return f'✅ {len(preds)}개 예측 생성 → [예측] 탭에서 검토 후 게시'
        return '예측 0개 생성 (필터 탈락 또는 AI 응답 파싱 실패)'
    except Exception as e:
        return f'❌ 오류: {e}'


def _show_card(db, s: dict):
    sid = s['id']
    cat = s.get('category', 'variety')
    days = ''.join(DAY_KR.get(d, d) for d in (s.get('air_days') or []))
    air_time = s.get('air_time_kst', '')
    ep_now = int(s.get('current_episode', 1))

    # ── 메인 행: 항상 보임 ──────────────────────────────
    c_info, c_ep, c_btn = st.columns([4, 2, 2])
    c_info.markdown(
        f"{CAT_EMOJI.get(cat, '📺')} **{s['name']}**  "
        f"<span style='color:gray;font-size:0.85em'>"
        f"{s.get('channel','?')} · {days} {air_time}</span>",
        unsafe_allow_html=True,
    )
    ep_input = c_ep.number_input(
        '방영된 회차', min_value=1, value=ep_now,
        key=f'ep_{sid}', label_visibility='collapsed',
        help='방금 방영된 회차 번호',
    )
    gen_btn = c_btn.button('예측 생성 ▶', key=f'gen_{sid}',
                            type='primary', use_container_width=True)

    # ── 컨텍스트 + 설정 expander ───────────────────────
    with st.expander('컨텍스트 · 설정', expanded=False):

        st.caption('📝 컨텍스트 입력 — 입력할수록 예측이 정확해집니다')
        summary = st.text_area(
            '이번 회차 핵심 내용',
            key=f'summary_{sid}',
            placeholder=(
                '예) 이번주 1위는 aespa, NewJeans가 컴백 무대 첫 선 보임. '
                '특별 콜라보 무대 있었음.'
            ),
            height=80,
        )
        trailer = st.text_area(
            '다음 회차 예고/힌트 (선택)',
            key=f'trailer_{sid}',
            placeholder='예) 다음주 BTS 지민 솔로 컴백 예고, 깜짝 게스트 예정',
            height=60,
        )

        st.divider()
        st.caption('⚙️ 설정')
        ec1, ec2 = st.columns(2)
        new_days = ec1.multiselect('방영 요일', DAYS_OPTIONS,
                                    default=s.get('air_days') or [], key=f'days_{sid}')
        new_time = ec2.text_input('방영 시각 (HH:MM)', value=air_time, key=f'time_{sid}')
        new_cat = ec1.selectbox('카테고리', CATEGORIES,
                                 index=CATEGORIES.index(cat) if cat in CATEGORIES else 3,
                                 key=f'cat_{sid}')
        sc1, sc2 = st.columns(2)
        if sc1.button('설정 저장', key=f'save_{sid}'):
            db.table('shows').update({
                'air_days': new_days,
                'air_time_kst': new_time,
                'category': new_cat,
            }).eq('id', sid).execute()
            st.toast('저장됨')
            st.rerun()
        if sc2.button('종영 처리', key=f'end_{sid}'):
            db.table('shows').update({'ended': True}).eq('id', sid).execute()
            st.toast(f'{s["name"]} 종영 처리됨')
            st.rerun()

    # 버튼 처리 (expander 밖에서)
    if gen_btn:
        extra = {
            'episode_summary': st.session_state.get(f'summary_{sid}', ''),
            'trailer_hints': st.session_state.get(f'trailer_{sid}', ''),
        }
        with st.spinner(f'{s["name"]} {ep_input}회 예측 생성 중... (10~20초)'):
            msg = _run_generate(db, s, ep_input, extra)
        st.toast(msg)

    st.divider()


def render(db):
    st.subheader('추적 프로그램')
    shows = (db.table('shows').select('*').eq('ended', False)
             .order('name').execute().data or [])

    if not shows:
        st.info('추적 중인 프로그램 없음 — 아래에서 추가하세요')
    else:
        h1, h2, h3 = st.columns([4, 2, 2])
        h2.caption('방영된 회차')
        h3.caption('수동 예측 생성')
        st.divider()
        for s in shows:
            _show_card(db, s)

    with st.expander('＋ 새 프로그램 추가'):
        c1, c2 = st.columns(2)
        new_name = c1.text_input('프로그램명', key='new_name')
        new_ch = c2.text_input('방송사', key='new_ch', placeholder='예: tvN')
        c3, c4 = st.columns(2)
        new_cat = c3.selectbox('카테고리', CATEGORIES, key='new_cat')
        new_ep = c4.number_input('현재 회차', min_value=1, value=1, key='new_ep',
                                   help='가장 최근에 방영된 회차 번호')
        c5, c6 = st.columns(2)
        new_days = c5.multiselect('방영 요일', DAYS_OPTIONS, key='new_days')
        new_time = c6.text_input('방영 시각 (HH:MM)', key='new_time', placeholder='예: 21:30')

        new_show_type = st.radio(
            '방영 유형', ['regular', 'event'],
            format_func=lambda x: '정기 방영' if x == 'regular' else '일회성 이벤트 (시상식 등)',
            horizontal=True, key='new_show_type',
        )

        if st.button('추가', type='primary', key='btn_add_show'):
            if not new_name.strip():
                st.error('프로그램명을 입력하세요')
            else:
                dup = db.table('shows').select('id').eq('name', new_name.strip()).execute().data
                if dup:
                    st.warning(f'"{new_name}" 이미 추적 중')
                else:
                    db.table('shows').insert({
                        'name': new_name.strip(),
                        'channel': new_ch.strip(),
                        'category': new_cat,
                        'air_days': new_days if new_show_type == 'regular' else [],
                        'air_time_kst': new_time.strip(),
                        'current_episode': int(new_ep),
                        'ended': False,
                        'show_type': new_show_type,
                        'source': 'manual',
                    }).execute()
                    st.success(f'"{new_name}" 추가됨 ({"일회성" if new_show_type == "event" else "정기"})')
                    st.rerun()
