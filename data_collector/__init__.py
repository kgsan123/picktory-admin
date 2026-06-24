"""
data_collector — Phase 1 entry point.
collect_all(episode_id) 가 메인 인터페이스.
"""
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from validators import run_all, ValidationResult
from data_collector.ratings import fetch_ratings
from data_collector.news import fetch_news
from data_collector.reactions import fetch_reactions
from data_collector.ott_rank import fetch_ott_rank

KST = ZoneInfo('Asia/Seoul')
log = logging.getLogger(__name__)


def _log_pipeline(client, episode_id: str, step: str, status: str,
                  duration: float, error: str = None):
    try:
        client.table('pipeline_logs').upsert({
            'episode_id': episode_id,
            'step': step,
            'status': status,
            'duration_sec': round(duration, 2),
            'error_msg': error,
            'created_at': datetime.now(KST).isoformat(),
        }).execute()
    except Exception as e:
        log.warning(f"pipeline_logs write failed: {e}")


def collect_all(episode_id: str) -> dict:
    """
    Fetches episode from DB, runs all collectors, validates, upserts results.
    Returns dict with all collected data (including None fields on failure).
    Never raises.
    """
    from db import get_client
    client = get_client()
    t0 = time.monotonic()

    try:
        ep_resp = client.table('episodes').select('*').eq('id', episode_id).single().execute()
        episode = ep_resp.data
    except Exception as e:
        log.error(f"Episode fetch failed: {e}")
        return {'error': str(e)}

    program_name: str = episode.get('program_name', '')
    episode_num = episode.get('episode_number')
    category = episode.get('category', 'drama')
    aired_at_raw: str = episode.get('aired_at') or episode.get('created_at')
    aired_at = datetime.fromisoformat(aired_at_raw).astimezone(KST)

    collected: dict = {'episode_id': episode_id}

    for label, fn, fn_args in [
        ('ratings',   fetch_ratings,  (program_name, aired_at, episode_num)),
        ('news',      fetch_news,     (program_name, aired_at)),
        ('reactions', fetch_reactions,(program_name, aired_at, category)),
        ('ott_rank',  fetch_ott_rank, (program_name,)),
    ]:
        step_t = time.monotonic()
        try:
            data = fn(*fn_args)
        except Exception as e:
            data = {'error': str(e)}
            log.warning(f"[{label}] collector error: {e}")
        elapsed = time.monotonic() - step_t
        collected[label] = data
        status = 'failed' if 'error' in data else 'success'
        _log_pipeline(client, episode_id, f'collect_{label}', status, elapsed,
                      data.get('error'))

    # Validate ratings + news (core fields)
    ratings_data = collected.get('ratings', {})
    news_data = collected.get('news', {})
    reaction_data = collected.get('reactions', {})

    val_result: ValidationResult = run_all(
        {**ratings_data, **news_data, 'collected_at': datetime.now(KST)},
        {
            'required_fields': ['collected_at'],
            'korean_text_fields': ['news_summary'],
            'aired_at': aired_at,
            'max_hours': 30,
        }
    )
    collected['validation'] = {
        'passed': val_result.passed,
        'checks': val_result.checks,
        'errors': val_result.errors,
    }

    # Upsert episode columns
    try:
        update = {'pipeline_status': 'collected'}
        if ratings_data.get('ratings_percent') is not None:
            update['ratings_percent'] = ratings_data['ratings_percent']
        if reaction_data.get('reaction_score') is not None:
            update['reaction_score'] = reaction_data['reaction_score']
        if news_data.get('news_summary'):
            update['news_summary'] = news_data['news_summary']

        client.table('episodes').upsert({'id': episode_id, **update}).execute()
    except Exception as e:
        log.error(f"Episode upsert failed: {e}")
        collected['db_error'] = str(e)

    total_elapsed = time.monotonic() - t0
    _log_pipeline(client, episode_id, 'collect', 'success', total_elapsed)
    return collected
