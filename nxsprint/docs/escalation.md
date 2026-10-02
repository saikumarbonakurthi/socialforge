# Escalation ladder and WhatsApp

A nudge nobody answers climbs a fixed ladder. Each rung happens once per nudge, one rung per run, and only inside the receiver's working hours (their timezone, working days, outside quiet hours).

| Level | When | What | Who gets it |
|---|---|---|---|
| 1 | the nudge itself | Teams DM | the assignee, or the lead if there is no owner |
| 2 | no ack after `ack_hours_before_channel` | a reminder in the project channel that names the assignee | the team channel |
| 3 | no ack after `ack_hours_before_lead`, severity high or critical | Teams DM saying who has not replied | the project lead (skipped, and recorded, if the lead is the one who was nudged) |
| 4 | no ack after `ack_hours_before_whatsapp`, severity critical only | WhatsApp template message | the project lead |

The hours are measured from the moment the nudge was created, in wall clock hours. They must increase from level to level (config rejects anything else). If the problem goes away (item assigned, finished, label removed) the nudge is marked `suppressed` and its escalations are closed. An `ack` in Teams stops the ladder, also after it has started.

Severity is recomputed on every run, so something that was medium on Thursday can be high by Friday and then climbs a rung.

## What counts as critical
Only two situations, both from your spec, both defined in the project config under `critical` (nothing is assumed):
- `GOAL_ITEM_NOT_STARTED`: the sprint ends within `sprint_end_within_working_days` working days and an item carrying `goal_label` is still in one of `not_started_statuses`.
- `PRODUCTION_BLOCKER_UNOWNED`: an item carrying `production_blocker_label` has had no assignee for at least `unowned_production_blocker_working_hours` working hours (counted inside working hours on working days).

Each is also nudged like any other rule (level 1) and has its own cooldown.

## WhatsApp is off unless you turn it on
All of these must hold before a single WhatsApp message can be queued:
1. `NXSPRINT_WHATSAPP_ENABLED=true` and the token, phone number id and API version are set.
2. The project has a `whatsapp` block (template name, language, `max_per_person_per_day`) and the lead has `whatsapp_number` (international format, `+919876543210`). Startup fails if either is missing.
3. The finding is critical and has already gone through levels 2 and 3.
4. No test redirect is active (`NXSPRINT_DELIVERY_REDIRECT_TARGET` unset). A redirect blocks WhatsApp completely, so a test can never ring a real phone.
5. The lead has had fewer than `max_per_person_per_day` WhatsApp messages today (project timezone).
6. For delivery, the app is in `live` mode. In `dry_run` the message only appears in the outbox as `whatsapp`.

Phone numbers are masked (`...3210`) in `GET /outbox` and never appear in errors or logs.

## Setting up WhatsApp (not tested, from Meta's documentation)
1. Meta Business account, create an app, add the WhatsApp product (Cloud API).
2. Register a phone number for sending. Note its **Phone number ID**.
3. Create a system user with the `whatsapp_business_messaging` and `whatsapp_business_management` permissions and generate a token that does not expire. This is `NXSPRINT_WHATSAPP_TOKEN`.
4. Create a message template, category Utility, language `en`, named as in config. Suggested body, with three variables and no dashes:
   `Hi {{1}}, this is NxSprint from SRIA. A critical item needs you: {{2}}. Details: {{3}}`
   NxSprint sends exactly three text parameters: the lead's first name, one plain sentence describing the problem, and the issue link (or `no link`). Wait for Meta to approve it. Until it is approved, sends fail and the row is parked as `dead` after 5 attempts.
5. Set `NXSPRINT_WHATSAPP_API_VERSION` to a version Meta currently supports.
6. The lead must have agreed to receive these messages. Meta requires opt in, and that is for you to arrange.
7. Import `escalation-whatsapp` in n8n (see `docs/n8n.md`).

Template messages are the only kind allowed outside Meta's 24 hour customer window, which is why nothing else is sent.

## Known limits
- Level 2 names the person in the text. A real Teams @mention needs the Power Automate flow to build it, so it is not done here.
- WhatsApp is one way. Replies on WhatsApp are not read. Acknowledge in Teams.
- Level 3 is counted as done when the lead was the one nudged, even though no message is sent for it.
