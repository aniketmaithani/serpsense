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

## Run it on your machine

No Docker needed: one script runs a private Postgres and Redis, applies the migrations, and
starts the web app, both Celery workers and beat in one terminal.

**Requirements:** [uv](https://docs.astral.sh/uv/), PostgreSQL 16+ and Redis on your `PATH`, and
optionally Mailpit to catch emails. On macOS:

```bash
brew install uv postgresql@17 redis mailpit
export PATH="$(brew --prefix postgresql@17)/bin:$PATH"   # postgresql@17 is keg-only
```

**First run:**

```bash
git clone https://github.com/aniketmaithani/serpsense && cd serpsense
cp .env.example .env
uv run serpsense gen-secrets >> .env          # SECRET_KEY and OUTBOX_ENCRYPTION_KEYS
```

Then choose a mode in `.env`:

- **Replay** (no keys, spends nothing): add `SERPSENSE_MODE=replay`. Recorded, redacted scans of
  Ola, Uber, Rapido, Namma Yatri and inDrive go through the same pipeline as live mode.
- **Live**: set `SERPAPI_API_KEY` and `ANTHROPIC_API_KEY` (`SERPSENSE_MODE=live` is the default).

**Start it:**

```bash
scripts/dev.sh                                         # app on http://127.0.0.1:8000
scripts/dev.sh cli seed-demo --owner you@example.com   # optional, in a second terminal: the Ola demo
```

In live mode `seed-demo` puts the demo brands on a schedule, so they make real (budgeted)
searches; in replay mode they are scanned on request only.

Open http://127.0.0.1:8000, sign in with your email and enter the code you're sent. With Mailpit
the code is at http://127.0.0.1:8026; without it, the code prints in the `[outbox]` log lines.
`scripts/dev.sh --real-mail` sends real email with the `SMTP_*` settings in `.env` instead.

| Command | What it does |
|---|---|
| `scripts/dev.sh` | Start everything; Ctrl-C stops the app (data stays in `.dev/`) |
| `scripts/dev.sh --real-mail` | The same, sending email through the SMTP settings in `.env` |
| `scripts/dev.sh cli <command>` | Run a `serpsense` command against it, e.g. `cli seed-demo --owner you@example.com` |
| `scripts/dev.sh test [pytest args]` | Run the tests on a throwaway database and Redis, removed afterwards |
| `scripts/dev.sh stop` | Stop leftover app processes and the private Postgres, Redis and Mailpit |

It uses its own ports, so a Postgres or Redis you already run is left alone: Postgres 5434, Redis
6381, the app 8000, Mailpit 8026 (SMTP 1026). `DEV_PG_PORT`, `DEV_REDIS_PORT` and `DEV_WEB_PORT`
change them. After pulling new code, restart `scripts/dev.sh`: it applies any new migrations.

## Run it with Docker Compose

Requirements: Docker with Compose v2.

```bash
git clone https://github.com/aniketmaithani/serpsense && cd serpsense
cp .env.example .env
docker compose run --rm --no-deps tools serpsense gen-secrets >> .env
echo "SERPSENSE_MODE=replay" >> .env                 # or set the two API keys for live mode
docker compose up --build -d
docker compose run --rm tools serpsense seed-demo --owner you@example.com
```

Open http://localhost:8000; sign-in codes and alert emails arrive in Mailpit at
http://localhost:8025.

The hosted instance at https://serpsense.ai is invite-only. To run your own on one host with
TLS, see [`docs/operations.md`](docs/operations.md#production).

## Live mode and spend

Live mode calls SerpApi and the Anthropic API. Budgets keep spend bounded: a monthly search budget
per user (`DEFAULT_MONTHLY_SEARCH_BUDGET`), a global daily cap (`SERPAPI_DAILY_GLOBAL_CAP`), a
per-scan limit (`MAX_SEARCHES_PER_SCAN`), each scan's own estimate, and a monthly LLM budget per
user (`DEFAULT_MONTHLY_LLM_BUDGET_MICROS`). A brand's settings page shows what its scans will cost
before you save. In replay mode recorded answers count as served from SerpApi's cache, and
recorded labels stand in for the model.

## Using it

1. **Add a brand** (home page → *Add a brand*): its name, an optional Google Play app id, up to
   four competitors (each tracked as a brand of its own), a preset (Lean, Standard, Deep) and a
   schedule, or manual scans only.
2. **Scan now** on the brand's page. A scan collects the brand's enabled surfaces, labels the
   mentions, groups complaints into stories, scores the brand and raises any alerts.
3. **Read the brand page:** health and crisis over time; **how the crisis is picked up** (each
   signal's value, weight and contribution, the level cut-offs, warm-up progress and alert rules);
   the stories found; what people see, ten mentions to a page; surfaces, alerts and competitors.
4. **Tune it:**
   - *Search settings*, in sections: surfaces, search templates and result pages, autocomplete
     prefixes, news terms, Trends region and range, review sort and pages, languages, country and
     Google domain, SerpApi cache, searches per scan and schedule, with a cost preview.
   - *Crisis tuning*: warm-up scans, where medium and high start, the alert cooldown, and when a
     story counts as spreading.
   - *AI settings*: the model and effort for each task.
5. **Draft a reply:** press *Draft a reply* on a story or a negative mention, or open a story for a
   holding statement or FAQ entry. Drafts cite the mentions they answer, are labelled
   AI-generated, and are listed on the *Drafts* page with a copy button. Nothing is ever sent for
   you.
6. **Notifications:** alerts arrive by email and in the app; in live mode each gets a plain-words
   explanation, labelled AI-generated.

## How SerpApi is used

| Surface | Engine | Why |
|---|---|---|
| Search results page | `google` | What most people see first: results, People also ask, top stories |
| AI Overview | `google_ai_overview` | Google's own summary of the brand; fetched by page token when the results page only links to it |
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
- **Crisis tuning.** Per brand: the warm-up, the medium and high cut-offs, the alert cooldown and
  the spreading rule. The signals and the score stay the same; the brand page shows how each scan's
  crisis was picked up.
- **Stories.** Negative mentions are grouped into narratives; a story reaching 5 mentions on 2
  surfaces (tunable) raises an alert.
- **Alerts.** Deterministic rules (crisis level rises, a new negative suggestion, a story
  spreads), with cooldowns, sent by email through a transactional outbox and shown in the app.
  The model only writes the explanation.
- **Drafts.** A reply, holding statement or FAQ entry drafted from a story's evidence, citing the
  mentions it answers; a person reads, edits and copies it.
- **Settings.** Every search knob per brand (surfaces, templates, pages, languages, Trends,
  reviews, schedule), presets (Lean, Standard, Deep) and a live cost estimate; model settings per
  task.

## Architecture

Python 3.11, FastAPI (server-rendered, no inline scripts), PostgreSQL 16 as the system of record,
Redis for rebuildable state, Celery for jobs, Docker Compose on one host. Layers: entrypoints →
services → domain, with ports and adapters for every external system (SerpApi, Claude, SMTP,
Redis, crypto), enforced by import-linter in CI. Decisions are recorded as ADRs in
[`docs/adr/`](docs/adr/); the data model is [`docs/architecture/data-model.md`](docs/architecture/data-model.md);
running it is [`docs/operations.md`](docs/operations.md).

## Quality

- **Tests:** `pytest` with `unit`, `integration` (real Postgres: a throwaway database through
  `scripts/dev.sh test`, or testcontainers in CI), `api`, `security` and `contract` markers; no network in tests; concurrency tests on two real
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

- Google Maps and YouTube can be configured in settings but are not collected yet.
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
uv run ruff format --check . && uv run ruff check . && uv run mypy src && uv run lint-imports
scripts/dev.sh test -m "unit or contract or security"   # some security tests need the database
scripts/dev.sh test -m "integration or api"
```

`scripts/dev.sh test` needs no Docker: each run gets its own `serpsense_test_*` database and Redis,
removed when it ends. With Docker running, plain `uv run pytest` uses testcontainers instead, as
CI does.

Project rules for humans and agents: [`AGENTS.md`](AGENTS.md). Plan: [`BUILD_PLAN.md`](BUILD_PLAN.md).

## License

[MIT](LICENSE)
