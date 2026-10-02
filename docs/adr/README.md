# Architecture Decision Records

Accepted ADRs are binding (see `AGENTS.md`). A change that contradicts one needs a new ADR that supersedes it.

**Lifecycle:** `Proposed` → (human review) → `Accepted` | `Rejected`; later `Superseded by ADR-XXXX`. Only a human changes a status to `Accepted`.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-python-fastapi-server-rendered-ui.md) | Python 3.11, FastAPI (sync services), server-rendered UI (Jinja + HTMX), uv tooling | Accepted |
| [0003](0003-postgresql-system-of-record.md) | PostgreSQL 16 as the system of record | Accepted |
| [0004](0004-redis-for-rebuildable-state.md) | Redis for broker, rate limits and caches only | Accepted |
| [0005](0005-celery-background-jobs.md) | Celery + Beat for background jobs (Postgres is the source of truth) | Accepted |
| [0006](0006-docker-compose-deployment.md) | Docker Compose for local and single-host deployment | Accepted |
| [0007](0007-serpapi-search-provider.md) | SerpApi as the search data provider | Accepted |
| [0008](0008-anthropic-claude-llm-features.md) | Anthropic Claude for LLM features | Accepted |
| [0009](0009-passwordless-email-otp-auth.md) | Passwordless email OTP authentication with server-side sessions | Accepted |
| [0010](0010-transactional-outbox-email.md) | Outbound email through a transactional outbox | Accepted |
| [0011](0011-no-user-supplied-outbound-urls.md) | No user-supplied outbound URLs (webhooks dropped) | Accepted |
| [0012](0012-observability-logs-metrics-runbooks.md) | Observability: structured logs, Prometheus metrics, runbooks | Accepted |
| [0013](0013-account-deletion-and-pii.md) | Account deletion and personal-data handling | Accepted |

Template: [template.md](template.md)
