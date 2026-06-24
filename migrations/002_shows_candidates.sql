-- Migration 002: shows + show_candidates 테이블 추가
-- Supabase Dashboard > SQL Editor 에서 실행

-- ── 1. shows 테이블 ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS shows (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name          VARCHAR     NOT NULL,
    category      VARCHAR     DEFAULT 'variety',
    channel       VARCHAR     DEFAULT '',
    air_days      JSONB       DEFAULT '[]',
    air_time_kst  VARCHAR     DEFAULT '',
    current_episode INT       DEFAULT 1,
    always_on     BOOLEAN     DEFAULT FALSE,
    ended         BOOLEAN     DEFAULT FALSE,
    season        VARCHAR,
    source        VARCHAR     DEFAULT 'manual',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- 프로그램명 중복 방지
CREATE UNIQUE INDEX IF NOT EXISTS shows_name_unique ON shows (name);

-- ── 2. show_candidates 테이블 ────────────────────────────────────
CREATE TABLE IF NOT EXISTS show_candidates (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR     NOT NULL,
    channel         VARCHAR     DEFAULT '',
    category        VARCHAR     DEFAULT 'variety',
    air_days        JSONB       DEFAULT '[]',
    air_time_kst    VARCHAR     DEFAULT '',
    current_episode INT         DEFAULT 1,
    source          VARCHAR     DEFAULT '',
    clip_count_7d   INT         DEFAULT 0,
    season          VARCHAR,
    status          VARCHAR     DEFAULT 'pending',  -- pending | approved | rejected
    discovered_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ── 3. episodes 유니크 제약 (upsert 지원) ───────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS episodes_program_episode_unique
    ON episodes (program_name, episode_number);

-- ── 4. 기존 음악 방송 시드 데이터 ───────────────────────────────
INSERT INTO shows (name, category, channel, air_days, air_time_kst, current_episode, always_on, ended, source)
VALUES
    ('뮤직뱅크',     'music', 'KBS2', '["Fri"]', '17:00', 1, TRUE, FALSE, 'manual'),
    ('쇼! 음악중심', 'music', 'MBC',  '["Sat"]', '15:00', 1, TRUE, FALSE, 'manual'),
    ('인기가요',     'music', 'SBS',  '["Sun"]', '15:30', 1, TRUE, FALSE, 'manual'),
    ('M 카운트다운', 'music', 'Mnet', '["Thu"]', '18:00', 1, TRUE, FALSE, 'manual')
ON CONFLICT (name) DO NOTHING;
