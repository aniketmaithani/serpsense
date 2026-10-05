# How SerpSense uses SerpApi

SerpSense answers one question: *what does someone see when they Google this brand?* Every
answer comes from [SerpApi](https://serpapi.com) (ADR-0007), through one search service that
caches, budgets, retries, records and redacts every call. This page is for judges and operators:
which engines, why, with what settings, and how many searches a scan costs.

## Engines and what each one is for

| Surface | Engine | Request | Why it matters | Searches |
|---|---|---|---|---|
| Search results page | `google` | `q` = each search template (`{brand}`, `{brand} reviews`, …), `gl`, `hl` (the first language), `google_domain`; `start` for pages 2–3 | The first page *is* the brand's reputation for most people: organic results, People also ask, top stories | templates × pages |
| Autocomplete | `google_autocomplete` | `q` = each prefix (`{brand} `, `{brand} is `, `is {brand} `), lower case, in each language | A negative suggestion ("ola is fraud") is seen before any result; it has its own alert rule | prefixes × languages |
| News | `google_news` | `q` = the brand, and the brand with each extra term (`complaint`), in each language | Where a crisis usually starts | languages × (1 + terms) |
| Google Trends | `google_trends` | one **joint** `TIMESERIES` query for the brand and up to four competitors (one scale, so share of search compares), plus the brand's `RELATED_QUERIES`; `geo`, `date` | Spikes in interest, and what people search next to the brand | 1 + 1 |
| Google Play | `google_play_product` | each app's product page (`product_id`, `store=apps`) for the rating, then pages of reviews (`all_reviews`, `sort_by` newest or most relevant, `next_page_token`) | Ratings and the newest reviews move first in an app-led brand | apps × (1 + review pages) |

Planned and already budgeted for (engines in the schema and the estimator, collectors not yet
built): the `google_ai_overview` follow-up when a results page only links to Google's AI
Overview, `google_maps` / `google_maps_reviews` for places, and `youtube`. Until they are built
those surfaces show as not collected, never as a score.

Each mention a collector finds is parsed once at the boundary into a typed `Mention` (source,
text, URL, outlet, position, publication date), keyed so the same result seen in many scans is
one row with many observations.

## Settings and what a scan costs

Settings are layered: system defaults → the owner's defaults → the brand's own layer → the
admin's per-scan cap (`MAX_SEARCHES_PER_SCAN`), which always wins. Each surface can be turned
off; templates, prefixes, languages, extra news terms, review pages and order, and the Trends
window are all settings. Three presets set a starting point: **Lean** (English only, no AI
Overview follow-up or Maps), **Standard** (the defaults) and **Deep** (more templates, complaint
news, more review pages, YouTube).

The estimator (`domain/estimator.py`) counts the most searches a scan can make from the settings
and the brand's facts (apps, places), using the formulas in the table above. The settings page
shows it per scan and per month at the brand's schedule before anything is saved, and every scan
is capped at it.

The demo brands, on SerpApi's free plan (250 searches a month):

| Brand | Settings | Schedule | Searches a scan |
|---|---|---|---|
| Ola | search page ("Ola cabs", 1 page, AI Overview follow-up), autocomplete (1 prefix), English news, Trends (joint with 4 competitors + related queries), Play (rating + 1 page of newest reviews) | every 12 h | at most 8 |
| Uber, Rapido, Namma Yatri, inDrive | search page, news, Play rating | every 24 h | 3 |

## Every call goes through one service

`services/search.py`, in this order:

1. **Local cache** (Redis, per-engine TTL: news 3 h; search page, autocomplete and AI Overview
   6 h; Play, Maps and YouTube 12 h; Trends 24 h). A hit costs nothing and isn't an attempt.
2. **Circuit breaker**, derived from the ledger: an engine is open when its last 5 attempts in
   15 minutes all failed transiently (timeouts, 429, 5xx). A permanent 4xx never opens it, so one
   brand's bad parameters can't close an engine for everyone.
3. **Budgets**, counted from the ledger over UTC calendar periods: the scan's own estimate, the
   owner's monthly budget (`DEFAULT_MONTHLY_SEARCH_BUDGET` unless set), and a global daily cap
   (`SERPAPI_DAILY_GLOBAL_CAP`). A call over a budget is skipped and recorded as skipped. "Scan
   now" checks the month's budget before queueing and refuses within 15 minutes of the brand's
   last scan.
4. **The call** with the official `serpapi` SDK (only in `adapters/serp/`), a 30-second timeout,
   and up to 3 attempts with exponential backoff and jitter for transient failures. SerpApi's own
   one-hour cache is used unless a brand turns it off; a cached answer isn't billed.
5. **The ledger**: every attempt and every skipped call is a `serp_calls` row (engine, redacted
   params, params hash, outcome, error code, latency, `served_from`). Billable is derived:
   `served_from = 'live'`. Usage pages and budgets read the same rows.
6. **Redaction** before anything is stored: `search_metadata` URLs, any value containing
   `api_key=`, any string matching the key, and reviewer and author identity (names, profile
   links, avatars, user ids) are removed. A database CHECK refuses an `api_key` anywhere in the
   recorded params, and security tests assert that the key and reviewer identities never reach
   the database, the logs or committed fixtures.

Outbound HTTP goes to `serpapi.com` only (ADR-0011); nothing a user types becomes a URL.

## Replay mode

`SERPSENSE_MODE=replay` runs the same pipeline with no SerpApi key: the built-in demo story
(fictional VoltBox and SoundNest, `adapters/replay/story/`) answers each search the collectors
make, in SerpApi's answer shapes, and replayed answers are recorded as served from SerpApi's
cache, so they are never billed. See the README's quick start.
