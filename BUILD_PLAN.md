# SerpSense — Build Plan (v3.2)

> **Your brand's reputation is whatever Google shows people.**
> SerpSense watches the search surfaces a customer, investor or candidate sees when they Google a brand (results page, autocomplete, AI Overview, news, Trends, app and map reviews), detects emerging crises, and drafts a response backed by evidence.

- **Hackathon:** SerpApi India Hackathon 2026, **deadline Sat, Oct 10, 23:59 IST** (target: submit by 18:00)
- **Track:** Commerce & Market Intelligence
- **Inspiration:** EBIC.AI (enterprise PR intelligence). SerpSense is a lighter version for Indian startups and D2C brands, built on public search data.
- **Judging criteria:** idea strength · originality · technical complexity · usefulness · meaningful SerpApi usage
- **Tagline:** *SerpSense — reputation intelligence from what Google shows.* Not affiliated with SerpApi, LLC.

### Governing documents (binding)
| Document | Contents |
|---|---|
| `AGENTS.md` | Project rules: scope, module layout, coding/data/security/LLM/observability/testing rules, local gate, workflow |
| `docs/adr/` | Architecture decisions 0001–0013. **All are `Proposed` until you accept them** |
| `docs/architecture/data-model.md` | Full Postgres schema (source of truth) |

### What changed in v3.2 (architecture review, round 2)
- Each scan runs as **one claimed task** (collect → normalise → enrich → score → alert → finish), so alerts see fresh labels and there is no separate re-score job. The LLM backlog is handled by the next scan.
- Added `running → skipped` (budget checked after the claim); every finishing transition is a compare-and-set; scan tasks never use Celery `retry`.
- Account deletion also removes pre-login IP records and stops in-flight scans from sending alerts. Redaction now also strips reviewer identity. The pseudonymous email domain is `serpsense.invalid`.
- The outbox status now follows a written derivation rule (new `dropped` outcome).
- Owner decisions recorded in §27 (competitor alerts: email + in-app; Trends: joint query).

### What changed in v3.1 (after the architecture review)
- **Postgres is the source of truth for jobs; Celery messages are only nudges.** Scans are claimed with compare-and-set, and a maintenance sweep recovers lost enqueues and stuck scans (ADR-0005).
- **One active scan per brand** (database-enforced). "Scan now" during an active scan is rejected and shows its progress.
- **Competitors are independent brands** with their own schedules, settings and dashboards (your decision).
- **Account deletion is in scope** (ADR-0013). Personal data is kept out of append-only tables so it can be scrubbed.
- **Sync services everywhere** (FastAPI `def` routes, `smtplib`); new ports `JobQueue`, `RateLimiter`, `ResponseCache`, `SecretBox`, `UnitOfWork`.
- **Derived values removed:** health, crisis score and level come from a view using versioned weights; billable comes from `served_from`; the circuit breaker comes from recent `serp_calls`.
- **Alerts are idempotent** per (scan, rule, narrative). The outbox holds its row lock through sending (no stuck `sending` state).
- **Trimmed for proportion:** per-brand LLM overrides, alert-delivery table, worker metrics and LLM batch mode are dropped or deferred; 4 runbooks now, 6 later.
- ADR-0008: no extra retention requirement (your decision); the ledger records both the requested and the served model.

### What changed from v2
- **Data model rebuilt to follow the rules:** no arrays/JSON for queried data (aliases, languages, apps, locations, watch terms are tables); changing facts become rows (budgets, settings versions, status transitions, observations); derived values aren't stored; money as integer micros; append-only ledgers with database triggers.
- **Scan state machine** with a recorded transition table; **slot-based scheduling** (a unique constraint makes double scheduling impossible).
- **Transactional outbox** for all emails; the OTP inside an outbox row is encrypted and wiped after sending.
- **Webhooks dropped** (user-supplied URLs are an SSRF risk, ADR-0011). Alerts: email + in-app.
- **LLM work follows the prompt-eval rules:** SDK behind a port with a fake, prompts as versioned files, golden sets, eval runner and reports, provenance labels, author names stripped, prompts never logged.
- **Layered architecture** enforced by import-linter; runbooks for each failure mode; no APM vendor.
- **Workflow:** issue → branch → atomic commits → PR (≤ 400 lines) → reviewers → rebase-merge; some areas need **your** merge.

---

## 1. Product flow

```
 ① Sign up / log in (email OTP) ─► ② Set up brand + settings ─► ③ Collect (SerpApi) ─► ④ Normalise ─► ⑤ Enrich (LLM)
                                                                      ▲                                    │
                                                                      │                                    ▼
                                    slot dispatcher (every 5 min) ────┘   ⑧ Act ◄─ ⑦ Alert ◄─ ⑥ Score & compare to last scan
```

Example used throughout: *VoltBox* (fictional Indian D2C earbuds brand), competitor *SoundNest*.

---

## 2. Scope

**In scope:** see `AGENTS.md` §1. Summary: email OTP accounts, account deletion, per-user brands (competitors are independent brands), SerpApi collectors, LLM labelling/grouping/explanations/drafts, health and crisis scores, email + in-app alerts, configurable SerpApi and LLM settings, replay mode, Docker Compose.

**Out of scope:** teams/RBAC, SSO, billing, webhooks or any user-supplied URL, Telegram/Slack/SMS, Kubernetes, social-media scraping, APM vendors.

### "Production grade" in practice
| Area | What we build | Reference |
|---|---|---|
| Data | Postgres 16, SQLAlchemy 2, Alembic (reversible, named constraints), append-only triggers, 3NF | ADR-0003, data model |
| Jobs | Celery + Beat, idempotent handlers, retryable vs permanent errors, slot scheduling, outbox dispatcher with `SKIP LOCKED` | ADR-0005, 0010 |
| Reliability | Timeouts everywhere, backoff + jitter, circuit breaker per engine, partial scans, graceful LLM degradation | ADR-0007, 0008 |
| Security | OTP + server-side sessions, CSRF, rate limits, scoped queries (404), CSP, redaction, gitleaks, pip-audit | ADR-0009, AGENTS §6 |
| Cost control | Per-user budgets (rows), append-only usage ledgers, estimator before every scan, SerpApi quota check | data model §1, §5, §6 |
| Observability | structlog JSON, Prometheus metrics, `/healthz` `/readyz`, alert rules, dashboards, runbooks | ADR-0012 |
| Delivery | Docker Compose (migrate, web, worker, beat, postgres, redis, mailpit), non-root image, CI gate | ADR-0006 |
| Reproducibility | Resolved settings copied into every scan and LLM call; `prompt_version` + `scoring_version` | data model |

---

## 3. Third-party services

| Service | Purpose | Required? |
|---|---|---|
| **SerpApi** | All search data + Account API (quota) + Locations API (location picker) | ✅ |
| **Anthropic (Claude API)** | Labelling, narrative grouping, crisis explanations, drafts | ✅ |
| **SMTP provider** (Brevo / Resend / Amazon SES) | OTP and alert email | Production only |
| **Mailpit** (self-hosted container) | Catches all mail locally; web inbox on `:8025` | ✅ local/demo |
| **GitHub** | Public repo, issues, PRs, Actions CI | ✅ |
| **YouTube (unlisted) / Google Drive** | Demo video | ✅ |

Self-hosted in Compose: Postgres, Redis, Mailpit. HTMX and Chart.js are bundled in `static/`.

---

## 4. Architecture

```
                    ┌──────────────────────────── Docker Compose ─────────────────────────────┐
 Browser ──HTTPS──► │ web: FastAPI (entrypoints/web) → services → domain                      │
                    │      auth · brands · settings · dashboard · drafts · /healthz /readyz    │
                    │                 │ writes outbox rows in the same transaction             │
                    │                 ▼                                                         │
                    │ postgres ◄──── system of record (ADR-0003)                               │
                    │   ▲  ▲                                                                    │
                    │   │  └── worker: Celery (entrypoints/jobs) → services → ports → adapters  │
                    │   │        queues: scans · outbox · maintenance (migrate runs first)     │
                    │   │        adapters: serp (SerpApi SDK) · llm (Claude SDK) · mail (SMTP)  │
                    │   └───── beat: dispatch_due_scans (5 min) · sweep_stuck_work (5 min)      │
                    │                dispatch_outbox (15 s) · scrub_personal_data (daily)       │
                    │ redis ◄── broker (nudges only) · per-IP limits · SerpApi + quota cache    │
                    │ mailpit (dev) / SMTP provider (prod)                                      │
                    └──────────────────────────────────────────────────────────────────────────┘
                              outbound hosts (fixed): serpapi.com · api.anthropic.com · SMTP host
```

**Layers** (`AGENTS.md` §2): `entrypoints → services → domain`; services depend on `ports`; `adapters` implement ports; `composition.py` wires them together. Enforced by `lint-imports`.

### Stack
| Layer | Choice | ADR |
|---|---|---|
| Language & tooling | Python 3.11, uv, ruff, mypy (strict), import-linter | 0002 |
| Web | FastAPI, uvicorn, Jinja2, HTMX, Chart.js, `python-multipart` | 0002 |
| DB | PostgreSQL 16, SQLAlchemy 2.0, Alembic, `psycopg` 3 | 0003 |
| Cache/broker | Redis 7 | 0004 |
| Jobs | Celery 5 + Beat | 0005 |
| Settings | Pydantic v2, `pydantic-settings` | 0002 |
| External | `serpapi`, `anthropic`, `tenacity`, `smtplib` (stdlib) — all synchronous | 0002, 0007, 0008, 0010 |
| Crypto | `cryptography` (MultiFernet, outbox sensitive data only, behind `SecretBox`) | 0010 |
| Observability | `structlog`, `prometheus-client` | 0012 |
| Tests | pytest, testcontainers, pytest-socket, time-machine, diff-cover, polyfactory | AGENTS §9 |
| Security tooling | gitleaks, pip-audit, ruff `S` rules | AGENTS §6 |

---

## 5. Settings (layered and versioned)

```
Search:  system defaults (code) → user defaults → brand settings → per-run override
LLM:     system defaults (code) → user profile → per-request override (e.g. [High thinking] button)
```
- Settings documents are validated by Pydantic in `domain/settings/` and stored as **append-only versions** (latest wins). History comes for free.
- The resolved result is copied into `scans.settings_snapshot` and `llm_calls.request_settings`.
- Admin limits from env always win (`MAX_SEARCHES_PER_SCAN`, `ALLOWED_MODELS`, global daily cap).

---

## 6. SerpApi settings

### 6.1 Global (per brand, with user defaults)
| Setting | Options | Default |
|---|---|---|
| Country (`gl`) | country code | `in` |
| Languages (`hl`) | multi-select (en, hi, ta, te, kn, ml, bn, mr…) → `brand_languages` rows | en, hi |
| Location | city/state via the SerpApi Locations API | none |
| Google domain | google.co.in, google.com… | google.co.in |
| Device | desktop / mobile / tablet | desktop |
| SafeSearch | active / off | off |
| SerpApi cache | on / off (`no_cache`). Cached results (~1h) don't count against quota | on |
| Local cache TTL | per engine | see 6.2 |
| Scan frequency | 1h / 3h / 6h / 12h / 24h / manual → `brand_schedule_versions` row | 6h |
| Quiet hours | e.g. 00:00–06:00 IST → same row | off |
| Max searches per scan | number (capped by admin limit) | 25 |
| Concurrency | parallel calls per scan | 4 |
| Retries | attempts / backoff | 3 / exponential + jitter |

Monthly search budget is a `user_search_budgets` row (default from config: 1,500).

### 6.2 Per engine
| Engine | Settings | Defaults |
|---|---|---|
| **Google search** | on/off · search templates (`{brand}`, `{brand} reviews`, `{brand} complaints`) · pages (1–3) · time filter · include AI Overview | 1 template · 1 page · any time · on · cache 6h |
| **AI Overview** | on/off · follow-up call when only a page token is returned | on · 6h |
| **Autocomplete** | on/off · prefix templates · languages | `{brand} `, `{brand} is `, `is {brand} ` · 6h |
| **Google News** | on/off · languages · max articles · extra search terms | en + hi · 1 page · 3h |
| **Google Trends** | on/off · region (`IN`, `IN-KA`…) · date range · source (web/news/YouTube) · data types | IN · 3 months · web · time + related · 24h |
| **Google Play** | on/off · apps (`brand_apps`) · review sort · pages | newest · 1 page · 12h |
| **Google Maps reviews** | on/off · locations (`brand_locations`, resolved once) · sort · pages per location | newest · 1 page · 12h |
| **YouTube** | on/off · templates · upload-date filter · pages | off · 12h |

**Competitors** are independent brands with their own settings and schedule. When you add one during onboarding it gets the **Lean** preset and a 12h schedule by default, so it costs less; the estimator shows the combined monthly total.

### 6.3 Making the settings safe to use
- **Live estimator** (`domain/estimator.py`, pure function): "17 searches/scan → 68/day → ~2,040/month (budget 1,500 ⚠️)". Used by the UI, before every scan, and in tests.
- **Preview**: one engine, one call, shows the parsed mentions; logged as a `serp_calls` row with no scan; rate-limited.
- **Presets**: Lean / Standard / Deep (they only fill in values).
- **Quota check** before each scan: SerpApi Account API (cached 10 min) + the user's month-to-date ledger. If insufficient → scan `skipped` with a reason + in-app notification.
- **Async searches** (P2): `async=true` for Deep scans.

---

## 7. LLM settings ("High thinking") — ADR-0008

### 7.1 Tasks and defaults
| Task | Purpose | Default model | Default effort | Prompt file |
|---|---|---|---|---|
| `label_mentions` | Sentiment, topic, severity, complaint flag (batches of 20–30) | Opus 5.5 | low | `prompts/label_mentions/v1.md` |
| `classify_autocomplete` | Borderline negative suggestions | Opus 5.5 | low | `prompts/classify_autocomplete/v1.md` |
| `assess_ai_overview` | Sentiment of Google's AI summary + risky cited sources | Opus 5.5 | low | `prompts/assess_ai_overview/v1.md` |
| `group_narratives` | Assign mentions to stories / propose new ones | Opus 5.5 | medium | `prompts/group_narratives/v1.md` |
| `explain_crisis` | Plain-language alert explanation | Opus 5.5 | medium | `prompts/explain_crisis/v1.md` |
| `draft_response` | Holding statement / review reply / FAQ entry with citations | Opus 5.5 | high | `prompts/draft_response/v1.md` |

Sonnet 5.5 and Haiku 4.5 can be selected per task. Settings each model accepts: ADR-0008 table (enforced in `domain/llm_capabilities.py`, UI and gateway).

### 7.2 Settings per task
Model · effort (low → max, "High thinking" = xhigh) · show reasoning summary · max output tokens · temperature (Haiku with thinking off only) · prompt caching · refusal fallback (on by default) · timeout/retries. *(Batch mode is P2, ADR-0008.)*

### 7.3 Presets
| Preset | label / classify / assess | group / explain | draft |
|---|---|---|---|
| Fast | low | low | medium |
| **Balanced** (default) | low | medium | high |
| **High thinking** | medium | high | xhigh |
| Maximum | high | xhigh | max |

The draft screen has **[Draft] [Draft – High thinking] [Draft – Max]** buttons; each overrides only that one call.

### 7.4 Gateway and quality process
- `services/llm_gateway.py` (via the `LLMClient` port): resolve profile → check against model capabilities → render the versioned prompt → call → check `stop_reason` → validate structured output → append an `llm_calls` row → return a typed result.
- Before sending, mention text has **author names/handles stripped**, is wrapped in delimiters and treated as data only. Prompts and completions are never logged.
- Model output is stored with `prompt_version` + `llm_call_id` and shown as **"AI-generated"**.
- **Golden sets** (`evals/golden/<task>.jsonl`): built on Day 3 from real public mentions of the chosen brand, anonymised, hand-labelled (~60 for labelling, ~15 for grouping, ~10 for drafts), including hard and "unknown" cases.
- **Eval runner** `serpsense eval <task> [--sample N]` → quality, cost/item, p95 latency → `evals/reports/<date>-<task>.md`. Ship gate: quality not down > 2 points on any class, cost/item not up > 15%. Labelling must agree with human labels ≥ 90% before its output appears in the UI (until then it runs in log-only mode).
- **Graceful degradation:** LLM down → the scan finishes `partial` with deterministic scores; pending enrichment (recent mentions only) is picked up by the brand's next scan.

---

## 8. Authentication — ADR-0009

```
 [Enter email] → POST /auth/request-otp → normalise → sign-up policy → limits (per email: Postgres; per IP: Redis) → supersede old codes
               → new code (secrets) → store HMAC → outbox row (code encrypted) in the SAME transaction → commit
               → dispatcher sends via Mailpit/SMTP → encrypted code wiped
 ◄── "If that email can sign in, a code is on its way" (same response either way)

 [Enter code]  → POST /auth/verify-otp → latest active code → expired/consumed/≥5 attempts? reject
               → constant-time compare → otp_verify_attempts row → consume → create user if new
               → session row (token hash) → Set-Cookie → audit event
 ◄── new user → /onboarding · existing → /dashboard
```

Rules: ADR-0009. Highlights: HMAC-hashed codes, 10-min expiry, single use, 5 attempts (row-locked), per-email limits in Postgres + per-IP limits in Redis, no account enumeration, hashed session tokens, CSRF on all writes, `SIGNUP_MODE=invite` in production, console mailer refused in production, codes/tokens/emails never logged. **All PRs in this area need your merge.**

---

## 9. Data model

**See `docs/architecture/data-model.md`.** Summary of the main tables:

| Group | Tables |
|---|---|
| Identity | `users`, `otp_codes`, 🔒`otp_verify_attempts`, `sessions`, `user_search_budgets`, `user_llm_budgets` |
| Settings (versioned) | `user_llm_profile_versions`, `user_search_default_versions`, `brand_search_settings_versions`, `brand_schedule_versions` |
| Brands | `brands`, `brand_competitors`, `brand_aliases`, `brand_languages`, `brand_watch_terms`, `brand_apps`, `brand_locations` |
| Scans | `scans` (unique `scheduled_for`; one active per brand), 🔒`scan_status_transitions`, 🔒`scan_surface_results` |
| Search data | 🔒`serp_calls`, `raw_responses` (redacted), `mentions`, 🔒`mention_observations`, 🔒`app_rating_observations`, 🔒`trends_observations` |
| Model output | `enrichments`, `narratives`, 🔒`narrative_assignments`, `drafts`, `draft_citations`, 🔒`llm_calls` |
| Scores | reference: `scoring_versions`, `scoring_weights`, `crisis_level_thresholds`; results: `score_runs`, `surface_scores`, `crisis_components`; totals via `v_scan_scores` |
| Alerts | `alerts` (unique per scan/rule/narrative), `notifications`, `notification_reads`, `outbox_messages`, 🔒`outbox_attempts` |
| Audit | 🔒`audit_events`, `audit_event_network` (scrubbable) |

🔒 = append-only, enforced by a database trigger.

---

## 10. Scan pipeline

1. **Dispatch** (Beat, every 5 min): for each non-archived brand with a schedule, outside quiet hours, compute `scheduled_for` (start of the current slot) and `INSERT … ON CONFLICT DO NOTHING` (no conflict target, so both the slot constraint and the active-scan index apply) into `scans` (`queued`). Enqueue only rows actually inserted, **after commit** (`JobQueue` port). "Scan now" uses the same insert; a conflict returns "scan in progress".
2. **Claim:** `UPDATE scans SET status='running' WHERE id=:id AND status='queued' RETURNING` + transition row, in one transaction. Zero rows → exit without doing anything (a redelivered message, or another worker has it).
3. **Resolve settings** → snapshot → **estimate** → check the user's budget and the SerpApi quota. If insufficient → `running → skipped` with a reason + notification.
4. **Collect** with limited concurrency through the SerpApi client (cache → retry → circuit breaker → `serp_calls` row → redaction → `raw_responses`). Each surface records a `scan_surface_results` row.
5. **Normalise** (adapter parsers → typed `Mention`): insert `mentions` first-seen (`ON CONFLICT DO NOTHING` on the unique key), insert `mention_observations`, app ratings and Trends observations.
6. **Enrich (inline, same task):** label recent mentions (observed in the last 7 days) without an enrichment for the active `prompt_version`, then group into narratives. If the LLM fails, continue and finish `partial`; the next scan picks up the backlog.
7. **Score** (pure domain functions) → `score_runs`, `surface_scores`, `crisis_components` for the active `scoring_version`; health/crisis/level are derived by `v_scan_scores`.
8. **Compare with the last scan** (derived from observations) → **alert rules** (deterministic) → `alerts` + in-app `notifications` + `outbox_messages` in **one transaction** → the dispatcher sends emails.
9. **Finish:** compare-and-set `running → succeeded | partial | failed | skipped` with a reason. Before LLM calls and before alerts, the task checks `archived_at` / `deleted_at`; if set it finishes `skipped` with no LLM calls, alerts or emails.

All stages run inside **one claimed task**; there is no separate enrichment or re-score job. Scan tasks never use Celery `retry` (retries are per external call).

Every step can safely run twice: unique keys, `ON CONFLICT`, and "already done" checks per step.

**Recovery sweep** (Beat, every 5 min): `queued` > 2 min → re-enqueue; `running` past the time limit → `failed` (`timed_out`); due outbox rows → nudge. Archived brands and deleted users are ignored. A Redis outage or crashed worker therefore delays work but never loses it.

### Collectors
| Collector | Engine | Searches (Standard) | Stored as |
|---|---|---|---|
| Search page | `google` | 1 per template × page | mentions: `serp_result`, `top_story`, `people_also_ask` |
| AI Overview | in the result / `google_ai_overview` | 0–1 | mention `ai_overview` (or surface `not_shown`) |
| Autocomplete | `google_autocomplete` | 1 per prefix × language | mentions `autocomplete` + positions |
| News | `google_news` | 1 per language × search term | mentions `news` |
| Trends | `google_trends` — **joint query** (brand + up to 4 competitors) for interest over time; related queries for the brand only | 1 per data type | `trends_observations` (one series per subject brand) + mentions `trends_query` |
| Play | `google_play_product` | 1 per app × page | mentions `play_review` + `app_rating_observations` |
| Maps | `google_maps` (resolve once) → `google_maps_reviews` | 1 per location × page | mentions `maps_review` |
| YouTube | `youtube` | 1 per template × page | mentions `youtube_video` |

Standard preset ≈ **14 searches** per brand scan. Each competitor is its own brand with its own scans (Lean preset by default ≈ 8 searches).

---

## 11. Scoring (`domain/scoring/` + `v_scan_scores`, `scoring_version = "s1"`)

All values are integers 0–100.
- **Search page:** 100 − (negative share weighted by position, weight 1/log₂(pos+1)) × 100
- **Autocomplete:** 100 − 30 per negative suggestion (more if higher in the list), minimum 0
- **AI Overview:** LLM sentiment mapped to 0–100
- **News:** (positive + ½ neutral) / total over 7 days × 100
- **Play / Maps:** ½ normalised star rating + ½ sentiment of the newest 50 reviews
- **Overall health:** weights in basis points: search page 2500 · autocomplete 2000 · AI Overview 1500 · news 1500 · Play 1500 · Maps 1000. Re-weighted when a surface is unavailable.
- **Crisis (0–100):** velocity 3000 bp · spread 2500 · new negative autocomplete 2000 · rising negative Trends query 1500 · press in 48h 1000. Levels: < 40 low, 40–69 medium, ≥ 70 high.
- **Competitor check:** same topic rising at the competitor → "category-wide".

Weights and thresholds are **reference rows per scoring version** (`scoring_weights`, `crisis_level_thresholds`); totals and levels are derived by `v_scan_scores` and mirrored by pure domain functions (a test asserts both agree). Per-brand custom weights are not offered. Documented in `docs/scoring.md`; unit tests use fixed inputs.

---

## 12. Alerts

- **Deterministic rules** fire when:
  - the crisis level goes up, or
  - a new negative autocomplete suggestion appears, or
  - a narrative reaches 5 or more mentions on 2 or more surfaces.
- **Idempotent:** unique per (scan, rule, narrative). **Cooldown:** 12h per (brand, narrative, rule), checked by query; race-free because a brand has at most one active scan.
- **Applies to every brand, including competitors** (your decision): a competitor crisis also sends email + in-app, with the subject line marked "Competitor: …".
- **Channels:** **email** (outbox `alert_email`) and **in-app** notification. New channels = new outbox kind + adapter via a new ADR (ADR-0011).
- **Content:** level, the narrative and its mention count, surfaces, what's new since the last scan, the competitor check, and the LLM explanation (labelled AI-generated).

---

## 13. Web pages

| Page | Content |
|---|---|
| Log in / verify | Email → 6-digit code (paste support, resend countdown) |
| Onboarding | Brand, aliases, competitor, apps, locations, languages, watch terms → preset with live estimate |
| Overview | Health/crisis, trend, top narratives, autocomplete watch, AI Overview, last scan status (partial surfaces flagged), **[Scan now]** |
| Narratives | List + detail + **[Draft] [High thinking] [Max]**, optional reasoning summary, "AI-generated" labels |
| Surfaces | One tab per surface; normalised mentions with labels |
| Competitors | Share of voice, sentiment comparison, Trends |
| Timeline replay | Slider across scans (key demo screen) |
| Brand → Search settings | §6 with estimator, presets, Preview, version history |
| Brands list | Your brands and competitors (each a full brand), with a "competitor of …" badge |
| Settings → AI | Per-task model/effort profiles, presets, capability-checked controls |
| Settings → Account | Log out everywhere · **Delete account** (fresh OTP + typed confirmation, ADR-0013) |
| Usage & budgets | Searches by engine and cache source, LLM tokens/cost by task/model, budgets, SerpApi quota |
| Notifications | In-app alert centre |

---

## 14. Reliability & operations

| Concern | Approach |
|---|---|
| Timeouts | SerpApi 30s · Claude per task (streaming for long outputs) · SMTP 10s |
| Retries | Retryable (network, 429, 5xx) with exponential backoff + jitter; permanent errors fail fast |
| Circuit breaker | Derived from the last 5 `serp_calls` per engine (all failed within 15 min → open) → surface `circuit_open` |
| Idempotency | Unique keys, `ON CONFLICT`, slot constraint, outbox `dedupe_key` |
| Partial failure | `partial` scans, re-weighted scores, UI shows which surfaces failed |
| Health | `/healthz`, `/readyz` (Postgres + Redis) |
| Metrics | Exposed by `web` only; job signals computed from Postgres ledgers + Redis queue length at scrape time. `serp_calls_total{engine,served_from,outcome}` · `llm_calls_total{task,model,outcome}` · `llm_cost_micros_total{task,model}` · `scans_total{status}` · `scan_duration_seconds` · `outbox_pending` · `outbox_dead_total` · `celery_queue_depth{queue}` · `otp_requests_total{outcome}` |
| Alerts & dashboards | PromQL in `docs/observability/alerts.md` and `dashboards.md` |
| Runbooks | **P0:** scan-failures-high · serpapi-errors-high · llm-errors-high · outbox-backlog. **P1:** serpapi-quota-low · llm-budget-exhausted · queue-backlog · otp-abuse · redis-unavailable · postgres-unavailable |
| Migrations | Alembic, reversible, run as a one-off `migrate` service before `web` starts |
| Backups | `pg_dump` script + restore steps in `docs/operations.md` |

---

## 15. Security checklist

See `AGENTS.md` §6 and ADR-0009/0011. Key items: secrets only via `config.py` and listed in `.env.example`; SerpApi payload redaction plus a test; gitleaks in pre-commit and CI on full history; OTP and session hashing; CSRF; rate limits; scoped queries returning 404; outbound hosts fixed; CSP without inline scripts; non-root container; pip-audit; console mailer refused in production; OTP encrypted in the outbox and wiped after sending; no emails/codes/tokens/prompts in logs.

---

## 16. Testing & CI

| Marker | Covers |
|---|---|
| `unit` | Estimator, settings resolution, LLM capability rules, scoring, comparison, scan state machine, OTP rules, redaction |
| `contract` | Each SerpApi parser against recorded, redacted fixtures |
| `integration` | Postgres (testcontainers): repositories, append-only triggers, full scan with fake ports, status-consistency check, budget skip, partial scans, sweep recovery. **Two-connection concurrency:** scheduled-slot insert, scan claim (redelivery = no-op), one active scan per brand, OTP attempt limit, outbox dispatcher |
| `api` | Routes via FastAPI `TestClient`: auth flow, onboarding, settings, drafts, account deletion |
| `security` | Cross-user 404s on every brand-scoped route, CSRF, OTP expiry/single-use/lockout/enumeration, invite mode, Secure cookies in production, key never in DB/logs, console mailer refused in production, deletion leaves no email/IP/user agent and needs a fresh OTP |

No network (`pytest-socket`), injected clock, coverage on changed lines ≥ 90%.
**CI (GitHub Actions)** = the `AGENTS.md` §10 gate + docker build. Changes to the workflow files need your merge.

---

## 17. Repo layout

```
serpsense/
├─ AGENTS.md · CLAUDE.md · README.md · BUILD_PLAN.md · LICENSE · .env.example · .gitignore
├─ pyproject.toml · uv.lock · .pre-commit-config.yaml · .importlinter
├─ Dockerfile · docker-compose.yml · docker-compose.prod.yml (P2)
├─ alembic.ini · migrations/
├─ src/serpsense/
│  ├─ entrypoints/  web/ (app, routes, templates, static) · jobs/ (celery app, beat, tasks) · cli.py
│  ├─ services/     scans · dispatch · sweep · enrichment · scoring_run · alerts · drafts · auth · accounts · brands · settings · usage · llm_gateway · outbox
│  ├─ domain/       enums · mention · scan_state · settings/ · estimator · llm_capabilities · scoring/ · diff · crisis · alert_rules
│  ├─ ports/        search_provider · llm_client · mailer · clock · unit_of_work + repositories · job_queue · rate_limiter · response_cache · secret_box
│  ├─ adapters/     serp/ (client, collectors, parsers, redaction) · llm/ (anthropic_client, fake, prompts/) · mail/ (smtp, console, fake) · db/ (base, models, repositories, uow) · cache/ (redis cache, rate limiter) · jobs/ (celery job queue) · crypto/ (multifernet)
│  ├─ config.py · observability.py · composition.py
├─ evals/           golden/ · reports/ · runner (via CLI)
├─ tests/           unit/ · contract/ · integration/ · api/ · security/ · fixtures/ (redacted)
└─ docs/            adr/ · architecture/data-model.md · runbooks/ · observability/ · scoring.md · serpapi-usage.md · operations.md
```

---

## 18. Environment variables (`.env.example`, names only)

```
# Core
APP_ENV=development                  # development | production
BASE_URL=http://localhost:8000
SECRET_KEY=                          # OTP HMAC, CSRF
OUTBOX_ENCRYPTION_KEYS=              # comma-separated Fernet keys (first encrypts; all decrypt) for rotation
DATABASE_URL=postgresql+psycopg://serpsense:serpsense@postgres:5432/serpsense
REDIS_URL=redis://redis:6379/0
SERPSENSE_MODE=live                  # live | replay (fixtures + fake LLM, no keys needed)

# SerpApi
SERPAPI_API_KEY=
MAX_SEARCHES_PER_SCAN=40
SERPAPI_DAILY_GLOBAL_CAP=500
DEFAULT_MONTHLY_SEARCH_BUDGET=1500

# Claude
ANTHROPIC_API_KEY=
ALLOWED_MODELS=claude-opus-5-5,claude-sonnet-5-5,claude-haiku-4-5
DEFAULT_LLM_PRESET=balanced          # fast | balanced | high_thinking | maximum
DEFAULT_MONTHLY_LLM_BUDGET_MICROS=30000000   # US$30
LLM_REFUSAL_FALLBACK=true

# Auth
SESSION_DAYS=7
OTP_TTL_MINUTES=10
SIGNUP_MODE=open                     # open | invite (invite required in production; production also requires an https BASE_URL)
ALLOWED_EMAILS=
ALLOWED_DOMAINS=

# Email
EMAIL_BACKEND=smtp                   # smtp | console (console refused in production)
SMTP_HOST=mailpit
SMTP_PORT=1025
SMTP_USER=
SMTP_PASSWORD=
SMTP_STARTTLS=false
EMAIL_FROM=SerpSense <no-reply@serpsense.local>

# Observability
LOG_LEVEL=INFO
```

---

## 19. Workflow (from `AGENTS.md` §11 and the `github-issue-picker` agent)

- Each schedule item below becomes **GitHub issues** with checkable acceptance criteria.
- Branch → PR (≤ 400 changed lines / 20 files) → reviewers (architect, python, security, test; observability and prompt-eval when relevant) → green CI → rebase-merge (atomic commits land on `main` unchanged).
- **Your merge is required** for PRs touching: auth/OTP/sessions, `.github/workflows/`, Docker/Compose, secrets handling, destructive migrations, new dependencies. In this plan that covers the Day 0 bootstrap, the Day 1 schema PRs (they add dependencies and infra), Day 5 auth + account deletion, and any new package.
- **The schema lands as one PR per table group** (identity · settings + brands · scans + search data · model output + scores · alerts + outbox + audit) to respect the 400-line limit.
- Run all relevant reviewers **in parallel once per PR round**.
- **Bootstrap exception:** the Day 0 repo bootstrap (tooling, CI, Compose, empty package) is a single human-reviewed PR, since there's no CI to gate it yet.

---

## 20. Priorities

| Priority | Items |
|---|---|
| **P0: must ship** | Bootstrap + CI gate · schema + migrations · SerpApi client (cache/retry/redaction/ledger) · core 5 collectors · state machine + slot dispatcher + recovery sweep · LLM gateway + capability rules + presets + versioned prompts · labelling + grouping with golden sets and eval reports · scoring + comparison + deterministic alerts · outbox + email + in-app notifications · OTP auth + **account deletion** · Overview / Narratives / Search settings / AI settings pages · drafter with High thinking · replay mode · README |
| **P1: should ship** | Maps + AI Overview collectors · competitor comparison views · timeline replay · Usage & budgets page · Preview · metrics + P1 runbooks · personal-data scrub job |
| **P2: if time allows** | YouTube · async SerpApi · batch-mode labelling · `docker-compose.prod.yml` + TLS proxy · PDF export |

**Cut decision checkpoints:** end of Day 3 and end of Day 5.

---

## 21. Day-by-day schedule

| Day | Date | Build (issues/PRs) | Done when |
|---|---|---|---|
| **0** | Fri Oct 2 | **Review and accept ADRs 0001–0013**. Pick the brand + competitor; try each engine in the Playground. Create the GitHub repo + labels + branch protection. **Bootstrap PR (your merge):** pyproject/uv, ruff/mypy/import-linter config, pre-commit + gitleaks, CI gate, Dockerfile + Compose (postgres, redis, mailpit), empty layered package, `.env.example`. | CI green on an empty package; `docker compose up` healthy |
| **1** | Sat Oct 3 | **Schema PRs, one per table group (your merge):** SQLAlchemy models + reversible migrations + append-only triggers + naming convention + views + scoring reference seed; data-model doc in sync. **SerpApi PR:** port + client (cache, retry, circuit breaker, redaction, ledger) + collectors for search page / autocomplete / news + contract tests on recorded fixtures. | Migration up/down passes; contract tests green; redaction test proves the key never lands |
| **2** | Sun Oct 4 | Scan state machine + transition log; slot dispatcher + claim + recovery sweep + concurrency tests; scan service end to end with fakes; collectors for Trends + Play (+ Maps/AI Overview if time allows); quota check. **Start scheduled scans for the real brand tonight.** | Two concurrent dispatchers create exactly one scan per slot; scheduled scans running |
| **3** | Mon Oct 5 | LLM port + Anthropic adapter + fake; gateway with capability rules + presets; prompt files v1; **golden sets** from real data; eval runner + first reports; labelling + grouping; backfill from dates. **Cut checkpoint 1.** | Eval reports committed; labelling ≥ 90% agreement or kept log-only |
| **4** | Tue Oct 6 | Scoring + crisis + comparison (pure, unit tested); deterministic alert rules + cooldown; outbox + dispatcher + concurrency test; in-app notifications; first runbooks + alert PromQL | Replaying two snapshots creates one alert → email in Mailpit, no duplicates |
| **5** | Wed Oct 7 | **Auth PR (your merge):** OTP + sessions + CSRF + rate limits + invite mode + security tests. **Account deletion PR (your merge).** Web skeleton (middleware, CSP), onboarding + estimator, Overview, Search settings, AI settings. **Cut checkpoint 2.** | Sign up via Mailpit → onboard → settings → estimate updates; security tests green |
| **6** | Thu Oct 8 | Narratives + drafter (**[High thinking]**, reasoning summary, citations check) + draft eval report; Surfaces, Competitors, Timeline replay, Usage & budgets, Notifications; Preview | Full story works in the browser |
| **7** | Fri Oct 9 | Metrics, health, remaining runbooks/dashboards; replay mode + `seed-demo`; README + `docs/serpapi-usage.md` + `docs/scoring.md` + `docs/operations.md`; **fresh-clone `docker compose up`**; gitleaks full history; pip-audit | A stranger runs replay mode with no keys in under 5 minutes |
| **8** | Sat Oct 10 | Record demo in the morning, upload unlisted, check links in a private window, **submit by 18:00** | Submitted |

> **Scheduled scans need a machine that stays on** from Day 2: your laptop running Compose, or `worker` + `beat` + `postgres` + `redis` on a small VPS (`pg_dump` back before recording).

---

## 22. Timeline data and budget

1. Scheduled scans from Day 2 (every 6h) → about 28 snapshots by Oct 9.
2. **Backfill from dates:** reviews, news and Trends carry dates, so one scan can rebuild a past timeline. Only autocomplete and the search page need repeated snapshots.
3. Choose a brand with a recent controversy. Present findings as "signals found in public search data", not accusations.
4. **Budget:** about 1,000 SerpApi searches (scheduled + development); Claude spend tracked in `llm_calls`, capped by `user_llm_budgets` (default US$30/month).

---

## 23. README outline (judges read this)

1. Pitch + screenshot + CI badge + "Not affiliated with SerpApi, LLC."
2. Problem: "your reputation is what Google shows people"
3. **How SerpApi is used:** engine → purpose → settings → searches per scan
4. Architecture (layers, Compose services) + link to ADRs
5. Settings: SerpApi settings + estimator; LLM profiles, presets, High thinking, capability rules
6. Scoring method
7. Quick start: **replay mode** (`docker compose up`, no keys, OTP from Mailpit at `localhost:8025`) and **live mode**
8. Quality: eval reports, golden sets, test markers, coverage
9. Operations: health, metrics, runbooks, backups
10. Security model
11. Limitations + out of scope
12. AI tools used

---

## 24. Demo video (under 3 minutes)

| Time | Show |
|---|---|
| 0:00–0:15 | Problem statement |
| 0:15–0:35 | Sign up → OTP in Mailpit → onboarding with preset + live estimate |
| 0:35–0:55 | Search settings (Bengaluru, Hindi + English, Deep) → Preview → Scan now |
| 0:55–1:35 | Overview: health/crisis, narratives, autocomplete watch, AI Overview, competitor check |
| 1:35–2:10 | Timeline replay: the crisis spreads → alert email in Mailpit + in-app notification |
| 2:10–2:45 | Draft → **High thinking** draft with reasoning summary + citations ("AI-generated") |
| 2:45–3:00 | Usage & budgets + eval report + CI badge |

Before recording, make sure no `.env`, keys, raw payloads or real personal emails are on screen.

---

## 25. Risks

| Risk | Fix |
|---|---|
| Process + production scope vs 8 days | P0/P1/P2, two cut checkpoints, small PRs, fakes in tests |
| ADRs not accepted in time | Accept (or amend) all 13 tonight; reviewers block infra without them |
| Human-merge bottleneck | Batch human-merge PRs (bootstrap, schema groups, auth, deletion) and review them promptly |
| Competitors as full brands double SerpApi cost | Lean preset + 12h schedule by default for competitors; estimator shows the combined total; budgets enforce it |
| Golden-set labelling takes time | Keep sets small (~60/15/10), label on Day 3 morning |
| AI Overview missing | Surface outcome `not_shown` |
| No crisis this week | Backfill from dates + a brand with a recent controversy + replay |
| API key leak | Redaction + test + gitleaks (pre-commit + CI) + video check |
| Open sign-up uses up paid keys | Invite mode in production, budgets, global caps |
| LLM outage | Scan finishes `partial` with deterministic scores; the next scan enriches the backlog |
| Laptop off during scheduled scans | Small VPS or accept gaps + backfill |
| Judges can't run it | Replay mode + Compose + fresh-clone test on Day 7 |

---

## 26. Tonight's checklist (Day 0)

- [ ] Read and accept (or amend) **ADRs 0001–0013**
- [ ] Pick the brand + competitor; try `google`, `google_autocomplete`, `google_news` (en/hi), `google_trends`, `google_play_product`, `google_maps_reviews`, `google_ai_overview` in the Playground
- [ ] Note the Play app id(s) and Maps location queries
- [ ] Add `SERPAPI_API_KEY` to `.env` (only `ANTHROPIC_API_KEY` is there today)
- [ ] Create the GitHub repo `serpsense`, labels (`agent:in-progress`, `needs-human`, `needs-info`, `blocked`), branch protection on `main`
- [ ] Bootstrap PR (tooling, CI, Compose, empty package) — your merge
- [ ] Decide where scheduled scans will run from Day 2 (laptop vs small VPS)

---

## 27. Decisions log (owner)

| Date | Decision |
|---|---|
| 2026-10-02 | Competitors are independent brands with their own schedules and dashboards |
| 2026-10-02 | "Scan now" is rejected while a scan of that brand is active |
| 2026-10-02 | Account deletion is in scope now (ADR-0013) |
| 2026-10-02 | No extra Anthropic retention requirement (ADR-0008) |
| 2026-10-03 | Competitor crises send **email + in-app** alerts, like any brand |
| 2026-10-03 | Google Trends uses a **joint query** (brand + up to 4 competitors) for comparable share of search |
| 2026-10-03 | Default budgets kept: 1,500 SerpApi searches and US$30 Claude spend per user per month; global cap 500 searches/day |
| 2026-10-03 | Dedicated `worker-outbox` service so long scans can't delay OTP/alert email (ADR-0006 amendment) |
| 2026-10-03 | Rebase-merge only; atomic commits; no AI attribution; no schedule-day labels in commits/PRs/issues |

Also deferred to P2 (not in the data model): a per-user "email alerts on/off" setting. Alerts always go to email + in-app.
