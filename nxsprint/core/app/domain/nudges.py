"""Nudge engine: findings in, nudges and outbox rows out. Never sends anything itself."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import MemberCfg, ProjectCfg, Role
from app.domain.board import load_board
from app.domain.calendar import can_message_now
from app.domain.messages import render
from app.domain.rules import Finding, run_rules
from app.domain.sync import utc
from app.llm.phraser import Phraser
from app.models import Member, Nudge, Outbox, Project
from app.settings import Mode

CHANNEL = "teams_dm"


@dataclass
class NudgeRun:
    findings: int = 0
    created: list[Nudge] = field(default_factory=list)
    deferred_outside_hours: int = 0
    skipped_cooldown: int = 0
    skipped_no_recipient: int = 0
    phrasing: Counter = field(default_factory=Counter)  # llm, or template:<reason>


def recipient(f: Finding, cfg: ProjectCfg) -> MemberCfg | None:
    """The finding's member if they are an active member, otherwise the project lead.

    Findings with no member (unowned item, sprint risk) go to the lead: they are the
    person who can assign work or change scope. The lead is returned even if inactive
    so the caller can see there is nobody to message.
    """
    by_login = {m.github_login: m for m in cfg.members}
    target = by_login.get(f.member) if f.member else None
    if target is None or not target.active:
        target = next(m for m in cfg.members if m.role is Role.LEAD)
    return target if target.active else None


def _in_cooldown(session: Session, project: Project, f: Finding, cfg: ProjectCfg, now: datetime) -> bool:
    last = session.scalars(
        select(Nudge)
        .where(
            Nudge.project_id == project.id,
            Nudge.rule == f.rule_id,
            Nudge.issue_node_id == f.issue_node_id,
            Nudge.status != "suppressed",
        )
        .order_by(Nudge.id.desc())
    ).first()
    return last is not None and utc(last.created_at) > now - timedelta(hours=cfg.cooldowns.hours[f.rule_id])


def run_nudges(
    session: Session,
    project: Project,
    cfg: ProjectCfg,
    mode: Mode,
    now: datetime,
    phraser: Phraser | None = None,
) -> NudgeRun:
    run = NudgeRun()
    findings = run_rules(load_board(session, project, cfg, now))
    run.findings = len(findings)
    members = {m.github_login: m for m in session.scalars(select(Member))}

    for f in findings:
        target = recipient(f, cfg)
        if target is None or target.github_login not in members:
            run.skipped_no_recipient += 1
            continue
        if not can_message_now(now, target.timezone, cfg):
            run.deferred_outside_hours += 1  # no row, so the next hourly run tries again
            continue
        if _in_cooldown(session, project, f, cfg, now):
            run.skipped_cooldown += 1
            continue
        first = target.name.split()[0]
        if phraser is None:
            body = render(f, first, cfg)
            run.phrasing["template:disabled"] += 1
        else:
            phrased = phraser.phrase(
                session, project_id=project.id, cfg=cfg, mode=mode, finding=f, first_name=first, now=now
            )
            body = phrased.message
            run.phrasing[
                "llm" if phrased.source == "llm" else f"template:{phrased.reason.split(':')[0]}"
            ] += 1
        nudge = Nudge(
            project_id=project.id,
            member_id=members[target.github_login].id,
            issue_node_id=f.issue_node_id,
            rule=f.rule_id,
            channel=CHANNEL,
            message=body,
            status="queued",
            created_at=now,
        )
        session.add(nudge)
        # In dry_run this row is the only effect; delivery (Phase 4) reads it in live mode.
        session.add(
            Outbox(channel=CHANNEL, target=target.teams_user_id, body=body, mode=mode.value, created_at=now)
        )
        session.flush()
        run.created.append(nudge)
    session.commit()
    return run
