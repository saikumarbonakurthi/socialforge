# n8n workflows

n8n is only the scheduler and the courier. All logic and all state live in core. Workflows call core over HTTP with the shared bearer secret and never hold GitHub, Claude or Teams credentials.

| Workflow | Runs | Calls |
|---|---|---|
| `cron-sync` | every 15 minutes | `POST /jobs/sync` |
| `cron-nudges` | every hour | `POST /jobs/nudges` (core skips quiet hours, weekends and holidays per member) |
| `deliver-outbox` | every minute | `GET /outbox/pending`, posts each item, then `POST /outbox/{id}/sent` or `/failed` |
| `cron-standup` | every 15 minutes on weekdays | `POST /jobs/standup`, then `POST /jobs/standup_summary`. Core decides what is due, so extra knocks do nothing |
| `deliver-bot` | every minute | `POST /jobs/deliver_bot`. Core sends live bot DMs itself because the bot token never leaves core |

Not built yet because core has no endpoint for them: `cron-weekly-report` (Phase 6), `escalation-whatsapp` (Phase 7).

## Credentials
None are stored in n8n. The workflows read two environment variables, set by `docker-compose.yml`:
`NXSPRINT_CORE_URL` (default `http://core:8000`) and `NXSPRINT_API_SECRET` (from `.env`).
The Teams webhook URL is a secret and stays in core's environment. Core returns it with each item to post, so it never appears in a workflow file or an n8n credential.

## Import
1. `make up`, then open http://localhost:5678 and create the owner account.
2. Skip `deliver-bot` and `cron-standup` if you have not set up the bot (`docs/teams-setup.md`), `deliver-bot` answers 503 without it. For each file in `n8n/workflows/`: Workflows, menu, Import from file.
3. Open each workflow once and check the HTTP nodes show no red warnings.
4. Activate `cron-sync` and `cron-nudges`. Activating `deliver-outbox` is safe in dry run: while core is in `dry_run` it always returns an empty list.

These files were written to n8n's export format by hand and checked by tests for structure, authentication and real routes. They have not been imported into a running n8n yet. If an import complains, send me the message.

## Delivery guarantees
Core leases each live row for 10 minutes when n8n fetches it. n8n posts it, then reports back.
- Success: the row is marked delivered and its nudge becomes `sent`.
- Failure: the row is released straight away and retried on the next poll, up to 5 attempts, then it is parked as `dead` (visible at `GET /outbox`).
- n8n crashes after posting but before reporting: the row is handed out again after 10 minutes, so one duplicate message is possible. This is at least once delivery, not exactly once.

## Going live on one test channel
1. Stay in `dry_run` first. Check `GET /outbox` and read the messages. Nothing is posted.
2. Replace the placeholder config with real values and set `placeholder: false` (live mode refuses sample config).
3. Build the Power Automate flow in `docs/teams-outbound.md`, pointed at a test channel, and put its URL in `.env` under the variable named by `channels.dm_webhook_env`.
4. Set `NXSPRINT_MODE=live` and `NXSPRINT_DELIVERY_REDIRECT_TARGET=<your test marker>`, restart core. Core refuses to start if the webhook variable is missing or not https.
5. Messages now go to the test channel only, each prefixed with who it was really for.
6. Remove the redirect only when you are happy. Rows created while in `dry_run` are never sent, even after switching to live.

Smoke test without n8n (replace the secret):
```
curl -s -H "Authorization: Bearer $NXSPRINT_API_SECRET" localhost:8000/outbox/pending
```
