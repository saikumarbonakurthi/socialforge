"""Persist a project's work items as snapshots. Pure DB logic, no network."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import ProjectCfg
from app.integrations.github import ProjectData, WorkItem
from app.models import Event, Member, Project, Sprint, WorkItemSnapshot


@dataclass
class SyncResult:
    project: str
    items_seen: int
    snapshots_written: int
    removed: list[str]


def utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; everything we store is UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _upsert_project(session: Session, cfg: ProjectCfg, github_project_id: str) -> Project:
    project = session.scalar(select(Project).where(Project.name == cfg.name))
    if project is None:
        project = Project(name=cfg.name)
        session.add(project)
    project.github_project_id = github_project_id
    project.team_channel_ref = cfg.channels.team_webhook_env
    project.sprint_length_days = cfg.sprint_length_days
    project.working_hours = f"{cfg.working_hours.start:%H:%M}-{cfg.working_hours.end:%H:%M}"
    project.timezone = cfg.timezone
    session.flush()
    return project


def _upsert_members(session: Session, cfg: ProjectCfg) -> None:
    for m in cfg.members:
        row = session.scalar(select(Member).where(Member.github_login == m.github_login))
        if row is None:
            row = Member(github_login=m.github_login)
            session.add(row)
        row.name, row.teams_user_id, row.whatsapp_number = (
            m.name,
            m.teams_user_id,
            m.whatsapp_number,
        )
        row.role, row.timezone, row.active = m.role.value, m.timezone, m.active


def _sprint_for(session: Session, project: Project, item: WorkItem, today) -> Sprint | None:
    if not item.sprint_name or not item.sprint_start or not item.sprint_days:
        return None
    sprint = session.scalar(
        select(Sprint).where(Sprint.project_id == project.id, Sprint.name == item.sprint_name)
    )
    end = item.sprint_start + timedelta(days=item.sprint_days)
    status = "completed" if today > end else "active" if today >= item.sprint_start else "planned"
    if sprint is None:
        sprint = Sprint(project_id=project.id, name=item.sprint_name)
        session.add(sprint)
    sprint.start, sprint.end, sprint.status = item.sprint_start, end, status
    session.flush()
    return sprint


def _latest_snapshots(session: Session, project_id: int) -> dict[str, WorkItemSnapshot]:
    newest = (
        select(func.max(WorkItemSnapshot.id))
        .where(WorkItemSnapshot.project_id == project_id)
        .group_by(WorkItemSnapshot.issue_node_id)
    )
    rows = session.scalars(select(WorkItemSnapshot).where(WorkItemSnapshot.id.in_(newest)))
    return {r.issue_node_id: r for r in rows}


def _changed(old: WorkItemSnapshot, new: WorkItemSnapshot) -> bool:
    return (
        old.title,
        old.status,
        old.priority,
        old.assignee_login,
        old.estimate,
        old.sprint_id,
        sorted(old.labels),
        utc(old.updated_at),
    ) != (
        new.title,
        new.status,
        new.priority,
        new.assignee_login,
        new.estimate,
        new.sprint_id,
        sorted(new.labels),
        utc(new.updated_at),
    )


def sync_project(
    session: Session, cfg: ProjectCfg, data: ProjectData, now: datetime | None = None
) -> SyncResult:
    """Write a snapshot per item, but only when something changed since the last one.

    This keeps the table small (an unchanged board writes nothing) while still letting
    us compute how long an item has been in its current status.
    """
    now = now or datetime.now(UTC)
    today = now.astimezone(ZoneInfo(cfg.timezone)).date()
    project = _upsert_project(session, cfg, data.project_node_id)
    _upsert_members(session, cfg)
    previous = _latest_snapshots(session, project.id)

    written = 0
    for item in data.items:
        sprint = _sprint_for(session, project, item, today)
        snap = WorkItemSnapshot(
            project_id=project.id,
            issue_node_id=item.issue_node_id,
            title=item.title,
            url=item.url,
            status=item.status,
            priority=item.priority,
            assignee_login=item.assignee_login,
            estimate=item.estimate,
            sprint_id=sprint.id if sprint else None,
            labels=list(item.labels),
            updated_at=item.updated_at,
            captured_at=now,
        )
        old = previous.get(item.issue_node_id)
        if old is None or _changed(old, snap):
            session.add(snap)
            written += 1

    seen = {i.issue_node_id for i in data.items}
    removed = sorted(set(previous) - seen)
    session.add(
        Event(
            type="sync",
            project_id=project.id,
            payload_json={"items": len(seen), "written": written, "removed": removed},
            created_at=now,
        )
    )
    session.commit()
    return SyncResult(cfg.name, len(seen), written, removed)


def status_since(session: Session, issue_node_id: str) -> tuple[str | None, datetime] | None:
    """Current status and when we first saw it.

    A lower bound: if an item was already In Progress when we first synced, we only know
    it has been there at least since then.
    """
    snaps = session.scalars(
        select(WorkItemSnapshot)
        .where(WorkItemSnapshot.issue_node_id == issue_node_id)
        .order_by(WorkItemSnapshot.id.desc())
    ).all()
    if not snaps:
        return None
    current = snaps[0].status
    since = snaps[0].captured_at
    for s in snaps[1:]:
        if s.status != current:
            break
        since = s.captured_at
    return current, utc(since)
