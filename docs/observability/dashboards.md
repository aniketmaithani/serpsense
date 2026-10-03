# Dashboards

Queries for the operations dashboard (ADR-0012). Until metrics land they run against Postgres;
each names the failure mode in `alerts.md` it watches.

## Outbox (ADR-0010)

```sql
-- Backlog: due and still pending, by kind, with the oldest due time.
SELECT kind, count(*) AS due, min(next_attempt_at) AS oldest
FROM outbox_messages WHERE status = 'pending' AND next_attempt_at <= now() GROUP BY kind;

-- Outcomes in the last 24 hours, by error code (codes only; never addresses or content).
SELECT outcome, error_code, count(*) FROM outbox_attempts
WHERE attempted_at > now() - interval '24 hours' GROUP BY 1, 2 ORDER BY 3 DESC;

-- Dead emails in the last day, by kind.
SELECT m.kind, count(*) FROM outbox_messages m
WHERE m.status = 'dead' AND m.created_at > now() - interval '1 day' GROUP BY 1;
```
