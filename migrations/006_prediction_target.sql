-- 예측이 "어느 회차에 관한 것인지" 영속화 + 조회 단순화용 비정규화
-- predictions.episode_id 는 "예측을 생성한 회차 N", target_episode_number 는 "예측 대상 회차 N+1"
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS target_episode_number INT;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS program_name VARCHAR;

CREATE INDEX IF NOT EXISTS idx_predictions_target
    ON predictions(program_name, target_episode_number, verdict);
