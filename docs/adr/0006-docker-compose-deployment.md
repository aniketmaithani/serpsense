# ADR-0006: Docker Compose for local and single-host deployment

- **Status:** Accepted (2026-10-03)
- **Date:** 2026-10-02
- **Deciders:** Project owner

## Context
Judges must run the project locally from a fresh clone; the demo must show it running locally. The stack has several processes (web, worker, beat) and services (Postgres, Redis, a mail catcher).

## Decision
- One multi-stage `Dockerfile` (non-root user, pinned base image, no secrets baked in) used by `web`, `worker`, `beat`.
- `docker-compose.yml` services: `migrate` (one-off `alembic upgrade head`), `web`, `worker`, `beat`, `postgres`, `redis`, `mailpit` (dev mail catcher, web inbox on `:8025`). Health checks on every long-running service; `web`, `worker` and `beat` start only after `migrate` completes successfully and DB/Redis are healthy.
- Configuration only via environment (`.env`, never committed; `.env.example` lists names).
- **Amendment (2026-10-03):** a dedicated `worker-outbox` service consumes only the `outbox` queue, and `worker` consumes `scans` and `maintenance`. Long scans therefore can't delay OTP or alert emails. Each worker's healthcheck pings its own node.
- `docker-compose.prod.yml` (P2) swaps Mailpit for a real SMTP provider and adds a TLS reverse proxy; single host only.
- **Amendment (2026-10-04):** `docker-compose.prod.yml` uses Caddy as the TLS proxy (automatic Let's Encrypt certificates, no access log), and the web app trusts forwarded headers only from the network it shares with Caddy. Host setup, deploys and backups: `docs/operations.md#production`.

## Alternatives considered
- **Bare-metal virtualenv + local services** — judges would need to install Postgres/Redis by hand.
- **Kubernetes / managed PaaS** — out of scope (AGENTS.md §1).

## Consequences
- Positive: `docker compose up` is the whole setup; same images in CI and locally.
- Negative: Docker required for judges (documented; replay mode still needs Compose for Postgres).
- Follow-ups: fresh-clone test on Day 7; `docs/operations.md`.
