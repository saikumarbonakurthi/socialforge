# Ceremonies: planning prep, retro prep, weekly owner report

Everything here is a proposal or a report. NxSprint never changes scope, assignees or the board, and the text is deterministic (no LLM), built only from stored data.

| What | To | When (all from `ceremonies` in the project config) | Once per |
|---|---|---|---|
| Sprint planning proposal | project lead (DM) | when working days left after today are at most `planning_prep_working_days_before_sprint_end` | sprint |
| Sprint review and retro prep | project lead (DM) | same, with `retro_prep_working_days_before_sprint_end` (0 means the last working day) | sprint |
| Weekly owner report | `channels.owner_report_target` | on `weekly_report_weekday`, from `weekly_report_time` | week |

Both lead messages wait for the lead's own working hours. n8n only knocks every 30 minutes on weekdays (`cron-ceremonies`, `cron-weekly-report`); core decides what is due.

## What each contains
**Planning proposal.** Capacity per member (the configured `capacity_points`, leave is not known), what carries over if unfinished, the room left, then backlog items in `priority_order` that fit the room (first fit). It also lists items that did not fit and items that need an estimate first. Backlog means on the board, not in a sprint, not Done.

**Review and retro prep.** Planned versus completed (the plan is what we saw at the start, or at our first sync if we began watching later, and the message says so), scope added and removed, carryover, cycle time (median working days from In Progress to Done, only for items we saw both start and finish), items that carried the blocked label and whether they were blocked in an earlier sprint too, and three discussion questions. The questions come from the data (scope change, carryover, blockers, the slowest item, plan shortfall) and are filled up from three standing ones, so there is always a set of three.

**Weekly owner report.** Top 3 risks (by severity, then a fixed rule order), who is blocked (board labels plus this week's standup replies), velocity of the last 4 completed sprints with a rising, falling or flat trend, and decisions needed (unowned items, sprint at risk, overloaded members, items blocked for a long time).

## Read without sending
`GET /projects/{id}/planning`, `/retro`, `/weekly` return the text as it would be sent and record nothing. `make demo` prints all three.

## Known limits
- Cycle time and "how long blocked" are measured from our snapshots, so they are only as precise as the 15 minute sync, and only from when we started watching.
- Velocity needs completed sprints to be visible on the board after they end. A sprint we never saw finish does not appear.
- The owner report goes through the DM webhook. If the owner should get it from the bot, say so.
