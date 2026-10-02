"""Dead letters: live messages that failed every attempt. Fail over, tell the owner once, never lose them."""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import AppConfig, ProjectCfg
from app.domain.channels import BOT_CHANNEL, BOT_FOOTER
from app.domain.delivery import LEASE, MAX_ATTEMPTS, DeliveryError, status_of
from app.domain.sync import utc
from app.models import Event, LlmCall, Nudge, Outbox, Project
from app.settings import Mode


@dataclass
class DeadLetterRun:
    dead: int = 0
    failed_over: int = 0
    alerted: int = 0


def dead_rows(session: Session, now: datetime) -> list[Outbox]:
    rows = session.scalars(
        select(Outbox)
        .where(
            Outbox.mode == "live",
            Outbox.delivered_at.is_(None),
            Outbox.dismissed_at.is_(None),
            Outbox.attempts >= MAX_ATTEMPTS,
        )
        .order_by(Outbox.id)
        .with_for_update(skip_locked=True)  # overlapping runs must not both fail over or alert the same row
    )
    return [r for r in rows if r.leased_at is None or utc(r.leased_at) < now - LEASE]


def _live(session: Session, outbox_id: int) -> Outbox:
    row = session.get(Outbox, outbox_id)
    if row is None:
        raise LookupError("outbox row not found")
    if row.mode != "live":
        raise DeliveryError("only live rows can be retried or dismissed")
    return row


def retry_row(session: Session, outbox_id: int, now: datetime) -> Outbox:
    """Put a dead or waiting row back in the queue with a fresh set of attempts, after the cause is fixed.

    Refused for rows that are delivered, dismissed (a failed over bot message is dismissed, retrying it would
    send it twice), currently leased (someone is posting it right now) or not failed yet.
    """
    row = _live(session, outbox_id)
    status = status_of(row, now)
    if status not in ("dead", "retrying"):
        raise DeliveryError(f"only dead or retrying rows can be retried, this one is {status}")
    row.attempts, row.leased_at, row.next_attempt_at, row.dead_alerted_at = 0, None, None, None
    session.commit()
    return row


def dismiss_row(session: Session, outbox_id: int, now: datetime) -> Outbox:
    """Give up on a message on purpose. It is kept for the record but never sent."""
    row = _live(session, outbox_id)
    if row.delivered_at is None and row.dismissed_at is None:
        row.dismissed_at = now
        session.commit()
    return row


def _project_cfg(row: Outbox, config: AppConfig, names: dict[int, str]) -> ProjectCfg:
    by_name = {p.name: p for p in config.projects}
    return by_name.get(names.get(row.project_id, ""), config.projects[0])


def process_dead_letters(session: Session, config: AppConfig, mode: Mode, now: datetime) -> DeadLetterRun:
    """Bot messages fall back to the webhook, anything else is reported to the owner once."""
    run = DeadLetterRun()
    if mode is not Mode.LIVE:  # nothing is delivered in dry_run, so there is nothing to fail over or report
        run.dead = len(dead_rows(session, now))
        return run
    names = {p.id: p.name for p in session.scalars(select(Project))}
    pending_alert: dict[str, list[Outbox]] = {}

    for row in dead_rows(session, now):
        run.dead += 1
        if row.channel == BOT_CHANNEL:
            session.add(
                Outbox(
                    project_id=row.project_id,
                    nudge_id=row.nudge_id,
                    channel="teams_dm",
                    target=row.target,
                    body=row.body.removesuffix(f"\n\n{BOT_FOOTER}"),
                    mode="live",
                    created_at=now,
                )
            )
            row.dismissed_at = now
            row.last_error = f"{row.last_error or 'failed'} (sent through the webhook instead)"[:500]
            nudge = session.get(Nudge, row.nudge_id) if row.nudge_id else None
            if nudge is not None:
                nudge.channel = "teams_dm"
            run.failed_over += 1
        elif row.dead_alerted_at is None and row.channel != "owner_alert":  # never alert about an alert
            pending_alert.setdefault(_project_cfg(row, config, names).name, []).append(row)

    by_name = {p.name: p for p in config.projects}
    pid = {n: i for i, n in names.items()}
    for project_name, rows in pending_alert.items():
        cfg = by_name[project_name]
        counts = Counter(r.channel for r in rows)
        listing = ", ".join(f"{n} {ch}" for ch, n in sorted(counts.items()))
        word = "message" if len(rows) == 1 else "messages"
        body = (
            f"NxSprint could not deliver {len(rows)} {word} after several attempts: {listing}. "
            "Please look at the dead letters in the runbook."
        )
        session.add(
            Outbox(
                project_id=pid.get(project_name),
                channel="owner_alert",
                target=cfg.channels.owner_report_target,
                body=body,
                mode=mode.value,
                created_at=now,
            )
        )
        for r in rows:
            r.dead_alerted_at = now
        run.alerted += 1
    session.commit()
    return run


# Events that are only a log. Everything else (planning_prep, retro_prep, weekly_report, standup_summary) is
# a once per sprint, week or day marker that stops a ceremony being sent twice, so it is never pruned.
LOG_EVENT_TYPES = ("sync", "llm_budget_alert")


def prune(session: Session, retention_days: int, now: datetime) -> dict[str, int]:
    """Delete old bookkeeping rows. Nudges, snapshots, sprints, standups and ceremony markers are kept."""
    cutoff = now - timedelta(days=retention_days)
    finished = (Outbox.mode != "live") | Outbox.delivered_at.is_not(None) | Outbox.dismissed_at.is_not(None)
    out = session.execute(delete(Outbox).where(Outbox.created_at < cutoff, finished)).rowcount
    # The newest sync event per project is kept: it records which items have left the board.
    newest_sync = select(func.max(Event.id)).where(Event.type == "sync").group_by(Event.project_id)
    log_event = Event.type.in_(LOG_EVENT_TYPES) | Event.type.like("github.%")
    ev = session.execute(
        delete(Event).where(Event.created_at < cutoff, log_event, Event.id.not_in(newest_sync))
    ).rowcount
    llm = session.execute(delete(LlmCall).where(LlmCall.created_at < cutoff)).rowcount
    session.commit()
    return {"outbox": out, "events": ev, "llm_calls": llm}
