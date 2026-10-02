# ADR-0012: Observability: structured logs, Prometheus metrics, runbooks

- **Status:** Accepted (2026-10-03)
- **Date:** 2026-10-02
- **Deciders:** Project owner

## Context
When a scan fails at 2 a.m., the on-call person must see what broke, for whom, and what to do — without reading code. We call three external services (SerpApi, Claude, SMTP) and run background jobs.

## Decision
- **Logs:** `structlog`, JSON in production, console-pretty in development. Shared logger from `observability.get_logger()`. Event names `noun.verb_past`. Context bound via contextvars: `request_id`, `user_id`, `scan_id`, `job_name`, `attempt`. A processor drops forbidden keys (api keys, codes, tokens, emails, prompts, completions, payloads).
- **Metrics:** `prometheus-client`, exposed at `/metrics` on the web process. Labels are low-cardinality (engine, task, model, status, channel) — never user ids. Job-level signals (scan outcomes, SerpApi/LLM call outcomes, outbox backlog, queue depth) are computed **from Postgres ledgers and Redis queue length at scrape time** by the web process, so no multi-process worker metrics are needed. Worker-side metrics are deferred.
- **Health:** `/healthz` (process up), `/readyz` (Postgres + Redis reachable).
- **Alerts / dashboards:** documented as PromQL in `docs/observability/alerts.md` and `docs/observability/dashboards.md` (ready for any Prometheus/Grafana); no Grafana container in the hackathon stack.
- **Runbooks:** `docs/runbooks/<slug>.md` for every alert (meaning, confirm, likely causes, fix/mitigate, escalate).
- **No APM vendor** (Sentry, Datadog, …) for now; tracing deferred. A future ADR may add OpenTelemetry.

## Alternatives considered
- **Sentry** — great error grouping, but another third party and data-processing review; deferred.
- **OpenTelemetry tracing now** — valuable, but collector setup costs a day we don't have.

## Consequences
- Positive: actionable signals with no external services.
- Negative: no hosted error aggregation; trace correlation via `request_id`/`scan_id` only.
- Follow-ups: **P0 runbooks** — `scan-failures-high`, `serpapi-errors-high`, `llm-errors-high`, `outbox-backlog`. **P1 runbooks** — `serpapi-quota-low`, `llm-budget-exhausted`, `queue-backlog`, `otp-abuse`, `redis-unavailable`, `postgres-unavailable`. Each P1 failure mode has a written "alert deferred" note until its runbook lands.
