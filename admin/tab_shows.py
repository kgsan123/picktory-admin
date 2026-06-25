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
CAT_COLOR = {'romance': 'red', 'survival': 'orange', 'variety': 'green',
             'drama': 'violet', 'music': 'blue'}
CAT_LABEL = {'romance': '연애', 'survival': '서바이벌', 'variety': '예능',
             'drama': '드라마', 'music': '음악'}


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
        return '❌ 실패: 에피소드 DB 등록 실패'
    try:
        from ai_engine.prediction_generator import generate_episode_predictions
        preds = generate_episode_predictions(episode_id, extra_context=extra_context)
        if preds:
            db.table('shows').update(
                {'current_episode': episode_num + 1}
            ).eq('id', show['id']).execute()
            return f'✅ {len(preds)}개 예측 생성 → [예측] 탭에서 검토 후 게시'
        # 0개 — 이유 구분
        status = ''
        try:
            ep_row = db.table('episodes').select('pipeline_status').eq('id', episode_id).single().execute().data
            status = (ep_row or {}).get('pipeline_status', '')
        except Exception:
            pass
        if status == 'context_insufficient':
            return ('⚠️ 실패(수집 정보 부족): 이 회차에 대한 뉴스·후기가 충분히 수집되지 않았습니다. '
                    '아래 [컨텍스트·설정]의 "이번 회차 핵심 내용"에 줄거리·출연자를 직접 입력 후 다시 시도하세요.')
        return '⚠️ 실패: 예측 0개 (생성 결과 없음) — 다시 시도하세요.'
    except Exception as e:
        # generate_predictions가 이미 사유를 분류해 raise하므로 그대로 표시.
        # 그 외(DB 등) 예외는 분류해서 보강.
        from ai_engine.prediction_generator import classify_generation_error
        msg = str(e)
        if '한도' in msg or '품질 기준' in msg or 'JSON' in msg or '미설정' in msg or '연결' in msg:
            return f'❌ 실패: {msg}'
        return f'❌ 실패: {classify_generation_error(msg)}'


def _show_card(db, s: dict):
    sid = s['id']
    cat = s.get('category', 'variety')
    days = ''.join(DAY_KR.get(d, d) for d in (s.get('air_days') or []))
    air_time = s.get('air_time_kst', '')
    ep_now = int(s.get('current_episode', 1))
    is_event = s.get('show_type') == 'event'

    with st.container(border=True):
        # ── 헤더: 제목 + 카테고리 배지 ──────────────────
        st.markdown(f"**{CAT_EMOJI.get(cat, '📺')} {s['name']}**")
        st.badge(CAT_LABEL.get(cat, cat), color=CAT_COLOR.get(cat, 'gray'))

        # ── 메타 정보 라인 ──────────────────────────────
        meta = s.get('channel') or '방송사 미지정'
        if is_event:
            meta += ' · 일회성'
        elif days or air_time:
            meta += f" · {days} {air_time}".rstrip()
        st.caption(f"{meta}　·　현재 {ep_now}회")

        # ── 액션: 회차 입력 + 생성 버튼 (좁은 카드 → 세로 배치) ──
        ep_input = st.number_input(
            '방영된 회차', min_value=1, value=ep_now,
            key=f'ep_{sid}',
            help='방금 방영 끝난 회차 → 다음 회차 예측 자동 생성',
        )
        gen_btn = st.button('예측 생성 ▶', key=f'gen_{sid}',
                            type='primary', use_container_width=True)

        # ── 컨텍스트 + 설정 expander ────────────────────
        with st.expander('컨텍스트 · 설정', expanded=False):
            st.caption('📝 컨텍스트 입력 — 입력할수록 예측이 정확해집니다')
            st.text_area(
                '이번 회차 핵심 내용',
                key=f'summary_{sid}',
                placeholder=(
                    '예) 이번주 1위는 aespa, NewJeans가 컴백 무대 첫 선 보임. '
                    '특별 콜라보 무대 있었음.'
                ),
                height=80,
            )
            st.text_area(
                '다음 회차 예고/힌트 (선택)',
                key=f'trailer_{sid}',
                placeholder='예) 다음주 BTS 지민 솔로 컴백 예고, 깜짝 게스트 예정',
                height=60,
            )

            st.divider()
            st.caption('⚙️ 설정')
            ec1, ec2 = st.columns(2)
            new_channel = ec1.text_input('방송사', value=s.get('channel') or '',
                                          key=f'ch_{sid}', placeholder='예: SBS, tvN, ENA')
            new_time = ec2.text_input('방영 시각 (HH:MM)', value=air_time, key=f'time_{sid}')
            new_days = ec1.multiselect('방영 요일', DAYS_OPTIONS,
                                        default=s.get('air_days') or [], key=f'days_{sid}')
            new_cat = ec2.selectbox('카테고리', CATEGORIES,
                                     index=CATEGORIES.index(cat) if cat in CATEGORIES else 3,
                                     format_func=lambda c: CAT_LABEL.get(c, c),
                                     key=f'cat_{sid}')
            new_notes = st.text_area(
                '프로그램 형식 설명 (AI에 전달)',
                value=s.get('notes') or '',
                key=f'notes_{sid}',
                placeholder=(
                    '예) 같은 기수의 남녀 솔로들이 만나는 형식. 하트시그널·다른 프로그램 이름 사용 금지.\n'
                    '예) 요리사 두 명이 대결하는 형식. 이번 회차 대결자는 컨텍스트에서 파악.'
                ),
                height=70,
            )
            sc1, sc2 = st.columns(2)
            if sc1.button('설정 저장', key=f'save_{sid}', use_container_width=True):
                db.table('shows').update({
                    'channel': new_channel.strip(),
                    'air_days': new_days,
                    'air_time_kst': new_time,
                    'category': new_cat,
                    'notes': new_notes,
                }).eq('id', sid).execute()
                st.toast('저장됨')
                st.rerun()
            if sc2.button('종영 처리', key=f'end_{sid}', use_container_width=True):
                db.table('shows').update({'ended': True}).eq('id', sid).execute()
                st.toast(f'{s["name"]} 종영 처리됨')
                st.rerun()

    # 버튼 처리 (카드 컨테이너 밖에서 spinner 표시)
    if gen_btn:
        extra = {
            'episode_summary': st.session_state.get(f'summary_{sid}', ''),
            'trailer_hints': st.session_state.get(f'trailer_{sid}', ''),
        }
        with st.spinner(f'{s["name"]} {ep_input}회 예측 생성 중... (10~20초)'):
            msg = _run_generate(db, s, ep_input, extra)
        # 성공은 토스트, 실패는 사유가 보이도록 지속 표시
        if msg.startswith('✅'):
            st.success(msg)
        elif msg.startswith('⚠️'):
            st.warning(msg)
        else:
            st.error(msg)


def render(db):
    shows = (db.table('shows').select('*').eq('ended', False)
             .order('category').order('name').execute().data or [])

    st.subheader(f'추적 프로그램 · {len(shows)}개')

    if not shows:
        st.info('추적 중인 프로그램 없음 — 아래에서 추가하세요')
    else:
        # 카테고리 필터 (전체 + 존재하는 카테고리)
        cats_present = sorted({s.get('category', 'variety') for s in shows})
        counts = {c: sum(1 for s in shows if s.get('category') == c) for c in cats_present}
        options = ['전체'] + cats_present

        def _fmt(c):
            if c == '전체':
                return f'전체 ({len(shows)})'
            return f'{CAT_EMOJI.get(c, "📺")} {CAT_LABEL.get(c, c)} ({counts[c]})'

        filt = st.segmented_control(
            '카테고리 필터', options, default='전체',
            format_func=_fmt, label_visibility='collapsed', key='cat_filter',
        ) or '전체'
        visible = [s for s in shows if filt == '전체' or s.get('category') == filt]

        # 4열 카드 그리드
        cols = st.columns(4, gap='small')
        for i, s in enumerate(visible):
            with cols[i % 4]:
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
