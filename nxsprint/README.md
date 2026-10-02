# NxSprint

AI scrum master for SRIA Infotech. Reads GitHub Projects v2, detects problems with deterministic rules, nudges the right person, prepares ceremonies. Dry-run by default. Humans stay accountable.

Status: **all 8 phases built** (sync, rules, nudges, Claude wording, n8n, Teams two way, standup, ceremonies, escalation with feature flagged WhatsApp, hardening). Dry run by default. Nothing has been run against real GitHub, Teams, Claude, WhatsApp or Docker yet: start with `docs/go-live-checklist.md`.

## Layout
`core/` FastAPI service (`app/{api,domain,integrations,llm,jobs}`, `alembic/`, `tests/`), `n8n/workflows/`, `config/`, `docs/`.
Lives in `nxsprint/` because this repo root belongs to SocialForge.

## Run
```
make env     # creates .env (generated secrets) and config/projects.yaml from SAMPLE values
make up      # postgres + core (runs migrations) + n8n
curl localhost:8000/health
```
See it with zero integrations: `make demo` seeds a fake project through the real sync and rule path, then prints the board and the exact messages NxSprint would send.

Sync: `POST /jobs/sync` (bearer secret) pulls every configured project from GitHub (needs `NXSPRINT_GITHUB_TOKEN`, read only) and writes a snapshot only when an item changed. `POST /webhooks/github` verifies the HMAC signature and records the event; it does not trigger a sync yet.

Nudges: `POST /jobs/nudges` runs the 7 rules, applies cooldowns, working hours (in each member's timezone), weekends and holidays, and queues one nudge plus one `outbox` row per finding. Nothing is delivered anywhere yet; read the result at `GET /outbox` and `GET /nudges`. `POST /nudges/{id}/ack` acknowledges, `GET /projects/{id}/risk` shows current findings without creating nudges.
Findings with no owner (unassigned item, sprint at risk) go to the project lead. `PR_WAITING_REVIEW` is implemented and tested but dormant: the sync does not fetch PR review data yet.

Wording (Phase 3): rules decide who and why; Claude only phrases. Off by default. Set `NXSPRINT_MODEL`, `NXSPRINT_MAX_DAILY_USD` and both `NXSPRINT_PRICE_*` variables to turn it on (startup refuses a model without a ceiling and prices). Each message is checked (greeting, length, no dashes, no invented numbers, links or mentions, title kept, link placeholder used once); one retry, then the deterministic template is sent instead. Only issue titles, status names, counts and first names are sent to the model, never logins or links (the real link is substituted after validation). Every call is logged to `llm_call` with tokens and an estimated cost; when the day's ceiling is hit, templates are used and the owner gets one alert in the outbox. Prompt: `core/app/llm/prompts/nudge_v1.md`. Golden set: `core/tests/golden/` (offline), and `NXSPRINT_LIVE_LLM=1 pytest tests/golden/test_live.py -s` runs it against the real model (costs money).

Delivery (Phase 4): in `dry_run` nothing is ever sent. In `live`, the `deliver-outbox` n8n workflow fetches leased rows from `GET /outbox/pending`, posts them to the Power Automate webhook, and reports back (`POST /outbox/{id}/sent` or `/failed`, 5 attempts then dead). Live mode refuses to start without an https webhook, and `NXSPRINT_DELIVERY_REDIRECT_TARGET` sends everything to a test target first. See `docs/n8n.md` and `docs/teams-outbound.md`.

Teams two way (Phase 5): with the Azure bot configured (`docs/teams-setup.md`), people reply `ack` to nudges and answer a daily standup prompt (Done, Doing, Blocked); one deterministic team summary is posted at `standup_summary_time`. The bot is used only for people who have messaged it, everyone else is reached through the webhook. Inbound requests are verified against Microsoft's signature, audience, issuer and service URL.

Ceremonies (Phase 6): planning proposal and retro prep go to the project lead, a weekly report goes to the owner. Proposals only, nothing on the board is changed. Timing and priority order are required config (`priority_order`, `ceremonies`). See `docs/ceremonies.md`; `make demo` prints all three.

Escalation (Phase 7): unacknowledged nudges climb a ladder (team channel, then the lead by Teams DM, then for critical items only a WhatsApp template to the lead). WhatsApp is off by default behind `NXSPRINT_WHATSAPP_ENABLED`, capped per person per day, and impossible while a test redirect is active. What counts as critical is required config (`critical`). See `docs/escalation.md`.

Operations (Phase 8): retries back off (1, 5, 15, 60 minutes) and give up after 5, parked rows are failed over (bot to webhook), reported to the owner once, and can be retried or dismissed by hand. One project failing does not stop the others. `GET /status` and `GET /metrics` (Prometheus) need the bearer secret. `make backup`, `make restore`, `make smoke`. The coverage gate for `domain/` is 85%. See `docs/runbook.md`.

Dev without Docker (Python 3.12): `make install && make migrate && make test && make lint`.

## Config
`config/projects.yaml` (see `projects.example.yaml`). Validated at startup, bad config stops the service.
Nothing that messages a human has a default: holidays, quiet hours, thresholds, cooldowns, escalation hours and channels are all required.
The example is marked `placeholder: true`, which the service refuses in `live` mode.

## Modes
`NXSPRINT_MODE=dry_run` (default) or `live`. See `.env.example` for all variables.
