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

**Amendment (2026-10-03, the deletion flow as built):**
- The step-up code is a code for the account's address like any other (`SignIn.send_step_up_code`, within every code limit, without the invite check), sent in an email of its own (outbox template `delete_code`) saying it deletes the account and to sign out everywhere if they didn't ask. Any live code for the address proves the same thing, so one asked for on the sign-in page also works, and a deletion code also signs in. The code is checked in the deletion's own transaction (`SignIn.check_code`), so a deletion that fails never uses it up.
- The typed address is compared ignoring case and surrounding spaces. A wrong address counts as a wrong guess at the code: the code is compared and the attempt recorded either way, so neither the answer nor its timing tells a wrong address from a wrong code.
- The code is locked before the user's row, the order signing in takes them in (a new session's foreign key share-locks the user's row), so a deletion and a sign-in with the same code can't deadlock. Holding the code also queues a second deletion, which then finds the code used.
- Pending emails are dropped through the outbox status rule (a `dropped` attempt each): those to the user or their address, including code emails that name no user (sent before the account existed). A message being sent at that moment is waited for, and is then no longer pending. If it holds its row past the session's lock timeout (15 s), the unit of work ends `Busy` and the page answers "try again in a minute" (503 with `Retry-After`), with nothing changed and the code still good.
- Queued scans move to `skipped` (`account_deleted`) with the user as the transition's actor.
- `account.deleted` details hold only how many rows changed per table (brands, sessions, codes, messages, network rows).
- Settings → Account also has "Sign out everywhere" (ADR-0009). After deletion the cookie is cleared (deleted Secure in production, as a `__Host-` cookie must be) and the sign-in page says the account was deleted.
- Not built yet: the retention job for codes, sessions and network rows older than their limits (BUILD_PLAN P1, the personal-data scrub job).

## Alternatives considered
- **Hard delete with cascades** — impossible with append-only ledgers; would also destroy cost and audit history.
- **No deletion (out of scope)** — rejected by the owner.

## Consequences
- Positive: deletion works without breaking ledger integrity; personal data is isolated.
- Negative: an extra table (`audit_event_network`) and a scheduled scrub job.
- Follow-ups: security tests (step-up required, other users unaffected, no email left in any table for the deleted user, sessions gone); runbook not needed (no alert). **PRs in this area need a human merge.**
