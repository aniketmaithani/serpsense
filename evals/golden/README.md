# Golden sets

Hand-checked samples the eval runner (`serpsense eval <task>`) scores a prompt against (ADR-0008, AGENTS §7). One JSONL file per LLM task.

## `label_mentions.jsonl`

### Status: draft labels, not yet reviewed by a person

**An AI agent drafted all 64 labels on 2026-10-03.** No person has checked them yet. Until a human labeller has reviewed every item, eval results on this set are only indicative. They **cannot be used to pass the ≥ 90% agreement gate** that keeps labelling in log-only mode (ADR-0008, BUILD_PLAN §7.4).

To review the set:

- read each item's `text` and `expected` without looking at a model's output;
- fix any label you disagree with and update its `notes`;
- look hardest at items marked `hard` and at the Ola Electric decision below;
- then replace this status block with the reviewer and the date.

### Runner contract: the brand context

The set describes one brand. The runner must send these variables exactly as written, together with `mentions`:

| Variable | Value |
|---|---|
| `brand` | `Ola` (the ride-hailing service: Play app `com.olacabs.customer`; competitors Uber, Rapido, Namma Yatri and inDrive) |
| `aliases` | `Ola Cabs, Olacabs, Ola Consumer, ANI Technologies` |
| `not_the_brand` | `Ola Electric (electric scooters and motorcycles, S1, showrooms and service centres, Ola Electric shares)` |

**Ola Electric is per-brand data, not part of the prompt.** The shared system prompt only says how to use `<not_the_brand>`, and it names no brand. With the context above, a mention only about Ola Electric gets `is_about_brand: false`. That covers its scooters, showrooms, service centres and shares, including bare-name queries such as "ola share price".

Twelve items depend on this decision: g017, g029, g030, g031, g044, g045, g046, g050, g058, g061, g063 and g064. If the owner decides the brand should include Ola Electric, move it from `not_the_brand` to `aliases` in the brand settings and relabel those twelve.

**All four Maps items (g030–g033) come from an Ola Electric location.** The recorded Maps place is the Ola Electric Experience Centre in Indiranagar. Each item's notes say so, and the labels stand as they are:

- **g030 and g031** are not about the brand: g030 names Ola Electric, and g031 describes vehicle servicing.
- **g032 and g033** are about the brand. Their text gives no clue to the business, and the model cannot see the location.

The fix is configuration, not labelling: remove that location from brand Ola, or move Ola Electric into the aliases.

### Sources

All data is real public data, recorded on 2026-10-03 with `gl=in` and `hl=en`.

| `source` | Recorded response | Items | About the brand | Hard | Dev / test |
|---|---|---|---|---|---|
| `play_review` | Play reviews of `com.olacabs.customer`: the 40 newest, and the "most relevant" reviews on the product page (the first five are also in `tests/fixtures/serpapi/ola/play_product.json`) | 29 | 27 | 14 | 15 / 14 |
| `maps_review` | Google Maps reviews of the recorded (Ola Electric) location, newest first | 4 | 2 | 2 | 2 / 2 |
| `news` | Google News `q=Ola`: 100 headlines (the first five are also in `ola/news.json`) | 13 | 6 | 9 | 6 / 7 |
| `serp_result` | Google `q=Ola` organic results, as `title\nsnippet` | 4 | 3 | 1 | 2 / 2 |
| `people_also_ask` | Google `q=Ola` related questions | 4 | 4 | 2 | 2 / 2 |
| `trends_query` | Google Trends related queries for `Ola`, 3 months, India (as in `ola/trends_related.json`) | 7 | 3 | 3 | 3 / 4 |
| `autocomplete` | Google Autocomplete `q=ola ` | 3 | 1 | 1 | 2 / 1 |
| **Total** | | **64** | **46** | **32** | **32 / 32** |

The unredacted recordings contain reviewer identities and are not committed. Only text was copied from them.

**Labels on the 46 items about the brand:**

| Measure | Breakdown |
|---|---|
| Sentiment | −1: 24 · 0: 12 · +1: 10 |
| Topic | other 8 · app_experience 6 · reliability 6 · product_quality 5 · pricing 5 · customer_service 5 · billing_refunds 3 · corporate 3 · safety 2 · legal_regulatory 2 · workforce 1 |
| Complaints | 18 |

**Item mix across all 64:**

| Kind of item | Items |
|---|---|
| Hinglish or Devanagari | g005, g013, g019, g022 |
| Sarcasm | g011, g022, g027 |
| Mixed (the driver praised, the brand blamed) | g024 |
| "Unknown" | g006, g007, g009, g020, g051 |
| Ambiguous by the prompt's rule | g048: an "OLA Taxi" Play listing that is really another company's app, which only its URL shows |
| Ola Electric versus Ola cabs | the twelve listed above |
| Name collisions | a person (g040), an oil company (g041), a German stadium wave (g042), a place (g043), a rummy app (g059) |
| Competitor alone | g060 |
| Query fragments | g055 to g064 |

### Dev and test splits

Every item has `"split": "dev"` or `"split": "test"`. **Prompt changes may be tuned only on `dev`.** That covers wording, rules, examples, thresholds and model settings: look only at dev items and dev results while iterating. **`test` decides the baseline** and the ship decision; run it to measure a candidate, not to find what to fix.

If a test item turns out to be mislabelled, fixing its golden label is allowed. Record the fix in the commit, then re-run the baseline.

**How the split was made.** The split is deterministic and stratified by source and difficulty:

1. Group items into strata by (`source`, `difficulty`), and sort the strata alphabetically.
2. Sort the items in each stratum by `id`.
3. Deal the items dev, test, dev, test, …, carrying the alternation over from one stratum to the next.

Each stratum's dev and test counts differ by at most one. The result is 32 dev and 32 test items, 16 of them hard in each split, and 9 not-about-the-brand items and 9 complaints in each.

**Splits are stored, not recomputed.** Never move an existing item, and never re-run the dealing over the file. A new item goes to whichever split has fewer items in its stratum; on a tie it goes to `test`.

**Known limits:**

- **Topics are not stratified.** Both `safety` items are in dev, both `legal_regulatory` items are in test, and the single `workforce` item is in dev.
- **The first test score is likely optimistic.** Prompt v1 was written with all 64 items visible, before the split existed. A review removed the clauses that mirrored specific items, but the score can still flatter the prompt.

### Scoring rubric

Score each split separately and gate on `test`. Every metric is also reported by difficulty and by source. Report `autocomplete` items separately: production routes them to `classify_autocomplete` (data model §6), so they stay out of the production-path figures. A missing or extra id counts as wrong on every field.

| Metric | Computed on | How |
|---|---|---|
| `is_about_brand` | all items | Precision and recall, with "not about the brand" as the positive class: precision is noise let through, recall is real mentions dropped. Also accuracy. |
| Sentiment | items expected about the brand | Per-class agreement (recall) for −1, 0 and +1, plus macro-F1 |
| Topic | items expected about the brand | Accuracy, per-topic recall and a confusion matrix |
| Severity | items expected about the brand | Share within ±15 of the expected value, plus mean absolute error |
| `is_complaint` | all items | Precision and recall |

**Minimum class size.** A class with fewer than 5 items in the split being scored is reported but not gated. A class here is a sentiment value, a topic, a source or a difficulty. On the current test split this leaves:

| Status | Classes |
|---|---|
| Gated | sentiment −1 (13), 0 (5) and +1 (5); not about the brand (9); complaints (9); easy (16); hard (16); the play_review (14) and news (7) sources |
| Reported only | every individual topic, because none reaches 5 items in test |

Topic is therefore gated through its overall accuracy.

**Proposed gates.** These need the owner's sign-off. They are measured on `test`, and only after a human has reviewed the labels:

- `is_about_brand` accuracy ≥ 90% and sentiment agreement ≥ 90%: the bar that ends log-only mode;
- topic accuracy ≥ 80%;
- severity within ±15 ≥ 80%.

**The 2-point rule in practice.** The ship gate in AGENTS §7 says quality may not fall more than 2 points on any class. A gated class here holds 5 to 16 items, so one item moves its score by 6 to 20 points. In practice the rule means that no gated class may lose an item compared with the baseline.

### Anonymisation

- Only the text a parser would keep was copied: the review snippet, the headline, the result title and snippet, the question or the query. Reviewer names, avatars, profile links, review ids, dates, like counts, URLs and the developer's replies were not copied.
- Person names inside texts were replaced with `[name]`. This affected two headlines. In one of them (g040) the first name "Ola" is kept, because the name collision is what the item tests; the surname is replaced.
- No handles or @mentions occur in the selected texts. Place and business names were kept (a hospital, a market, a rival service centre).
- A script confirmed that none of the reviewer names, avatar links, profile links or review ids in the raw recordings appear in this file.

### Schema

One JSON object per line:

| Field | Meaning |
|---|---|
| `id` | `g001`, `g002`, …: stable, never renumbered or reused; sent as the mention id |
| `source` | a `domain.enums.MentionSource` value |
| `language` | the language the search ran in, which is what the pipeline sends as the `language` attribute (from `hl`). It is not a detection: Hinglish and German texts still say `en` |
| `text` | the mention text as the parser would produce it; truncated headlines are kept as served |
| `expected.is_about_brand` | boolean; listed first because the model decides it first |
| `expected.sentiment` | −1, 0 or 1 |
| `expected.severity` | 0–100, labelled near the middle of its band (see the prompt's anchors) |
| `expected.topic` | a `domain.enums.Topic` value |
| `expected.is_complaint` | boolean; true only for customer reviews reporting a problem |
| `expected.reason` | a reference explanation for human reviewers; not scored |
| `difficulty` | `easy`, or `hard` when a careful human could hesitate |
| `split` | `dev` or `test` (see above) |
| `notes` | why the label is what it is, including the nearest wrong answer |

When `is_about_brand` is false, the other fields are fixed: sentiment 0, severity 0, topic `other` and `is_complaint` false. This is the same convention as the prompt.

### Known gaps

- **Topic classes with no real examples.** The recording has no real `fraud_scam`, `privacy_security` or `marketing_ethics` mentions. Per-class scores for these topics stay undefined until real samples are added, for example from scans of fraud or scam searches. Do not invent texts to fill these classes.
- **Thin classes.** `safety` (2 items), `legal_regulatory` (2) and `workforce` (1) are below the class-size floor and are reported only.
- **Missing cases.** The set has no balanced review with sentiment 0, no neutral news about the brand, and no review that literally praises the driver while complaining about the price (g024 is the closest real one).
- **Prompt injection.** No real sample exists. Cover it with synthetic security tests, not in this set.
- **Autocomplete routing.** Data model §6 sends autocomplete to `classify_autocomplete`, not to `label_mentions`. The three `autocomplete` items stay here for query-fragment behaviour, but are excluded from production-path figures. Move them to `classify_autocomplete.jsonl` once that set exists.

### Adding or changing items

1. Use only real public text from a recorded response. Never write or paraphrase a sample.
2. Copy only the text. Replace any person name or handle inside it with `[name]`. Never copy reviewer names, avatars, links or ids.
3. Append the item with the next id. Never renumber or reuse ids.
4. Assign its split by the rule above. Never move existing items between splits.
5. Label it by the rules of the active prompt version and the runner's brand context. Write `notes` for anything that isn't obvious, including the nearest wrong answer. Mark the item `hard` if a careful human could hesitate.
6. Don't copy golden texts or their distinctive wording into a prompt's rules or examples. That inflates the score.
7. Changing an existing label changes the baseline. Say so in the commit and re-run the baseline eval.
8. Check that every line parses:
   `python3 -c "import json; [json.loads(l) for l in open('evals/golden/label_mentions.jsonl', encoding='utf-8')]"`
