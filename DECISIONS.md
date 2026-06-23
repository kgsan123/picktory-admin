# DECISIONS.md

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

## 2026-06-23 OTT 랭킹 — Netflix CSV 방식
**Decision:** Netflix TOP10 주간 CSV를 직접 요청 시도, 실패 시 None 반환 후 파이프라인 계속.
**Reason:** CLAUDE.md 명세대로 공식 CSV 사용. 블록 시 파이프라인을 중단하지 않도록 soft fail.
**Alternatives considered:** Playwright 스크래핑 (Cloudflare 차단 가능성으로 제외).
