# NxSprint runbook

Written for whoever is on the hook when something looks wrong. Everything marked *untested* has not been run against the real service yet (see `docs/go-live-checklist.md`).

## What runs
`core` (FastAPI, all logic), `postgres` (all state), `n8n` (schedules and the Teams webhook courier). Compose restarts all three after a crash. Core has a health check, n8n waits for it.

| n8n workflow | Calls | Cadence |
|---|---|---|
| `cron-sync` | `/jobs/sync` | 15 minutes |
| `cron-nudges` | `/jobs/nudges`, `/jobs/escalations` | hourly |
| `cron-standup` | `/jobs/standup`, `/jobs/standup_summary` | weekdays, 15 minutes |
| `cron-ceremonies` | `/jobs/planning_prep`, `/jobs/retro_prep` | weekdays, 30 minutes |
| `cron-weekly-report` | `/jobs/weekly_report` | weekdays, 30 minutes |
| `deliver-outbox` | `/outbox/pending`, `/outbox/{id}/sent`, `/failed` | every minute |
| `deliver-bot` | `/jobs/deliver_bot` | every minute |
| `escalation-whatsapp` | `/jobs/deliver_whatsapp` | every minute |
| `cron-maintenance` | `/jobs/dead_letters`, `/jobs/prune` | hourly |

Every job is safe to run twice: cooldowns, once per day or sprint or week markers and delivery leases stop duplicates. So if n8n was down, just start it, nothing needs replaying. The one thing that cannot be caught up is a window that has closed (for example the standup prompt window).

## Is it healthy?
```
make smoke                      # /health and /status from the host
curl -H "Authorization: Bearer $NXSPRINT_API_SECRET" localhost:8000/status
```
`/health` is open and only says the database answers. `/status` (bearer) shows mode, which features are on, seconds since the last sync per project, outbox counts by status, nudge counts and today's Claude spend.

### Metrics
`GET /metrics` (bearer) is Prometheus text. Scrape config:
```
scrape_configs:
  - job_name: nxsprint
    metrics_path: /metrics
    authorization: {credentials: "<NXSPRINT_API_SECRET>"}
    static_configs: [{targets: ["core:8000"]}]
```
Suggested alerts (starting points, not tuned):
- `nxsprint_db_up == 0`
- `nxsprint_last_sync_age_seconds > 3600` (sync runs every 15 minutes)
- `nxsprint_outbox_rows{status="dead"} > 0`
- `nxsprint_outbox_rows{status="pending"} > 50` for 15 minutes (delivery is stuck)
- `rate(nxsprint_http_requests_total{status_class="5xx"}[15m]) > 0`
- `nxsprint_llm_spend_usd_today` close to your ceiling

If the Teams webhook is itself what is broken, NxSprint's own dead letter alert goes through the same pipe and will not reach anyone. Rely on the Prometheus alert for that case.

## The kill switch
Set `NXSPRINT_MODE=dry_run` and restart core. From then on nothing is delivered: `/outbox/pending`, `deliver_bot`, `deliver_whatsapp` and the dead letter job all do nothing, and messages are only written to the outbox. Live rows already queued are kept, not sent, and go out if you switch back. To drop a queued message for good use `dismiss` below.
Narrower switches: remove `NXSPRINT_WHATSAPP_ENABLED` to stop WhatsApp only, unset the three `NXSPRINT_BOT_*` values to stop the bot only, set `NXSPRINT_DELIVERY_REDIRECT_TARGET` to send everything to a test target.

## Problems and what to do
| Symptom | Likely cause | Action |
|---|---|---|
| n8n shows `cron-sync` failing, `last_sync_at` old | GitHub token expired or lost access to the project, or rate limit | Read the failed execution body (it names the project and error). Rotate `NXSPRINT_GITHUB_TOKEN`. Rate limits are retried automatically with backoff. |
| No nudges | still `dry_run`; outside working hours or a holiday; cooldown; project not synced | `GET /projects/{id}/risk` shows what the rules see. `POST /jobs/nudges` response counts `deferred_outside_hours` and `skipped_cooldown`. |
| Messages stuck as `retrying` or `dead` | Power Automate flow disabled or URL rotated, Teams outage | `GET /outbox?status=dead` (or `retrying`) shows `last_error`. Fix the cause, then retry (below). |
| Bot replies or DMs fail | bot secret expired (Entra secrets expire, usually in 6 to 24 months), wrong messaging endpoint, member never opened the bot | Rotate `NXSPRINT_BOT_APP_PASSWORD`. Bot messages that give up fail over to the webhook automatically. |
| Everyone gets templates, not Claude wording | daily ceiling reached, or API errors | `GET /status` `llm.spent_today_usd`. The owner gets one alert per day. Raise `NXSPRINT_MAX_DAILY_USD` or wait for UTC midnight. Wording quality and prompts: `core/app/llm/prompts/nudge_v1.md`. |
| WhatsApp rows `dead` | template not approved or renamed, token expired, bad number, daily cap | `last_error` carries Meta's message (never the number). Fix, then retry. The cap is `max_per_person_per_day`. |
| Standup summary says "No reply yet" for everyone | members have not opened the bot, so they have no way to reply | `docs/teams-setup.md` step 4. |
| n8n down | container crashed or host reboot | Compose restarts it. Jobs catch up on their own. Check `nxsprint_last_sync_age_seconds`. |
| Core returns 500 or 502 on a job | one project failed, others ran. 502 means GitHub was the cause, 500 means our own error | The response `detail.projects` marks which. The stack trace is in core's log, search for the project name. |

## Dead letters
A live message that fails 5 times is parked. Waits between attempts are 1, 5, 15 and 60 minutes, so a short outage is ridden out before anything is parked.
- Bot messages that give up are sent through the webhook instead (the original is dismissed with a note).
- Everything else is reported to the owner once, as a single message listing counts per channel (never message text or numbers).
- Parked rows are visible with `GET /outbox?status=dead`.

```
curl -s -H "Authorization: Bearer $S" "localhost:8000/outbox?status=dead"          # what and why
curl -s -X POST -H "Authorization: Bearer $S" localhost:8000/outbox/42/retry        # cause fixed, try again (fresh 5 attempts)
curl -s -X POST -H "Authorization: Bearer $S" localhost:8000/outbox/42/dismiss      # give up on purpose, kept for the record
```
`retry` works only on rows that are `dead` or `retrying`. It is refused for rows that are delivered, dismissed (including bot messages that were failed over, retrying one would send it twice), currently `leased` (someone is posting it right now, wait a few minutes) or not failed yet. A `dismiss` is final. Only live rows can be retried or dismissed.

n8n reports each failure with the attempt number it was given. If a slow execution reports late, after the row was already handed out again, the old report is ignored so it cannot disturb the newer attempt.

## Secrets
All live in `.env` (never committed) and are read at start, so every rotation means restarting core.
| Secret | Rotate by |
|---|---|
| `NXSPRINT_API_SECRET` | change in `.env`, restart `core` and `n8n` together (n8n reads it from the same file) |
| `NXSPRINT_GITHUB_TOKEN` | new fine grained token with read access, replace, restart core |
| `NXSPRINT_GITHUB_WEBHOOK_SECRET` | change in `.env` and in the GitHub webhook settings at the same time |
| Teams webhook URLs (`TEAMS_WEBHOOK_*`) | regenerate the flow trigger URL, replace, restart core |
| `NXSPRINT_BOT_APP_PASSWORD` | new client secret in Entra, replace, restart. Put the expiry date in a calendar. |
| `NXSPRINT_WHATSAPP_TOKEN` | new system user token in Meta, replace, restart |
| `ANTHROPIC_API_KEY` | new key, replace, restart |
Webhook URLs and tokens are never logged (HTTP client logging is silenced) and phone numbers are masked in the API.

## Backups
**What matters.** Postgres holds all state. Snapshots are the only record of how long items sat in a status and of past sprints, so losing them loses velocity, cycle time and retro history. A re-sync only rebuilds from now. Also keep `.env` and `config/projects.yaml` somewhere safe (a password manager or secrets store, not the repo). n8n's own volume holds only the owner login and execution history, the workflows are in `n8n/workflows/`.

```
make backup                                   # writes backups/nxsprint-<time>.dump (pg_dump custom format)
make restore FILE=backups/nxsprint-<time>.dump
```
`backup` writes to a temporary file and only keeps it if the dump succeeded and is not empty, `restore` first checks that the file is a readable dump. *Both targets are untested, this sandbox had no Docker.*

Suggested routine: a nightly host cron (`0 2 * * * cd /path/to/nxsprint && make backup`), copy the dump off the machine, keep about 14 dailies, and do a test restore into a scratch copy once a quarter. At most a day of nudges, acks and standup replies is lost on a restore, snapshots older than the dump are safe.

**Restore.**
1. `docker compose stop core n8n`
2. `make restore FILE=backups/<dump>`
3. `docker compose up -d` (core runs `alembic upgrade head` on start, so an older dump is brought up to date)
4. `make smoke`, then `POST /jobs/sync` to catch up with GitHub.

## Upgrades and rollback
Take a backup first. `git pull`, `make up`; migrations run when core starts. Rolling back code across a migration needs `alembic downgrade <revision>` run inside the core container before you start the old version, or a restore of the pre upgrade dump (simpler and safer).

## Housekeeping
Set `NXSPRINT_RETENTION_DAYS` (30 or more) and `cron-maintenance` prunes, on its hourly run (cheap when nothing is old): finished outbox rows, log events (`sync`, budget alerts, webhook records) and LLM call rows older than that. The newest sync event per project is always kept, it records which items left the board. Nudges, snapshots, sprints, standups and the once per sprint, week and day ceremony markers are history and are never deleted. Unset keeps everything.

## Security notes
- Every endpoint except `/health`, `/webhooks/github` (HMAC) and `/webhooks/teams` (Microsoft token) needs the bearer secret. A test walks the whole API to keep it that way.
- FastAPI serves `/docs` and `/openapi.json` openly. They show endpoint names only. Keep core off the public internet except the two webhook paths (reverse proxy, TLS), and keep `/metrics` internal.
- NxSprint never writes to GitHub. Its only writes outside its own database are Teams and WhatsApp messages, all gated by `NXSPRINT_MODE=live`.

## Known limits
No leader election: run one core container. Time based windows use the container clock. Retries between n8n and core are at least once, so a duplicate message is possible if n8n dies between posting and reporting.
