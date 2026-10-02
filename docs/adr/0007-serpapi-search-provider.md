# ADR-0007: SerpApi as the search data provider

- **Status:** Accepted (2026-10-03)
- **Date:** 2026-10-02
- **Deciders:** Project owner

## Context
The product's core insight is that a brand's reputation is what search surfaces show. The hackathon requires meaningful SerpApi usage. We use a paid SerpApi key; the rules disqualify entries that expose API keys.

## Decision
- **SerpApi** (official `serpapi` Python SDK) is the only search data source. Engines: `google`, `google_ai_overview`, `google_autocomplete`, `google_news`, `google_trends`, `google_play_product`, `google_maps`, `google_maps_reviews`, `youtube`. Free auxiliary APIs: Account API (quota), Locations API (location picker).
- The SDK is imported only in `adapters/serp/` behind the `SearchProvider` port; services never see raw payloads.
- Every call goes through one client that applies: settings resolution, Redis cache (per-engine TTL), SerpApi's own 1-hour cache unless `no_cache`, timeout (30 s), retries (429/5xx/network, exponential backoff + jitter), a per-engine circuit breaker **derived from the last 5 `serp_calls` outcomes for that engine within 15 minutes** (no separate state), per-scan and per-user search budgets, an append-only `serp_calls` ledger row, and **payload redaction** before storage.
- Competitors are independent brands with their own schedules and settings; each brand's scan costs its own searches, and the estimator and budgets count them.
- **Google Trends uses one joint query per scan** (brand + up to 4 competitors) so interest values are comparable (share of search); related queries are fetched for the scan's own brand only.
- **Redaction:** remove `search_metadata` URLs, any field whose value contains `api_key=`, and any string matching the configured key; **also strip reviewer/author identity** (names, profile links, avatars, user ids in review and video payloads) before storing `raw_responses` or writing fixtures. Security tests assert the key and reviewer identities never reach the DB, logs or committed fixtures.
- Outbound host allow-list: `serpapi.com` only.
- Integration contracts are documented from real, recorded, redacted responses (`tests/fixtures/serpapi/`), never guessed.

## Alternatives considered
- **Scraping Google directly** — against Google's terms, brittle, and defeats the hackathon's purpose.
- **Other SERP APIs** — not eligible for the hackathon.

## Consequences
- Positive: one consistent, structured source for all surfaces.
- Negative: cost per search; some surfaces (AI Overview) are absent for many queries — handled as "not shown".
- Follow-ups: runbooks `serpapi-errors-high.md`, `serpapi-quota-low.md`; `docs/serpapi-usage.md` for judges.
