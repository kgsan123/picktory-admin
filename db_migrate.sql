-- Phase 1 마이그레이션
-- Supabase SQL Editor에서 한 번만 실행하면 됩니다.

-- 1. episodes 테이블 생성 (없으면)
CREATE TABLE IF NOT EXISTS episodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    program_name VARCHAR NOT NULL,
    episode_number INT,
    channel VARCHAR,
    category VARCHAR,
    aired_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. episodes 컬럼 추가
ALTER TABLE episodes ADD COLUMN IF NOT EXISTS ratings_percent FLOAT;
ALTER TABLE episodes ADD COLUMN IF NOT EXISTS reaction_score FLOAT;
ALTER TABLE episodes ADD COLUMN IF NOT EXISTS news_summary TEXT;
ALTER TABLE episodes ADD COLUMN IF NOT EXISTS pipeline_status VARCHAR DEFAULT 'detected';

-- 3. predictions 테이블 생성
CREATE TABLE IF NOT EXISTS predictions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    episode_id UUID REFERENCES episodes(id),
    category VARCHAR,
    title TEXT,
    content TEXT,
    options JSONB,
    difficulty INT,
    fun_score INT,
    verdict VARCHAR DEFAULT 'pending',
    confidence FLOAT,
    evidence_text TEXT,
    prompt_version VARCHAR,
    status VARCHAR DEFAULT 'draft',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. pipeline_logs 테이블 생성
CREATE TABLE IF NOT EXISTS pipeline_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    episode_id UUID REFERENCES episodes(id),
    step VARCHAR,
    status VARCHAR,
    duration_sec FLOAT,
    error_msg TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 확인
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;
