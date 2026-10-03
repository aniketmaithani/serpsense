# Runbook: outbox backlog or dead emails

**Signals:** `outbox.send_failed` (warning) and `outbox.dead` (error) in the outbox worker's logs;
pending emails due for more than 5 minutes (query below).

```sql
-- Due and still pending, oldest first: the dispatcher's backlog.
SELECT kind, count(*), min(next_attempt_at) FROM outbox_messages
WHERE status = 'pending' AND next_attempt_at <= now() GROUP BY kind;
-- Why the last attempts failed (codes only; never addresses or content).
SELECT error_code, outcome, count(*) FROM outbox_attempts
WHERE attempted_at > now() - interval '1 hour' AND outcome <> 'sent' GROUP BY 1, 2;
```

**Diagnose:**
1. Nothing is being attempted at all: the dispatcher isn't running. Check
   `docker compose ps worker-outbox beat` and the beat schedule (`dispatch_outbox`, every 15 s).
2. `smtp.unavailable`, `smtp.timeout` or `smtp.deferred`: the mail server is down, unreachable
   or busy. Emails back off (1 minute, doubling, at most an hour); after 8 failures, about two
   hours, an email is `dead`, so fix the cause within that window.
3. `smtp.auth_failed` or `smtp.sender_refused`: `SMTP_USER`, `SMTP_PASSWORD` or `EMAIL_FROM` is
   wrong. Fix `.env` and restart `worker-outbox`; the emails are retried within the same window.
4. `smtp.rejected` or `smtp.recipient_refused`: the server refused the email for good; the
   message is `dead`. Check the recipient address in the app, not in logs.
5. `smtp.invalid_recipient`, `smtp.invalid_message`, `outbox.render_failed` or
   `outbox.unknown_template`: the message itself is wrong (not one bare address, a line break in
   the subject, missing template fields, a template the dispatcher doesn't know). It is `dead` and
   doesn't hold up the others; fix the code that wrote it.

**Recover:** a `dead` message is not retried. To resend one after fixing the cause, the business
action that wrote it must write a new message (a new dedupe key); never edit attempts, which are
append-only.
