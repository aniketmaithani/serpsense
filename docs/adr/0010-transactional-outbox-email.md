# ADR-0010: Outbound email through a transactional outbox

- **Status:** Accepted (2026-10-03)
- **Date:** 2026-10-02
- **Deciders:** Project owner

## Context
We send OTP codes and crisis alerts by email. Sending inside a transaction risks emails for changes that rolled back; sending after commit without a record risks lost or duplicate emails.

## Decision
- Business code writes an `outbox_messages` row (kind, recipient, template, non-sensitive `template_data`, unique `dedupe_key`, and `otp_code_id` or `alert_id`) **in the same transaction** as the change that causes it.
- `dispatch_outbox` (Beat every 15 s + post-commit nudge via the `JobQueue` port) selects due `pending` rows with `FOR UPDATE SKIP LOCKED` and **holds the row lock through render → send → record → commit**. There is no intermediate `sending` state, so a crashed worker simply releases the lock.
- Outcome per attempt goes to `outbox_attempts` (`sent`, `retryable_error`, `permanent_error`, `dropped`). Retryable errors → `next_attempt_at` with backoff; after 8 retryable errors → `dead` (alerts). Permanent errors → `dead`. Expired OTP messages and messages cancelled by account deletion get a `dropped` attempt row. `outbox_messages.status` always follows the derivation rule in the data model.
- **Delivery is at-least-once:** if SMTP accepts the message but the commit fails, the message is sent again. Acceptable for OTP and alert emails; the `dedupe_key` prevents duplicate *rows*.
- **OTP specifics:** expiry is derived from the linked `otp_codes` row; expired OTP messages are marked `dropped` without sending.
- **Sensitive payloads:** the plaintext OTP is stored only in `sensitive_data_encrypted`, encrypted with **MultiFernet** (`cryptography`; keys from `OUTBOX_ENCRYPTION_KEYS`, first key encrypts, all decrypt, so keys can be rotated). The column is **nulled** when the message is sent, dropped, dead or expired. Ciphertext may persist in WAL/dead tuples until vacuum; accepted because it is encrypted and short-lived.
- The `Mailer` port has adapters: `smtp` (`smtplib`; Mailpit in development, provider in production), `console` (dev/test only; **startup fails with `APP_ENV=production`**), `fake` (tests).
- In-app notifications are rows written in the same transaction as the alert; no outbox needed.

## Alternatives considered
- **Send synchronously in the request** — slow, couples web latency to SMTP, unsafe on rollback.
- **Enqueue a Celery task directly** — may run before commit or be lost after commit.
- **`sending` state + lease** — needs a reclaim path; holding the row lock is simpler.

## Consequences
- Positive: no email for rolled-back changes; no lost emails; restarts are safe.
- Negative: at-least-once (rare duplicate email); OTP latency up to one dispatch interval (mitigated by the nudge).
- Follow-ups: concurrency test (two dispatchers → each message sent once in the normal path); runbook `outbox-backlog.md`.
