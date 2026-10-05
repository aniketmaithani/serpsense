# ADR-0009: Passwordless email OTP authentication with server-side sessions

- **Status:** Accepted (2026-10-03)
- **Date:** 2026-10-02
- **Deciders:** Project owner

## Context
Users need accounts that own brands. Requirement: sign-up and log-in via a one-time code sent to email. No passwords, no third-party identity provider (SSO out of scope). Open sign-up must not let strangers spend the paid SerpApi/Claude keys.

## Decision

### Keys
`K_otp` and `K_csrf` are derived from `SECRET_KEY` with HKDF (distinct `info` labels). `OUTBOX_ENCRYPTION_KEYS` (MultiFernet, comma-separated for rotation) is separate (ADR-0010).

### Codes
- One flow for sign-up and log-in; the first successful verification creates the user.
- 6 digits from `secrets.randbelow`; stored as `HMAC-SHA256(K_otp, email + ":" + code)`; 10-minute expiry; single use; issuing a new code supersedes older unused ones.
- **Verification locks the code row (`SELECT … FOR UPDATE`)**, records an `otp_verify_attempts` row, and allows at most 5 attempts per code, so parallel guesses can't bypass the limit. Comparison with `hmac.compare_digest`.
- **Request limits:** per email (1 per 60 s, 5 per hour) counted from `otp_codes` rows in Postgres; per IP (20 per hour) in Redis.
- **No account enumeration:** same response for known, unknown and invite-blocked emails.
- Codes are delivered through the outbox (ADR-0010).

### Sessions
- Server-side rows; the cookie holds a 32-byte random token; the DB stores only its SHA-256.
- Cookie: `HttpOnly`, `SameSite=Lax`, **`Secure` always when `APP_ENV=production`** (startup fails if `BASE_URL` isn't https in production).
- 7-day sliding expiry, refreshed **at most once per hour** per session.
- **Logout sets `revoked_at`**; "log out everywhere" revokes all the user's sessions; account deletion deletes them (ADR-0013).
- **CSRF:** per-session secret; token = HMAC(K_csrf, secret); required on every state-changing request (form field or `X-CSRF-Token` for HTMX).

### Policy and logging
- `SIGNUP_MODE=open|invite` + `ALLOWED_EMAILS`/`ALLOWED_DOMAINS`; `invite` required in production. Since ADR-0014, invite mode also admits addresses the operator approved from an access request, and the operator console's session is the one not stored server-side. Since ADR-0015, the operator can switch the mode from the console; the environment's value is where it starts.
- Console mailer refused in production (ADR-0010).
- Never logged or put in audit details: codes, tokens, emails. Audit events: `auth.code_requested`, `auth.login_succeeded`, `auth.verify_failed`, `auth.logged_out`, `auth.sessions_revoked`; IP/user agent go to `audit_event_network` (ADR-0013).

**Amendment (2026-10-03):** an operator can also create a user from the command line
(`serpsense seed-demo --owner EMAIL`), to own the demo brands before anyone signs in. Such a user
signs in like any other: the first successful verification finds the existing row. Their
`created_at` is when they were seeded, not a verification time. Seeding is an operator action, so
it bypasses `SIGNUP_MODE=invite`; a seeded address should also be on the invite list. Addresses
are checked the same way everywhere (`ports.accounts.email_address`): at most 254 characters, no
control characters, and never the reserved `.invalid` domain of deleted accounts' pseudonyms.

**Amendment (2026-10-03, the sign-in service review):** besides the per-email and per-IP request
limits, verification is limited to 50 an hour per IP, IPv6 sources count per /64, and at most 200
codes an hour are issued for every address together (counted in Postgres, so it holds while the
Redis limiter is down and fails open). Rate-limit keys are an HMAC under a key derived like the
others. A session ends 30 days after it began, however often its expiry slides. A guess at a code
that is already expired or locked out isn't compared, recorded or audited. Accepted risk: a
refused or rate-limited request returns sooner than one that issues a code, so timing could tell
who is invited; the invite list is short and operator-run, and every answer's content is the same.

## Alternatives considered
- **Passwords (Argon2)** — more surface (reset flow, breach handling) for no user benefit.
- **Magic links** — similar security; codes work across devices and survive link pre-fetching by email scanners.
- **OAuth/OIDC** — out of scope.

## Consequences
- Positive: no password storage; small, testable surface.
- Negative: login depends on email delivery (Mailpit locally, SMTP provider in production).
- Follow-ups: security tests (expiry, single use, lockout under concurrency, enumeration, CSRF, cross-user 404, Secure in production); runbook `otp-abuse.md` (P1). **All PRs in this area need a human merge.**
