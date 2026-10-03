# Runbook: score the scans that finished before scoring

**When:** once, after deploying scoring (migrations 0020–0021 and the scoring code) to a database
whose scans already finished unscored. `serpsense score-backlog` scores every scan that succeeded
or is partial and has no scores, oldest first, so each one's usual is in place. It raises no
alerts: an alert is news, and theirs is old.

**Order matters.** A live scan that finishes before the backlog is scored is scored against a
history with gaps (its usual and its warm-up count miss the unscored scans), and scores are
append-only. So:

1. Stop the scan worker and beat: `docker compose stop worker beat`.
2. Migrate: `docker compose run --rm migrate`.
3. Score the backlog: `docker compose run --rm tools serpsense score-backlog`.
   It prints how many scans it scored. If a scan can't be scored, it stops and logs
   `scan.score_failed` with that scan's id; fix the cause and run it again (scored scans are
   skipped).
4. Start the worker and beat again: `docker compose up -d worker beat`.
