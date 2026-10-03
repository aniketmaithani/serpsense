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
docker compose run --rm tools serpsense seed-demo you@example.com   # the demo brands, owned by you
docker compose run --rm tools serpsense score-backlog          # score finished scans that missed scoring
docker compose run --rm tools serpsense eval label_mentions    # an eval run; live mode only, spends credits
docker compose logs -f worker                                  # JSON logs (structlog)
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
  ledger; when it runs out, scans go on without labels.

## Backups

Everything worth keeping is in Postgres; Redis can be lost. Back up with:

```bash
docker compose exec -T postgres pg_dump -U serpsense -Fc serpsense > serpsense-$(date +%F).dump
docker compose exec -T postgres pg_restore -U serpsense -d serpsense --clean < serpsense-YYYY-MM-DD.dump
```

Restore into a stopped stack (`docker compose stop web worker worker-outbox beat`), then start it;
`migrate` brings an older dump up to the current schema.

## Account deletion

A user deletes their account from Settings → Account with a fresh code and their address typed
out (ADR-0013). Their brands are archived, sessions ended, pending emails dropped, and their
email, IP and user agent scrubbed from every table in the same transaction.
