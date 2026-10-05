# ADR-0008: Anthropic Claude for LLM features

- **Status:** Accepted (2026-10-03)
- **Date:** 2026-10-02
- **Deciders:** Project owner

## Context
SerpSense must turn hundreds of heterogeneous mentions (reviews, headlines, autocomplete suggestions, AI Overview text) into sentiment/topic labels, group them into narratives, explain crises in plain language, and draft responses with citations. Users want per-task control, including a "High thinking" option.

## Decision

### Provider and models
- **Anthropic Claude API** via the official `anthropic` Python SDK, imported only in `adapters/llm/anthropic_client.py` behind the `LLMClient` port. Tests use scripted fakes (`tests/fakes.py`). *Amended (#117):* replay mode is served by `adapters/llm/replay.py` (`ReplayLlm`), which answers only from recorded model output, of the same brand, text and prompt version, records each answer as served by the model `replay` at no cost, and never invents an answer: a text with nothing recorded is left unlabelled. *Amended (#233):* recordings may also hold alert explanations and drafts, so replay mode explains alerts and drafts a story's replies with recorded words, and fails for anything unrecorded; drafting is offered in replay mode for that reason. The built-in demo story's model output (labels, stories, explanations, drafts) was written for the demo, not produced by a model; a replayed draft cites only the texts its recording names, and replay-mode pages say the demo is fictional and its AI text written for it.
- Default model for every task: **`claude-opus-5-5`**, with per-task effort. Users may choose **`claude-sonnet-5-5`** or **`claude-haiku-4-5`** per task. `ALLOWED_MODELS` caps choices.
- Per-model capability rules (enforced in `domain/llm_capabilities.py`, UI and gateway):

| Model | Effort (`output_config.effort`) | Thinking | Temperature |
|---|---|---|---|
| Opus 5.5 | low…max; API default is `medium`, so always sent explicitly | always adaptive; cannot be disabled | rejected |
| Sonnet 5.5 | low…max (default high) | adaptive; "off" = `between_tools`, only at effort ≤ high | non-default rejected |
| Haiku 4.5 | not supported | `budget_tokens` (≥1024, < max_tokens) or off; effort presets map to off/2k/8k/16k/32k | allowed only with thinking off |

- Structured outputs (`output_config.format` / `messages.parse`) for all tasks; forced `tool_choice` is not used (rejected by Opus 5.5 / Sonnet 5.5).
- `stop_reason` is checked on every response (`refusal`, `max_tokens`). Server-side refusal fallback (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`) is on by default for Opus 5.5 / Sonnet 5.5 and can be switched off.
- Prompt caching for the stable prefix (system prompt, topic taxonomy, examples). Message Batches for scheduled labelling (50% cheaper, asynchronous) is **P2** and would need its own polling job; not part of the initial build.
- Three models are offered (not two) because the owner asked for per-task model choice; the capability table above keeps the extra option cheap to support.

### Data sent to the provider
- Public mention text (review text, headlines, snippets, suggestions, AI Overview text), brand name/aliases, topic taxonomy, brand tone notes.
- **Author names and handles are stripped** before sending. No user emails or account data are sent.
- Mention text is treated as untrusted input: wrapped in delimiters, the system prompt instructs the model to treat it as data only.

### Retention and privacy
- **Owner decision (2026-10-02):** no additional provider-retention requirement is imposed for this project. Rationale: only public search content is sent (author names stripped); no account data, emails or user-entered personal data reach the provider.
- Prompts and completions are never logged; ledger rows store ids, token counts, cost, `prompt_version`, settings — not content.
- Ledger rows record both `requested_model` and `served_model` (from the response), and cost is priced on the served model, because refusal fallback can switch models.

### Cost ceilings (estimates — replace with eval-runner measurements)
| Item | Ceiling |
|---|---|
| Labelling, per mention (Opus 5.5, effort low, cached prefix) | ≤ US$0.003 |
| Narrative grouping + crisis explanation, per scan | ≤ US$0.15 |
| Draft, per draft at `xhigh` ("High thinking") | ≤ US$0.75 |
| Per user per month (default budget, configurable) | US$30 — hard stop + notification |
| Every user together per UTC day (`LLM_DAILY_GLOBAL_CAP_MICROS`; amendment 2026-10-05) | US$5 — hard stop, like SerpApi's daily cap; bounds the total when sign-up is open (ADR-0015) |
Amounts are stored as integer micros (see data model).

### When the model is down or slow
- Timeouts per task; SDK retries (2) plus our capped backoff.
- A scan never fails because of the LLM: collection and deterministic scoring complete and the scan finishes `partial`. Mentions without an enrichment for the active prompt version are "pending" (derived, not stored) and are enriched by the brand's next scan. Sentiment-dependent scores for that scan are shown as partial.
- Drafting failures show a retry button; nothing is auto-sent.

### Where a human decides
- The model **labels, groups, explains and drafts. It never sends, publishes or acts.**
- Alerts fire from **deterministic rules over scores**; the LLM writes only the explanation text.
- Drafts are labelled "AI-generated draft" and must be copied by a person.
- New classifiers start in log-only mode until they agree with human labels on the golden set at ≥ 90%.

### Quality process
- Prompts are versioned files (`prompts/<task>/v<N>.md`, `prompt_version`).
- Golden sets in `evals/golden/<task>.jsonl` (real public samples, anonymised, incl. hard and "unknown" cases); eval runner `serpsense eval <task> [--sample N]` reports quality, cost/item, p95 latency to `evals/reports/`.
- Ship gate: quality not down > 2 points on any class; cost/item not up > 15%.

## Alternatives considered
- **OpenAI / Gemini** — viable; Claude chosen for structured outputs, adaptive thinking + effort control, and refusal fallback.
- **Local models** — no GPU budget; quality/latency risk for an 8-day build.
- **Rule-based sentiment only** — cannot group narratives or draft responses.

## Consequences
- Positive: rich, configurable intelligence layer; measurable quality.
- Negative: per-call cost; provider dependency (mitigated by the port + fake + graceful degradation).
- Follow-ups: runbooks `llm-errors-high.md`, `llm-budget-exhausted.md`; golden sets before the first prompt ships.
