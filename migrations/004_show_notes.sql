-- shows 테이블에 프로그램 형식 설명 컬럼 추가
ALTER TABLE shows ADD COLUMN IF NOT EXISTS notes TEXT;

-- 알려진 프로그램 형식 설명 사전 입력
UPDATE shows SET notes = '같은 기수의 남녀 솔로들이 한 장소에서 만나는 형식. 같은 기수끼리만 선택 가능. 예: 25기 남솔로와 25기 여솔로끼리만 만남. 하트시그널이나 다른 연애 프로그램의 인물 이름은 절대 사용 불가.' WHERE name = '나는 SOLO';

UPDATE shows SET notes = '다른 기수의 솔로들이 함께 만나는 형식. 기수 간 선택 가능. 예: 18기, 20기, 25기가 함께 만남. 회차 컨텍스트에 있는 기수+이름만 사용.' WHERE name = '나는 SOLO, 그 후 사랑은 계속된다';

UPDATE shows SET notes = '남녀 참가자들이 하우스에 함께 살며 시그널을 주고받는 형식. 별도 패널 진행자(예측 분석 패널)와 하우스 참가자를 구분해야 함. 예측은 하우스 참가자의 행동을 대상으로.' WHERE name = '하트시그널';

UPDATE shows SET notes = '연애 리얼리티. 컨텍스트에 이름이 있으면 그 이름만 사용.' WHERE name = '연애실험실';
UPDATE shows SET notes = '연애 리얼리티. 컨텍스트에 이름이 있으면 그 이름만 사용.' WHERE name = '래퍼 여친 구함';
UPDATE shows SET notes = '연애 리얼리티. 컨텍스트에 이름이 있으면 그 이름만 사용.' WHERE name = '연애전쟁';
UPDATE shows SET notes = '연애 리얼리티. 컨텍스트에 이름이 있으면 그 이름만 사용.' WHERE name = '합숙맞선';
UPDATE shows SET notes = '연애 리얼리티. 컨텍스트에 이름이 있으면 그 이름만 사용.' WHERE name = '웨이브 스탠바이미';
