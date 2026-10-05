# ADR-0015: The operator chooses the sign-up mode from the console

- **Status:** Accepted (2026-10-05)
- **Date:** 2026-10-05
- **Deciders:** Project owner

## Context
`SIGNUP_MODE` (ADR-0009) is read from the environment, and production refuses to start unless it
is `invite`, so nobody can open sign-up by accident. With the operator console (ADR-0014) the
owner approves people one by one; they also want to be able to let everyone in for a while (a
launch, a demo day) and close it again, without editing the host's environment.

## Decision
- **Two modes, chosen in the console:** *approval required* (`invite`: the invite lists and
  approved access requests, as ADR-0014 describes) and *open* (anyone with an email address can get
  a code and an account). Each switch is a row in the append-only `signup_mode_changes` with its
  time; the latest counts. With no row, the environment's `SIGNUP_MODE` applies.
- **The environment stays the starting point.** Production still refuses to start unless
  `SIGNUP_MODE=invite`, so a fresh or misconfigured host is closed; only a signed-in operator can
  open it, deliberately, and the switch is kept. Switches count only while the console is on
  (`ADMIN_PASSWORD` set): removing the password and restarting returns sign-up to the
  environment's mode, which is how to close it without the console (a locked or forgotten one).
- **Accounts made while sign-up is open stay in when it closes.** In open mode, an address that
  wasn't invited or approved is approved as it signs up (an access request and an `approved`
  decision, as if the operator had clicked it), so closing sign-up stops new people only; the
  operator can still reject someone later.
- **Sign-in follows the current mode**, read in the same transaction as the code it issues or
  checks. In open mode no access requests are recorded. Every answer to "send me a code" still
  looks the same, and the check-your-email page tells newcomers they are waiting for the operator
  only while approval is required.

## Alternatives considered
- **Keep it in the environment** — every switch needs a shell, an edit and a restart.
- **Drop the production guard** — a host that forgot the setting would start open.
- **Open sign-up limited to some domains** — `ALLOWED_DOMAINS` already does that in invite mode.

## Consequences
- Positive: the owner opens and closes sign-up from a page; every switch is on record.
- Negative / costs: while sign-up is open, every new account gets the default monthly search and
  LLM budgets, inside the caps across all users: `SERPAPI_DAILY_GLOBAL_CAP` for searches and
  `LLM_DAILY_GLOBAL_CAP_MICROS` for the model (ADR-0008). Many accounts can use those caps up for
  everyone until the day ends. After the first switch, the environment's `SIGNUP_MODE` no longer
  decides while the console is on.
- Follow-ups: AGENTS §6 notes the exception; data-model §1 gains the table.
