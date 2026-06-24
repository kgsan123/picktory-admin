"""Tab 1: 프로그램 관리 — 예측 생성 버튼이 항상 보이는 카드 레이아웃"""
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


def _run_generate(db, show: dict, episode_num: int) -> str:
    episode_id = _upsert_episode(db, show, episode_num)
    if not episode_id:
        return '에피소드 DB 등록 실패'
    try:
        from ai_engine.prediction_generator import generate_episode_predictions
        preds = generate_episode_predictions(episode_id)
        if preds:
            db.table('shows').update({'current_episode': episode_num + 1}).eq('id', show['id']).execute()
            return f'✅ {len(preds)}개 예측 생성 → [예측] 탭에서 검토 후 게시'
        return '예측 0개 생성 (필터 탈락 또는 AI 응답 파싱 실패)'
    except Exception as e:
        return f'❌ 오류: {e}'


def _show_card(db, s: dict):
    """프로그램 1개 카드: 메타 정보 + 즉시 예측 생성 버튼 + 설정 expander"""
    sid = s['id']
    cat = s.get('category', 'variety')
    days = ''.join(DAY_KR.get(d, d) for d in (s.get('air_days') or []))
    air_time = s.get('air_time_kst', '')
    ep_now = int(s.get('current_episode', 1))

    # ── 메인 행: 항상 보임 ───────────────────────────
    c_info, c_ep, c_btn = st.columns([4, 2, 2])

    with c_info:
        st.markdown(
            f"{CAT_EMOJI.get(cat, '📺')} **{s['name']}**  "
            f"<span style='color:gray;font-size:0.85em'>{s.get('channel','?')} · {days} {air_time}</span>",
            unsafe_allow_html=True,
        )

    ep_input = c_ep.number_input(
        '방영된 회차',
        min_value=1, value=ep_now,
        key=f'ep_{sid}',
        label_visibility='collapsed',
        help='방금 방영된 회차 번호 입력 후 예측 생성',
    )

    if c_btn.button('예측 생성 ▶', key=f'gen_{sid}', type='primary', use_container_width=True):
        with st.spinner(f'{s["name"]} {ep_input}회 예측 생성 중... (10~20초)'):
            msg = _run_generate(db, s, ep_input)
        st.toast(msg)

    # ── 설정 expander: 접혀 있음 ─────────────────────
    with st.expander('설정', expanded=False):
        ec1, ec2 = st.columns(2)
        new_days = ec1.multiselect('방영 요일', DAYS_OPTIONS,
                                    default=s.get('air_days') or [], key=f'days_{sid}')
        new_time = ec2.text_input('방영 시각 (HH:MM)',
                                   value=air_time, key=f'time_{sid}')
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

    st.divider()


def render(db):
    st.subheader('추적 프로그램')

    shows = (db.table('shows').select('*').eq('ended', False)
             .order('name').execute().data or [])

    if not shows:
        st.info('추적 중인 프로그램 없음 — 아래에서 추가하세요')
    else:
        # 컬럼 헤더
        h1, h2, h3 = st.columns([4, 2, 2])
        h2.caption('방영된 회차')
        h3.caption('수동 예측 생성')
        st.divider()
        for s in shows:
            _show_card(db, s)

    # ── 새 프로그램 추가 ───────────────────────────────
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
                        'air_days': new_days,
                        'air_time_kst': new_time.strip(),
                        'current_episode': int(new_ep),
                        'ended': False,
                        'source': 'manual',
                    }).execute()
                    st.success(f'"{new_name}" 추가됨')
                    st.rerun()
