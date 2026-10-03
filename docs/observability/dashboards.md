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

## Alert explanations (ADR-0008)

```sql
-- Alerts in the last day without an explanation, older than ten minutes (the job failed or
-- its nudge was lost), by rule. Watches `explanation.failed`.
SELECT a.rule, count(*) FROM alerts a
LEFT JOIN alert_explanations e ON e.alert_id = a.id
WHERE e.id IS NULL AND a.created_at BETWEEN now() - interval '1 day' AND now() - interval '10 minutes'
GROUP BY 1;

-- Explanation calls in the last day, by outcome, with their cost (micros).
SELECT outcome, count(*), sum(cost_micros) FROM llm_calls
WHERE task = 'explain_crisis' AND created_at > now() - interval '1 day' GROUP BY 1;
```

## Response drafts (ADR-0008)

```sql
-- Draft calls in the last week by outcome and effort, with cost (micros) and mean latency.
SELECT outcome, request_settings ->> 'effort' AS effort, count(*), sum(cost_micros),
       avg(latency_ms)::int AS mean_ms
FROM llm_calls WHERE task = 'draft_response' AND created_at > now() - interval '7 days'
GROUP BY 1, 2 ORDER BY 1, 2;

-- Succeeded draft calls that left no draft (refused for their citations): watches draft.uncited.
SELECT count(*) FROM llm_calls c
WHERE c.task = 'draft_response' AND c.outcome = 'succeeded'
  AND c.created_at > now() - interval '7 days'
  AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.llm_call_id = c.id);
```

