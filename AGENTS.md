# SerpSense — Agent & Contributor Rules

SerpSense monitors how a brand looks to anyone who Googles it (results page, autocomplete, AI Overview, news, Trends, app and map reviews), detects emerging reputation crises, and drafts responses backed by evidence. Built for the SerpApi India Hackathon 2026 (deadline **2026-10-10 23:59 IST**).

These rules bind every human and agent working in this repo. The reviewer agents in `.claude/agents/` (not committed) enforce them. Where this file and a generic agent default disagree, **this file wins**. Accepted ADRs in `docs/adr/` are binding; a change that contradicts one needs a new ADR first.

- Product & build plan: `BUILD_PLAN.md`
- Architecture decisions: `docs/adr/`
- Data model (source of truth): `docs/architecture/data-model.md`
- Runbooks: `docs/runbooks/`

---

## 1. Scope

**In scope:** single-tenant-per-user brand monitoring (competitors are independent brands with their own schedules); email OTP auth; account deletion with personal-data scrubbing (ADR-0013); SerpApi collectors; LLM labelling, narrative grouping, crisis explanation and response drafting; health/crisis scoring; email + in-app alerts; replay/demo mode; Docker Compose deployment on one host.

**Out of scope (do not add, not even as placeholder columns or modules):** teams/organisations/RBAC, SSO/OAuth login, billing/payments, outbound webhooks or any other user-supplied outbound URL (ADR-0011), Telegram/Slack/SMS integrations, Kubernetes/multi-region, social-media scraping, APM vendors (ADR-0012).

**Future scope (don't close these doors):** teams (keep ownership checks behind one scoping function, not scattered `owner_id ==`), more alert channels (`services/alerts.py` fans out to in-app rows and outbox kinds; a new channel = new outbox kind + adapter), more search engines (collectors are registered, not hard-coded in the scan service).

---

## 2. Module layout and dependency direction

```
src/serpsense/
├─ entrypoints/        web/ (FastAPI routes, templates), cli.py, jobs/ (Celery tasks)   ← thin: validate, call one service, return
├─ services/           use cases: scans, dispatch, sweep, enrichment, scoring_run, alerts, drafts, auth, accounts
│                      (deletion), brands, settings, usage, llm_gateway, outbox
├─ domain/             pure logic, no I/O: enums, value objects, scoring, crisis, diff, alert_rules, settings schemas,
│                      estimator, llm_capabilities, scan_state, mention model
├─ ports/              Protocols the services depend on: SearchProvider, LLMClient, Mailer, Clock, UnitOfWork +
│                      repositories, JobQueue (after-commit enqueue), RateLimiter, ResponseCache, SecretBox (encryption)
├─ adapters/           implementations: serp/ (SerpApi SDK + collectors/parsers), llm/ (Anthropic SDK, prompts, fake),
│                      mail/ (smtp, console, fake), db/ (SQLAlchemy models, repositories, unit of work),
│                      cache/ (Redis cache + rate limiter), jobs/ (Celery JobQueue), crypto/ (MultiFernet SecretBox)
├─ config.py           the ONLY place env vars are read (pydantic-settings)
├─ observability.py    logger, metrics helpers
└─ composition.py      wires adapters into services (the composition root)
```

**Allowed imports** (enforced by import-linter, `lint-imports` in CI):
`entrypoints → services → domain`, `services → ports`, `adapters → ports, domain`, `composition → everything`.
**Forbidden:** `domain` importing anything with I/O; `services` importing `adapters`; entrypoints importing `adapters` (except through `composition`); function-local imports or `importlib` to dodge these rules.

**SDK isolation:** `serpapi` only in `adapters/serp/`; `anthropic` only in `adapters/llm/`; `smtplib` only in `adapters/mail/`; `redis` only in `adapters/cache/`; `celery` only in `adapters/jobs/` (the `JobQueue` adapter) and `entrypoints/jobs/`; `cryptography` only in `adapters/crypto/`.

**Sync only** (ADR-0002): services, adapters and repositories are synchronous; FastAPI routes are `def`, not `async def`. Parallel SerpApi calls use a bounded `ThreadPoolExecutor` inside the scan service's collector runner.

---

## 3. Coding rules

- Python 3.11, full type hints on new/changed functions, `mypy --strict` on `src/`.
- **Budgets:** cyclomatic complexity ≤ 10, function body ≤ 60 lines, module ≤ 400 lines, ≤ 5 positional args. Split by responsibility; never raise the limit.
- External data (HTTP bodies, SerpApi/Claude payloads, env vars, files) is parsed **once at the boundary** into Pydantic models or dataclasses. Raw dicts don't cross layers.
- Statuses and kinds are `StrEnum`s.
- **Time:** timezone-aware UTC only. No `datetime.now()` without tz, no `utcnow()`. Anything depending on "now" takes a `Clock` port.
- **Money:** integers only. User-facing amounts in minor units (`*_cents` + `currency`); LLM cost accounting in `*_micros` (1 micro = 10⁻⁶ of the currency unit) + `currency`. Never `float`.
- **Randomness for security** (OTP, tokens): `secrets` only.
- No bare `except`, no `except Exception: pass`. Re-raise with `from exc`. Exception text never reaches users or HTTP responses.
- No `eval`/`exec`/`pickle`/`yaml.load` on untrusted data; no `subprocess` with `shell=True`.
- No commented-out code, stray `print`, `TODO` stubs or debug routes. `# noqa` / `# type: ignore` always carry a code and a reason.
- Imports at the top of the module, sorted and grouped.

---

## 4. Data rules (details: `docs/architecture/data-model.md`)

- PostgreSQL is the system of record (ADR-0003). Redis holds only rebuildable data (ADR-0004).
- Normalised to 3NF. **No arrays/JSON for anything queried or filtered.** JSONB is allowed only for: raw provider payloads, immutable settings snapshots, and settings documents that are always read whole (ADR-0003).
- No stored values that can be derived from other columns. The only named exceptions are `scans.status` and `outbox_messages.status` (claim targets), each written only together with its transition/attempt row and covered by a consistency test.
- Facts that change over time are rows with timestamps (budgets, settings, schedules, status transitions, observations), not overwritten columns.
- **Append-only tables** (marked 🔒 in the data model: ledgers, transitions, observations, attempts, audit events, narrative assignments) have no update/delete path in code, and the migration installs a trigger that rejects UPDATE/DELETE. Repeatable inserts use `ON CONFLICT DO NOTHING`.
- **No raw personal data (email, IP, user agent) in append-only tables**; it lives in mutable tables that account deletion scrubs (ADR-0013). FKs from append-only tables use `ON DELETE RESTRICT`.
- Constraints and indexes are **named** (SQLAlchemy naming convention in `adapters/db/base.py`).
- Migrations (Alembic) are reversible and follow expand → backfill → contract. The data-model doc is updated in the **same commit** as the migration.
- User-scoped data is read through **one scoped query path** (`repositories.scoped(user)`); other users' records return **404**, identical to a missing record.

---

## 5. State, jobs and side effects

- Scan status changes only through `domain/scan_state.py`'s transition table; every transition writes a `scan_status_transitions` row (who, from, to, when, why) in the same transaction. Undeclared transitions raise.
- **Postgres is the source of truth for jobs; Celery messages are nudges** (ADR-0005). Handlers re-read state, claim with compare-and-set (`UPDATE … WHERE status = 'queued' RETURNING`), and exit quietly if the claim fails. A maintenance sweep recovers lost enqueues and stuck scans.
- Background jobs are idempotent, safe to retry, and classify errors as retryable or permanent. Enqueue only via the `JobQueue` port, which sends **after commit**.
- Scheduled scans are unique per `(brand_id, scheduled_for)`; at most one active (`queued`/`running`) scan per brand (`uq_scans_brand_id_active`); "Scan now" during an active scan is rejected.
- Alert creation is idempotent per `(scan_id, rule, narrative_id)`.
- **Emails leave only through the outbox** (`outbox_messages`, ADR-0010): written in the same transaction as the business change, with a unique `dedupe_key`, dispatched after commit by a worker that claims rows with `FOR UPDATE SKIP LOCKED` and holds the lock through send and commit (at-least-once delivery). In-app notifications are rows written in the same transaction.
- No module-level mutable state, no background threads in the web process.
- Outbound HTTP goes only to configured hosts: `serpapi.com`, `api.anthropic.com`, the configured SMTP host. No user-supplied URLs (ADR-0011).

---

## 6. Security rules

- Secrets come from env via `config.py`, are listed by name in `.env.example`, and never appear in the repo, fixtures, logs, metrics, error messages or the demo video. **Leaking an API key disqualifies the hackathon entry.**
- SerpApi payloads are redacted before storage (strip `search_metadata` URLs, any `*_link`/URL containing `api_key`, anything matching the key pattern, and reviewer/author identity). Tests assert the key and reviewer identities never reach the DB, logs or committed fixtures.
- Auth per ADR-0009: hashed OTPs and session tokens, constant-time compare, expiry, single use, attempt limits, resend cooldown, CSRF on cookie-authenticated writes, same response for known/unknown emails.
- The console mail backend (prints OTPs) **refuses to start when `APP_ENV=production`**; production also requires `SIGNUP_MODE=invite` and an https `BASE_URL` (Secure cookies).
- Account deletion (ADR-0013) requires a fresh OTP and leaves no email, IP or user agent for the deleted user in any table.
- Security headers + CSP (no inline scripts) on every HTML response.
- New dependencies: maintained, permissive licence, pinned, `pip-audit` clean.

---

## 7. LLM rules (ADR-0008)

- The Anthropic SDK is used only in `adapters/llm/anthropic_client.py`, behind the `LLMClient` port; tests use `adapters/llm/fake.py`.
- **Prompts are files:** `src/serpsense/adapters/llm/prompts/<task>/v<N>.md`, referenced by `prompt_version`. No prompts assembled inline in business code. Changing a prompt bumps its version.
- Every prompt/model/threshold change runs the eval harness against the baseline (`serpsense eval <task>`), reports to `evals/reports/<date>-<task>.md`, and must not drop quality > 2 points on any class or raise cost/item > 15%.
- Golden sets: `evals/golden/<task>.jsonl` — real public samples, anonymised (reviewer names and handles removed), including hard and "unknown" cases.
- **Provenance:** every model-output row carries `prompt_version` and `llm_call_id` (that is the provenance marker), and is labelled "AI-generated" in the UI.
- **Human in the loop:** the model labels, groups, explains and drafts. It never sends anything to anyone. Alerts fire from deterministic rules over scores; the LLM only writes the explanation text. Drafts are copied by a human.
- Mention text is **untrusted input**: wrapped in delimiters, never followed as instructions. Author names/handles are stripped before sending.
- Prompts and completions are **never logged**; refer to them by `prompt_version` and `llm_call_id`.

---

## 8. Observability rules (ADR-0012)

- `structlog` JSON logs via `observability.get_logger()`. Event names are `noun.verb_past` (`scan.completed`, `otp.verify_failed`, `serp_call.failed`).
- Context (request_id, user_id, scan_id, job attempt) is bound once via contextvars.
- **Never log:** API keys, OTPs, session tokens, email addresses (log `user_id` or a salted hash), prompts/completions, full provider payloads.
- Metrics via the shared helper; no user ids in labels; once per unit of work.
- Expected outcomes (validation errors, 404s, rate limits, skipped scans) are not errors.
- Every new failure mode ships with an alert rule (or a written decision not to), a dashboard query in `docs/observability/dashboards.md`, and a runbook in `docs/runbooks/<slug>.md`.

---

## 9. Testing rules

- `pytest` markers: `unit`, `integration`, `api`, `security`, `contract`.
- Integration tests run on **Postgres** (testcontainers), never SQLite.
- **No network in tests** (`pytest-socket`); external systems via fakes behind ports; adapter tests use recorded, redacted fixtures (`tests/fixtures/`).
- Deterministic: injected clock / `time-machine`, seeded randomness, no `sleep`, order-independent.
- Concurrency tests with two real DB connections for: scheduled-slot insert, scan claim (redelivery is a no-op), one-active-scan rule, OTP verification attempt limit, and the outbox dispatcher.
- API tests use FastAPI's `TestClient` (sync).
- Coverage on changed lines ≥ 90% (`diff-cover`). Never weaken assertions, skip or xfail to get green.
- Bug fix = failing test first.

---

## 10. Local gate (must match CI)

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run lint-imports
uv run pytest -m "unit or contract or security"
uv run pytest -m "integration or api"          # needs Docker for testcontainers
uv run alembic check
gitleaks git --redact .
uv run pip-audit
```

---

## 11. Workflow

- Work starts from a GitHub issue with checkable acceptance criteria.
- Branch: `agent/issue-<N>-<slug>` (agents) or `feat/<N>-<slug>` (humans). Never commit to `main`.
- Conventional commits: `feat(scope): summary (#N)`, `fix(…)`, `docs(…)`, `test(…)`, `chore(…)`.
- PR limit: **400 changed lines or 20 files**. Split bigger work.
- **Rebase-merge only** (`gh pr merge --rebase`), after green CI and reviewer approval (architect, python, security, test; observability/prompt-eval when relevant). Squash and merge commits are disabled in the repository settings, so each atomic commit lands on `main` as-is and history stays linear. Where an agent definition says `--squash`, use `--rebase`.
- **Human merge required** (agents stop and hand over) for any PR touching: auth/sessions/OTP, account deletion, `.github/workflows/`, Docker/Compose/infra config, secrets handling, migrations that drop/rename, or new dependencies.
- **Documented exceptions to the PR size limit:** (1) the one-time repo bootstrap PR (no CI exists yet to gate it); (2) none other — the schema lands as one PR per table group.
- Batch reviewer runs: run all relevant reviewers in parallel once per PR round, not after every commit.

### Definition of done
Acceptance criteria ticked · local gate green · CI green · reviewers approve · data-model doc / ADR / runbook updated where relevant · no secrets anywhere · README updated if behaviour or setup changed.
