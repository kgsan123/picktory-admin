"""PICKTORY 관리자 페이지 — Supabase 기반"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
KST = ZoneInfo('Asia/Seoul')

st.set_page_config(page_title='PICKTORY Admin', page_icon='🎯', layout='wide')


def _secret(key: str, default: str = '') -> str:
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)


ADMIN_PIN = _secret('ADMIN_PIN', '1234')


def check_auth() -> bool:
    if st.session_state.get('authenticated'):
        return True
    st.title('🎯 PICKTORY Admin')
    pin = st.text_input('PIN 입력', type='password')
    if st.button('접속'):
        if pin == ADMIN_PIN:
            st.session_state['authenticated'] = True
            st.rerun()
        else:
            st.error('PIN이 올바르지 않습니다')
    return False


@st.cache_resource
def get_db():
    return create_client(_secret('SUPABASE_URL'), _secret('SUPABASE_KEY'))


def main():
    if not check_auth():
        return

    db = get_db()
    st.title('🎯 PICKTORY Admin')
    st.caption(datetime.now(KST).strftime('%Y-%m-%d %H:%M KST'))

    from admin.tab_shows import render as shows_tab
    from admin.tab_predictions import render as pred_tab
    from admin.tab_discovery import render as disc_tab

    t1, t2, t3 = st.tabs(['📺 프로그램', '🗳️ 예측', '🔍 신규 발견'])
    with t1:
        shows_tab(db)
    with t2:
        pred_tab(db)
    with t3:
        disc_tab(db)


if __name__ == '__main__':
    main()
