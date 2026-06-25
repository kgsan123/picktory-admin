# DECISIONS.md

## 2026-06-25 검증 자동 해소 (pending 재검증 sweep)
**Decision:** orchestrator에 매일 11:00 KST `resolve_pending_sweep()` cron 추가. pending+published 예측을 (program, target_episode_number)로 그룹화해, 방영된 회차 레코드가 있고 aired_at이 SETTLE_HOURS(6h) 지난 회차에 대해 기존 `verify_episode`를 재호출. EXPIRE_DAYS(7일) 초과 미판정은 status='expired' + Discord 알림. 한 sweep당 MAX_PER_SWEEP(15)회차 상한(Groq 한도 보호). 어드민 [예측] 탭에 "지금 재검증" 수동 버튼 추가.
**Reason:** 기존엔 verify가 방영 +1h에 한 번만 실행 → 결과 기사 미축적으로 대부분 pending인데 재시도가 없어 영영 미판정. 게임에 정답이 안 생김.
**Impact:** 새 검증 로직 없이 verify_episode 재사용. 데이터 부족 회차는 Groq 호출 없이 다음 날로 보류. 게이팅(만료/검증/SETTLE skip) 3시나리오 테스트 통과.
**Alternatives considered:** run_pipeline의 즉시 verify를 +12~24h 지연 — sweep이 더 견고(여러 날 재시도)해서 채택, 즉시 verify는 보조로 유지.

## 2026-06-25 트랙 C(일부): 컨텍스트 게이트 + 수집 정밀화
**Decision:** (rank8) 운영자 입력도 없고 자동 수집 신호도 부족(news+community<2 AND 줄거리 없음)하면 생성을 건너뛰고 pipeline_status='context_insufficient' + 어드민/Discord 알림. (rank12) fetch_ratings에 episode_num 추가해 해당 회차 언급 기사 시청률 우선, fetch_reactions에 category 추가해 DC 갤러리 라우팅(music→kpop, 그 외→drama).
**Reason:** 컨텍스트 빈약 시 환각 예측만 나오고 Groq 토큰만 소모. 회차 무관 시청률/항상 drama 갤러리는 부정확.
**Impact:** 빈약 회차는 운영자가 컨텍스트 직접 입력하면 통과. reaction_score 공식(blog+dc, 0-10)은 불변, dc_score의 출처 갤러리만 카테고리별로 달라짐. rank9(grounding 필터)·rank10(roster)은 라이브 테스트 후로 보류(과탈락 위험).

## 2026-06-25 예측 품질 종합 개선 (멀티에이전트 분석 기반)
**Decision:** 데이터 수집/생성/검증 3단계 개선. (1) 나무위키 회차 줄거리를 컨텍스트 최상단 "사실 앵커"로 통합해 환각 차단. (2) 검증 루프 재구성 — 예측에 target_episode_number/program_name 컬럼 추가, 검증은 "방금 방영된 회차 N을 대상으로 한(target=N) 예측"을 N의 fresh 데이터로 판정. (3) verifier_v2 — 선택지(options) 전달 + correct_option_id(실제 일어난 선택지) 반환, max_tokens 4096, _reconcile로 누락/환각 id 처리.
**Reason:** 기존 검증기가 결과-전 news_summary로 판정하고 선택지를 안 넘겨 대부분 pending. 예측은 N회 episode_id에 저장되나 내용은 N+1 관련이라 검증 lookup이 어긋남.
**Impact:** episode_id=생성 회차 N, target_episode_number=대상 회차 N+1로 역할 분리. verdict=유력 후보(최고 배당)가 맞았는지, correct_option_id=실제 결과. 마이그레이션 006/007 수동 적용 필요.
**Alternatives considered:** verdict를 resolved/pending로 재정의 — 기존 admin UI 호환 위해 보류.

## 2026-06-23 Python 버전
**Decision:** Python 3.12 사용 (CLAUDE.md 명세는 3.11)
**Reason:** 개발 환경에 3.12가 설치되어 있으며, 3.12는 3.11의 완전한 상위 호환이므로 기능 차이 없음.
**Impact:** 없음 — zoneinfo, dataclasses, typing 모두 3.11과 동일하게 동작.

## 2026-06-23 shared db.py 추가
**Decision:** CLAUDE.md 구조에 없는 db.py를 루트에 추가
**Reason:** 여러 모듈(data_collector, ai_engine)이 Supabase 클라이언트를 공유해야 하므로 싱글톤 패턴으로 분리.
**Impact:** import db 후 db.get_client() 호출로 통일. 크레덴셜 로직 단일화.

## 2026-06-23 reaction_score 공식
**Decision:** reaction_score = blog_score(0-5) + dc_score(0-5), 범위 0.0–10.0
**Reason:** Naver Blog API(공식) + DC인사이드 드라마 갤러리(BeautifulSoup)를 각각 0-5로 정규화해 합산.
**Alternatives considered:** 트위터/X (비용 문제로 제외), 나무위키 수정 횟수 (JS 렌더링 이슈로 Phase 1에서 제외).

## 2026-06-23 AI 모델 — Groq (Llama 3.3 70B) 로 대체
**Decision:** Anthropic API 결제 불가, Google Gemini 무료 크레딧 소진으로 Groq 사용
**Reason:** Groq는 카드 없이 무료로 일 14,400회 요청 가능. llama-3.3-70b-versatile(메인), llama-3.1-8b-instant(경량) 사용.
**Impact:** CLAUDE.md의 claude-sonnet-4-6 / claude-haiku-4-5 대신 Groq 모델 사용. 코드 구조(JSON 출력, 프롬프트 형식)는 동일하게 유지. Anthropic 결제 해결 시 모델명만 바꾸면 됨.
**Alternatives considered:** Google Gemini(크레딧 소진), OpenAI(미시도), Ollama(로컬, 하드웨어 부담).

## 2026-06-24 shows/show_candidates → Supabase 이전
**Decision:** shows.json, discovered_shows.json 폐기. Supabase `shows`, `show_candidates` 테이블로 이전.
**Reason:** Streamlit Cloud는 파일 시스템이 재배포 시 초기화됨. 관리자 UI에서 승인/제외 등 상태 변경이 유지되려면 DB 저장 필수.
**Impact:** admin_app.py → admin/ 모듈로 분리. episode_detector.py `_increment_episode` → Supabase 업데이트. orchestrator.py `load_shows()` → Supabase 조회. Migration: migrations/002_shows_candidates.sql 실행 필요.

## 2026-06-24 예측 생성 파이프라인 단순화
**Decision:** `generate_episode_predictions()` 에서 4개 데이터 수집 호출(news, reactions, summary, yt) 제거. 에피소드 DB 레코드에 있는 데이터만 사용하고 없으면 빈 값으로 진행.
**Reason:** 데이터 수집 실패 시 예측 생성 자체가 블로킹됨. AI 프롬프트는 "정보 없음"으로도 동작하므로 수집 실패가 생성을 막으면 안 됨.
**Impact:** 예측 품질이 다소 낮아질 수 있으나 실제 생성이 되는 것이 우선.

## 2026-06-24 관리자 UI 재설계
**Decision:** 5탭 → 3탭. 통계탭 제거, 탭 구성: 프로그램 / 예측 / 신규 발견.
**Reason:** 핵심 워크플로우(프로그램 추가 → 예측 생성 → 검토/게시 → 정답 입력)에 집중. 통계는 부가 기능.
**Impact:** admin_app.py는 50줄 이하로 감소. 각 탭은 admin/ 서브모듈로 분리(200줄 제한 준수).

## 2026-06-23 OTT 랭킹 — Netflix CSV 방식
**Decision:** Netflix TOP10 주간 CSV를 직접 요청 시도, 실패 시 None 반환 후 파이프라인 계속.
**Reason:** CLAUDE.md 명세대로 공식 CSV 사용. 블록 시 파이프라인을 중단하지 않도록 soft fail.
**Alternatives considered:** Playwright 스크래핑 (Cloudflare 차단 가능성으로 제외).
