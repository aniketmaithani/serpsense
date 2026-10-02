# ADR-0005: Celery + Beat for background jobs (Postgres is the source of truth)

- **Status:** Accepted (2026-10-03)
- **Date:** 2026-10-02
- **Deciders:** Project owner

## Context
Scans call many external APIs and must not run in web requests. Scheduled scans, outbox dispatch and recovery need periodic triggers. Jobs must be idempotent, and no scan may be lost or run twice, even if the broker loses a message or a worker crashes.

## Decision

### Principle
**Postgres holds all job state; a Celery message is only a nudge.** Every task re-reads its state from Postgres, claims it with a compare-and-set, and exits without doing anything if the claim fails. A periodic sweep re-nudges anything stuck.

### Tooling
- **Celery 5**, Redis broker (ADR-0004), **Beat** for periodic tasks, result backend disabled, `acks_late=True`, `task_reject_on_worker_lost=True`.
- Queues: `scans`, `outbox`, `maintenance`, consumed by two worker services: `worker` (`scans`, `maintenance`) and `worker-outbox` (`outbox` only, so email delivery is never stuck behind a scan; ADR-0006 amendment). Every task has a hard time limit; the broker `visibility_timeout` is set **above the largest time limit** (scan limit 15 min → visibility timeout 30 min).
- Services enqueue through the **`JobQueue` port**. Enqueues are registered with the unit of work and sent **only after commit**.
- Errors are classified as retryable (network, 429, 5xx) or permanent (validation, other 4xx). **Retries happen per external call inside the task** (tenacity, capped exponential backoff + jitter). **Scan tasks never use Celery `retry`**: a re-run would find the scan `running`, fail the claim and exit, leaving it for the sweep. A scan task that can't finish records `partial`/`failed` itself.

### Scans
- **Dispatch** (`dispatch_due_scans`, Beat every 5 min): for each non-archived brand with an interval, compute `scheduled_for` (start of the current slot in the brand's schedule) and `INSERT … ON CONFLICT DO NOTHING` (`uq_scans_brand_id_scheduled_for`). The insert also respects `uq_scans_brand_id_active`, so a brand with an active scan gets no new one. Enqueue only rows actually inserted, after commit.
- **Manual "Scan now"**: same insert path; a conflict on `uq_scans_brand_id_active` returns "scan in progress".
- **Claim**: `UPDATE scans SET status='running' WHERE id=:id AND status='queued' RETURNING id`, with the transition row in the same transaction. Zero rows → another worker has it or it is finished → exit. A redelivered message for a `running` scan therefore does nothing.
- **One claimed task runs the whole pipeline as stages:** settings → budget/quota check (`running → skipped` if insufficient) → collect → normalise → enrich (inline LLM calls) → score → alerts → finish. Every finishing transition is a compare-and-set on `status = 'running'`, so it can't collide with the sweep. If the LLM fails, the scan finishes `partial` with deterministic scores; pending enrichment is picked up by the brand's **next** scan (there is no separate re-score job). Stages check `brands.archived_at` / `users.deleted_at` before LLM calls and before alerts; if set, the scan finishes `skipped`.
- **Sweep** (`sweep_stuck_work`, Beat every 5 min), ignoring archived brands and deleted users:
  - `queued` scans older than 2 min → re-enqueue (or `skipped` if the brand is archived);
  - `running` scans older than the time limit + margin → `failed` (reason `timed_out`);
  - `pending` outbox rows past `next_attempt_at` → nudge the dispatcher.
- **Other Beat tasks:** `dispatch_due_scans` (5 min, respects each brand's quiet hours in its timezone), `dispatch_outbox` (15 s), `scrub_personal_data` (daily, ADR-0013 retention).

### Outbox
`dispatch_outbox` (Beat every 15 s, plus a post-commit nudge) claims rows with `FOR UPDATE SKIP LOCKED` and holds the lock through send + commit (ADR-0010).

### Context propagation
`request_id` / `scan_id` travel in task headers and are re-bound in the handler (ADR-0012).

## Alternatives considered
- **RQ / Dramatiq / arq** — lighter; Celery chosen for maturity, Beat and familiarity.
- **Procrastinate (Postgres-backed)** — transactional enqueue with no broker; strong alternative, deferred.
- **APScheduler in-process** — pins scheduling to one process.

## Consequences
- Positive: no lost or duplicated scans even with broker loss or worker crashes; scheduling correctness enforced by the database.
- Negative: every handler must be written as claim → work → transition (intended).
- Follow-ups: concurrency tests (two dispatchers → one scan per slot; redelivered claim → no-op; sweep recovers a lost enqueue); runbook `scan-failures-high.md`.
