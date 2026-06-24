-- 검증 AI가 판정한 "실제로 일어난 선택지 id" 저장
-- verdict 는 "최고 배당(유력 후보)이 맞았는지" (correct/incorrect/pending) 유지
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS correct_option_id VARCHAR;
