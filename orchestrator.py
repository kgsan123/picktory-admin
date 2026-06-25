"""
PICKTORY 오케스트레이터
APScheduler로 방영 일정에 맞춰 파이프라인 자동 실행

실행: python orchestrator.py
"""
import time
import logging
import os
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from dotenv import load_dotenv

load_dotenv()
KST = ZoneInfo('Asia/Seoul')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)

DISCORD_WEBHOOK = os.environ.get('DISCORD_WEBHOOK_URL', '')
DAY_MAP = {'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3, 'Fri': 4, 'Sat': 5, 'Sun': 6}

# pending 재검증 sweep 튜닝 상수
SETTLE_HOURS = 6      # 방영 후 이 시간은 지나야 데이터 축적됐다고 보고 검증 시도
EXPIRE_DAYS = 7       # 이 기간 넘게 판정 못 한 pending 예측은 만료 처리
MAX_PER_SWEEP = 15    # 한 sweep당 최대 검증 회차 수 (Groq 일일 한도 보호)


# ── 유틸 ─────────────────────────────────────────────────────

def load_shows() -> list:
    """Supabase shows 테이블에서 추적 중인 프로그램 로드."""
    from data_collector.episode_detector import get_shows_to_check
    return get_shows_to_check()


def send_discord(msg: str):
    if not DISCORD_WEBHOOK:
        return
    try:
        requests.post(DISCORD_WEBHOOK, json={'content': msg}, timeout=5)
    except Exception:
        pass


def with_retry(fn, *args, step='', program='', ep_num=0, **kwargs):
    """3회 재시도, 지수 백오프 (30s → 120s → 480s)"""
    delays = [30, 120, 480]
    last_err = None
    for attempt, delay in enumerate(delays, 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            log.warning(f"[{step}] {program} EP{ep_num} 시도 {attempt} 실패: {e}")
            if attempt < len(delays):
                time.sleep(delay)

    msg = f"⚠️ [{step}] {program} EP{ep_num} 최종 실패: {last_err}"
    log.error(msg)
    send_discord(msg)
    return None


# ── 파이프라인 단계 ───────────────────────────────────────────

def step_detect(show: dict) -> str | None:
    from data_collector.episode_detector import detect_new_episode
    return with_retry(
        detect_new_episode, show,
        step='detect', program=show['name'], ep_num=show.get('current_episode', 0)
    )


def step_collect(episode_id: str, show: dict):
    from data_collector import collect_all
    with_retry(
        collect_all, episode_id,
        step='collect', program=show['name'], ep_num=show.get('current_episode', 0)
    )


def step_verify(episode_id: str, show: dict):
    # 방금 방영된 회차 N을 대상으로, 직전 회차에 생성된 예측
    # (target_episode_number == N)을 N의 fresh 데이터로 판정.
    from ai_engine.answer_verifier import verify_episode
    with_retry(
        verify_episode, episode_id,
        step='verify', program=show['name'], ep_num=show.get('current_episode', 0)
    )


def step_generate(episode_id: str, show: dict):
    from ai_engine.prediction_generator import generate_episode_predictions
    preds = with_retry(
        generate_episode_predictions, episode_id,
        step='generate', program=show['name'], ep_num=show.get('current_episode', 0)
    )
    # 컨텍스트 부족으로 건너뛴 경우 운영자에게 알림 (1회)
    if preds == []:
        try:
            from db import get_client
            ep = get_client().table('episodes').select('pipeline_status').eq('id', episode_id).single().execute().data
            if ep and ep.get('pipeline_status') == 'context_insufficient':
                send_discord(
                    f"⚠️ {show['name']} EP{show.get('current_episode', 0)}: "
                    f"수집 정보 부족으로 예측 생성 건너뜀 — 어드민에서 컨텍스트 직접 입력 필요"
                )
        except Exception:
            pass


def run_pipeline(show: dict):
    """전체 파이프라인 — 단계 실패해도 다음 단계 계속 진행"""
    program = show['name']
    log.info(f"=== 파이프라인 시작: {program} ===")

    episode_id = step_detect(show)
    if not episode_id:
        log.warning(f"{program}: 에피소드 미감지, 파이프라인 중단")
        send_discord(f"ℹ️ {program} 에피소드 미감지 — 방영 전이거나 기사 없음")
        return

    log.info(f"{program}: 감지 완료 ({episode_id})")
    step_collect(episode_id, show)
    log.info(f"{program}: 수집 완료")
    step_verify(episode_id, show)
    log.info(f"{program}: 검증 완료")
    step_generate(episode_id, show)
    log.info(f"{program}: 예측 생성 완료")
    send_discord(f"✅ {program} 파이프라인 완료")


# ── 스케줄러 ─────────────────────────────────────────────────

def schedule_today(scheduler: BlockingScheduler, shows: list):
    """오늘 방영 예정인 프로그램 스케줄 등록"""
    now = datetime.now(KST)
    today_weekday = now.weekday()

    for show in shows:
        air_weekdays = [DAY_MAP[d] for d in show.get('air_days', [])]
        if today_weekday not in air_weekdays:
            continue

        h, m = map(int, show['air_time_kst'].split(':'))
        air_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
        trigger_time = air_time + timedelta(minutes=30)  # 방영 30분 후 감지

        if trigger_time < now:
            log.info(f"{show['name']}: 오늘 방영 시간 이미 지남")
            continue

        job_id = f"pipeline_{show['name']}_{now.date()}"
        scheduler.add_job(
            run_pipeline,
            trigger=DateTrigger(run_date=trigger_time, timezone=KST),
            args=[show],
            id=job_id,
            replace_existing=True,
        )
        log.info(f"등록: {show['name']} → {trigger_time.strftime('%m/%d %H:%M')}")


def run_weekly_discovery():
    """매주 일요일 자동 프로그램 발견 실행 + Discord 알림."""
    try:
        from data_collector.show_discovery import discover_shows
        found = discover_shows()
        if found:
            names = ', '.join(s['name'] for s in found[:5])
            extra = f' 외 {len(found)-5}개' if len(found) > 5 else ''
            send_discord(f'🔍 신규 발견 {len(found)}개: {names}{extra} — 관리자 페이지에서 검토하세요')
        else:
            log.info('주간 발견: 신규 프로그램 없음')
    except Exception as e:
        log.error(f'주간 발견 실패: {e}')
        send_discord(f'⚠️ [discovery] 주간 자동 발견 실패: {e}')


def _parse_aired(raw: str | None):
    """aired_at 문자열 → KST datetime. 실패 시 None."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST)
    except Exception:
        return None


def resolve_pending_sweep():
    """
    아직 pending인 published 예측을, 방영이 끝나 데이터가 쌓인 회차에 대해 재검증.
    verify_episode(fresh 재수집 + 선택지 매칭)를 재사용. 데이터 부족하면 토큰 없이 보류.
    """
    from db import get_client
    from ai_engine.answer_verifier import verify_episode
    client = get_client()
    now = datetime.now(KST)

    try:
        rows = (client.table('predictions')
                .select('program_name,target_episode_number')
                .eq('verdict', 'pending').eq('status', 'published')
                .execute().data) or []
    except Exception as e:
        log.error(f'[sweep] pending 조회 실패: {e}')
        return

    groups = sorted({
        (r['program_name'], r['target_episode_number']) for r in rows
        if r.get('program_name') and r.get('target_episode_number')
    })
    if not groups:
        log.info('[sweep] 재검증할 pending 예측 없음')
        return

    resolved_eps = pending_eps = expired_eps = verified = capped = 0

    for program, target_ep in groups:
        if verified >= MAX_PER_SWEEP:
            capped += 1
            continue

        ep_rows = (client.table('episodes')
                   .select('id,aired_at')
                   .eq('program_name', program).eq('episode_number', target_ep)
                   .order('aired_at', desc=True).limit(1).execute().data) or []
        if not ep_rows:
            continue  # target 회차 방영 기록 없음 → 아직 검증 불가

        aired = _parse_aired(ep_rows[0].get('aired_at'))
        if aired is None or aired > now - timedelta(hours=SETTLE_HOURS):
            continue  # 아직 방영 직후 → 데이터 미축적, 다음 sweep에서

        if aired < now - timedelta(days=EXPIRE_DAYS):
            # 기한 초과 → 만료 처리 (무한 재시도 방지)
            client.table('predictions').update({'status': 'expired'}) \
                .eq('program_name', program).eq('target_episode_number', target_ep) \
                .eq('verdict', 'pending').eq('status', 'published').execute()
            expired_eps += 1
            send_discord(f'⏰ {program} {target_ep}회: {EXPIRE_DAYS}일 내 판정 실패로 만료 처리 — 필요시 수동 판정')
            continue

        results = verify_episode(ep_rows[0]['id'])
        verified += 1
        if any(r.get('verdict') != 'pending' for r in results):
            resolved_eps += 1
        else:
            pending_eps += 1

    log.info(f'[sweep] 검증 {verified}회차: 판정 {resolved_eps} / 보류 {pending_eps} / 만료 {expired_eps}'
             + (f' / 상한초과 {capped}' if capped else ''))
    if resolved_eps or expired_eps:
        msg = f'🔁 검증 sweep: {resolved_eps}회차 판정 완료, {pending_eps}회차 보류, {expired_eps}회차 만료'
        if capped:
            msg += f' (상한으로 {capped}회차 다음 sweep 대기)'
        send_discord(msg)


def main():
    shows = load_shows()
    scheduler = BlockingScheduler(timezone=KST)

    schedule_today(scheduler, shows)

    # 매일 00:05에 다음 날 스케줄 재등록
    def daily_reschedule():
        schedule_today(scheduler, load_shows())

    scheduler.add_job(
        daily_reschedule,
        trigger=CronTrigger(hour=0, minute=5, timezone=KST),
        id='daily_reschedule',
        replace_existing=True,
    )

    # 매주 일요일 06:00 프로그램 자동 발견
    scheduler.add_job(
        run_weekly_discovery,
        trigger=CronTrigger(day_of_week='sun', hour=6, minute=0, timezone=KST),
        id='weekly_discovery',
        replace_existing=True,
    )

    # 매일 11:00 pending 예측 재검증 (전날 저녁 방영분 데이터 축적 후)
    scheduler.add_job(
        resolve_pending_sweep,
        trigger=CronTrigger(hour=11, minute=0, timezone=KST),
        id='resolve_pending_sweep',
        replace_existing=True,
    )

    log.info(f"오케스트레이터 시작 — {len(shows)}개 프로그램 모니터링 중")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        log.info("오케스트레이터 종료")


if __name__ == '__main__':
    main()
