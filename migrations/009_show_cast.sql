-- 프로그램 출연자 명단 (운영자 입력). 쉼표/줄바꿈 구분.
-- 뉴스에서 실명 추출이 안 되는 프로그램(넷플릭스 연애·서바이벌 등)의 예측 선택지 확보용.
ALTER TABLE shows ADD COLUMN IF NOT EXISTS cast_names TEXT;
