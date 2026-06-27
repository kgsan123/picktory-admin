# PICKTORY Auto Engine
# Master Context & Methodology — All Phases

> This file is the single source of truth for Claude Code.
> Read this fully at the start of every session before touching any code.
> When in doubt, re-read the relevant section here rather than asking.

---

## 1. What We're Building

An automated pipeline that runs after each Korean broadcast episode airs:

```
Episode detected → Multi-source data collected → Past predictions verified (AI)
→ Next-episode predictions generated (AI) → Stored in Supabase → Served to users
```

The user-facing product is a K-content prediction game. Quality = prediction accuracy
+ fun factor. Both must be optimized, not just one.

---

## 2. Tech Stack (Fixed — Do Not Change Without Flagging)

| Layer        | Choice                                      |
|--------------|---------------------------------------------|
| Language     | Python 3.11                                 |
| DB           | Supabase (PostgreSQL) — credentials in .env |
| Scheduler    | APScheduler                                 |
| AI (main)    | claude-sonnet-4-6                           |
| AI (cheap)   | claude-haiku-4-5-20251001                   |
| Timezone     | ALL datetimes in KST (Asia/Seoul)           |
| DB pattern   | upsert-on-conflict ONLY — never plain INSERT|

Model IDs are pinned. Do not use aliases or newer models without explicit instruction.

---

## 3. Project Structure

```
picktory/
├── CLAUDE.md                    ← this file
├── DECISIONS.md                 ← log every non-trivial decision made (auto-maintained)
├── .env                         ← SUPABASE_URL, SUPABASE_KEY, ANTHROPIC_API_KEY
│
├── validators.py                ← 4-check validation framework (shared)
├── orchestrator.py              ← APScheduler + retry + Discord webhook
│
├── data_collector/
│   ├── __init__.py
│   ├── ratings.py               ← Nielsen ratings via Naver News
│   ├── reactions.py             ← Namu wiki + community hot posts
│   ├── news.py                  ← Naver News API 24h window
│   └── ott_rank.py              ← Netflix TOP10 CSV + Tving/Coupang
│
├── ai_engine/
│   ├── __init__.py
│   ├── answer_verifier.py       ← batch AI verdict on past predictions
│   ├── prediction_generator.py  ← batch AI generates next-episode predictions
│   └── prompts/
│       ├── verifier_v1.txt
│       ├── generator_survival_v1.txt
│       ├── generator_romance_v1.txt
│       ├── generator_drama_v1.txt
│       ├── generator_music_v1.txt
│       └── generator_variety_v1.txt
│
└── tests/
    ├── test_validators.py
    ├── test_collectors.py       ← Phase 1 checkpoint
    ├── test_verifier.py         ← Phase 2 checkpoint
    └── test_generator.py        ← Phase 3 checkpoint
```

Hard rules on files:
- Max 200 lines per file. Split into submodules if larger.
- Every module runnable standalone: `python -m module.name --args`
- No credentials in code — always `os.environ.get('KEY')`

---

## 4. Database Schema (Target State)

Check current schema first. Run migrations only for missing columns/tables.
Never DROP columns. Never change column types on existing data.

```sql
-- Extend episodes (add only if missing)
ALTER TABLE episodes ADD COLUMN IF NOT EXISTS ratings_percent FLOAT;
ALTER TABLE episodes ADD COLUMN IF NOT EXISTS reaction_score FLOAT;
ALTER TABLE episodes ADD COLUMN IF NOT EXISTS news_summary TEXT;
ALTER TABLE episodes ADD COLUMN IF NOT EXISTS pipeline_status VARCHAR DEFAULT 'detected';
-- pipeline_status flow: detected → collected → verified → generated

-- New table: predictions
CREATE TABLE IF NOT EXISTS predictions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    episode_id UUID REFERENCES episodes(id),  -- 예측을 생성한 회차 N
    program_name VARCHAR,        -- 비정규화 (검증 조회 단순화)
    target_episode_number INT,   -- 예측 대상 회차 N+1 (검증 시 이 키로 조회)
    category VARCHAR,   -- survival | romance | drama | music | variety
    title TEXT,
    content TEXT,       -- the prediction question shown to users
    options JSONB,      -- [{id, text}] (확률 미사용)
    difficulty INT,     -- 1 (easy) to 5 (hard)
    fun_score INT,      -- 1-5, AI self-rating on engagement potential
    verification_method TEXT,  -- how to confirm answer after airing
    verdict VARCHAR DEFAULT 'pending',  -- pending | resolved (정답 선택지 확정 여부)
    correct_option_id VARCHAR,  -- 실제 일어난 선택지 id (AI/운영자 판정)
    confidence FLOAT,   -- 0.0-1.0, AI confidence in verdict
    evidence_text TEXT, -- AI reasoning for verdict
    prompt_version VARCHAR,
    status VARCHAR DEFAULT 'draft',  -- draft | review | published | expired
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- New table: pipeline_logs
CREATE TABLE IF NOT EXISTS pipeline_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    episode_id UUID REFERENCES episodes(id),
    step VARCHAR,       -- detect | collect | verify | generate
    status VARCHAR,     -- running | success | failed
    duration_sec FLOAT,
    error_msg TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 5. Validation Framework (validators.py)

Every collector output must pass all applicable checks before DB write.
Build this first — all other modules depend on it.

```python
# Interface contract — implement exactly this
def validate_schema(data: dict, required_fields: list) -> bool
def validate_korean(data: dict, text_fields: list) -> bool  # regex [가-힣]
def validate_freshness(data_ts: datetime, aired_at: datetime, max_hours: int = 24) -> bool
def validate_cross_source(text_a: str, text_b: str, keyword: str) -> float  # 0.0-1.0
def run_all(data: dict, config: dict) -> ValidationResult  # dataclass with pass/fail + details
```

Failing validation → log to pipeline_logs, skip DB write, do NOT raise exception
(partial collection is acceptable; total block is not).

---

## 6. Data Sources — What Works, What Doesn't

### Use these
| Source             | Method                  | Notes                                    |
|--------------------|-------------------------|------------------------------------------|
| Naver News API     | Official API            | Ratings extraction, general news         |
| Namu Wiki          | BeautifulSoup           | Reaction spike detection via edit volume |
| DC인사이드 / 에펨  | BeautifulSoup           | Add User-Agent header, rate limit: 1 req/2s |
| Naver TV           | BeautifulSoup (fallback)| Already implemented for JTBC/tvN        |
| Netflix TOP10 CSV  | Direct download         | https://www.netflix.com/tudum/top10      |
| KBS / MBC / SBS    | Playwright              | JS-rendered — requests returns empty HTML|

### Do NOT use
| Source             | Reason                           | Alternative                          |
|--------------------|----------------------------------|--------------------------------------|
| X / Twitter API    | Expensive, limited free tier     | Naver News search volume             |
| Netflix scraping   | Cloudflare-protected             | Official weekly TOP10 CSV            |
| Direct OTT APIs    | Auth-walled                      | Public ranking pages with Playwright |

### Ratings data strategy
1. Search Naver News: `{program_name} 시청률 닐슨`
2. Extract float with regex: `(\d+\.?\d*)\s*%` near `시청률|닐슨|가구`
3. If multiple hits: use median, discard outliers > 2x median
4. If no hit within 24h: mark `ratings_percent = NULL`, do not block pipeline

---

## 7. AI Engine Design

### Answer Verifier (ai_engine/answer_verifier.py)

Purpose: Given past predictions + episode data, determine correct/incorrect/pending.

```
Input per call:
  - system: prompts/verifier_v1.txt (cacheable — same every call)
  - user: program name, episode, ratings, news summary, reaction score, predictions JSON

Output (JSON only):
  {
    "results": [
      {
        "prediction_id": "uuid",
        "verdict": "correct|incorrect|pending",
        "confidence": 0.85,
        "evidence": "one-sentence reason"
      }
    ]
  }
```

Rules:
- Batch all predictions for one episode into a single API call
- Use Batch API (`/v1/messages/batches`) — no real-time requirement
- Verdict threshold: confidence >= 0.70 for correct/incorrect; below → pending
- Target pending rate: <= 15% of predictions
- If JSON parse fails: retry once, then mark all as pending + log error

### Prediction Generator (ai_engine/prediction_generator.py)

Purpose: Generate 5-10 predictions for the next episode.

```
Input per call:
  - system: prompts/generator_{category}_v1.txt (cacheable)
  - user: program meta + last 3 episodes summary + current episode reactions/news

Output (JSON only):
  {
    "predictions": [
      {
        "title": "string",
        "content": "question shown to users",
        "options": [{"id": "A", "text": "..."}, ...],
        "difficulty": 3,
        "fun_score": 4,
        "category": "survival",
        "verification_method": "how to confirm answer after airing"
      }
    ]
  }
```

Quality filters (auto-reject before saving):
- fun_score < 3 → discard
- difficulty == 1 AND confidence_of_correct_option > 0.85 → discard (too obvious)
- No clear verification_method → discard
- After filters, minimum 3 predictions must remain; if not, regenerate once with temp +0.2

Prompt versioning: When changing a prompt, increment version (v1 → v2).
Track which version generated each prediction via `prompt_version` column.

### Prompt File Format
```
# VERSION: v1
# CATEGORY: verifier | survival | romance | drama | music | variety
# TOKENS_ESTIMATE: ~450

[SYSTEM]
... system prompt here ...

[USER]
... user prompt template with {placeholders} ...
```

### API Cost Strategy
| Task                  | Model     | API mode  | Est. cost/episode |
|-----------------------|-----------|-----------|-------------------|
| Data classification   | haiku-4-5 | sync      | ~$0.001           |
| Answer verification   | sonnet-4-6| batch     | ~$0.015           |
| Prediction generation | sonnet-4-6| batch     | ~$0.025           |

Always use prompt caching for system prompts (add `"cache_control": {"type": "ephemeral"}`
to system message). Cached reads cost 10% of normal input price.

---

## 8. Methodology: Spec-First Modular Build

This is how every development session should proceed:

1. **Read before writing** — Check what already exists in the codebase before creating anything
2. **Build smallest unit first** — One function, one collector, one prompt at a time
3. **Test with real data immediately** — No mocking for this project. Run against real Korean shows.
4. **Validate before integrating** — Module must pass its checkpoint test before connecting to others
5. **Document decisions** — Any non-obvious choice goes in DECISIONS.md

### Phase Gate System
Each phase has a checkpoint test. Do not advance until it passes.

```
Phase 1 → test_collectors.py  → validates all 4 collectors with real aired episodes
Phase 2 → test_verifier.py    → backtest accuracy >= 80% on manually-labeled set
Phase 3 → test_generator.py   → avg fun_score >= 3.5, all predictions pass filters
Phase 4 → 48h unattended run  → pipeline_logs show 0 unhandled exceptions
```

### Handling Blocked Crawlers
If a site blocks the crawler during development:
1. Try rotating User-Agent first
2. Try 5s delay between requests
3. If still blocked, find an alternative data source and document in DECISIONS.md
4. Never use a proxy service without flagging it

---

## 9. Orchestrator Design (Phase 4)

```python
# Schedule template — adjust times based on actual broadcast schedules
schedules = [
    # (trigger_offset_minutes_after_air, function)
    (30,  detect_episode),      # confirm episode aired, create DB record
    (120, collect_data),        # multi-source data collection
    (180, verify_answers),      # AI verdict on previous episode predictions
    (240, generate_predictions) # AI generates predictions for next episode
]
```

Retry policy: 3 attempts with exponential backoff (30s, 120s, 480s).
After 3 failures: log to pipeline_logs, send Discord webhook, move on.
Pipeline is non-blocking — one step failure does not cancel subsequent steps.

Discord webhook payload:
```json
{"content": "⚠️ [{step}] {program_name} EP{num} failed: {error_msg}"}
```

---

## 10. Autonomy Guidelines

### Decide without asking
- Which exact CSS selector or regex pattern to use for a crawler
- How to split a file that exceeds 200 lines
- Whether to use asyncio for parallel collection
- Prompt wording adjustments that don't change the output schema
- Which alternative data source to use when the primary is blocked
- Error handling implementation details
- Adding indexes to the DB for query performance

### Document in DECISIONS.md, then proceed
- Switching from one library to another (e.g., requests → httpx)
- Adding a new data source not listed here
- Changing the reaction_score formula
- Adjusting quality filter thresholds
- Changing retry counts or backoff times

### Stop and flag (write clearly in output, then wait)
- Changing the DB schema in a breaking way (dropping columns, changing types)
- Changing the output JSON schema of AI calls (breaks downstream)
- Skipping a phase checkpoint because "it's close enough"
- Using a different AI model than specified
- Spending more than $5 on API calls in a single session

### When a prompt produces poor results
1. Try adjusting temperature and/or adding examples (max 2 attempts)
2. If still poor: increment prompt version, document what failed and why in DECISIONS.md
3. After 3 versions still failing: write a summary of the failure pattern and stop

---

## 11. DECISIONS.md Format

Auto-maintain this file. Append an entry whenever a non-trivial decision is made.

```markdown
## [DATE] [TOPIC]
**Decision:** what was decided
**Reason:** why (1-2 sentences)
**Impact:** what this changes
**Alternatives considered:** (optional)
```

---

## 12. Phase Summaries

### Phase 1 — Data Foundation
**Goal:** Reliable, validated data for any recently-aired Korean episode
**Deliverables:** validators.py, data_collector/ (4 modules), DB migration
**Checkpoint:** test_collectors.py passes for 3+ real episodes across different show types
**Done when:** Can call `collect_all(episode_id)` and get validated data back for any major show

### Phase 2 — Answer Verification
**Goal:** AI accurately judges past predictions using collected data
**Deliverables:** ai_engine/answer_verifier.py, prompts/verifier_v1.txt, test_verifier.py
**Checkpoint:** Backtest on 20+ manually-labeled predictions → accuracy >= 80%, pending <= 15%
**How to backtest:** Use existing predictions (if any) or create 20 test cases from past episodes
where the correct answer is already known. Compare AI verdict to ground truth.
**Done when:** Can call `verify_episode(episode_id)` and get accurate verdicts back

### Phase 3 — Prediction Generation
**Goal:** AI generates engaging, verifiable predictions for next episodes
**Deliverables:** ai_engine/prediction_generator.py, 5 prompt templates, test_generator.py
**Checkpoint:** Generate predictions for 5 real upcoming episodes → avg fun_score >= 3.5,
all pass quality filters, at least 5 predictions survive per episode
**Done when:** Can call `generate_predictions(episode_id)` and get publish-ready predictions

### Phase 4 — Full Pipeline
**Goal:** End-to-end automation, runs without manual intervention
**Deliverables:** orchestrator.py, Discord alerts, 48h stability test
**Checkpoint:** Run pipeline on real schedule for 48 hours → zero unhandled exceptions,
pipeline_logs show all steps completing, Discord alerts firing correctly
**Done when:** Pipeline runs overnight without touching it and predictions appear in DB

---

## 13. How to Start Each Session

```
1. Read this file
2. Check DECISIONS.md for context on past choices
3. Check pipeline_logs (if DB accessible) for recent failures
4. Run the relevant checkpoint test to see current state
5. Continue from where the last session left off
```

To check current phase status:
```bash
python -m tests.test_collectors   # Phase 1 done?
python -m tests.test_verifier     # Phase 2 done?
python -m tests.test_generator    # Phase 3 done?
```
