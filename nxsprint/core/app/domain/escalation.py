"""The escalation ladder (section 9). Deterministic and conservative: one step per nudge per run.

1. DM to the assignee (the nudge itself).
2. No ack after N hours: a message in the project channel naming the assignee.
3. Still open and severity high or critical: DM to the project lead.
4. Critical only: WhatsApp template to the lead. Feature flagged, capped per person per day.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import AppConfig, ConfigError, ProjectCfg, Role
from app.domain.board import load_board
from app.domain.calendar import can_message_now
from app.domain.ceremonies import describe
from app.domain.channels import BOT_CHANNEL, dm_channel
from app.domain.rules import Finding, Severity, run_rules
from app.domain.sync import utc
from app.models import Escalation, Member, Nudge, Outbox, Project
from app.settings import Mode

OPEN = ("queued", "sent", "escalated")
HIGH_OR_WORSE = (Severity.HIGH, Severity.CRITICAL)


@dataclass
class EscalationRun:
    checked: int = 0
    resolved: int = 0
    level2: int = 0
    level3: int = 0
    level4: int = 0
    deferred_outside_hours: int = 0
    whatsapp_off: int = 0
    whatsapp_capped: int = 0
    whatsapp_blocked_by_redirect: int = 0


def check_whatsapp_ready(config: AppConfig) -> None:
    """With the flag on, every project needs the template config and a lead with a number. Fail at startup."""
    for p in config.projects:
        lead = next(m for m in p.members if m.role is Role.LEAD)
        if p.whatsapp is None:
            raise ConfigError(f"WhatsApp is enabled but project {p.name} has no whatsapp block")
        if not lead.whatsapp_number:
            raise ConfigError(f"WhatsApp is enabled but the lead of {p.name} has no whatsapp_number")


def _hours(h: float) -> str:
    n = int(h)
    return f"{n} hour" + ("" if n == 1 else "s")


def _link(f: Finding) -> str:
    return f" {f.url}" if f.url else ""


def _local_day_start(now: datetime, cfg: ProjectCfg) -> datetime:
    tz = ZoneInfo(cfg.timezone)
    return datetime.combine(now.astimezone(tz).date(), time.min, tz).astimezone(UTC)


def _queue(session, project, channel, target, body, mode, now) -> None:
    session.add(
        Outbox(
            project_id=project.id, channel=channel, target=target, body=body, mode=mode.value, created_at=now
        )
    )


def run_escalations(
    session: Session,
    project: Project,
    cfg: ProjectCfg,
    mode: Mode,
    now: datetime,
    *,
    use_bot: bool = False,
    whatsapp_enabled: bool = False,
    redirect_active: bool = False,
) -> EscalationRun:
    run = EscalationRun()
    esc = cfg.escalation
    active = {(f.rule_id, f.issue_node_id): f for f in run_rules(load_board(session, project, cfg, now))}
    members = {m.id: m for m in session.scalars(select(Member))}
    lead_cfg = next(m for m in cfg.members if m.role is Role.LEAD)
    lead_row = next((m for m in members.values() if m.github_login == lead_cfg.github_login), None)

    nudges = session.scalars(
        select(Nudge).where(Nudge.project_id == project.id, Nudge.status.in_(OPEN)).order_by(Nudge.id)
    ).all()
    # Acked or no longer needed: close their open escalations so the history is honest.
    for e in session.scalars(
        select(Escalation)
        .join(Nudge, Nudge.id == Escalation.nudge_id)
        .where(
            Escalation.resolved_at.is_(None),
            Nudge.project_id == project.id,
            Nudge.status.in_(("acked", "suppressed")),
        )
    ):
        e.resolved_at = now

    for n in nudges:
        run.checked += 1
        f = active.get((n.rule, n.issue_node_id))
        rows = session.scalars(select(Escalation).where(Escalation.nudge_id == n.id)).all()
        if f is None:  # the underlying problem went away on its own
            n.status = "suppressed"
            for e in rows:
                e.resolved_at = e.resolved_at or now
            run.resolved += 1
            continue

        level = max((e.level for e in rows), default=1)
        age = (now - utc(n.created_at)).total_seconds() / 3600
        recipient = members.get(n.member_id)
        if recipient is None or lead_row is None:
            continue
        first, lead_first = recipient.name.split()[0], lead_row.name.split()[0]

        if level == 1 and age >= esc.ack_hours_before_channel:
            if not can_message_now(now, recipient.timezone, cfg):
                run.deferred_outside_hours += 1
                continue
            body = (
                f"Reminder: {describe(f, cfg)}. {first}, we asked about this {_hours(age)} ago and have not heard "
                f"back. Could you reply or update the item?{_link(f)}"
            )
            _queue(session, project, "teams_team", "team", body, mode, now)
            session.add(Escalation(nudge_id=n.id, level=2, channel="teams_team", created_at=now))
            n.status = "escalated"
            run.level2 += 1

        elif level == 2 and f.severity in HIGH_OR_WORSE and age >= esc.ack_hours_before_lead:
            if not can_message_now(now, lead_cfg.timezone, cfg):
                run.deferred_outside_hours += 1
                continue
            if (
                recipient.id == lead_row.id
            ):  # the lead already got the first message, do not repeat it to them
                session.add(Escalation(nudge_id=n.id, level=3, channel="none", created_at=now))
            else:
                channel = dm_channel(session, lead_row.id, use_bot)
                body = (
                    f"Hi {lead_first}, {describe(f, cfg)}. We asked {first} {_hours(age)} ago and have had no reply. "
                    f"Could you step in?{_link(f)}"
                )
                _queue(session, project, channel, lead_cfg.teams_user_id, body, mode, now)
                session.add(Escalation(nudge_id=n.id, level=3, channel=channel, created_at=now))
            run.level3 += 1

        elif level == 3 and f.severity is Severity.CRITICAL and age >= esc.ack_hours_before_whatsapp:
            if not whatsapp_enabled or cfg.whatsapp is None or not lead_cfg.whatsapp_number:
                run.whatsapp_off += 1
                continue
            if redirect_active:  # a test redirect must never let a real phone ring
                run.whatsapp_blocked_by_redirect += 1
                continue
            if not can_message_now(now, lead_cfg.timezone, cfg):
                run.deferred_outside_hours += 1
                continue
            sent_today = session.scalar(
                select(func.count(Outbox.id)).where(
                    Outbox.channel == "whatsapp",
                    Outbox.target == lead_cfg.whatsapp_number,
                    Outbox.mode == mode.value,
                    Outbox.created_at >= _local_day_start(now, cfg),
                )
            )
            if sent_today >= cfg.whatsapp.max_per_person_per_day:
                run.whatsapp_capped += 1
                continue
            params = [lead_first, describe(f, cfg), f.url or "no link"]
            payload = {
                "template": cfg.whatsapp.template_name,
                "language": cfg.whatsapp.language,
                "params": params,
            }
            _queue(session, project, "whatsapp", lead_cfg.whatsapp_number, json.dumps(payload), mode, now)
            session.add(Escalation(nudge_id=n.id, level=4, channel="whatsapp", created_at=now))
            run.level4 += 1
        session.flush()
    session.commit()
    return run


__all__ = ["BOT_CHANNEL", "EscalationRun", "check_whatsapp_ready", "run_escalations"]
