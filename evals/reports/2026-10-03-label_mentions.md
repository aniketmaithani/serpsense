# Eval: label_mentions (label_mentions/v1)

- Run on 2026-10-03, model `claude-opus-5-5`, golden split `test`.
- Items: 32, answered: 32.
- Calls: 2 (0 without a usable answer); mean latency 11,522 ms.
- Cost: $0.0806 in all, $0.00252 per answered item.

## Agreement with the golden labels

| Field | Agreed | Rate |
|---|---|---|
| answered | 32/32 | 100.0% |
| is_about_brand | 32/32 | 100.0% |
| sentiment | 22/23 | 95.7% |
| sentiment[-1] | 12/13 | 92.3% |
| sentiment[0] | 5/5 | 100.0% |
| sentiment[1] | 5/5 | 100.0% |
| topic | 22/23 | 95.7% |
| is_complaint | 23/23 | 100.0% |
| severity±15 | 23/23 | 100.0% |

Sentiment, topic, complaint and severity are scored only on items about the brand;
`sentiment[c]` is the agreement on items whose golden sentiment is `c` (the per-class quality
AGENTS §7 tracks); severity agrees within 15 points. A label the model gives a text it
calls unrelated is scored as the product stores it.

## Disagreements

| Item | Field | Golden | Model |
|---|---|---|---|
| g005 | topic | app_experience | other |
| g039 | sentiment | -1 | 0 |

## Notes (added by hand)

- The golden labels were drafted by an AI agent and are not yet reviewed by a person
  (evals/golden/README.md), so this baseline is indicative: it can't pass the 90% gate yet.
- Two runs of this split today: $0.0800 (before the scorer matched stored labels), then this one,
  $0.0806. Eval spend to date: $0.16. The first run agreed on g039's sentiment and gave g005 the
  topic `product_quality`, so single items move between runs at this set size.
