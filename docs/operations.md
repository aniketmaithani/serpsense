# Operations

How SerpSense runs on one host with Docker Compose (ADR-0006), how to tell it's healthy, and what
to do when it isn't. Alert rules and their status are in
[`observability/alerts.md`](observability/alerts.md), dashboard queries in
[`observability/dashboards.md`](observability/dashboards.md), runbooks in [`runbooks/`](runbooks/).

## Services

| Service | Runs | Health check |
|---|---|---|
| `postgres` | PostgreSQL 16, the system of record (ADR-0003) | `pg_isready` |
| `redis` | Celery broker, response cache and rate limits; everything in it can be rebuilt (ADR-0004) | `redis-cli ping` |
| `mailpit` | Catches every email locally; inbox at http://localhost:8025 | `/mailpit readyz` |
| `migrate` | `alembic upgrade head`, once, before anything else starts | exits 0 |
| `web` | FastAPI, server-rendered pages, `/healthz` and `/readyz` | `GET /healthz` |
| `worker` | Celery queues `scans` and `maintenance`: scans, the dispatcher, the sweep | `celery inspect ping` |
| `worker-outbox` | Celery queue `outbox`: sends emails (ADR-0010) | `celery inspect ping` |
| `beat` | The schedule below | its schedule file was written in the last 3 minutes |
| `tools` | One-off commands: `docker compose run --rm tools serpsense …` | – |

`/healthz` says the process is up; `/readyz` also checks Postgres and Redis and answers 503,
naming the failed dependency in the log (`health.not_ready`), when one is down.

## Schedule (beat)

| Every | Task | What it does |
|---|---|---|
| 15 s | `dispatch_outbox` | Sends due emails; a run that waited past the next one is dropped. Emails are also nudged right after the commit that wrote them. |
| 1 min | `heartbeat` | Proves beat → broker → worker is flowing. |
| 5 min | `dispatch_due_scans` | Queues a scan for each brand whose slot is due (one per brand per slot, never during quiet hours). |
| 5 min | `sweep_stuck_work` | Re-sends scans whose job was lost, and times out scans stuck past their limit (Postgres is the source of truth; Celery messages are nudges, ADR-0005). |

## Commands

```bash
docker compose run --rm tools serpsense gen-secrets           # SECRET_KEY and OUTBOX_ENCRYPTION_KEYS
docker compose run --rm tools serpsense seed-demo --owner you@example.com   # the demo brands; in replay mode, plays the recordings back
docker compose run --rm tools serpsense score-backlog          # score finished scans that missed scoring
docker compose run --rm tools serpsense eval label_mentions    # an eval run; live mode only, spends credits
docker compose logs -f worker                                  # JSON logs (structlog)
```

### Without Docker (a development machine)

`scripts/dev.sh` runs the same processes natively: a private Postgres (port 5434) and Redis
(6381) with their data in `.dev/`, the web app, both workers and beat, their log lines prefixed
`[web]`, `[worker]`, `[outbox]` and `[beat]` in one terminal. The commands above become:

```bash
uv run serpsense gen-secrets >> .env              # once: SECRET_KEY and OUTBOX_ENCRYPTION_KEYS
scripts/dev.sh cli seed-demo --owner you@example.com
scripts/dev.sh cli score-backlog
scripts/dev.sh test -m "integration or api"      # tests on a throwaway database and Redis
pg_dump -h 127.0.0.1 -p 5434 -U serpsense -Fc serpsense > serpsense-$(date +%F).dump
scripts/dev.sh stop                              # stop the app and the private services
```

## Logs

Every process logs JSON lines through `observability.get_logger()`, with `request_id`, `user_id`,
`scan_id` and the job attempt bound as context. Event names are `noun.verb_past`
(`scan.completed`, `serp_call.failed`, `otp.verify_failed`). Logs never hold API keys, sign-in
codes, session tokens, email addresses, prompts, completions or full provider payloads; known
secret values are scrubbed from every line as a last guard.

## Secrets and their rotation

All configuration is read in `config.py` from the environment; `.env.example` names every
variable and holds no values.

- **`SECRET_KEY`** derives separate keys (HKDF) for sign-in codes, CSRF tokens and rate-limit
  keys. Rotating it signs everyone out and invalidates codes in flight.
- **`OUTBOX_ENCRYPTION_KEYS`** is a comma-separated list of Fernet keys: the first encrypts, all
  decrypt. To rotate, put a new key first, keep the old one until pending emails have gone (a
  few minutes), then drop it.
- **`SERPAPI_API_KEY`, `ANTHROPIC_API_KEY`**: live mode only; replay mode needs neither.

## Budgets and spend

- SerpApi: each user's monthly search budget (`DEFAULT_MONTHLY_SEARCH_BUDGET` unless set), a
  global daily cap (`SERPAPI_DAILY_GLOBAL_CAP`) and each scan's own estimate, all counted from the
  `serp_calls` ledger ([`serpapi-usage.md`](serpapi-usage.md)).
- Claude: each user's monthly LLM budget in micros of a currency, counted from the `llm_calls`
  ledger; when it runs out, scans go on without labels. Every user's calls together stop for the
  rest of the UTC day at `LLM_DAILY_GLOBAL_CAP_MICROS` (default 5,000,000 micros = US$5).

## Backups

Everything worth keeping is in Postgres; Redis can be lost. Back up with:

```bash
docker compose exec -T postgres pg_dump -U serpsense -Fc serpsense > serpsense-$(date +%F).dump
docker compose exec -T postgres pg_restore -U serpsense -d serpsense --clean < serpsense-YYYY-MM-DD.dump
```

Restore into a stopped stack (`docker compose stop web worker worker-outbox beat`), then start it;
`migrate` brings an older dump up to the current schema.

## Operator console

Set `ADMIN_PASSWORD` (16 characters or more) and restart the web app to turn on the console at
`/admin` (ADR-0014); without it every console route answers 404. It shows how many users,
brands and scans there are, every brand with its owner, and the access requests: in invite
mode, an address that isn't invited and asks for a code leaves one. **Approve** lets that address
sign in (it asks for a code again; nothing is emailed), **Reject** keeps it out, and a later
decision replaces an earlier one. The invite lists (`ALLOWED_EMAILS`, `ALLOWED_DOMAINS`) still
work alongside; rejecting doesn't end sessions already open. **Sign-up** switches between
*approval required* (the above) and *open to everyone*, where anyone with an email address gets an
account straight away and no requests are left (ADR-0015). People who sign up while it's open are
approved as they do, so closing it again stops newcomers only; reject someone to stop them. Each
switch is kept with its time; with none, `SIGNUP_MODE` from the environment applies, which
production requires to be `invite`. Switches count only while the console is on: to close sign-up
without the console, remove `ADMIN_PASSWORD` and restart. While sign-up is open every new account
gets the default monthly search and LLM budgets, inside the daily caps across all users
(`SERPAPI_DAILY_GLOBAL_CAP`, `LLM_DAILY_GLOBAL_CAP_MICROS`). A console session lasts 4 hours;
**Sign out** ends every console session (copies of the cookie included), and so does changing the
password or `SECRET_KEY`. Thirty failed logins in an hour, from anywhere, lock the console for
the rest of that hour, the right password included: if that happens, wait it out, then change
the password.

## Account deletion

A user deletes their account from Settings → Account with a fresh code and their address typed
out (ADR-0013). Their brands are archived, sessions ended, pending emails dropped, and their
email, IP and user agent scrubbed from every table in the same transaction.
