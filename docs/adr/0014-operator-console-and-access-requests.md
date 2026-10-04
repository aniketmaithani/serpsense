# ADR-0014: An operator console behind one password, and access requests

- **Status:** Accepted (2026-10-04)
- **Date:** 2026-10-04
- **Deciders:** Project owner

## Context
Production runs invite mode (ADR-0009): only addresses in `ALLOWED_EMAILS` or `ALLOWED_DOMAINS`
get a code, and letting someone in means editing the host's environment and restarting. The owner
wants to see the whole service at a glance (users, brands, scans) and to let people in from a
page: someone asks for a code, the owner approves them, they sign in. Until now the data model
said operational tasks were CLI-only, and RBAC is out of scope (AGENTS §1).

## Decision
- **An operator console at `/admin`**, on only when `ADMIN_PASSWORD` is set (at least 16
  characters); without it every console route answers 404. It is one operator credential, not a
  role: users get no admin flag, and no user session grants anything in the console. This is not
  RBAC, and the scope rule stands.
- **Logging in** takes the password, compared as HMACs in constant time. The form carries the
  sign-in pages' double-submit token. Attempts are limited per source (5 per 15 minutes per IPv4
  address or IPv6 /64) and across all sources (30 an hour) in Redis, and, since that limiter lets
  requests through when Redis is down, also in Postgres: 30 failures in the last hour refuse
  every attempt, the right password included. Each success and failure is an audit event
  (`admin.login_succeeded`, `admin.login_failed`) with no actor and no network row.
- **The console session** is a cookie (`__Host-serpsense_admin` in production; HttpOnly, Secure,
  SameSite=Strict) holding an expiry 4 hours out and a random nonce, signed with a key derived
  from `SECRET_KEY` and the password: changing either signs the operator out. Logging out records
  `admin.logged_out`, and a session issued before the latest one is refused, so logging out ends
  every console session, copied cookies included. Console writes carry a CSRF token, an HMAC of
  the cookie under its own derived key.
- **Access requests.** In invite mode, asking for a code with an address that isn't invited
  records an access request (`access_requests`: mutable, holds the address) and answers exactly
  like every other request, so the answer still reveals nothing (ADR-0009). At most 50 new
  requests are recorded an hour, across all sources. The operator approves or rejects a request;
  each decision is a row in the append-only `access_decisions`, one per request per instant, and
  the latest counts. An address may get a code and sign in if it is invited or its latest
  decision is `approved`. Approving sends nothing: the person asks for a code again. Rejecting
  stops future codes and sign-ins; it doesn't end sessions already open.
- **Reads across users** happen in one place, the console's read port (`ports/console.py`), which
  only the console service uses (an import-linter contract). Every user-facing read keeps going
  through the scoped path (AGENTS §4).
- **Personal data:** account deletion pseudonymises the user's access request like their codes
  (ADR-0013). A request that never became an account keeps its address and nothing else.

## Alternatives considered
- **An admin flag on users, signed in with an emailed code** — roles on user accounts are RBAC,
  out of scope, and a stolen user session would carry the flag.
- **Editing `ALLOWED_EMAILS` on the host** — what we had: it needs a shell and a restart.
- **Server-side console sessions** — revocable one by one, but one operator doesn't need a table:
  a logout event ends them all, and a password change does too.
- **A status column on access requests** — it would overwrite a fact that changes over time
  (AGENTS §4); decisions are rows instead.

## Consequences
- Positive: the owner lets people in from a page; the invite lists still work for addresses known
  in advance; every decision is kept.
- Negative / costs: a leaked `ADMIN_PASSWORD` reads every user's brands and address and lets
  anyone in, so it must be long and random. Someone guessing it from enough sources can keep the
  operator out for up to an hour (the Postgres cap counts everyone's failures). Anyone can add
  pending requests, up to 50 an hour.
- Open: requests that never became accounts have no retention limit yet, and someone without an
  account can't remove their address themselves; for now the operator does it on request, in
  the database. A purge of old undecided and rejected requests is a candidate follow-up, as is
  an approval email through the outbox (ADR-0010).
- Follow-ups: data-model §1 (the two tables; "CLI-only" amended); AGENTS §4 names this exception.
