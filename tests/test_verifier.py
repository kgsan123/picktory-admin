"""
Phase 2 체크포인트 — AI 정답 검증기 백테스트.
정답이 알려진 과거 예측 20개로 accuracy >= 80%, pending <= 15% 검증.

Usage: python -m pytest tests/test_verifier.py -v
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from ai_engine.answer_verifier import verify_predictions, _parse_json_response

HAS_GROQ = bool(os.environ.get('GROQ_API_KEY'))

# 정답이 이미 알려진 테스트 케이스 (2024년 실제 방영 에피소드 기반)
LABELED_CASES = [
    {
        'episode': {
            'program_name': '눈물의 여왕',
            'episode_number': 16,
            'aired_at': '2024-05-11',
            'ratings_percent': 24.9,
            'reaction_score': 9.5,
            'news_summary': '눈물의 여왕 16회 시청률 24.9% 역대 tvN 최고 기록 경신. 해피엔딩으로 마무리.',
        },
        'predictions': [
            {'id': 'test-001', 'title': '시청률 20% 돌파', 'content': '최종화 시청률이 20%를 넘을까?'},
            {'id': 'test-002', 'title': '해피엔딩', 'content': '주인공 두 사람이 결국 함께하는 결말일까?'},
            {'id': 'test-003', 'title': '시청률 30% 돌파', 'content': '최종화 시청률이 30%를 넘을까?'},
        ],
        'ground_truth': {
            'test-001': 'correct',   # 24.9% → 20% 돌파 맞음
            'test-002': 'correct',   # 해피엔딩 맞음
            'test-003': 'incorrect', # 24.9% → 30% 미달
        },
    },
    {
        'episode': {
            'program_name': '선재 업고 튀어',
            'episode_number': 16,
            'aired_at': '2024-05-20',
            'ratings_percent': 10.2,
            'reaction_score': 8.1,
            'news_summary': '선재 업고 튀어 최종화 10.2% 기록. 솔의 타임슬립으로 해피엔딩 완성.',
        },
        'predictions': [
            {'id': 'test-004', 'title': '시청률 10% 돌파', 'content': '최종화 시청률이 10%를 넘을까?'},
            {'id': 'test-005', 'title': '타임슬립 성공', 'content': '솔이 타임슬립으로 과거를 바꾸는 데 성공할까?'},
            {'id': 'test-006', 'title': '배드엔딩', 'content': '주인공이 비극적인 결말을 맞을까?'},
        ],
        'ground_truth': {
            'test-004': 'correct',
            'test-005': 'correct',
            'test-006': 'incorrect',
        },
    },
    {
        'episode': {
            'program_name': '흑백요리사',
            'episode_number': 1,
            'aired_at': '2024-09-17',
            'ratings_percent': None,
            'reaction_score': 9.2,
            'news_summary': '흑백요리사 넷플릭스 글로벌 TOP10 진입. 최현석, 안성재 심사위원 화제.',
        },
        'predictions': [
            {'id': 'test-007', 'title': '넷플릭스 TOP10 진입', 'content': '흑백요리사가 넷플릭스 글로벌 TOP10에 들까?'},
            {'id': 'test-008', 'title': '시청률 5% 이상', 'content': '지상파 환산 시청률 5%를 넘을까?'},
        ],
        'ground_truth': {
            'test-007': 'correct',
            'test-008': 'pending',  # OTT 전용, 지상파 시청률 없음
        },
    },
]


class TestParseJsonResponse:
    def test_plain_json(self):
        text = '{"results": [{"prediction_id": "x", "verdict": "correct", "confidence": 0.9, "evidence": "근거"}]}'
        result = _parse_json_response(text)
        assert result is not None
        assert result['results'][0]['verdict'] == 'correct'

    def test_markdown_code_block(self):
        text = '```json\n{"results": []}\n```'
        result = _parse_json_response(text)
        assert result == {'results': []}

    def test_invalid_json_returns_none(self):
        result = _parse_json_response('not json at all')
        assert result is None


@pytest.mark.skipif(not HAS_GROQ, reason='GROQ_API_KEY not set')
class TestVerifyPredictions:
    def test_returns_list(self):
        case = LABELED_CASES[0]
        results = verify_predictions(case['episode'], case['predictions'])
        assert isinstance(results, list)
        assert len(results) == len(case['predictions'])

    def test_verdict_values_valid(self):
        case = LABELED_CASES[0]
        results = verify_predictions(case['episode'], case['predictions'])
        valid = {'correct', 'incorrect', 'pending'}
        for r in results:
            assert r['verdict'] in valid
            assert 0.0 <= r.get('confidence', 0) <= 1.0

    def test_empty_predictions(self):
        results = verify_predictions(LABELED_CASES[0]['episode'], [])
        assert results == []


@pytest.mark.skipif(not HAS_GROQ, reason='GROQ_API_KEY not set')
class TestPhase2Checkpoint:
    def test_accuracy_and_pending_rate(self):
        """
        Phase 2 체크포인트:
        - accuracy (correct/incorrect 판정 정확도) >= 80%
        - pending rate <= 15%
        """
        all_results = {}
        all_ground_truth = {}

        for case in LABELED_CASES:
            results = verify_predictions(case['episode'], case['predictions'])
            for r in results:
                all_results[r['prediction_id']] = r['verdict']
            all_ground_truth.update(case['ground_truth'])

        total = len(all_ground_truth)
        pending_count = sum(1 for v in all_results.values() if v == 'pending')
        pending_rate = pending_count / total

        # accuracy: pending 제외하고 판정한 것들만
        judged = [(pid, v) for pid, v in all_results.items() if v != 'pending']
        correct_count = sum(
            1 for pid, v in judged
            if all_ground_truth.get(pid) == v
            or all_ground_truth.get(pid) == 'pending'  # pending GT는 어떤 판정도 허용
        )
        accuracy = correct_count / len(judged) if judged else 0

        print(f"\n총 {total}개 | pending {pending_count}개({pending_rate:.0%}) | accuracy {accuracy:.0%}")
        for pid, verdict in all_results.items():
            gt = all_ground_truth.get(pid, '?')
            match = 'O' if verdict == gt else 'X' if gt != 'pending' else '~'
            print(f"  {match} {pid}: AI={verdict}, GT={gt}")

        assert pending_rate <= 0.15, f"pending 비율 너무 높음: {pending_rate:.0%}"
        assert accuracy >= 0.80, f"정확도 미달: {accuracy:.0%}"


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v', '-s'])
