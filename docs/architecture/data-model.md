# SerpSense — Data Model

Source of truth for the PostgreSQL schema (ADR-0003). **Update this file in the same commit as any migration.**

## Conventions

- Primary keys: `id uuid` (generated in the application, `uuid4`), unless noted.
- Timestamps: `timestamptz`, UTC, named `*_at` (or `scheduled_for`).
- Money: integer `*_micros` (10⁻⁶ of the currency unit) + `currency char(3)`. Never float.
- Bounded scores: `smallint` 0–100. Weights and ratios in basis points (`*_bp`, 0–10000).
- Enums: Postgres enums mirrored by Python `StrEnum`s in `domain/enums.py`.
- Constraint/index names via SQLAlchemy naming convention:
  `pk_%(table_name)s`, `fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s`, `uq_%(table_name)s_%(column_0_N_name)s`, `ix_%(column_0_N_label)s`, `ck_%(table_name)s_%(constraint_name)s`.
- **Append-only tables** (🔒) get two triggers from `serpsense.adapters.db.ddl.append_only_triggers()`: `trg_<table>_append_only` (BEFORE UPDATE OR DELETE, per row) and `trg_<table>_append_only_truncate` (BEFORE TRUNCATE, per statement). Both execute the shared function `serpsense_forbid_mutation()` (migration 0001), which raises `restrict_violation` (SQLSTATE 23001). Inserts that may repeat use `ON CONFLICT DO NOTHING` (never `DO UPDATE`).
- **Every foreign key** uses `ON DELETE RESTRICT` (enforced by a unit test over the metadata); erasure is by pseudonymisation (ADR-0013), never by deleting referenced rows.
- **JSONB** only where ADR-0003 allows it (📄). Never filtered on.
- **No raw personal data in append-only tables.** Emails, IP addresses and user agents live only in mutable tables that the account-deletion flow can scrub (ADR-0013).
- **Derived values are not stored.** Computed by query or by pure domain functions: a mention's first/last seen scan, searches used, billable flag, health score, crisis score and level, narrative activity, enrichment "pending", OTP message expiry.
- **Changing facts are rows**: budgets, settings, schedules, status transitions, observations.
- **Scoped access:** every user-owned row is reachable from `brands.owner_id` or `user_id`; repositories expose only `scoped(user)` query paths; out-of-scope reads return 404.

### Named exceptions to "no derived values"
| Column | Why it is stored | Guard |
|---|---|---|
| `scans.status` | Compare-and-set claim target (`UPDATE … WHERE status = 'queued'`) and predicate of `uq_scans_brand_id_active` | Transitions validated by `domain/scan_state.py`, written only by `services/scans` with a compare-and-set on the expected current status plus a `scan_status_transitions` row in the same transaction; integration test asserts `status` = latest `to_status` |
| `outbox_messages.status` | Dispatcher claim predicate and partial index | Written only by `services/outbox` together with an `outbox_attempts` row; derivation rule in §8; consistency test |

---

## 1. Identity & access

### `users`
| Column | Type | Notes |
|---|---|---|
| id | uuid | pk |
| email | citext | `uq_users_email`; replaced by `deleted+<id>@serpsense.invalid` (RFC 2606 reserved TLD) on deletion; never logged |
| created_at | timestamptz | when the user was created: at their first successful OTP verification, or when an operator seeded them (`serpsense seed-demo`, ADR-0009 amendment) |
| deleted_at | timestamptz null | set by the account-deletion flow (ADR-0013) |

No roles/admin flag (RBAC out of scope). Operational admin tasks are CLI-only.

### `otp_codes` (mutable: scrubbed on deletion)
| Column | Type | Notes |
|---|---|---|
| id | uuid | pk |
| email | citext | `ix_otp_codes_email_created_at`; pseudonymised on deletion |
| code_hash | bytea | HMAC-SHA256(K_otp, email:code); K_otp derived from `SECRET_KEY` via HKDF |
| created_at / expires_at | timestamptz | expiry = created + 10 min |
| consumed_at | timestamptz null | |
| superseded_at | timestamptz null | set when a newer code is issued |
| request_ip | inet null | nulled on deletion |

`ck_otp_codes_single_terminal`: not both `consumed_at` and `superseded_at`. `ck_otp_codes_expires_after_created`. **`uq_otp_codes_one_live_per_email`**: partial unique index on `email` where neither consumed nor superseded — at most one live code per email, so concurrent requests can't multiply the guess budget (issuing a code supersedes the previous live one). Verification locks the row `FOR UPDATE`. Per-email request limits (1/60 s, 5/h) are counted from this table.

### 🔒 `otp_verify_attempts`
`id`, `otp_code_id` fk (RESTRICT, `ix_otp_verify_attempts_otp_code_id`), `attempted_at`, `succeeded boolean`. No IP stored (per-IP limits live in Redis). Attempts per code = row count (limit 5). **`uq_otp_verify_attempts_one_success_per_code`**: partial unique index on `otp_code_id` where `succeeded` — a code verifies successfully at most once.

### `sessions` (mutable; model class `UserSession`)
| Column | Type | Notes |
|---|---|---|
| id | uuid | pk |
| user_id | uuid | fk → users (RESTRICT), `ix_sessions_user_id` |
| token_hash | bytea | `uq_sessions_token_hash` (SHA-256 of the cookie token) |
| csrf_secret | bytea | per-session; CSRF tokens are HMAC(K_csrf, secret) |
| created_at | timestamptz | |
| expires_at | timestamptz | sliding; refreshed at most once per hour |
| revoked_at | timestamptz null | logout / log out everywhere |
| ip | inet null | at creation; scrubbed on deletion |
| user_agent | text null | truncated to 256 (`ck_sessions_user_agent_length`); scrubbed on deletion |

`ck_sessions_expires_after_created`. Account deletion deletes the user's session rows (nothing references `sessions`).

### 🔒 `user_search_budgets` / 🔒 `user_llm_budgets`
| Table | Columns |
|---|---|
| `user_search_budgets` | `id`, `user_id` fk, `monthly_searches integer`, `effective_from`, `created_at` |
| `user_llm_budgets` | `id`, `user_id` fk, `monthly_micros bigint`, `currency char(3)`, `effective_from`, `created_at` |

Append-only: a budget change is a new row. Both: FK to users RESTRICT; **`uq_<table>_user_id_effective_from`** (at most one row per user per instant, so "latest `effective_from ≤ now`" has a single answer); amounts non-negative (`ck_<table>_monthly_*_non_negative`); `currency` must match `^[A-Z]{3}$` (`ck_user_llm_budgets_currency_iso_4217`). No row → config default (1,500 searches; 30,000,000 micros = US$30).

---

## 2. Settings (versioned, read whole)

Validated by Pydantic (`domain/settings/*`), always read whole. All four tables below are 🔒 **append-only**: a change is a new row, and the current version is the latest `created_at` per key. **`uq_<table>_<key>_created_at`** allows one version per key per instant, so "latest" has a single answer; its index also serves the "latest version" lookup.

| Table | Key (FK, RESTRICT) | 📄 `document` contents |
|---|---|---|
| 🔒 `user_llm_profile_versions` | user_id → users | per-task model/effort/display/max_tokens/temperature/caching/fallback + preset |
| 🔒 `user_search_default_versions` | user_id → users | default country, languages, device, cache, concurrency, caps |
| 🔒 `brand_search_settings_versions` | brand_id → brands | per-engine knobs (templates, pages, filters, TTLs) |

Each has `id`, the key FK, `document jsonb` (must be a JSON object: `ck_<table>_document_is_object`), `schema_version smallint` (≥ 1: `ck_<table>_schema_version_positive`) and `created_at`.

### 🔒 `brand_schedule_versions` (relational: the dispatcher filters on it)
| Column | Type | Notes |
|---|---|---|
| id | uuid | pk |
| brand_id | uuid | fk → brands (RESTRICT); `uq_brand_schedule_versions_brand_id_created_at` |
| interval_minutes | integer null | one of 60/180/360/720/1440 (`ck_brand_schedule_versions_interval_allowed`); null = manual only |
| quiet_start / quiet_end | time null | local time in `timezone`; both null, or both set and different (`ck_brand_schedule_versions_quiet_hours_valid`); start > end means overnight |
| timezone | text | IANA name, default `Asia/Kolkata`; 1–64 non-whitespace chars (`ck_brand_schedule_versions_timezone_format`); validity checked by the application (a CHECK can't consult `pg_timezone_names`; an invalid name would break `AT TIME ZONE` queries, so the dispatcher must not rely on SQL time-zone maths without validating) |
| created_at | timestamptz | |

Resolution order: system defaults → user defaults → brand settings → per-run override; resolved result snapshotted into `scans.settings_snapshot` / `llm_calls.request_settings`.

---

## 3. Brands

Every brand (including competitors) is a full brand with its own settings, schedule and dashboard.

### `brands`
| Column | Type | Notes |
|---|---|---|
| id | uuid | pk |
| owner_id | uuid | fk → users (RESTRICT); **immutable** (`trg_brands_owner_immutable`, BEFORE UPDATE, reported as `ck_brands_owner_immutable`) because the competitor same-owner rule depends on it |
| name | text | at most 120 characters and not only whitespace (`ck_brands_name_length`) |
| slug | text | `uq_brands_owner_id_slug` (its index also serves lookups by owner); lowercase words joined by hyphens, ≤ 64 (`ck_brands_slug_format`) |
| tone_notes | text null | used by the drafter; ≤ 2000 characters (`ck_brands_tone_notes_length`) |
| created_at | timestamptz | |
| archived_at | timestamptz null | archived brands are not scheduled; set for all brands on account deletion |

### `brand_competitors`
`brand_id` fk, `competitor_brand_id` fk (both RESTRICT); pk (brand_id, competitor_brand_id); `ix_brand_competitors_competitor_brand_id` for reverse lookups ("competitor of …"); `ck_brand_competitors_not_self`. **Same owner** enforced by `trg_brand_competitors_same_owner` (reported as `ck_brand_competitors_same_owner`; a missing brand is left to the FK/NOT NULL constraints so the real error is reported). **At most 4 competitors per brand** (`trg_brand_competitors_at_most_four`, reported as `ck_brand_competitors_at_most_four`; it locks the brand row so concurrent links can't exceed it under the default READ COMMITTED isolation, and re-linking an existing pair is left to the primary key or `ON CONFLICT DO NOTHING`), so every competitor is always in the brand's Trends joint query. Comparisons use the competitor's own latest completed scan.

### Brand attributes
All have a brand FK (RESTRICT). `brand_locations` and `brand_apps` also have a `(brand_id, id)` unique key (`uq_<table>_brand_id_id`), the target of the mentions' same-brand composite FKs. Aliases, watch terms and location queries are `citext`, so uniqueness ignores case; they may not start or end with whitespace, so padding can't sidestep uniqueness (the service trims input).

| Table | Columns | Uniqueness | Checks |
|---|---|---|---|
| `brand_aliases` | `id`, `brand_id`, `alias citext` | `uq_brand_aliases_brand_id_alias` | ≤ 120 chars, no leading/trailing whitespace (`ck_brand_aliases_alias_length`) |
| `brand_languages` | `brand_id`, `language_code` (lowercase BCP-47, e.g. `en`, `hi`, `zh-cn`) | pk (brand_id, language_code) | `ck_brand_languages_language_code_format` |
| `brand_watch_terms` | `id`, `brand_id`, `term citext` | `uq_brand_watch_terms_brand_id_term` | ≤ 80 chars, no leading/trailing whitespace (`ck_brand_watch_terms_term_length`) |
| `brand_apps` | `id`, `brand_id`, `store` enum `app_store` (`google_play`; mirrors `domain.enums.AppStore`), `app_id` | `uq_brand_apps_brand_id_store_app_id` | `app_id`: 1–255 non-whitespace chars (`ck_brand_apps_app_id_format`) |
| `brand_locations` | `id`, `brand_id`, `query citext`, `resolved_data_id null`, `resolved_at null` | `uq_brand_locations_brand_id_query` | query ≤ 200 chars, no leading/trailing whitespace (`ck_brand_locations_query_length`); `ck_brand_locations_resolution_together` (both resolution fields set, or neither) |

---

## 4. Scans

### `scans`
| Column | Type | Notes |
|---|---|---|
| id | uuid | pk |
| brand_id | uuid | fk → brands (RESTRICT), `ix_scans_brand_id_created_at` |
| trigger | enum `scan_trigger` (`schedule`, `manual`, `replay`; mirrors `domain.enums.ScanTrigger`) | |
| scheduled_for | timestamptz null | start of the schedule slot; **`uq_scans_brand_id_scheduled_for`** (a plain unique constraint: NULLs are distinct, so only scheduled scans can collide) |
| requested_by | uuid null | fk → users (RESTRICT); set exactly for manual scans (`ck_scans_requested_by_manual`) |
| status | enum `scan_status` (mirrors `domain.enums.ScanStatus`) | named exception (see conventions) |
| 📄 settings_snapshot | jsonb | resolved settings, immutable; must be a JSON object (`ck_scans_settings_snapshot_is_object`) |
| estimated_searches | integer | ≥ 0 (`ck_scans_estimated_searches_non_negative`) |
| created_at | timestamptz | |

- `ck_scans_scheduled_for_schedule`: `trigger = 'schedule'` ⇔ `scheduled_for is not null`.
- **`uq_scans_brand_id_active`**: partial unique index on (brand_id) where `status in ('queued','running')` — one active scan per brand, even across concurrent transactions; "Scan now" during an active scan is rejected and shows the active scan.
- Inserts use `INSERT … ON CONFLICT DO NOTHING` **without a conflict target**, so both uniqueness rules apply (a named target would still raise on the other one).
- **Only `status` can change after insert** (`trg_scans_identity_immutable`, reported as `ck_scans_identity_immutable`): observations and the Trends comparison rely on a scan's brand, slot and settings never changing. `brand_id`, `scheduled_for` and `settings_snapshot` never change, not even in a migration: billing ownership, same-brand observations and the Trends comparison were checked against them at insert time. Only a backfill of a column added later may disable the trigger, inside its own transaction (`ALTER TABLE scans DISABLE TRIGGER trg_scans_identity_immutable`, then `ENABLE`), and it says why in its docstring.
- Replay scans have no requester (they are created by the seed/replay CLI), so `requested_by` is set only for manual scans.

**Transitions** (`domain/scan_state.py`, each with the reasons it may be made for; anything else raises `IllegalTransition`):
```
(new)   → queued     scheduled | requested | replayed
queued  → running    claimed            (claim: UPDATE … WHERE status='queued' RETURNING)
queued  → skipped    brand_archived | account_deleted
running → succeeded  completed          (finishes are compare-and-sets on status = 'running')
running → partial    surfaces_failed | enrichment_failed (the LLM failed: deterministic scores only)
running → failed     all_surfaces_failed | stage_failed | timed_out (the maintenance sweep)
running → skipped    budget_exhausted | quota_insufficient (checked after claim) | brand_archived | account_deleted
```

### 🔒 `scan_status_transitions`
`id`, `scan_id` fk (RESTRICT), `from_status scan_status null`, `to_status scan_status`, `actor` enum `transition_actor` (`system`, `user`), `actor_user_id null` fk → users (RESTRICT), `reason text`, `at`. Index `ix_scan_status_transitions_scan_id_at`; **`uq_scan_status_transitions_scan_id_to_status`** (no scan reaches the same status twice). Started/finished times are derived from here.
- **`ck_scan_status_transitions_allowed_transition`**: only the state machine above (creation = `NULL → queued`), written as a NULL-safe `CASE` — defence in depth for `domain/scan_state.py`.
- `ck_scan_status_transitions_actor_user_matches`: `actor = 'user'` ⇔ `actor_user_id` is set.
- `reason` is a machine code (`ck_scan_status_transitions_reason_format`: `^[a-z][a-z0-9_.]{0,63}$`, e.g. `claimed`, `timed_out`, `budget_exhausted`) — never free text or personal data.

### 🔒 `scan_surface_results`
`scan_id` fk (RESTRICT), `surface` enum `surface` (`search_page`, `ai_overview`, `autocomplete`, `news`, `trends`, `play`, `maps`, `youtube`), `outcome` enum `surface_outcome` (`succeeded`, `failed`, `disabled`, `not_shown`, `circuit_open`, `budget_exhausted`), `error_code text null`; pk (scan_id, surface). `ck_scan_surface_results_error_code_iff_failed` (an error code exactly for failed surfaces) and `ck_scan_surface_results_error_code_format` (`^[a-z][a-z0-9_.]{0,63}$`, as for transition reasons). Enums mirror `domain.enums`.

---

## 5. Search data

### 🔒 `serp_calls` (ledger)
| Column | Type | Notes |
|---|---|---|
| id | uuid | pk |
| user_id | uuid | fk → users (RESTRICT), `ix_serp_calls_user_id_created_at`; a scan's calls belong to the owner of its brand (`trg_serp_calls_user_owns_scan` → `ck_serp_calls_user_owns_scan`), a Preview call to its caller |
| scan_id | uuid null | fk → scans (RESTRICT), `ix_serp_calls_scan_id`; null for Preview, which runs only the brand's configured templates, so nothing typed into Preview is stored here |
| engine | enum `serp_engine` (SerpApi engine ids; mirrors `domain.enums.SerpEngine`) | `ix_serp_calls_engine_created_at` (circuit breaker reads last N per engine) |
| params_hash | text | sha256 hex of the canonical JSON of `params` with `engine` added as a top-level key (params never carries its own): integers written as strings (`num=10` and `"10"` are the same request), sorted keys, `,`/`:` separators, UTF-8 without ASCII escaping (so a Hindi query hashes the same everywhere), as `domain.search.params_hash` computes it; never the key (`ck_serp_calls_params_hash_sha256`) |
| 📄 params | jsonb | redacted request params; a JSON object (`ck_serp_calls_params_is_object`) with no `api_key` field at any depth and no `api_key=` in any string (`ck_serp_calls_params_no_api_key`), since a leaked key could never be removed from this append-only table; the client applies the same rule (`domain.search.contains_api_key`, checked against the CHECK by a contract test) before calling SerpApi, so a billed call is never left unrecorded |
| served_from | enum `served_from` (`local_cache`, `serpapi_cache`, `live`) null | set **exactly for successful calls** (`ck_serp_calls_served_from_iff_succeeded`), so billable ⇔ `live` (derived) never counts failures, retries or skipped calls |
| outcome | enum `serp_call_outcome` (`succeeded`, `failed`, `skipped_budget`, `circuit_open`) | |
| http_status | smallint null | 100–599 (`ck_serp_calls_http_status_range`); null for skipped calls (`ck_serp_calls_skipped_not_sent`) |
| error_code | text null | exactly for failed calls (`ck_serp_calls_error_code_iff_failed`); machine code (`ck_serp_calls_error_code_format`) |
| latency_ms | integer | ≥ 0; 0 for skipped calls, which never reached SerpApi |
| created_at | timestamptz | |

**Circuit breaker** is derived, with no state of its own: an engine is open when its last 5 attempts that reached SerpApi within 15 minutes all failed **transiently**. An attempt is a successful call not served from `local_cache`, or a failed one; a failure is transient only for a network error or timeout (`serpapi.network`, `serpapi.timeout`), HTTP 429 (`serpapi.http_429`) or 5xx (`serpapi.http_5xx`); the codes are `domain.enums.SerpErrorCode`, whose `is_transient` encodes this rule. A permanent 4xx or a local rejection is still a failed call but never trips the breaker, so one brand's bad parameters can't close an engine for every user. Skipped calls and local-cache hits are ignored, so an open breaker never keeps itself open; it closes once those failures leave the window (ADR-0007, amended).

**Budgets** (search service) count billable (`live`) calls over UTC calendar periods: a global daily cap (`SERPAPI_DAILY_GLOBAL_CAP`), the user's monthly budget (latest `user_search_budgets` row, else `DEFAULT_MONTHLY_SEARCH_BUDGET`) and a per-scan cap (`MAX_SEARCHES_PER_SCAN`). A blocked call is a `skipped_budget` row. They are soft: calls are recorded when they return, so concurrent calls can pass a limit by the number in flight; the scan's quota check and SerpApi's own quota are the outer limits.

**Recording:** the client writes each `serp_calls` row in its own transaction as soon as the call returns, before any `raw_responses` row, so a rejected payload never loses the record of a billed call.

### `raw_responses`
`id`, `serp_call_id` fk (RESTRICT, `uq_raw_responses_serp_call_id`), 📄 `payload jsonb` (redacted; a JSON object under the same no-key rule: `ck_raw_responses_payload_no_api_key`), `created_at`. Mutable on purpose — not append-only — so a retention job can prune old payloads; they exist only for successful calls (`trg_raw_responses_call_succeeded` → `ck_raw_responses_call_succeeded`, on insert and on a change of `serp_call_id`). `ix_raw_responses_created_at` serves the retention job.

### `mentions`
| Column | Type | Notes |
|---|---|---|
| id | uuid | pk |
| brand_id | uuid | fk → brands (RESTRICT) |
| source | enum `mention_source` (`serp_result`, `top_story`, `people_also_ask`, `autocomplete`, `ai_overview`, `news`, `trends_query`, `play_review`, `maps_review`, `youtube_video`; mirrors `domain.enums.MentionSource`) | |
| identity_key | text | stable per source: the provider's id for reviews and videos; the sha256 hex of the canonical URL (`serp_result`, `top_story`, `news`) or of the normalised text (`people_also_ask`, `autocomplete`, `ai_overview`, `trends_query`), so long URLs fit. The canonical URL (`domain.mention.canonical_url`) has a lower-case scheme and host, sorted query, no trailing slash, no tracking parameters (`utm_*`, `gclid`, `fbclid`, `msclkid`, `igshid` anywhere; `ved`, `ei`, `usg`, `sa` on Google hosts), and a fragment only when it is a route (`#/…`, `#!…`); normalised text is NFKC, case-folded, single-spaced (`ck_mentions_identity_key_hashed`: anything but a review or video key is 64 lowercase hex chars); 1–512 chars (`ck_mentions_identity_key_length`) |
| text | text | author names/handles removed at parse time; not only whitespace, ≤ 10 000 chars (`ck_mentions_text_length`) |
| url | text null | `^https?://…`, ≤ 2048 chars (`ck_mentions_url_format`); never on reviews (`ck_mentions_review_has_no_url`), so no reviewer profile link is stored |
| outlet | text null | news publisher, only for `news` and `top_story` (`ck_mentions_outlet_news_only`): a channel or reviewer name is author identity (ADR-0007); ≤ 200 chars (`ck_mentions_outlet_length`) |
| brand_location_id | uuid null | set **exactly** for `maps_review` (`ck_mentions_location_iff_maps_review`); composite FK `(brand_id, brand_location_id)` → `brand_locations(brand_id, id)` (`fk_mentions_brand_location_id_brand_locations`), so it is always the **same brand's** location |
| brand_app_id | uuid null | set **exactly** for `play_review` (`ck_mentions_app_iff_play_review`); composite FK onto `brand_apps(brand_id, id)` likewise (`fk_mentions_brand_app_id_brand_apps`) |
| language_code | text null | lowercase BCP-47, as for `brand_languages` (`ck_mentions_language_code_format`) |
| published_at | timestamptz null | from provider when available |
| created_at | timestamptz | |

`uq_mentions_brand_id_source_identity_key`. **Content is first-seen:** mentions are written with `INSERT … ON CONFLICT DO NOTHING`, so a later edit of the same review never overwrites the text that was labelled; the edit becomes a revision (below). The table is mutable (not append-only) only so a redaction scrub can rewrite content; its identity — brand, source, identity key, cited location or app, creation time — never changes (see Observations). The observations below are the history.

### `mention_revisions`
Edited reviews keep their history (owner decision, 2026-10-03). Only reviews (`play_review`, `maps_review`) have revisions (`trg_mention_revisions_review_only` → `ck_mention_revisions_review_only`): other sources' text, such as a search snippet, varies between queries and isn't an edit. A mention's own `text` is revision 1. When a scan sees text that differs from the **latest** revision (or `mentions.text` when there is none), compared as normalised text after redaction, it adds the next revision, so A → B → A makes revision 3. Labels attach to (mention, revision) (see `enrichments`); the revision a scan saw is the latest one whose own scan was created at or before it (order by the creating scan, not by `created_at`, which is processing time). Known limitation: a redaction scrub that rewrites a review's text is undone by the next scan that sees the original; a scrub that must stick needs a deny list, which isn't built.

`mention_id` fk → mentions (RESTRICT), `revision smallint` (≥ 2, `ck_mention_revisions_revision_after_first`), `text` (as `mentions.text`: `ck_mention_revisions_text_length`), `scan_id` fk → scans (RESTRICT), the scan that first saw it (`ix_mention_revisions_scan_id`), `created_at`; pk (mention_id, revision); **`uq_mention_revisions_mention_id_scan_id`** (at most one new text per mention per scan). The scan must be of the mention's brand (`trg_mention_revisions_same_brand` → `ck_mention_revisions_same_brand`, the generic `serpsense_same_brand_as_scan`). Rows are never deleted (DELETE and TRUNCATE are rejected like an append-only table); only `text` may change, for a redaction scrub (`ck_mention_revisions_identity_immutable`).

### Trends comparison (deliberate design)
Google Trends interest is relative within a single query, so each scan runs **one joint query** (the scan's brand + all its competitors, at most 4) and stores every series. The rows belong to the **scan** (and so to the scanning brand's owner); `subject_brand_id` says which line of the comparison a row is. Trigger `trg_trends_observations_subject_in_comparison` (reported as `ck_trends_observations_subject_in_comparison`): the subject must be the scan's brand or one of its `brand_competitors` **at insert time** — unlinking a competitor later leaves past comparisons intact; a missing scan or subject is left to its foreign key. The same trigger caps a scan's comparison at **5 lines** (`ck_trends_observations_comparison_size`); one collector writes a scan's comparison in one transaction. A competitor's own scans run their own joint query; series from different scans are never mixed.

### 🔒 Observations
**Same brand:** an observation's mention or app must belong to the scan's brand — trigger `trg_<table>_same_brand` (generic function `serpsense_same_brand_as_scan(ref_table, ref_column, constraint)`), reported as `ck_<table>_same_brand`. It relies on identities that never change: a scan's brand (`trg_scans_identity_immutable`); a mention's brand, source, identity key, cited location or app and creation time (`trg_mentions_identity_immutable` → `ck_mentions_identity_immutable`); an app's brand, store and app id (`ck_brand_apps_identity_immutable`) and a location's brand and query (`ck_brand_locations_identity_immutable`; a case-only edit of the citext query and a re-resolved `resolved_data_id` are allowed) — the last three via the generic `serpsense_forbid_identity_change(constraint, columns…)`, which compares each column as its own type. Editing what an app or location *is* would rewrite its history, so a different app or place is a new row.

| Table | Columns | Key |
|---|---|---|
| `mention_observations` | `mention_id`, `scan_id`, `position smallint null` (≥ 1): the **best (lowest) rank** the mention had across all of the scan's calls for its source — one collector owns each source and records everything it saw in one sighting per scan, and the store keeps each mention's best rank within it (`domain.mention.best_ranked`) before inserting, so the value never depends on insert order; for `trends_query`, rising related queries rank 1–25 and top ones 26–50, so a rank also says which list it came from; `star_rating smallint null` (1–5; reviews only, a service rule) | pk (mention_id, scan_id); `ix_mention_observations_scan_id` |
| `app_rating_observations` | `brand_app_id`, `scan_id`, `rating_hundredths smallint` (100–500, i.e. 1.00–5.00 stars, rounded with `Decimal`), `review_count integer` (≥ 0); no row while the store shows no rating yet | pk (brand_app_id, scan_id); `ix_app_rating_observations_scan_id` |
| `trends_observations` | `scan_id`, `subject_brand_id` (fk → brands, RESTRICT), `observed_at timestamptz` (the point's start, from SerpApi's per-point timestamp — hourly or finer for short date ranges, stored in UTC), `interest smallint` (0–100; SerpApi's "<1" is 0), `is_partial boolean` (SerpApi's flag on the last, still-incomplete point, so a drop there isn't read as a decline) | pk (scan_id, subject_brand_id, observed_at) |

---

## 6. Model output (provenance: every row carries `prompt_version` + `llm_call_id`; labelled "AI-generated" in the UI)

### `enrichments`
`id`, `mention_id` fk → mentions (RESTRICT), `revision smallint` (≥ 1; 1 = `mentions.text`, ≥ 2 = a `mention_revisions` row, which must exist: `trg_enrichments_revision_exists` → `ck_enrichments_revision_exists`), `prompt_version`, `llm_call_id` fk → llm_calls (RESTRICT, `ix_enrichments_llm_call_id`), `sentiment smallint` (−1/0/1), `severity smallint` (0–100), `topic` enum `topic` (`product_quality`, `pricing`, `billing_refunds`, `customer_service`, `reliability`, `safety`, `app_experience`, `privacy_security`, `fraud_scam`, `marketing_ethics`, `legal_regulatory`, `corporate`, `workforce`, `other`; mirrors `domain.enums.Topic`, the taxonomy the labelling prompt uses), `is_complaint boolean`, `is_about_brand boolean` (false when the text only shares the brand's name, e.g. "ola" as a greeting; scoring ignores those), `reason text` (the model's one-line reason, non-blank, ≤ 500 chars), `created_at`; `uq_enrichments_mention_id_revision_prompt_version`, so an edited review is labelled again.
- **Provenance:** the labels come from a successful call, with the same prompt version, made for the owner of the mention's brand (`trg_enrichments_from_call` → `ck_enrichments_from_call`, the generic `serpsense_output_from_call(constraint, brand_from)`), and the prompt is a labelling task's (`ck_enrichments_prompt_from_labelling_task`).
- **One labeller per source** (`domain.labelling`): autocomplete suggestions are labelled by `classify_autocomplete`, AI Overview text by `assess_ai_overview`, everything else by `label_mentions`; scoring reads the enrichment of the active prompt version of that task. A source whose task has no prompt yet stays unlabelled and its scores are partial.
- **Mutable only for a redaction scrub of `reason`:** every other column is frozen (`ck_enrichments_identity_immutable`) and rows are never deleted (DELETE and TRUNCATE are rejected like an append-only table).
- "Pending" = no row for the mention's latest revision and the active prompt version (derived). Only mentions observed in the last 7 days are (re-)enriched, so a prompt-version bump doesn't re-process history.

### 🔒 `narratives`
`id`, `brand_id` fk → brands (RESTRICT), `label` (non-blank, ≤ 120 chars), `summary` (non-blank, ≤ 2000 chars), `prompt_version` (a `group_narratives` prompt: `ck_narratives_prompt_from_grouping_task`), `llm_call_id` fk → llm_calls (RESTRICT), `created_at`; `ix_narratives_brand_id_created_at`, `ix_narratives_llm_call_id`. **Immutable** (append-only triggers); a re-summarised story is a new narrative linked by assignments. From a successful call, with its prompt version, made for the brand's owner (`trg_narratives_from_call` → `ck_narratives_from_call`, the generic `serpsense_output_from_call`). Narratives hold no personal data (they summarise public mentions, ADR-0013), so they aren't scrubbed.

### 🔒 `narrative_assignments`
`id`, `narrative_id` fk, `mention_id` fk, `prompt_version` (a `group_narratives` prompt), `llm_call_id` fk (all RESTRICT), `created_at`. **Latest row per mention wins** (A→B→A allowed); **`uq_narrative_assignments_mention_id_created_at`**, so "latest" has a single answer, and its index serves the lookup. The narrative and the mention are of the same brand (`trg_narrative_assignments_same_brand` → `ck_narrative_assignments_same_brand`), and the row comes from a successful call, with its prompt version, made for the brand's owner (`ck_narrative_assignments_from_call`).

### `drafts` / `draft_citations`
`drafts`: `id`, `narrative_id` fk, `kind` enum (`holding_statement`, `review_reply`, `faq_entry`), `text`, `preset` enum, `prompt_version`, `llm_call_id` fk, `created_by` fk → users, `created_at`.
`draft_citations`: `draft_id`, `mention_id`; pk both. Unknown citations are rejected by the gateway.

### 🔒 `llm_calls` (ledger)
| Column | Type | Notes |
|---|---|---|
| id | uuid | pk |
| user_id | uuid | fk → users (RESTRICT), `ix_llm_calls_user_id_created_at` (monthly spend); a scan's calls belong to the owner of its brand (`trg_llm_calls_user_owns_scan` → `ck_llm_calls_user_owns_scan`, the generic `serpsense_user_owns_scan(constraint)`) |
| scan_id | uuid null | fk → scans (RESTRICT), `ix_llm_calls_scan_id`; null for calls outside a scan (drafts) |
| task | enum `llm_task` (`label_mentions`, `classify_autocomplete`, `assess_ai_overview`, `group_narratives`, `explain_crisis`, `draft_response`; mirrors `domain.enums.LlmTask`) | |
| requested_model | text | from settings; `^[a-z0-9][a-z0-9.-]{0,63}$` (`ck_llm_calls_requested_model_format`) |
| served_model | text null | from `response.model` (may differ after refusal fallback); set exactly when a response arrived, i.e. unless `outcome = 'failed'` (`ck_llm_calls_served_model_iff_response`); same format |
| prompt_version | text | `<task>/v<N>`, the prompt file (`ck_llm_calls_prompt_version_format`), of the call's own task (`ck_llm_calls_prompt_matches_task`) |
| 📄 request_settings | jsonb | resolved effort/thinking/max_tokens/etc.; a JSON object (`ck_llm_calls_request_settings_is_object`) |
| input_tokens / output_tokens / cache_read_tokens / cache_write_tokens | integer | ≥ 0 (`ck_llm_calls_<column>_non_negative`) |
| cost_micros | bigint | ≥ 0; priced on `served_model` |
| currency | char(3) | `USD`; `^[A-Z]{3}$` (`ck_llm_calls_currency_iso_4217`) |
| stop_reason | text null | `^[a-z][a-z_]{0,31}$` |
| outcome | enum `llm_call_outcome` (`succeeded`, `refused`, `truncated`, `invalid_output`, `failed`; mirrors `domain.enums.LlmCallOutcome`) | `failed` = no response (network, timeout, API error) |
| latency_ms | integer | ≥ 0 |
| created_at | timestamptz | |

No prompt or completion content stored.

---

## 7. Scores (results stored; totals derived)

Migrations 0020 (reference data) and 0021 (a scan's scores). Every table here is 🔒
**append-only**: a version's reference rows never change, and a scan is scored once.

### 🔒 Reference data (seeded by migration, per scoring version)
| Table | Columns |
|---|---|
| `scoring_versions` | `version text` pk (`^s[0-9]{1,3}$`), `warm_up_scans smallint` (≥ 0: earlier scored scans a brand needs before its crisis has a level), `created_at` |
| `scoring_weights` | `version` fk, `kind` enum `score_kind` (`health`, `crisis`; mirrors `domain.enums.ScoreKind`), `component text`, `weight_bp smallint` (1–10000); pk (version, kind, component); `ck_scoring_weights_component_of_kind`: a health weight names a `surface`, a crisis weight a `crisis_component` |
| `crisis_level_thresholds` | `version` fk, `level` enum `crisis_level` (`low`, `medium`, `high`), `min_score smallint` (0–100); pk (version, level); `uq_crisis_level_thresholds_version_min_score` |

`s1` is seeded with the numbers in `domain/scoring/` (docs/scoring.md); an integration test keeps the two equal.
Weights are 1–10000 (leave a surface out by giving it no row: a zero weight would divide by zero); crisis weights sum to 10000 and level floors are unique per version and include 0 (`uq_crisis_level_thresholds_version_min_score`; the cross-row rules are tested over every version). A new version is its own seed migration; since the tables are append-only, its downgrade disables the trigger in its own transaction to delete its rows (as `trg_scans_identity_immutable` does), and the RESTRICT foreign keys keep a version in use from being deleted. Adding a surface or crisis component recreates `ck_scoring_weights_component_of_kind` in the same migration.

### 🔒 Results
| Table | Columns | Key |
|---|---|---|
| `score_runs` | `scan_id` fk → scans, `version` fk, `computed_at` | pk (scan_id) |
| `surface_scores` | `scan_id` fk → score_runs, `surface` enum `surface`, `score smallint` (0–100) | pk (scan_id, surface); a surface that showed nothing about the brand has no row |
| `crisis_components` | `scan_id` fk → score_runs, `component` enum `crisis_component` (`velocity`, `spread`, `autocomplete`, `trends`, `press`), `value smallint` (0–100) | pk (scan_id, component); a missing component counts as 0 |

Health, crisis score and crisis level are derived in `v_scan_scores` (with each scan's `brand_id` and `created_at`; each row computed on its own, so reading a brand's latest scans costs only their rows; weights, thresholds and warm-up joined by version) and mirrored by pure functions in `domain/scoring/`; an integration test checks the view against them. Health is the weighted mean of the surface scores (none when no surface showed anything), the crisis score the weighted sum of the components ÷ 10000, both rounded half up; the level is the highest threshold the score reaches, once the brand has `warm_up_scans` earlier scored scans, succeeded or partial (by scan creation time). Weights are multiplied as integers: smallint products overflow.

---

## 8. Alerts, notifications, outbox

### `alerts`
| Column | Type | Notes |
|---|---|---|
| id | uuid | pk |
| brand_id | uuid | fk → brands, `ix_alerts_brand_id_created_at` |
| scan_id | uuid | fk → scans |
| narrative_id | uuid null | fk → narratives |
| rule | enum (`level_increase`, `new_negative_autocomplete`, `narrative_spread`) | |
| explanation | text null | model output, labelled AI-generated |
| explanation_llm_call_id | uuid null | fk → llm_calls |
| created_at | timestamptz | |

**`uq_alerts_scan_id_rule_narrative_id`** with `NULLS NOT DISTINCT` (PG16): re-running a scan's alert step can't duplicate alerts. Level is read from the scan's derived score. Cooldown is race-free because only one scan per brand is active at a time.

### `notifications` / `notification_reads`
`notifications`: `id`, `user_id` fk, `alert_id` null fk, `title`, `body`, `created_at`; `uq_notifications_alert_id_user_id`.
`notification_reads`: `notification_id` pk/fk, `read_at`.

### `outbox_messages` (mutable: scrubbed on deletion)
| Column | Type | Notes |
|---|---|---|
| id | uuid | pk |
| kind | enum (`otp_email`, `alert_email`) | |
| user_id | uuid null | fk → users |
| otp_code_id | uuid null | fk → otp_codes; expiry derived from the code |
| alert_id | uuid null | fk → alerts |
| recipient_email | citext | pseudonymised on deletion |
| template | text | |
| 📄 template_data | jsonb null | non-sensitive fields |
| sensitive_data_encrypted | bytea null | MultiFernet; **nulled after send, drop or expiry** |
| dedupe_key | text | `uq_outbox_messages_dedupe_key` (`otp:{otp_code_id}`, `alert:{alert_id}:email`) |
| status | enum (`pending`, `sent`, `dead`, `dropped`) | named exception (see conventions) |
| next_attempt_at | timestamptz | `ix_outbox_messages_pending` partial on status = 'pending' |
| created_at | timestamptz | |

`ck_outbox_messages_kind_refs`: `otp_email` ⇒ `otp_code_id` not null; `alert_email` ⇒ `alert_id` not null.

### 🔒 `outbox_attempts`
`id`, `outbox_message_id` fk, `attempted_at`, `outcome` enum (`sent`, `retryable_error`, `permanent_error`, `dropped`), `error_code null`.

**Status derivation rule** (what the consistency test checks): no attempts or only `retryable_error` attempts fewer than 8 → `pending`; last outcome `sent` → `sent`; last outcome `dropped` (OTP expired, account deleted) → `dropped`; last outcome `permanent_error`, or 8 `retryable_error` attempts → `dead`.

---

## 9. Audit

### 🔒 `audit_events`
| Column | Type | Notes |
|---|---|---|
| id | uuid | pk |
| actor_user_id | uuid null | fk → users (RESTRICT) |
| action | text | `noun.verb_past` |
| target_type / target_id | text / uuid null | |
| 📄 details | jsonb null | never contains email, IP, codes or tokens |
| created_at | timestamptz | `ix_audit_events_actor_user_id_created_at` |

### `audit_event_network` (mutable: scrubbed on deletion)
`audit_event_id` pk/fk, `ip inet`, `user_agent text`. Network details are kept apart so the append-only log never holds raw personal data.

Pre-login auth events (`auth.code_requested`, `auth.verify_failed`) have no actor; they set `target_type = 'otp_code'`, `target_id = otp_codes.id`, so deletion can find their network rows through the user's OTP codes. `account.deleted` writes no network row.

---

## 10. Views (derived, no storage)

- `v_mention_first_last_seen` — min/max scan per mention.
- `v_scan_usage` — billable searches (`served_from = 'live'`) and LLM cost per scan.
- `v_user_monthly_usage` — searches and LLM spend per user per month.
- `v_scan_scores` — health, crisis score, crisis level per scored scan (migration 0021; §7).
- `v_narrative_activity` — first/last seen and open/dormant per narrative; current assignment per mention.
- `v_current_settings`, `v_current_schedule`, `v_current_budgets` — latest version rows.
- `v_scan_timing` — started/finished from transitions.
