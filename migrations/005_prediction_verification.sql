-- predictions 테이블에 verification_method 컬럼 추가
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS verification_method TEXT;
