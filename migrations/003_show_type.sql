-- Migration 003: show_type 컬럼 추가 + 연애 프로그램 시드 데이터
-- Supabase SQL Editor에서 실행

-- 1. shows 테이블에 show_type 추가
ALTER TABLE shows ADD COLUMN IF NOT EXISTS show_type VARCHAR DEFAULT 'regular';
-- 'regular': 주기적 방영 | 'event': 일회성 시상식/행사

COMMENT ON COLUMN shows.show_type IS 'regular: 주기적 방영, event: 일회성 시상식/행사';

-- 2. 연애 프로그램 시드 데이터
INSERT INTO shows (name, channel, category, show_type, ended, source) VALUES
('나는 SOLO', 'ENA', 'romance', 'regular', false, 'manual'),
('하트시그널', 'Channel A', 'romance', 'regular', false, 'manual'),
('연애실험실', '', 'romance', 'regular', false, 'manual'),
('래퍼 여친 구함', '', 'romance', 'regular', false, 'manual'),
('연애전쟁', '', 'romance', 'regular', false, 'manual'),
('합숙맞선', '', 'romance', 'regular', false, 'manual'),
('모솔연애', '', 'romance', 'regular', false, 'manual'),
('나는 SOLO, 그 후 사랑은 계속된다', 'ENA', 'romance', 'regular', false, 'manual'),
('웨이브 스탠바이미', 'Wavve', 'romance', 'regular', false, 'manual')
ON CONFLICT (name) DO NOTHING;
