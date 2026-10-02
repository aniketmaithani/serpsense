# ADR-0013: Account deletion and personal-data handling

- **Status:** Accepted (2026-10-03)
- **Date:** 2026-10-02
- **Deciders:** Project owner

## Context
The owner wants users to be able to delete their account now. Ledgers and audit logs are append-only (AGENTS §4) and reference users, so rows cannot simply be deleted. Personal data we hold: email addresses, IP addresses, user agents, OTP codes (hashed), session tokens (hashed). Brand and mention data is public search data (author names are stripped at parse time).

## Decision
- **Keep raw personal data out of append-only tables.** Emails, IPs and user agents live only in mutable tables (`users`, `otp_codes`, `sessions`, `outbox_messages`, `audit_event_network`). Append-only tables reference `user_id` only. FKs from append-only tables use `ON DELETE RESTRICT`.
- **Deletion flow** (Settings → Delete account):
  1. Re-authenticate with a fresh OTP (step-up), then confirm by typing the account email.
  2. In **one transaction**:
     - `users.email` → `deleted+<user_id>@serpsense.invalid` (RFC 2606 reserved TLD), `deleted_at` = now
     - delete all `sessions` rows for the user
     - `otp_codes` for the old email: email pseudonymised, `request_ip` nulled, `code_hash` overwritten with zero bytes (an HMAC of a 6-digit code could otherwise confirm a guessed address to someone holding the database and `SECRET_KEY`)
     - `outbox_messages` for the user/email: `recipient_email` pseudonymised, `sensitive_data_encrypted` nulled, pending rows → `dropped`
     - delete `audit_event_network` rows where the user is the actor **or** the event targets one of the user's OTP codes (pre-login events have no actor)
     - archive all the user's brands (`archived_at`), so no further scans run; queued scans → `skipped` (reason `account_deleted`); running scans see `deleted_at`/`archived_at` at their next stage and finish `skipped` without LLM calls, alerts or emails; the sweep ignores archived brands
     - delete free-text the user wrote that may hold personal data (`brands.tone_notes`)
     - write audit event `account.deleted` (no email in details, **no network row**)
  3. Clear the cookie.
- Ledgers, scans, mentions and model output remain, linked to a pseudonymous user. The same email can sign up again as a new user.
- **Pseudonyms are unique per row:** `deleted+<row id>@serpsense.invalid`, so pseudonymised rows never collide on `uq_users_email` or `uq_otp_codes_one_live_per_email`. The scrub also marks any still-live code as superseded first.
- **Retention defaults:** `otp_codes` older than 30 days → superseded if still live, email pseudonymised, IP nulled and `code_hash` zeroed by the maintenance job; expired sessions deleted after 30 days; `audit_event_network` older than 90 days deleted.

## Alternatives considered
- **Hard delete with cascades** — impossible with append-only ledgers; would also destroy cost and audit history.
- **No deletion (out of scope)** — rejected by the owner.

## Consequences
- Positive: deletion works without breaking ledger integrity; personal data is isolated.
- Negative: an extra table (`audit_event_network`) and a scheduled scrub job.
- Follow-ups: security tests (step-up required, other users unaffected, no email left in any table for the deleted user, sessions gone); runbook not needed (no alert). **PRs in this area need a human merge.**
