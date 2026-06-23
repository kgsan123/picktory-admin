"""
Phase 3 체크포인트 — 예측 생성기 품질 검증.
5개 에피소드 × avg fun_score >= 3.5, 에피소드당 생존 예측 >= 5개.

Usage: python -m pytest tests/test_generator.py -v -s
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from ai_engine.prediction_generator import generate_predictions, _apply_filters

HAS_GROQ = bool(os.environ.get('GROQ_API_KEY'))

TEST_EPISODES = [
    {
        'episode': {
            'program_name': '선재 업고 튀어',
            'episode_number': 15,
            'category': 'romance',
        },
        'context': {
            'episode_summary': '15회: 솔이 선재의 죽음을 막기 위해 다시 타임슬립. 선재는 솔의 정체를 의심하기 시작함. 두 사람 사이에 감정이 고조되며 위기가 찾아옴.',
            'trailer_hints': '[16화 예고] 선재, 솔에게 "나 기억해?" | 두 사람 마지막 이별 장면',
            'news_summary': '선재 업고 튀어 15회 시청률 9.8%, 최종화 앞두고 최고 기록 경신',
            'reaction_score': 9.2,
            'top_clip_views': 5200000,
        },
    },
    {
        'episode': {
            'program_name': '흑백요리사',
            'episode_number': 7,
            'category': 'survival',
        },
        'context': {
            'episode_summary': '7회: 흑수저 5명 vs 백수저 5명 최후의 팀 대결. 최현석 심사위원 깜짝 등장으로 반전. 나폴리 맛피아 팀이 우위를 점하는 듯 보임.',
            'trailer_hints': '[8화 선공개] 나폴리 맛피아, 단독 탈락 위기? | 안성재 "이건 실격입니다"',
            'news_summary': '흑백요리사 8회 예고 공개, 충격 반전 예고로 커뮤니티 화제',
            'reaction_score': 9.5,
            'top_clip_views': 3800000,
        },
    },
    {
        'episode': {
            'program_name': '눈물의 여왕',
            'episode_number': 14,
            'category': 'romance',
        },
        'context': {
            'episode_summary': '14회: 해인의 병이 재발. 현우는 해인을 살리기 위해 모든 것을 포기하려 함. 빌런 윤은성의 계략으로 두 사람이 다시 위기에 처함.',
            'trailer_hints': '[15화 예고] 해인 "나 기억 잃어도 괜찮아" | 현우, 눈물로 고백',
            'news_summary': '눈물의 여왕 14회 22.1% 기록, tvN 역대 3위',
            'reaction_score': 9.8,
            'top_clip_views': 9100000,
        },
    },
    {
        'episode': {
            'program_name': '피지컬: 100',
            'episode_number': 6,
            'category': 'survival',
        },
        'context': {
            'episode_summary': '6회: 퀘스트 생존자 30명에서 15명으로 압축. 줄다리기 대결에서 이변 속출. 격투기 선수들이 우세를 점하고 있으나 일반인 참가자들의 반란 조짐.',
            'trailer_hints': '[7화 예고] 역대급 체력 대결 공개 | 탈락자 발표 순간 충격',
            'news_summary': '피지컬100 시즌2 넷플릭스 글로벌 TOP10 진입',
            'reaction_score': 8.7,
            'top_clip_views': 2100000,
        },
    },
    {
        'episode': {
            'program_name': '나는 솔로',
            'episode_number': 22,
            'category': 'variety',
        },
        'context': {
            'episode_summary': '22기: 최종 선택을 앞두고 출연자들 감정이 폭발. 영수와 영자의 삼각관계가 핵심. 정숙이 영호에게 마음을 열기 시작한 것으로 보임.',
            'trailer_hints': '[최종화 예고] 영수 "저는 영자씨를 선택하겠습니다" | 충격 결말',
            'news_summary': '나는 솔로 22기 최종화 커플 성사 여부 화제',
            'reaction_score': 8.1,
            'top_clip_views': 1500000,
        },
    },
]


class TestFilters:
    def test_low_fun_score_removed(self):
        preds = [
            {'title': 'A', 'content': 'q', 'fun_score': 2, 'difficulty': 3,
             'verification_method': '해당 회차 방영 후 장면 확인', 'options': [{'id':'A','odds':0.5},{'id':'B','odds':0.5}]},
            {'title': 'B', 'content': 'q', 'fun_score': 4, 'difficulty': 3,
             'verification_method': '해당 회차 방영 후 장면 확인', 'options': [{'id':'A','odds':0.5},{'id':'B','odds':0.5}]},
        ]
        result = _apply_filters(preds)
        assert len(result) == 1
        assert result[0]['title'] == 'B'

    def test_no_verification_method_removed(self):
        preds = [
            {'title': 'A', 'content': 'q', 'fun_score': 4, 'difficulty': 3,
             'verification_method': '', 'options': [{'id':'A','odds':0.5},{'id':'B','odds':0.5}]},
        ]
        assert _apply_filters(preds) == []

    def test_too_obvious_removed(self):
        preds = [
            {'title': 'A', 'content': 'q', 'fun_score': 4, 'difficulty': 1,
             'verification_method': '방영 후 확인 가능', 'options': [
                 {'id':'A','odds':0.92},{'id':'B','odds':0.08}]},
        ]
        assert _apply_filters(preds) == []

    def test_good_prediction_passes(self):
        preds = [
            {'title': 'A', 'content': 'q', 'fun_score': 4, 'difficulty': 3,
             'verification_method': '해당 장면에서 확인 가능', 'options': [
                 {'id':'A','odds':0.6},{'id':'B','odds':0.4}]},
        ]
        assert len(_apply_filters(preds)) == 1


@pytest.mark.skipif(not HAS_GROQ, reason='GROQ_API_KEY not set')
class TestGeneratePredictions:
    def test_returns_list(self):
        ep = TEST_EPISODES[0]
        results = generate_predictions(ep['episode'], ep['context'])
        assert isinstance(results, list)

    def test_minimum_three_predictions(self):
        ep = TEST_EPISODES[0]
        results = generate_predictions(ep['episode'], ep['context'])
        assert len(results) >= 3, f"필터 후 {len(results)}개만 남음"

    def test_schema_valid(self):
        ep = TEST_EPISODES[0]
        results = generate_predictions(ep['episode'], ep['context'])
        for p in results:
            assert 'title' in p
            assert 'content' in p
            assert 'options' in p and len(p['options']) >= 2
            assert 'fun_score' in p
            assert 'difficulty' in p
            assert 'verification_method' in p
            assert 1 <= p['fun_score'] <= 5
            assert 1 <= p['difficulty'] <= 5


@pytest.mark.skipif(not HAS_GROQ, reason='GROQ_API_KEY not set')
class TestPhase3Checkpoint:
    def test_five_episodes_quality(self):
        """
        Phase 3 체크포인트:
        - 5개 에피소드 모두 생존 예측 >= 5개
        - 전체 avg fun_score >= 3.5
        """
        all_fun_scores = []
        failures = []

        for ep_case in TEST_EPISODES:
            results = generate_predictions(ep_case['episode'], ep_case['context'])
            program = ep_case['episode']['program_name']

            if len(results) < 5:
                failures.append(f"{program}: {len(results)}개 (최소 5개 필요)")

            scores = [p['fun_score'] for p in results]
            all_fun_scores.extend(scores)
            avg = sum(scores) / len(scores) if scores else 0
            print(f"\n{program}: {len(results)}개 생존, avg fun_score={avg:.1f}")
            for p in results:
                print(f"  [{p['difficulty']}★/{p['fun_score']}♥] {p['title']}: {p['content']}")

        total_avg = sum(all_fun_scores) / len(all_fun_scores) if all_fun_scores else 0
        print(f"\n전체 avg fun_score: {total_avg:.2f}")

        assert not failures, f"생존 예측 부족: {failures}"
        assert total_avg >= 3.5, f"avg fun_score 미달: {total_avg:.2f}"


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v', '-s'])
