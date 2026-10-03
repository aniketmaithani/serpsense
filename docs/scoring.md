# Scoring (version `s1`)

How SerpSense turns what a scan saw into scores (BUILD_PLAN §11). The formulas are pure
functions in `src/serpsense/domain/scoring/` (`surfaces.py`, `crisis.py`). The weights and thresholds will also live in the
database as reference rows, and `v_scan_scores` will derive health, crisis and level from the
stored surface scores and components using the same numbers.

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

## Choices the plan left open

BUILD_PLAN §11 names the inputs but not every number. These are this version's choices:

- **Unranked results** weigh like rank 10.
- **Autocomplete:** the extra penalty for the top three places.

Changing any of these is a new scoring version, recorded with each score run.
