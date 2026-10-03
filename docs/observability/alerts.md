# Alerts

Alert rules as PromQL for any Prometheus (ADR-0012). Every failure mode has a rule here or a
written decision to defer it. Metrics are computed from Postgres ledgers at scrape time; until
they land, the deferred entries below say what watches the failure meanwhile.

| Failure mode | Signal | Status |
|---|---|---|
| The dispatcher stops (Beat or the broker down) | no `dispatch.completed` log for 15 min; later the age of the newest scheduled scan | Deferred to the P1 `queue-backlog` and `redis-unavailable` runbooks. Meanwhile the beat container's healthcheck watches its schedule file. |
| A job can't be enqueued after commit | `job.enqueue_failed` (warning) | Deferred to `redis-unavailable` (P1). The scan stays queued and the sweep sends it again within 5 min, so nothing is lost. |
| A brand is never scanned (bad schedule or settings) | `dispatch.brand_rejected` (warning, with the error class) | Deferred: a per-brand error belongs on the brand's settings page, which will show it; it is not an on-call page. |
| A scan runs past its time limit | `scan.timed_out` (warning); failed scans with reason `timed_out` | Covered by the P0 `scan-failures-high` alert when scan metrics land. |
| Labelling stops early (the model down or refusing) | `labelling.stopped` (warning); `llm_calls` rows with outcome `failed` | Covered by the P0 `llm-errors-high` alert when LLM metrics land. The texts stay pending and the next scan labels them. |
| A user's monthly LLM budget runs out | `llm_budget.exhausted` | Deferred to the P1 `llm-budget-exhausted` runbook. Scans go on without labels, and the usage page will show the spend. |
| A surface stops short in a scan (SerpApi failing, a circuit open, a budget spent) | `surface.stopped` (info, with surface, outcome and code); `scan_surface_results` rows not `succeeded` | Covered by the P0 `serpapi-errors-high` alert when SerpApi metrics land; the scan finishes `partial` and the overview shows which surface was missed. |
| A scan runs out of time mid-collection | surface outcome `failed` with code `scan.deadline_passed` | Covered by the P0 `scan-failures-high` alert with the scan's other failures; no search starts past the deadline, so nothing more is billed. |
| A scan stage raises (a database error, a bad settings snapshot) | `scan.stage_failed` (error, with the error class); scans finished `failed` with reason `stage_failed` | Covered by the P0 `scan-failures-high` alert when scan metrics land. |
| A failed scan can't even be finished (the database down) | `scan.finish_failed` (error) | Covered by `scan-failures-high` through the sweep, which times the scan out within its limit; `postgres-unavailable` (P1) for the cause. |
| A finish arrives after the sweep timed the scan out | `scan.finish_lost` (warning) | Deferred: a sign the time limit is too tight, not an outage; reviewed on the scans dashboard with `scan.timed_out`. |
| A scan is skipped for a spent search budget | scans finished `skipped` with reason `budget_exhausted` | Deferred to the P1 `serpapi-quota-low` runbook; the user sees the skip in the app (#96 adds the notification). |
