-- 예측별 결과 확인 시점(클로징)
-- 'next'   = 다음 회차에 결과 확인 (대부분)
-- 'finale' = 최종화/시즌 종료에 결과 확인 (우승자·최종순위 등)
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS resolution_horizon VARCHAR DEFAULT 'next';

-- 기존 행 백필 (NULL → 'next')
UPDATE predictions SET resolution_horizon = 'next' WHERE resolution_horizon IS NULL;
