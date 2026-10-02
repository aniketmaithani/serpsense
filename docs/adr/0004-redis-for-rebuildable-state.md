# ADR-0004: Redis for broker, rate limits and caches only

- **Status:** Accepted (2026-10-03)
- **Date:** 2026-10-02
- **Deciders:** Project owner

## Context
Celery needs a broker. Per-IP OTP throttling and Preview throttling need fast counters. Collectors benefit from a short-lived response cache, and the SerpApi Account API (quota) should not be called before every scan.

## Decision
Use **Redis 7** for exactly these:
1. **Celery broker** (result backend disabled).
2. **Rate-limit counters:** OTP requests per IP, Preview calls per user.
3. **SerpApi response cache** with per-engine TTLs.
4. **SerpApi Account API quota cache** (10 min).

**Postgres is the source of truth; Redis is rebuildable.** Losing Redis loses cache entries, counters **and in-flight broker messages**. Lost messages are recovered because every job is a *nudge* for state already in Postgres: the maintenance sweep (ADR-0005) re-enqueues stale `queued` scans, pending enrichment and pending outbox rows.

Not in Redis: OTP codes, attempt counts, per-email OTP limits, sessions, budgets, scan state, circuit-breaker state (derived from `serp_calls`), locks (one active scan per brand is a Postgres unique index).

## Alternatives considered
- **Postgres-only (Postgres job queue, rate-limit tables)** — fewer moving parts; Celery chosen for familiarity (ADR-0005). Revisit if Redis becomes an operational burden.
- **In-process memory** — breaks with more than one process.

## Consequences
- Positive: fast broker and counters; a Redis loss costs only cache and a short delay in job pickup.
- Negative: an extra container; per-IP limits reset on Redis restart (accepted: per-email and per-code limits are in Postgres).
- Follow-ups: runbook `redis-unavailable.md` (P1); `/readyz` checks Redis.
