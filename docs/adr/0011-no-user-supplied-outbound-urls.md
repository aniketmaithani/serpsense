# ADR-0011: No user-supplied outbound URLs (webhooks dropped)

- **Status:** Accepted (2026-10-03)
- **Date:** 2026-10-02
- **Deciders:** Project owner

## Context
An earlier plan offered signed outbound webhooks for alerts. Fetching or posting to user-supplied URLs is an SSRF risk (internal networks, cloud metadata endpoints) and the security rules forbid it without an ADR and a host allow-list. Telegram was also dropped by the owner.

## Decision
- **No feature may make HTTP requests to a URL supplied by a user.** Alerts are delivered by **email** (outbox) and **in-app notifications** only.
- Outbound hosts are fixed by configuration: `serpapi.com`, `api.anthropic.com`, the configured SMTP host.
- Alert delivery is pluggable at the service layer (`services/alerts.py` fans an alert out to an in-app notification row and an `alert_email` outbox row). A new channel = a new outbox `kind` + adapter, added via a new ADR.

## Alternatives considered
- **Webhooks with private-IP blocking** — still needs DNS-rebinding defences, redirect handling and an allow-list; not worth the risk for the hackathon.
- **Slack/Teams apps** — out of scope.

## Consequences
- Positive: removes an entire SSRF class; less code.
- Negative: no push integration into chat tools (email forwarding rules can cover it).
- Follow-ups: a future ADR may add allow-listed chat integrations.
