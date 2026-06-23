"""
Phase 1 Checkpoint — 실제 방영 에피소드로 4개 컬렉터 검증.
Naver API 키 없이도 구조 테스트는 통과. 키 있으면 실제 데이터 검증.

Usage: python -m pytest tests/test_collectors.py -v
       python -m tests.test_collectors  (standalone)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from data_collector.ratings import fetch_ratings
from data_collector.news import fetch_news
from data_collector.reactions import fetch_reactions
from data_collector.ott_rank import fetch_ott_rank

KST = ZoneInfo('Asia/Seoul')

# 실제 방영된 에피소드 (검증용)
TEST_EPISODES = [
    {
        'program': '선재 업고 튀어',
        'aired_at': datetime(2024, 5, 20, 21, 10, tzinfo=KST),
        'category': 'romance',
    },
    {
        'program': '눈물의 여왕',
        'aired_at': datetime(2024, 5, 11, 21, 10, tzinfo=KST),
        'category': 'romance',
    },
    {
        'program': '흑백요리사',
        'aired_at': datetime(2024, 10, 1, 0, 0, tzinfo=KST),
        'category': 'variety',
    },
]

HAS_NAVER_KEY = bool(os.environ.get('NAVER_CLIENT_ID'))


class TestRatingsCollector:
    def test_returns_expected_schema(self):
        ep = TEST_EPISODES[0]
        result = fetch_ratings(ep['program'], ep['aired_at'])
        assert 'ratings_percent' in result
        assert 'collected_at' in result

    def test_ratings_percent_type(self):
        ep = TEST_EPISODES[0]
        result = fetch_ratings(ep['program'], ep['aired_at'])
        val = result.get('ratings_percent')
        assert val is None or isinstance(val, float)

    @pytest.mark.skipif(not HAS_NAVER_KEY, reason='Naver API key not set')
    def test_real_ratings_in_range(self):
        ep = TEST_EPISODES[0]
        result = fetch_ratings(ep['program'], ep['aired_at'])
        val = result.get('ratings_percent')
        if val is not None:
            assert 0.1 <= val <= 50.0, f"Unexpected value: {val}"

    def test_no_exception_on_invalid_program(self):
        result = fetch_ratings('존재하지않는프로그램xyz123', datetime.now(KST))
        assert 'ratings_percent' in result


class TestNewsCollector:
    def test_returns_expected_schema(self):
        ep = TEST_EPISODES[0]
        result = fetch_news(ep['program'], ep['aired_at'])
        assert 'news_summary' in result
        assert 'article_count' in result
        assert 'collected_at' in result

    def test_article_count_non_negative(self):
        ep = TEST_EPISODES[0]
        result = fetch_news(ep['program'], ep['aired_at'])
        assert result.get('article_count', 0) >= 0

    @pytest.mark.skipif(not HAS_NAVER_KEY, reason='Naver API key not set')
    def test_real_news_contains_korean(self):
        import re
        ep = TEST_EPISODES[0]
        result = fetch_news(ep['program'], ep['aired_at'])
        summary = result.get('news_summary', '')
        if summary:
            assert re.search(r'[가-힣]', summary), "news_summary should contain Korean"

    def test_no_exception_on_invalid_program(self):
        result = fetch_news('존재하지않는프로그램xyz123', datetime.now(KST))
        assert 'news_summary' in result


class TestReactionsCollector:
    def test_returns_expected_schema(self):
        ep = TEST_EPISODES[0]
        result = fetch_reactions(ep['program'], ep['aired_at'])
        assert 'reaction_score' in result
        assert 'collected_at' in result

    def test_reaction_score_in_range(self):
        ep = TEST_EPISODES[0]
        result = fetch_reactions(ep['program'], ep['aired_at'])
        score = result.get('reaction_score', 0)
        assert 0.0 <= score <= 10.0, f"Score out of range: {score}"

    def test_no_exception_on_invalid_program(self):
        result = fetch_reactions('존재하지않는프로그램xyz123', datetime.now(KST))
        assert 'reaction_score' in result


class TestOttRankCollector:
    def test_returns_expected_schema(self):
        ep = TEST_EPISODES[2]  # 흑백요리사 — Netflix에 있었음
        result = fetch_ott_rank(ep['program'])
        assert 'netflix_rank' in result
        assert 'tving_rank' in result
        assert 'coupang_rank' in result
        assert 'collected_at' in result

    def test_ranks_are_int_or_none(self):
        result = fetch_ott_rank('선재 업고 튀어')
        for key in ('netflix_rank', 'tving_rank', 'coupang_rank'):
            val = result.get(key)
            assert val is None or isinstance(val, int)


class TestAllCollectors:
    @pytest.mark.skipif(not HAS_NAVER_KEY, reason='Naver API key not set')
    def test_three_episodes_pass_validation(self):
        """Phase 1 체크포인트: 3개 에피소드, 4개 컬렉터 모두 스키마 유효"""
        from validators import run_all
        errors = []
        for ep in TEST_EPISODES:
            ratings = fetch_ratings(ep['program'], ep['aired_at'])
            news = fetch_news(ep['program'], ep['aired_at'])
            reactions = fetch_reactions(ep['program'], ep['aired_at'])

            result = run_all(
                {**ratings, **news, **reactions},
                {
                    # freshness 체크 제외 — 과거 에피소드는 항상 실패하므로
                    # 실제 파이프라인에서는 aired_at + max_hours 사용
                    'required_fields': ['collected_at'],
                    'korean_text_fields': ['news_summary'],
                }
            )
            if not result.passed:
                errors.append(f"{ep['program']}: {result.errors}")

        assert not errors, f"Validation failed: {errors}"


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
