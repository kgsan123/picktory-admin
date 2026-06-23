"""
validators.py 단위 테스트.
Usage: python -m pytest tests/test_validators.py -v
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import pytest
from validators import (
    validate_schema, validate_korean, validate_freshness,
    validate_cross_source, run_all, ValidationResult,
)

KST = ZoneInfo('Asia/Seoul')


class TestValidateSchema:
    def test_passes_when_all_present(self):
        assert validate_schema({'a': 1, 'b': 'x'}, ['a', 'b']) is True

    def test_fails_on_missing_key(self):
        assert validate_schema({'a': 1}, ['a', 'b']) is False

    def test_fails_on_none_value(self):
        assert validate_schema({'a': None, 'b': 1}, ['a', 'b']) is False

    def test_empty_required_always_passes(self):
        assert validate_schema({}, []) is True


class TestValidateKorean:
    def test_korean_text_passes(self):
        assert validate_korean({'title': '선재 업고 튀어'}, ['title']) is True

    def test_empty_string_passes(self):
        assert validate_korean({'title': ''}, ['title']) is True

    def test_english_only_fails(self):
        assert validate_korean({'title': 'Hello World'}, ['title']) is False

    def test_mixed_passes(self):
        assert validate_korean({'title': 'Drama 선재'}, ['title']) is True

    def test_missing_field_passes(self):
        assert validate_korean({}, ['title']) is True


class TestValidateFreshness:
    def test_within_window(self):
        aired = datetime(2024, 5, 20, 21, 10, tzinfo=KST)
        collected = datetime(2024, 5, 20, 23, 0, tzinfo=KST)
        assert validate_freshness(collected, aired, max_hours=24) is True

    def test_exactly_at_boundary(self):
        aired = datetime(2024, 5, 20, 21, 10, tzinfo=KST)
        collected = aired + timedelta(hours=24)
        assert validate_freshness(collected, aired, max_hours=24) is True

    def test_too_old_fails(self):
        aired = datetime(2024, 5, 20, 21, 10, tzinfo=KST)
        collected = aired + timedelta(hours=25)
        assert validate_freshness(collected, aired, max_hours=24) is False


class TestValidateCrossSource:
    def test_keyword_in_both(self):
        assert validate_cross_source('선재 시청률', '선재 닐슨 5%', '선재') == 1.0

    def test_keyword_in_one(self):
        assert validate_cross_source('선재 시청률', '기타 뉴스', '선재') == 0.5

    def test_keyword_in_neither(self):
        assert validate_cross_source('기타', '내용', '선재') == 0.0


class TestRunAll:
    def test_all_pass(self):
        aired = datetime(2024, 5, 20, 21, 0, tzinfo=KST)
        data = {
            'ratings_percent': 5.2,
            'news_summary': '선재 업고 튀어 시청률',
            'collected_at': datetime(2024, 5, 20, 22, 0, tzinfo=KST),
        }
        config = {
            'required_fields': ['ratings_percent', 'news_summary'],
            'korean_text_fields': ['news_summary'],
            'aired_at': aired,
            'max_hours': 24,
        }
        result = run_all(data, config)
        assert result.passed is True
        assert not result.errors

    def test_missing_field_fails(self):
        data = {'news_summary': '선재', 'collected_at': datetime.now(KST)}
        config = {'required_fields': ['ratings_percent', 'news_summary']}
        result = run_all(data, config)
        assert result.passed is False
        assert result.checks['schema'] is False


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
