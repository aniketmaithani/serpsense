# Scoring (version `s1`)

How SerpSense turns what a scan saw into scores (BUILD_PLAN §11). The formulas are pure
functions in `src/serpsense/domain/scoring/` (`surfaces.py`, `crisis.py`). The weights, thresholds and warm-up also live in the
database as reference rows (migration 0020), and `v_scan_scores` derives health, crisis and level
from the stored surface scores and components using the same numbers.

Every score is an integer from 0 to 100. Every ratio is rounded half up with exact integer
arithmetic, the way Postgres rounds a positive numeric (Python's `round()` would round 62.5 to
62). The search page is the one score that needs floating point: its log weights make a share
that is rounded half up. Labels come from the model: sentiment is −1, 0 or 1, and out-of-range
inputs are refused. A mention the model marks as not about the brand (it only shares the name,
e.g. "ola" the greeting) is left out before scoring.

## Surface scores

A surface that showed nothing about the brand scores *none* and drops out of health.

| Surface | Score |
|---|---|
| Search page | 100 − the negative share of the results. Each result is weighted 1/log₂(rank + 1); an unranked result weighs like rank 10. |
| Autocomplete | 100 − a penalty per negative suggestion: 45, 40 and 35 for the top three places, 30 below; never below 0. |
| AI Overview | Its sentiment: 0, 50 or 100. |
| News | (positive + ½ neutral) ÷ all articles of the last seven days. |
| Play, Maps | ½ the store rating (1–5 stars as 0–100) + ½ the sentiment of the newest 50 reviews (−1…1 as 0–100), newest first. Either half alone if the other is missing. Maps has no stored place rating yet, so it uses the reviews' sentiment alone. |

## Health

The weighted mean of the surfaces that showed something, with weights in basis points:
search page 2500, autocomplete 2000, AI Overview 1500, news 1500, Play 1500, Maps 1000. A
missing surface's weight is spread over the others. Trends and YouTube don't count towards
health.

## Crisis

A crisis is a **change**, so every component compares this scan with the brand's **usual**,
or counts only what is new. A brand that always has some negative reviews and articles is not
in crisis because of them. The usual of a count is the median (the lower middle value) of the
brand's newest eight earlier scans, and never below 2, so a quiet brand isn't alarmed by its
second complaint. A mention is **new** in the scan that first saw it, and a surface's mentions
count as new only once that surface has been collected before: its first collection is the
baseline, not a crisis. The surfaces are the health surfaces: search page, autocomplete, AI
Overview, news, Play and Maps.

**Warm-up.** On a brand's first scans everything it shows is new, so its crisis has **no level**,
and raises no alert, until it has 3 earlier scored scans (succeeded or partial). A newly added competitor therefore
never sends a "Competitor: …" alert on its first scan. The components are still recorded.

Each component is 0–100 and weighted in basis points; a missing component counts as 0.

| Component | Weight | Value |
|---|---|---|
| Velocity | 3000 | Negative mentions new in this scan against their usual: 0 at or below it, 100 at four times it. |
| Spread | 2500 | The share of the surfaces that showed something whose new negative mentions exceed that surface's own usual. |
| New negative autocomplete | 2000 | A negative suggestion first seen for the brand in this scan: 100, 90 or 80 in the top three places, 70 below. A suggestion that drops out and comes back is not new. |
| Rising negative Trends query | 1500 | A negative rising query first seen for the brand: 60 for one, plus 20 for each more, capped at 100. |
| Press in 48 hours | 1000 | Negative articles new in this scan and published in the last 48 hours: half the sum of their severities, capped at 100. |

Crisis = Σ weight × value ÷ 10 000. Levels: **low** below 40, **medium** from 40 to 69,
**high** from 70. Compare levels by their rank, never as strings, since "low" sorts after
"high". Velocity, spread and press alone reach at most 65, so with the plan's weights a crisis
reaches **high** only with a search-visible signal (a new negative suggestion or a rising
negative query). That is intended: what someone sees when they Google the brand is what this
product watches.

## Where the inputs come from

`domain/scoring/scan.py` scores one scan; the score store (`adapters/db/score_store.py`) reads
its inputs from what the brand's scans recorded:

- **Labels.** A mention counts once its latest revision is labelled by its own labelling task
  (`label_mentions`, `classify_autocomplete` or `assess_ai_overview`; `domain/labelling.py`).
  The task's active prompt's label comes first; until a new prompt version has labelled the
  mention, its newest earlier label stands, so a new version doesn't reset the brand's usual. A
  mention without a label, or labelled as not about the brand, is left out, so a surface whose
  task has no prompt yet (autocomplete, AI Overview) scores none.
- **New.** A mention is new in the brand's earliest scan that observed it, scored or not.
- **Collected before.** A surface was collected before a scan when an earlier scan of the brand
  has a `succeeded` result for it.
- **Play.** The mean of the scan's app ratings, and the scan's labelled reviews, newest
  published first.

## Choices the plan left open

BUILD_PLAN §11 names the inputs but not every number. These are this version's choices:

- **Unranked results** weigh like rank 10.
- **Autocomplete:** the extra penalty for the top three places.
- **The usual:** the median of eight scans with a floor of 2; four times the usual means velocity 100.
- **Change, not level:** crisis counts only new mentions, and spread compares each surface with its own usual.
- **Baseline per surface, not per query:** adding a search prefix or a language to a surface
  already collected makes its new results count as new, so a settings change can raise the
  crisis once.
- **Labelled late:** a mention's first sighting stays the scan that first saw it. If that scan's
  labelling didn't finish (a model outage, or the per-run cap), the mention is labelled later
  but is never new: the crisis components miss it, and health counts it from the next scan.
- **Press:** uses severity.
- **New negative suggestions:** the scale for them.

Changing any of these is a new scoring version, recorded with each score run.
