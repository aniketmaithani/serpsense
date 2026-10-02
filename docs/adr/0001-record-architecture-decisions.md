# ADR-0001: Record architecture decisions

- **Status:** Accepted (2026-10-03)
- **Date:** 2026-10-02
- **Deciders:** Project owner

## Context
The project uses reviewer agents (`architect-reviewer` and others) that refuse new infrastructure, datastores, queues or major libraries without an accepted ADR. We need a lightweight, reviewable record of those decisions.

## Decision
- Decisions live in `docs/adr/NNNN-kebab-title.md` using `template.md`, indexed in `docs/adr/README.md`.
- Status lifecycle: Proposed → Accepted / Rejected → Superseded. Only a human sets `Accepted`.
- Accepted ADRs are binding; contradicting one requires a superseding ADR in the same PR as the change.

## Alternatives considered
- **Decisions only in `BUILD_PLAN.md`** — mixes plan and decisions; the plan changes daily, decisions shouldn't.
- **No records** — reviewers would reject every infra change.

## Consequences
- Positive: reviewers have ground truth; judges can read the reasoning.
- Negative: small writing overhead per decision.
