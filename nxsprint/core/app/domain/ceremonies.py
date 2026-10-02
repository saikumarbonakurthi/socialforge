"""Sprint planning prep, review and retro prep, and the weekly owner report.

Everything here is a proposal or a report. Nothing changes scope, assignees or the board (rule 2),
and every number comes from stored data (rule 3). The text is deterministic.
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ProjectCfg, Role
from app.domain.board import load_board
from app.domain.calendar import can_message_now, is_working_day, working_days_between, working_days_in_range
from app.domain.channels import dm_channel
from app.domain.messages import _days
from app.domain.rules import BoardState, ItemView, Severity, run_rules
from app.domain.sync import utc
from app.models import Event, Member, Outbox, Project, Sprint, StandupResponse, WorkItemSnapshot

LIST_CAP = 10
VELOCITY_SPRINTS = 4
_NONE_WORDS = {"", "none", "nil", "no", "nothing", "n/a", "na"}


def _pts(x: float) -> str:
    return f"{x:g} point" + ("" if x == 1 else "s")


def _items(n: int) -> str:
    return f"{n} item" + ("" if n == 1 else "s")


def _more(items: list, n: int = LIST_CAP) -> tuple[list, str | None]:
    return items[:n], (f"and {len(items) - n} more" if len(items) > n else None)


def _numbered(lines: list[str]) -> list[str]:
    shown, more = _more(lines)
    out = [f"{i}. {line}" for i, line in enumerate(shown, 1)]
    return out + ([more] if more else [])


# Planning prep ------------------------------------------------------------------------------------------
@dataclass
class Planning:
    sprint: str
    capacity: list[tuple[str, float]]
    total_capacity: float
    carryover: list[ItemView]
    carryover_points: float
    carryover_unestimated: int
    room: float
    over_by: float
    suggested: list[ItemView] = field(default_factory=list)
    suggested_points: float = 0.0
    did_not_fit: list[ItemView] = field(default_factory=list)
    unestimated: list[ItemView] = field(default_factory=list)


def build_planning(board: BoardState) -> Planning | None:
    """Capacity, carryover and a first fit pick from the backlog in priority order."""
    cfg, sprint = board.cfg, board.sprint
    if sprint is None:
        return None
    capacity = [(m.name, m.capacity_points) for m in cfg.members if m.active]
    total = sum(c for _, c in capacity)
    carry = [i for i in board.items if i.sprint_name == sprint.name and i.status != cfg.statuses.done]
    carry_pts = sum(i.estimate or 0 for i in carry)
    room = max(0.0, total - carry_pts)
    plan = Planning(
        sprint.name, capacity, total, carry, carry_pts, sum(1 for i in carry if i.estimate is None), room,
        max(0.0, carry_pts - total),
    )  # fmt: skip

    order = {p: n for n, p in enumerate(cfg.priority_order)}
    backlog = [i for i in board.items if i.sprint_name is None and i.status != cfg.statuses.done]
    backlog.sort(key=lambda i: order.get(i.priority, len(order)))  # stable: ties keep board order
    left = room
    for i in backlog:
        if i.estimate is None:
            plan.unestimated.append(i)
        elif i.estimate <= left:
            plan.suggested.append(i)
            left -= i.estimate
        else:
            plan.did_not_fit.append(i)
    plan.suggested_points = room - left
    return plan


def render_planning(p: Planning, cfg: ProjectCfg) -> str:
    done = cfg.statuses.done
    lines = [
        f"Sprint planning proposal for the sprint after {p.sprint}. This is only a proposal for you to approve, "
        "nothing on the board has been changed.",
        "Capacity: " + ", ".join(f"{n} {c:g}" for n, c in p.capacity) + f". Total {_pts(p.total_capacity)}.",
        f"Carrying over from {p.sprint} if not finished: {_items(len(p.carryover))}, {_pts(p.carryover_points)}"
        + (f", and {p.carryover_unestimated} without an estimate." if p.carryover_unestimated else "."),
    ]
    lines += _numbered(
        [
            f"{i.title} ({i.status}, {_pts(i.estimate) if i.estimate is not None else 'no estimate'})"
            for i in p.carryover
        ]
    )
    if p.over_by:
        lines.append(
            f"Carryover alone is {_pts(p.over_by)} over capacity, so nothing new fits unless something moves."
        )
    else:
        lines.append(f"Room left for new work: {_pts(p.room)}.")
    if p.suggested:
        lines.append(f"Suggested from the backlog in priority order, {_pts(p.suggested_points)} in total:")
        lines += _numbered(
            [f"{i.title} ({i.priority or 'no priority'}, {_pts(i.estimate)})" for i in p.suggested]
        )
    else:
        lines.append("Nothing from the backlog fits the room left.")
    if p.did_not_fit:
        lines.append("Did not fit the room left:")
        lines += _numbered(
            [f"{i.title} ({i.priority or 'no priority'}, {_pts(i.estimate)})" for i in p.did_not_fit]
        )
    if p.unestimated:
        lines.append("Need an estimate before they can be planned:")
        lines += _numbered([f"{i.title} ({i.priority or 'no priority'})" for i in p.unestimated])
    lines.append(
        f"Items already marked {done} are left out. Leave and holidays are not known to us, so capacity is the configured figure."
    )
    return "\n".join(lines)


# Sprint review and retro prep ----------------------------------------------------------------------------
@dataclass
class Retro:
    sprint: str
    start: date
    end: date
    planned_count: int = 0
    planned_points: float = 0.0
    planned_done_count: int = 0
    planned_done_points: float = 0.0
    added: list[tuple[str, float | None, bool]] = field(default_factory=list)  # title, points, done
    removed: list[str] = field(default_factory=list)
    done_total_count: int = 0
    done_total_points: float = 0.0
    carryover: list[str] = field(default_factory=list)
    cycle_median: float | None = None
    cycle_samples: int = 0
    cycle_longest: tuple[str, int] | None = None
    blocked: list[tuple[str, int, bool]] = field(
        default_factory=list
    )  # title, working days, blocked earlier too
    observed_from: date | None = None
    prompts: list[str] = field(default_factory=list)


GENERIC_PROMPTS = (
    "What went well this sprint that we should keep doing?",
    "What should we change in how we work next sprint?",
    "Was there anything we should have asked for help with earlier?",
)


def retro_prompts(r: Retro) -> list[str]:
    """Three prompts. Data driven ones first, only when their data exists, then the standing ones."""
    out = []
    if r.added or r.removed:
        out.append(
            f"Scope changed during the sprint: {len(r.added)} added and {len(r.removed)} removed. "
            "What made that necessary, and what could we agree at planning to avoid it?"
        )
    if r.carryover:
        out.append(
            f"{_items(len(r.carryover))} {'is' if len(r.carryover) == 1 else 'are'} not finished. What slowed them down, and were they sized realistically?"
        )
    if r.blocked:
        out.append(
            f"{_items(len(r.blocked))} carried the blocked label. What was the longest wait, and who could have helped sooner?"
        )
    if r.cycle_longest:
        title, days = r.cycle_longest
        out.append(
            f"{title} took {_days(days)} from start to done, the longest this sprint. What made it slow?"
        )
    if r.planned_points and r.planned_done_points < r.planned_points:
        out.append(
            f"We finished {r.planned_done_points:g} of {r.planned_points:g} planned points. "
            "What would we do differently to make the plan fit?"
        )
    return (out + list(GENERIC_PROMPTS))[:3]


def build_retro(session: Session, project: Project, cfg: ProjectCfg, now: datetime) -> Retro | None:
    sprints = {s.id: s for s in session.scalars(select(Sprint).where(Sprint.project_id == project.id))}
    active = [s for s in sprints.values() if s.status == "active"]
    if not active:
        return None
    sp = max(active, key=lambda s: s.start)
    board = load_board(session, project, cfg, now)
    on_board = {i.issue_node_id: i for i in board.items}

    snaps = list(
        session.scalars(
            select(WorkItemSnapshot)
            .where(WorkItemSnapshot.project_id == project.id)
            .order_by(WorkItemSnapshot.id)
        )
    )
    by_issue: dict[str, list[WorkItemSnapshot]] = defaultdict(list)
    for s in snaps:
        by_issue[s.issue_node_id].append(s)
    if not snaps:
        return None

    tz = ZoneInfo(cfg.timezone)
    first_sync = utc(min(s.captured_at for s in snaps))
    day_one_end = datetime.combine(sp.start, time.min, tz).astimezone(first_sync.tzinfo) + timedelta(days=1)
    cutoff = max(day_one_end, first_sync)  # we cannot know what the plan was before we began watching
    done_status, wip = cfg.statuses.done, cfg.statuses.in_progress

    r = Retro(sp.name, sp.start, sp.end)
    if first_sync > datetime.combine(sp.start, time.min, tz).astimezone(first_sync.tzinfo):
        r.observed_from = first_sync.astimezone(tz).date()

    for issue, rows in by_issue.items():
        in_sprint = [s for s in rows if s.sprint_id == sp.id]
        if not in_sprint:
            continue
        last = in_sprint[-1]
        est = last.estimate
        was_done = any(s.status == done_status for s in in_sprint)
        planned = utc(in_sprint[0].captured_at) <= cutoff
        gone = issue not in on_board or rows[-1].sprint_id != sp.id
        if planned:
            r.planned_count += 1
            r.planned_points += est or 0
            if was_done:
                r.planned_done_count += 1
                r.planned_done_points += est or 0
        else:
            r.added.append((last.title, est, was_done))
        if was_done:
            r.done_total_count += 1
            r.done_total_points += est or 0
        if gone and not was_done:
            r.removed.append(last.title)
        elif not was_done:
            r.carryover.append(last.title)

    cycles = []
    for rows in by_issue.values():
        if not any(s.sprint_id == sp.id and s.status == done_status for s in rows):
            continue
        started = next((s for s in rows if s.status == wip), None)
        finished = next(
            (s for s in rows if s.status == done_status and (started is None or s.id > started.id)), None
        )
        if started and finished:
            cycles.append(
                (
                    working_days_between(utc(started.captured_at), utc(finished.captured_at), cfg),
                    rows[-1].title,
                )
            )
    if cycles:
        r.cycle_samples = len(cycles)
        r.cycle_median = statistics.median(c for c, _ in cycles)
        longest = max(cycles, key=lambda c: c[0])
        r.cycle_longest = (longest[1], longest[0]) if longest[0] > 0 else None

    label = cfg.thresholds.blocked_label
    for rows in by_issue.values():
        mine = [s for s in rows if s.sprint_id == sp.id]
        first_blocked = next((s for s in mine if label in s.labels), None)
        if first_blocked is None:
            continue
        cleared = next((s for s in mine if s.id > first_blocked.id and label not in s.labels), None)
        end = utc(cleared.captured_at) if cleared else now
        earlier = any(
            label in s.labels and s.sprint_id in sprints and sprints[s.sprint_id].start < sp.start
            for s in rows
        )
        r.blocked.append(
            (mine[-1].title, working_days_between(utc(first_blocked.captured_at), end, cfg), earlier)
        )
    r.blocked.sort(key=lambda b: -b[1])
    r.prompts = retro_prompts(r)
    return r


def render_retro(r: Retro, cfg: ProjectCfg) -> str:
    done = cfg.statuses.done
    lines = [
        f"Sprint review and retro prep for {r.sprint}, {r.start:%d %b} to {r.end - timedelta(days=1):%d %b}."
    ]
    if r.observed_from:
        lines.append(
            f"We only started watching this board on {r.observed_from:%d %b}, so earlier changes are not visible."
        )
    lines.append(
        f"Completed versus planned: {r.planned_done_count} of {r.planned_count} planned item{'' if r.planned_count == 1 else 's'} done, "
        f"{r.planned_done_points:g} of {r.planned_points:g} planned points. "
        f"Everything done in the sprint, including added work: {_items(r.done_total_count)}, {_pts(r.done_total_points)}."
    )
    if r.added or r.removed:
        lines.append(f"Scope changes: {len(r.added)} added, {len(r.removed)} removed.")
        lines += _numbered(
            [
                f"Added: {t} ({_pts(p) if p is not None else 'no estimate'}, {'done' if d else 'not done'})"
                for t, p, d in r.added
            ]
        )
        lines += _numbered([f"Removed: {t}" for t in r.removed])
    else:
        lines.append("No scope changes seen.")
    if r.carryover:
        lines.append(f"Not {done} yet, so carrying over: {len(r.carryover)}.")
        lines += _numbered(r.carryover)
    if r.cycle_median is not None:
        longest = (
            f" The longest was {r.cycle_longest[0]} at {_days(r.cycle_longest[1])}."
            if r.cycle_longest
            else ""
        )
        lines.append(
            f"Cycle time, from In Progress to {done}: median {r.cycle_median:g} working days across {_items(r.cycle_samples)}.{longest}"
        )
    else:
        lines.append("Cycle time: not enough finished items with a visible start to report.")
    if r.blocked:
        lines.append("Items that carried the blocked label:")
        lines += _numbered(
            [
                f"{t} ({_days(d)}{', and blocked in an earlier sprint too' if e else ''})"
                for t, d, e in r.blocked
            ]
        )
    else:
        lines.append("No item carried the blocked label.")
    lines.append("Three questions for the retro:")
    lines += [f"{i}. {q}" for i, q in enumerate(r.prompts, 1)]
    return "\n".join(lines)


# Weekly owner report -------------------------------------------------------------------------------------
_SEV = {Severity.CRITICAL: 3, Severity.HIGH: 2, Severity.MEDIUM: 1, Severity.LOW: 0}
_RULE_ORDER = ("SPRINT_AT_RISK", "BLOCKED_LABEL_AGING", "OVERLOADED_MEMBER", "UNASSIGNED_IN_SPRINT",
               "STALE_IN_PROGRESS", "PR_WAITING_REVIEW", "NO_ESTIMATE")  # fmt: skip


def describe(f, cfg: ProjectCfg) -> str:
    e = f.evidence
    names = {m.github_login: m.name for m in cfg.members}
    who = names.get(f.member or "", "someone")
    return {
        "SPRINT_AT_RISK": f"{f.title} looks at risk",
        "BLOCKED_LABEL_AGING": f"{f.title} has been blocked for {_days(e.get('working_days_blocked', 0))}",
        "OVERLOADED_MEMBER": f"{who} is above capacity in {e.get('sprint', 'the sprint')}",
        "UNASSIGNED_IN_SPRINT": f"{f.title} has no owner",
        "STALE_IN_PROGRESS": f"{f.title} has had no update for {_days(e.get('working_days_since_update', 0))}",
        "PR_WAITING_REVIEW": f"The pull request {f.title} is waiting",
        "NO_ESTIMATE": f"{f.title} has no estimate",
    }[f.rule_id]


def top_risks(findings, n: int = 3):
    rank = {r: i for i, r in enumerate(_RULE_ORDER)}
    return sorted(findings, key=lambda f: (-_SEV[f.severity], rank[f.rule_id], f.title))[:n]


def decisions(findings, cfg: ProjectCfg) -> list[str]:
    names = {m.github_login: m.name for m in cfg.members}
    out = []
    for f in findings:
        if f.rule_id == "UNASSIGNED_IN_SPRINT":
            out.append(f"Pick an owner for {f.title}.")
        elif f.rule_id == "SPRINT_AT_RISK":
            out.append(f"Decide what to cut or move so {f.title} can finish.")
        elif f.rule_id == "OVERLOADED_MEMBER":
            out.append(
                f"Decide what to move or pause for {names.get(f.member or '', 'a member')}, who is above capacity."
            )
        elif f.rule_id == "BLOCKED_LABEL_AGING" and f.severity in (Severity.HIGH, Severity.CRITICAL):
            out.append(
                f"Decide who can unblock {f.title}, blocked for {_days(f.evidence['working_days_blocked'])}."
            )
    return out


def velocity(session: Session, project: Project, cfg: ProjectCfg) -> list[tuple[str, float]]:
    """Points that reached Done while in each completed sprint, oldest first, the last few only."""
    sprints = list(
        session.scalars(
            select(Sprint)
            .where(Sprint.project_id == project.id, Sprint.status == "completed")
            .order_by(Sprint.start)
        )
    )
    out = []
    for sp in sprints[-VELOCITY_SPRINTS:]:
        finished: dict[str, float] = {}  # issue -> estimate when it first showed Done in this sprint
        rows = session.scalars(
            select(WorkItemSnapshot)
            .where(WorkItemSnapshot.project_id == project.id, WorkItemSnapshot.sprint_id == sp.id)
            .order_by(WorkItemSnapshot.id)
        )
        for snap in rows:
            if snap.status == cfg.statuses.done and snap.issue_node_id not in finished:
                finished[snap.issue_node_id] = snap.estimate or 0
        out.append((sp.name, sum(finished.values())))
    return out


def _trend(points: list[float]) -> str:
    if len(points) < 2:
        return "not enough finished sprints yet to show a trend"
    last, before = points[-1], points[-2]
    return "rising" if last > before else "falling" if last < before else "flat"


def _blocked_in_standup(session: Session, project: Project, cfg: ProjectCfg, today: date) -> list[str]:
    names = {m.id: m.name for m in session.scalars(select(Member))}
    latest: dict[int, StandupResponse] = {}
    rows = session.scalars(
        select(StandupResponse)
        .where(
            StandupResponse.project_id == project.id, StandupResponse.standup_date > today - timedelta(days=7)
        )
        .order_by(StandupResponse.standup_date)
    )
    for r in rows:
        latest[r.member_id] = r
    out = []
    for member_id, r in latest.items():
        text = " ".join((r.blocked or "").split())
        if text.lower().strip(" .") not in _NONE_WORDS:
            out.append(f"{names.get(member_id, 'A member')} said in standup: {text[:200]}")
    return out


def build_weekly(session: Session, project: Project, cfg: ProjectCfg, now: datetime) -> str:
    local = now.astimezone(ZoneInfo(cfg.timezone))
    board = load_board(session, project, cfg, now)
    findings = run_rules(board)
    names = {m.github_login: m.name for m in cfg.members}

    lines = [f"Weekly report for {local:%a %d %b}, {cfg.name}."]
    risks = top_risks(findings)
    lines.append("Top risks:" if risks else "Top risks: none from the rules.")
    lines += _numbered([describe(f, cfg) for f in risks])

    blocked = [i for i in board.items if i.blocked_since is not None and i.status != cfg.statuses.done]
    said = _blocked_in_standup(session, project, cfg, local.date())
    if blocked or said:
        lines.append("Who is blocked:")
        lines += _numbered(
            [
                f"{names.get(i.assignee_login or '', 'Unassigned')}: {i.title} ({_days(working_days_between(i.blocked_since, now, cfg))})"
                for i in blocked
            ]
            + said
        )
    else:
        lines.append("Who is blocked: nobody on the board or in this week's standups.")

    vel = velocity(session, project, cfg)
    if vel:
        lines.append(
            "Velocity, points done: "
            + ", ".join(f"{n} {p:g}" for n, p in vel)
            + f". Trend: {_trend([p for _, p in vel])}."
        )
    else:
        lines.append("Velocity: no completed sprint seen yet.")

    todo = decisions(findings, cfg)
    lines.append("Decisions needed from you:" if todo else "Decisions needed from you: none.")
    lines += _numbered(todo)
    return "\n".join(lines)


# Scheduling ----------------------------------------------------------------------------------------------
def _lead(cfg: ProjectCfg):
    return next(m for m in cfg.members if m.role is Role.LEAD)


def _already(session: Session, project: Project, kind: str, key: str, field_name: str) -> bool:
    events = session.scalars(select(Event).where(Event.type == kind, Event.project_id == project.id))
    return any(e.payload_json.get(field_name) == key for e in events)


def _working_days_after_today(cfg: ProjectCfg, end: date, now: datetime) -> int:
    today = now.astimezone(ZoneInfo(cfg.timezone)).date()
    return working_days_in_range(today + timedelta(days=1), end, cfg)


def _send_to_lead(session, project, cfg, mode, now, use_bot, body, kind, key, field_name) -> str | None:
    lead = _lead(cfg)
    row = session.scalar(select(Member).where(Member.github_login == lead.github_login))
    if not lead.active or row is None:
        return "the project lead is not an active member"
    if not can_message_now(now, lead.timezone, cfg):
        return "outside the lead's working hours"
    session.add(Event(type=kind, project_id=project.id, payload_json={field_name: key}, created_at=now))
    session.add(
        Outbox(
            project_id=project.id,
            channel=dm_channel(session, row.id, use_bot),
            target=lead.teams_user_id,
            body=body,
            mode=mode.value,
            created_at=now,
        )
    )
    session.commit()
    return None


def run_planning_prep(session, project, cfg, mode, now, use_bot=False) -> str | None:
    """Queue the planning proposal for the lead once per sprint. Returns None when sent, else why not."""
    board = load_board(session, project, cfg, now)
    if board.sprint is None:
        return "no active sprint"
    if (
        _working_days_after_today(cfg, board.sprint.end, now)
        > cfg.ceremonies.planning_prep_working_days_before_sprint_end
    ):
        return "not due yet"
    if _already(session, project, "planning_prep", board.sprint.name, "sprint"):
        return "already sent for this sprint"
    return _send_to_lead(
        session, project, cfg, mode, now, use_bot,
        render_planning(build_planning(board), cfg), "planning_prep", board.sprint.name, "sprint",
    )  # fmt: skip


def run_retro_prep(session, project, cfg, mode, now, use_bot=False) -> str | None:
    retro = build_retro(session, project, cfg, now)
    if retro is None:
        return "no active sprint"
    if (
        _working_days_after_today(cfg, retro.end, now)
        > cfg.ceremonies.retro_prep_working_days_before_sprint_end
    ):
        return "not due yet"
    if _already(session, project, "retro_prep", retro.sprint, "sprint"):
        return "already sent for this sprint"
    return _send_to_lead(
        session,
        project,
        cfg,
        mode,
        now,
        use_bot,
        render_retro(retro, cfg),
        "retro_prep",
        retro.sprint,
        "sprint",
    )


def run_weekly_report(session, project, cfg, mode, now) -> str | None:
    """Queue the owner report once per week, on the configured weekday from the configured time."""
    local = now.astimezone(ZoneInfo(cfg.timezone))
    c = cfg.ceremonies
    if local.isoweekday() != c.weekly_report_weekday or not is_working_day(local.date(), cfg):
        return "not the report day"
    if local.time() < c.weekly_report_time:
        return "not time yet"
    iso = local.isocalendar()
    week = f"{iso.year}-W{iso.week:02d}"
    if _already(session, project, "weekly_report", week, "week"):
        return "already sent this week"
    session.add(
        Event(type="weekly_report", project_id=project.id, payload_json={"week": week}, created_at=now)
    )
    session.add(
        Outbox(
            project_id=project.id,
            channel="owner_report",
            target=cfg.channels.owner_report_target,
            body=build_weekly(session, project, cfg, now),
            mode=mode.value,
            created_at=now,
        )
    )
    session.commit()
    return None
