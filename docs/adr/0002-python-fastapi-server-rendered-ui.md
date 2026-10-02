# ADR-0002: Python 3.11, FastAPI, server-rendered UI (Jinja + HTMX), uv tooling

- **Status:** Accepted (2026-10-03)
- **Date:** 2026-10-02
- **Deciders:** Project owner

## Context
8-day hackathon build, judged partly on technical complexity and on working software; judges are Python developer advocates. We need real cookie sessions for OTP login (rules out Streamlit), charts, forms, and minimal front-end build tooling.

## Decision
- **Python 3.11**, **FastAPI** (ASGI, Pydantic v2 validation, dependency-injected auth), served by **uvicorn**.
- **Server-rendered HTML** with **Jinja2**; interactivity via **HTMX**; charts via **Chart.js**. Both JS files are vendored into `static/` (no CDN at runtime, CSP `script-src 'self'`).
- **Synchronous services everywhere.** Celery, the `serpapi` SDK, SQLAlchemy sessions and `smtplib` are synchronous, and services are shared by web and worker. So: sync SQLAlchemy sessions, **FastAPI `def` routes** (run in the threadpool), sync Anthropic client, `smtplib`. No `async def` in services or adapters. Parallel SerpApi calls inside a scan use a bounded `ThreadPoolExecutor`. API tests use FastAPI's `TestClient`.
- **Typer** for the CLI.
- **uv** for dependency management and running tools; dependencies pinned in `uv.lock`.
- Quality tools: ruff (lint + format), mypy (strict on `src/`), import-linter, pytest.

## Alternatives considered
- **Streamlit** — fastest dashboards, but no proper cookie sessions/CSRF; poor fit for OTP auth.
- **React/Next.js SPA + API** — second language and build chain; too much for 8 days.
- **Django** — strong batteries; FastAPI + SQLAlchemy chosen for typed Pydantic boundaries shared by web, worker and CLI, and lighter routing for an HTMX UI.
- **Poetry / pip-tools** — uv is faster and gives one tool for venv, lock and run.

## Consequences
- Positive: one language, no JS build, fast iteration, typed boundaries.
- Negative: HTMX UIs need discipline to avoid inline scripts (CSP); fewer ready-made components.
- Follow-ups: CSP and security-header middleware; vendored asset versions recorded in `static/VENDORED.md`.
