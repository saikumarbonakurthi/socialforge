# Go live checklist

Everything the code needs from you, in the order to do it. Nothing here was possible for me to do or to test from the build environment. Tick as you go.

## A. Decisions only you can make (all go in `config/projects.yaml`, copy from `projects.example.yaml`)
The example file is full of placeholders and is marked `placeholder: true`, which live mode refuses. Replace every value, then set `placeholder: false`.

- [ ] GitHub: `org`, `repo`, `project_number`. Confirm your board has Status, Estimate (a **number** field), Sprint (an **iteration** field) and Priority (single select). If Estimate is something else, the parser and the overload rule need changing.
- [ ] Field names and the board's `In Progress` and `Done` status names (`statuses`).
- [ ] Members: names, GitHub logins, `teams_user_id` (the Entra object id), role (exactly one `lead`), timezone, `capacity_points`. Where does capacity come from? Today it is just this number.
- [ ] Working days, working hours, quiet hours, holidays (an explicit list, may be empty), standup time and `standup_summary_time`.
- [ ] Thresholds (stale days, PR hours, blocked days, parallel items, `sprint_risk_gap_pct`), and one cooldown per rule (nine rules).
- [ ] Escalation hours (channel, lead, whatsapp: must increase) and the `critical` block (goal label, production blocker label, "not started" statuses, sprint end window, unowned hours).
- [ ] `priority_order` as your board spells it, and the `ceremonies` timing (planning and retro prep days before sprint end, report weekday and time).
- [ ] Routing: unowned items and sprint risk go to the lead, and a stale item that is also blocked is not nudged twice. Say if either is wrong.
- [ ] Repository: NxSprint lives in `nxsprint/` of the SocialForge repo because that is where the work started. Move it to its own repo before real use.

## B. Accounts and credentials to create
- [ ] GitHub fine grained token, read only on the org project and issues (`NXSPRINT_GITHUB_TOKEN`), plus an optional webhook secret.
- [ ] Anthropic: key, then choose a model (`NXSPRINT_MODEL`), a daily ceiling (`NXSPRINT_MAX_DAILY_USD`) and the model's two prices per million tokens. Leave all blank to use plain templates only.
- [ ] Power Automate flow "When a Teams webhook request is received" pointed at a **test channel** (`docs/teams-outbound.md`), URL into `.env` under the variable named in `channels.dm_webhook_env` (and `team_webhook_env`).
- [ ] Azure bot registration and Teams app package (`docs/teams-setup.md`), three `NXSPRINT_BOT_*` values. Public https URL for `/webhooks/teams`.
- [ ] Optional, last: Meta WhatsApp Cloud API account, approved template, lead opt in (`docs/escalation.md`).

## C. Bring up, in this order
1. [ ] `make up`, then `make smoke`.
2. [ ] Import the nine workflows into n8n (`docs/n8n.md`), activate `cron-sync` and `cron-nudges` first. Run `POST /jobs/sync` once by hand and look at `/status`.
3. [ ] **Soak in `dry_run` for a few days.** Read `GET /outbox` daily. Judge tone, thresholds and who gets what. Nothing is sent.
4. [ ] If using Claude wording: run `NXSPRINT_LIVE_LLM=1 pytest core/tests/golden/test_live.py -s` and read the output. Prose claims the validator cannot check (for example "this will miss the goal") only a human can catch.
5. [ ] Go live on the test channel: `NXSPRINT_MODE=live` with `NXSPRINT_DELIVERY_REDIRECT_TARGET` set. Everything goes to the test target, prefixed with who it was meant for.
6. [ ] Watch `nxsprint_outbox_rows`, dead letters and the owner alert. Practise `retry` and `dismiss` once, and note that a dismiss is final.
7. [ ] Only then remove the redirect. Enable the bot for members. Enable WhatsApp last, if at all.
8. [ ] Set up backups and do one test restore (`docs/runbook.md`).

## D. Never run against the real thing
Honest list of what is only tested with fakes or fixtures:
- The GitHub GraphQL query and parsing (fixtures are hand written, never recorded from GitHub).
- The Claude calls (scripted fake client). The golden tests cover the validator, not the model.
- All n8n workflows (checked for structure, auth and real routes, never imported).
- Power Automate delivery, the Teams bot (token verification uses a locally generated key), the Azure registration and the Teams manifest.
- WhatsApp Cloud API.
- Docker: `docker compose config` validates, but `make up`, `make backup` and `make restore` were never run (no Docker daemon in the build environment).
- Postgres: tests run on SQLite. Migrations are plain and round trip on SQLite, but the first start on Postgres is a real test.

## E. Known gaps (not bugs, not built)
Team summary and ceremonies are plain templates (no Claude wording, no Adaptive Card). Level 2 escalation names the person but does not @mention. PR review waiting is implemented but needs PR review data the sync does not fetch yet. WhatsApp is one way. Capacity ignores leave.
