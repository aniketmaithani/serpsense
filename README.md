# SerpSense

[![CI](https://github.com/aniketmaithani/serpsense/actions/workflows/ci.yml/badge.svg)](https://github.com/aniketmaithani/serpsense/actions/workflows/ci.yml)

**Your reputation is what Google shows people.** SerpSense watches it.

When a customer, investor or candidate looks a brand up, they see a results page, autocomplete
suggestions, news, Google Trends and app reviews before they see anything the brand wrote.
SerpSense collects those surfaces with [SerpApi](https://serpapi.com) on a schedule, labels what
it finds with Claude, scores the brand's **health** and how fast it is turning bad (**crisis**),
groups complaints into **stories**, alerts by email and in the app when a crisis starts or a story
spreads, and drafts a response a human can copy, citing the mentions it used.

Built for the **SerpApi India Hackathon 2026**. *Built with SerpApi. Not affiliated with SerpApi,
LLC.*

## Quick start: replay mode (no API keys)

Requirements: Docker with Compose v2. About five minutes.

```bash
git clone https://github.com/aniketmaithani/serpsense && cd serpsense
cp .env.example .env
docker compose run --rm --no-deps tools serpsense gen-secrets >> .env   # SECRET_KEY, OUTBOX_ENCRYPTION_KEYS
echo "SERPSENSE_MODE=replay" >> .env
docker compose up --build -d
docker compose run --rm tools serpsense seed-demo --owner you@example.com   # Ola and four competitors, owned by you
```

1. `seed-demo` plays every recorded scan back in order, at the time it was recorded: recorded,
   redacted answers from real Ola, Uber, Rapido, Namma Yatri and inDrive scans go through the same
   pipeline as live mode (collect, label, group into stories, score, alert, email).
2. Open http://localhost:8000 and sign in with `you@example.com`; the sign-in code (and any alert
   emails) arrive in Mailpit at http://localhost:8025, so every email stays local.
3. Your brands appear with their scores, trends, stories and alerts. In replay mode the brands are
   scanned on request only: "Scan now" repeats the latest recorded answers.

Replay mode needs neither a SerpApi nor an Anthropic key and spends nothing: recorded answers are
counted as served from SerpApi's cache, and recorded labels stand in for the model.

## Live mode

Add `SERPAPI_API_KEY` and `ANTHROPIC_API_KEY` to `.env`, set `SERPSENSE_MODE=live` (the default),
and `docker compose up --build -d`. Budgets keep spend bounded: a monthly search budget per user
(`DEFAULT_MONTHLY_SEARCH_BUDGET`), a global daily cap (`SERPAPI_DAILY_GLOBAL_CAP`), each scan's own
estimate, and a monthly LLM budget per user. The settings page shows what a brand's scans will
cost before you save them.

## Run it without Docker

With Postgres and Redis installed (`brew install postgresql@17 redis`, and optionally
`brew install mailpit`), one command starts everything natively:

```bash
cp .env.example .env && uv run serpsense gen-secrets >> .env   # once
scripts/dev.sh                                                  # app on http://127.0.0.1:8000
scripts/dev.sh cli seed-demo --owner you@example.com            # in a second terminal
scripts/dev.sh test -m "integration or api"                     # tests on a throwaway database, no Docker
scripts/dev.sh stop                                             # stop the private Postgres and Redis
```

It runs its own Postgres (port 5434) and Redis (6381) with their data in `.dev/`, so a Postgres
or Redis you already run is left alone; applies the migrations; and runs the web app, both
Celery workers and beat in one terminal (Ctrl-C stops them). Mail goes to Mailpit at
http://127.0.0.1:8026, or without Mailpit the sign-in codes print in the `[outbox]` log lines;
`scripts/dev.sh --real-mail` uses the SMTP settings in `.env` instead.

## How SerpApi is used

| Surface | Engine | Why |
|---|---|---|
| Search results page | `google` | What most people see first: results, People also ask, top stories |
| Autocomplete | `google_autocomplete` | A negative suggestion is seen before any result |
| News | `google_news` | Where a crisis usually starts |
| Google Trends | `google_trends` | One joint query with competitors (share of search) and the brand's related queries |
| Google Play | `google_play_product` | App rating and the newest reviews |

Every call goes through one service: a local cache, a per-engine circuit breaker derived from the
call ledger, per-scan, per-user and global budgets, retries with backoff, an append-only ledger
row for every attempt, and redaction (no API key, no reviewer identity) before anything is
stored. Details, settings and the searches each scan costs: [`docs/serpapi-usage.md`](docs/serpapi-usage.md).

## What it does

- **Brands and competitors.** Each competitor is a full brand with its own schedule and settings,
  compared on the brand's page.
- **Labels.** Claude labels each mention: is it about the brand (not the Ola greeting, not Ola
  Electric), sentiment, topic, complaint, severity, and a one-line reason. Labels are shown as
  AI-generated and carry their prompt version.
- **Scores.** Health is a weighted mean of the surfaces; crisis measures change against the
  brand's own usual (velocity, spread, new negative autocomplete, rising Trends queries, fresh
  press), with a warm-up so a new brand isn't alarmed by its own baseline. Method:
  [`docs/scoring.md`](docs/scoring.md).
- **Stories.** Negative mentions are grouped into narratives; a story reaching 5 mentions on 2
  surfaces raises an alert.
- **Alerts.** Deterministic rules (crisis level rises, a new negative suggestion, a story
  spreads), with cooldowns, sent by email through a transactional outbox and shown in the app.
  The model only writes the explanation.
- **Settings.** Surfaces, templates, languages, review pages, schedule, presets (Lean, Standard,
  Deep) and a live cost estimate; model settings per task.

## Architecture

Python 3.11, FastAPI (server-rendered, no inline scripts), PostgreSQL 16 as the system of record,
Redis for rebuildable state, Celery for jobs, Docker Compose on one host. Layers: entrypoints →
services → domain, with ports and adapters for every external system (SerpApi, Claude, SMTP,
Redis, crypto), enforced by import-linter in CI. Decisions are recorded as ADRs in
[`docs/adr/`](docs/adr/); the data model is [`docs/architecture/data-model.md`](docs/architecture/data-model.md);
running it is [`docs/operations.md`](docs/operations.md).

## Quality

- **Tests:** `pytest` with `unit`, `integration` (real Postgres via testcontainers), `api`,
  `security` and `contract` markers; no network in tests; concurrency tests on two real
  connections for scheduling, claims, sign-in limits and the outbox; coverage of changed lines
  ≥ 90% in CI.
- **LLM evals:** every prompt is a versioned file with a golden set and an eval report
  ([`evals/`](evals/)): `serpsense eval label_mentions` agreed with the golden labels on relevance
  for 32/32 items and on sentiment for 22/23.
- **CI:** format, lint, `mypy --strict`, import contracts, migrations up/down with a drift check,
  tests, gitleaks and pip-audit on every PR.

## Security model

Passwordless sign-in with emailed codes (hashed, single use, attempt-limited, rate-limited per
address and source), sessions in HttpOnly cookies (`__Host-` and Secure in production), CSRF on
every write, a strict CSP, one owner check for every read (another user's data is a 404), no
user-supplied outbound URLs, secrets only from the environment, and account deletion that
scrubs email, IP and user agent from every table ([ADR-0009](docs/adr/0009-passwordless-email-otp-auth.md),
[ADR-0013](docs/adr/0013-account-deletion-and-pii.md)).

## Limitations

- Google's AI Overview, Maps and YouTube are budgeted for in settings but not collected yet.
- Golden sets were drafted by an AI agent from real public text and are not yet reviewed by a
  person, so eval baselines are indicative.
- One host, one tenant per user: no teams, SSO, billing or webhooks (out of scope by design).

## AI tools used

Built with Claude Code (Anthropic) as the main engineering agent, with reviewer agents for
architecture, Python, security, tests and prompts. The product itself uses Claude (Opus 5.5 by
default) through the Anthropic API for labelling, grouping, explanations and drafts.

## Development

```bash
uv sync --all-groups
uv run pre-commit install
uv run pytest -m "unit or contract or security"
uv run pytest -m "integration or api"        # needs Docker (testcontainers)
```

Project rules for humans and agents: [`AGENTS.md`](AGENTS.md). Plan: [`BUILD_PLAN.md`](BUILD_PLAN.md).

## License

[MIT](LICENSE)
