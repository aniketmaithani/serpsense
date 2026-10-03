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

## Production

One host runs the whole stack from [`docker-compose.prod.yml`](../docker-compose.prod.yml):
the services above without Mailpit, plus **Caddy** in front, which gets and renews the Let's
Encrypt certificate and redirects `www` to the apex. Only Caddy publishes ports (80, 443). The
app runs with `APP_ENV=production`, so it refuses unsafe settings at start (replay mode, the
console mailer, open sign-up, a non-https `BASE_URL`, the development database password).
The web app trusts `X-Forwarded-For` only from the network it shares with Caddy, so per-IP rate
limits see client addresses. Caddy keeps no access log.

serpsense.ai runs on a DigitalOcean droplet (Ubuntu 24.04, 2 vCPU / 4 GB, `blr1`, tagged
`serpsense`) behind the `serpsense-web` cloud firewall (SSH, HTTP, HTTPS in). DNS is at GoDaddy:
an `A` record for `@` pointing at the droplet and `www` as a `CNAME` to `@`; the Brevo DKIM and
DMARC records sit alongside.

### First setup

1. Create an Ubuntu 24.04 droplet with your SSH key, and point the domain's `A` record at it.
2. Provision it as root: `ssh root@HOST 'bash -s' < deploy/provision.sh`. This installs Docker,
   2 GB of swap, ufw, fail2ban and unattended upgrades, allows SSH by key only, creates a `deploy`
   user (in the `docker` group, no sudo) and the nightly backup.
3. Write `/opt/serpsense/.env` (owner `deploy`, mode 600). It needs, beyond the keys:
   `APP_ENV=production`, `DOMAIN`, `BASE_URL=https://DOMAIN`, `POSTGRES_PASSWORD`, fresh
   `SECRET_KEY` and `OUTBOX_ENCRYPTION_KEYS` (`uv run serpsense gen-secrets`),
   `SIGNUP_MODE=invite` with `ALLOWED_EMAILS` or `ALLOWED_DOMAINS`, and SMTP with
   `SMTP_STARTTLS=true`. Copy only what the app reads; tokens for DigitalOcean or GoDaddy never
   go on the host.
4. Deploy: `scripts/deploy.sh deploy@DOMAIN`.

DigitalOcean blocks outbound SMTP on ports 25, 465 and 587, so `SMTP_PORT` is **2525** (Brevo
offers STARTTLS there). A blocked port shows up as `outbox.send_failed` with
`error_code=smtp.timeout`; the outbox retries the message once the port is fixed.

### Deploys and rollback

```bash
scripts/deploy.sh deploy@serpsense.ai            # ship HEAD: rsync, rebuild, migrate, restart
scripts/deploy.sh deploy@serpsense.ai <older-sha> # roll back
ssh deploy@serpsense.ai cat /opt/serpsense/REVISION   # what is running
```

Only committed files ship; the host's `.env` is never touched. Migrations only go forward: to
roll back past one, run `alembic downgrade` with the newer code first, or restore a backup.

On the host, every command from [Commands](#commands) works with
`docker compose -f docker-compose.prod.yml` in `/opt/serpsense`. To let someone sign in, add
their address to `ALLOWED_EMAILS` in `.env` and run
`docker compose -f docker-compose.prod.yml up -d`.

### Production backups

`deploy/backup.sh` runs from cron at 02:30 UTC as `deploy`, writes
`/var/backups/serpsense/serpsense-<UTC time>.dump` and keeps 14 days (log:
`/var/backups/serpsense/backup.log`). Run it by hand before anything risky. The dumps are on the
droplet's own disk, so a lost droplet loses them: enable DigitalOcean droplet backups, or copy
dumps off the host. Restore as in [Backups](#backups), with
`docker compose -f docker-compose.prod.yml` and the `.dump` file from that directory.
