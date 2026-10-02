"""Daily standup: prompt each member, collect replies, post one team summary. All deterministic."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ProjectCfg
from app.domain.board import load_board
from app.domain.calendar import can_message_now, is_working_day, working_days_between
from app.domain.channels import BOT_CHANNEL, BOT_FOOTER, dm_channel
from app.domain.rules import BoardState, run_rules
from app.models import Event, Member, Outbox, Project, StandupPrompt, StandupResponse
from app.settings import Mode

MAX_ITEMS_LISTED = 8
MAX_REPLY_CHARS = 200


@dataclass
class StandupRun:
    prompted: int = 0
    deferred_outside_hours: int = 0
    already_prompted: int = 0
    not_due: str | None = None  # why nothing was attempted


def _local(now: datetime, cfg: ProjectCfg) -> datetime:
    return now.astimezone(ZoneInfo(cfg.timezone))


def _short(text: str | None) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= MAX_REPLY_CHARS else text[: MAX_REPLY_CHARS - 3] + "..."


def prompt_text(first: str, project_name: str, board: BoardState, login: str) -> str:
    items = [
        i
        for i in board.items
        if board.sprint and i.sprint_name == board.sprint.name and i.assignee_login == login
        and i.status != board.cfg.statuses.done
    ]  # fmt: skip
    if board.sprint is None:
        listing = "We do not see an active sprint right now."
    elif not items:
        listing = f"We see nothing open assigned to you in {board.sprint.name}."
    else:
        lines = [f"{n}. {i.title} ({i.status})" for n, i in enumerate(items[:MAX_ITEMS_LISTED], 1)]
        if len(items) > MAX_ITEMS_LISTED:
            lines.append(f"and {len(items) - MAX_ITEMS_LISTED} more")
        listing = f"Your open items in {board.sprint.name}:\n" + "\n".join(lines)
    return (
        f"Hi {first}, it is standup time for {project_name}.\n{listing}\n"
        "Please reply with three short lines: Done: ..., Doing: ..., Blocked: ... "
        "and write none where it does not apply."
    )


def run_standup_prompts(
    session: Session, project: Project, cfg: ProjectCfg, mode: Mode, now: datetime, use_bot: bool
) -> StandupRun:
    run = StandupRun()
    local = _local(now, cfg)
    if not is_working_day(local.date(), cfg):
        run.not_due = "not a working day"
        return run
    if not cfg.standup_time <= local.time() < cfg.standup_summary_time:
        run.not_due = "outside the standup window"
        return run

    board = load_board(session, project, cfg, now)
    members = {m.github_login: m for m in session.scalars(select(Member))}
    for mc in cfg.members:
        row = members.get(mc.github_login)
        if not mc.active or row is None:
            continue
        if session.scalar(
            select(StandupPrompt.id).where(
                StandupPrompt.member_id == row.id, StandupPrompt.standup_date == local.date()
            )
        ):
            run.already_prompted += 1
            continue
        if not can_message_now(now, mc.timezone, cfg):
            run.deferred_outside_hours += 1
            continue
        channel = dm_channel(session, row.id, use_bot)
        body = prompt_text(mc.name.split()[0], cfg.name, board, mc.github_login)
        session.add(
            StandupPrompt(project_id=project.id, member_id=row.id, standup_date=local.date(), created_at=now)
        )
        session.add(
            Outbox(
                project_id=project.id,
                channel=channel,
                target=mc.teams_user_id,
                body=f"{body}\n\n{BOT_FOOTER}" if channel == BOT_CHANNEL else body,
                mode=mode.value,
                created_at=now,
            )
        )
        run.prompted += 1
    session.commit()
    return run


def _previous_working_day_start(local: datetime, cfg: ProjectCfg) -> datetime:
    day = local.date() - timedelta(days=1)
    while not is_working_day(day, cfg):
        day -= timedelta(days=1)
    return datetime.combine(day, time.min, tzinfo=local.tzinfo)


def build_summary(session: Session, project: Project, cfg: ProjectCfg, now: datetime) -> str:
    local = _local(now, cfg)
    board = load_board(session, project, cfg, now)
    prompts = session.scalars(
        select(StandupPrompt).where(
            StandupPrompt.project_id == project.id, StandupPrompt.standup_date == local.date()
        )
    ).all()
    replies = {
        r.member_id: r
        for r in session.scalars(
            select(StandupResponse).where(
                StandupResponse.project_id == project.id, StandupResponse.standup_date == local.date()
            )
        )
    }
    names = {m.id: m.name for m in session.scalars(select(Member))}

    lines = [
        f"Standup summary for {local:%a %d %b}, {cfg.name}.",
        f"Replies: {len(replies)} of {len(prompts)} members.",
    ]
    for p in prompts:
        r = replies.get(p.member_id)
        who = names.get(p.member_id, "A member")
        if r is None:
            lines.append(f"{who}. No reply yet.")
        elif any((r.done, r.doing, r.blocked)):
            lines.append(
                f"{who}. Done: {_short(r.done) or 'none'}. Doing: {_short(r.doing) or 'none'}. "
                f"Blocked: {_short(r.blocked) or 'none'}."
            )
        else:
            lines.append(f"{who}. {_short(r.raw_text)}")

    since = _previous_working_day_start(local, cfg).astimezone(now.tzinfo)
    done = sum(1 for i in board.items if i.status == cfg.statuses.done and i.status_since >= since)
    lines.append(f"Moved to {cfg.statuses.done} since the last working day: {done}.")

    blocked = [i for i in board.items if i.blocked_since is not None and i.status != cfg.statuses.done]
    if blocked:
        listing = "; ".join(
            f"{i.title} ({working_days_between(i.blocked_since, now, cfg)} working days)" for i in blocked
        )
        lines.append(f"Blocked on the board: {listing}.")
    else:
        lines.append("Nothing on the board carries the blocked label.")

    risk_text = {
        "SPRINT_AT_RISK": "{t} looks at risk",
        "OVERLOADED_MEMBER": "{t} is above capacity",
        "UNASSIGNED_IN_SPRINT": "{t} has no owner",
    }
    risks = [risk_text[f.rule_id].format(t=f.title) for f in run_rules(board) if f.rule_id in risk_text]
    lines.append("Risks: " + "; ".join(risks) + "." if risks else "No open risks from the rules.")
    return "\n".join(lines)


def post_standup_summary(
    session: Session, project: Project, cfg: ProjectCfg, mode: Mode, now: datetime
) -> str | None:
    """Queue the team summary once per day. Returns None when posted, otherwise why not."""
    local = _local(now, cfg)
    if not is_working_day(local.date(), cfg):
        return "not a working day"
    if local.time() < cfg.standup_summary_time:
        return "not time yet"
    if not session.scalar(
        select(StandupPrompt.id).where(
            StandupPrompt.project_id == project.id, StandupPrompt.standup_date == local.date()
        )
    ):
        return "no standup prompts went out today"
    posted = session.scalars(
        select(Event).where(Event.type == "standup_summary", Event.project_id == project.id)
    )
    if any(e.payload_json.get("date") == local.date().isoformat() for e in posted):
        return "already posted"
    session.add(
        Event(type="standup_summary", project_id=project.id, payload_json={"date": local.date().isoformat()})
    )
    session.add(
        Outbox(
            project_id=project.id,
            channel="teams_team",
            target="team",
            body=build_summary(session, project, cfg, now),
            mode=mode.value,
            created_at=now,
        )
    )
    session.commit()
    return None
