# SerpSense

**Reputation intelligence from what Google shows.**

When a customer, investor or candidate looks up a brand, they see a search results page: results, autocomplete suggestions, Google's AI Overview, news, app and map reviews. SerpSense watches those surfaces with [SerpApi](https://serpapi.com), detects emerging reputation crises, and drafts responses backed by evidence.

Built for the **SerpApi India Hackathon 2026**. *Built with SerpApi. Not affiliated with SerpApi, LLC.*

> 🚧 Work in progress. See [`BUILD_PLAN.md`](BUILD_PLAN.md) for the plan and [`docs/adr/`](docs/adr/) for architecture decisions.

## Quick start (local)

Requirements: Docker with Compose v2.

```bash
cp .env.example .env
docker compose run --rm --no-deps tools serpsense gen-secrets >> .env   # adds SECRET_KEY and OUTBOX_ENCRYPTION_KEYS
# add SERPAPI_API_KEY and ANTHROPIC_API_KEY to .env for live mode
docker compose up --build
```

- App: http://localhost:8000 (`/healthz`, `/readyz`)
- Mail inbox (Mailpit): http://localhost:8025

## Development

```bash
uv sync --all-groups
uv run pre-commit install
uv run pytest -m "unit or contract or security or api"
uv run pytest -m integration        # needs Docker (testcontainers)
```

Project rules for humans and agents: [`AGENTS.md`](AGENTS.md).

## License

[MIT](LICENSE)
