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
