# ADR-0003: PostgreSQL 16 as the system of record

- **Status:** Accepted (2026-10-03)
- **Date:** 2026-10-02
- **Deciders:** Project owner

## Context
Web and worker processes share durable state: users, OTPs, sessions, brands, scans, mentions, scores, alerts, an outbox, append-only ledgers. We need transactions, row locking (`FOR UPDATE SKIP LOCKED`), constraints, triggers and good JSON support for raw provider payloads.

## Decision
- **PostgreSQL 16** is the only system of record. **SQLAlchemy 2.0** (typed ORM, `psycopg` 3 driver) and **Alembic** migrations.
- Migrations are reversible and follow expand → backfill → contract. Constraint/index names come from a naming convention.
- Append-only tables get a trigger rejecting UPDATE/DELETE.
- **JSONB is permitted only for:**
  1. raw provider payloads (`raw_responses.payload`, redacted) — stored for replay/re-parsing, never filtered on;
  2. immutable settings snapshots (`scans.settings_snapshot`, `llm_calls.request_settings`) — audit/reproducibility records;
  3. settings documents always read and written whole and validated by Pydantic, stored as **append-only versions** (`*_settings_versions`, `*_profile_versions`);
  4. message/audit payloads read whole by one consumer (`outbox_messages.template_data`, `audit_events.details`).
  Anything queried or filtered (aliases, languages, locations, app ids, watch terms, budgets, statuses) is relational. See `docs/architecture/data-model.md`.
- Timestamps are `timestamptz`, stored in UTC.

## Alternatives considered
- **SQLite** — no concurrent writers across web + workers, no `SKIP LOCKED`, test/prod engine mismatch.
- **MongoDB** — weaker constraints/transactions for ledgers and state machines.

## Consequences
- Positive: one durable store; strong constraints; integration tests on the same engine (testcontainers).
- Negative: an extra container; migrations need care.
- Follow-ups: `docs/architecture/data-model.md` kept in sync with every migration; backup/restore runbook.
