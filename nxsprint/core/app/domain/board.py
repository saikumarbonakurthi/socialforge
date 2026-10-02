"""Build the BoardState the rules read, from stored snapshots."""

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ProjectCfg
from app.domain.rules import BoardState, ItemView, SprintView
from app.domain.sync import utc
from app.models import Event, Project, Sprint, WorkItemSnapshot


def _run_start(rows: list[WorkItemSnapshot], same) -> datetime:
    """Earliest captured_at of the unbroken run, ending at the newest row, where `same(row)` holds."""
    since = rows[-1].captured_at
    for r in reversed(rows):
        if not same(r):
            break
        since = r.captured_at
    return utc(since)


def load_board(session: Session, project: Project, cfg: ProjectCfg, now: datetime) -> BoardState:
    sprints = {s.id: s for s in session.scalars(select(Sprint).where(Sprint.project_id == project.id))}
    last_sync = session.scalars(
        select(Event).where(Event.type == "sync", Event.project_id == project.id).order_by(Event.id.desc())
    ).first()
    removed = set(last_sync.payload_json.get("removed", [])) if last_sync else set()

    by_issue: dict[str, list[WorkItemSnapshot]] = defaultdict(list)
    for snap in session.scalars(
        select(WorkItemSnapshot)
        .where(WorkItemSnapshot.project_id == project.id)
        .order_by(WorkItemSnapshot.id)
    ):
        by_issue[snap.issue_node_id].append(snap)

    blocked_label = cfg.thresholds.blocked_label
    items = []
    for issue_id, rows in by_issue.items():
        if issue_id in removed:
            continue
        cur = rows[-1]
        blocked = blocked_label in cur.labels
        items.append(
            ItemView(
                issue_node_id=issue_id,
                title=cur.title,
                url=cur.url,
                status=cur.status,
                priority=cur.priority,
                assignee_login=cur.assignee_login,
                estimate=cur.estimate,
                sprint_name=sprints[cur.sprint_id].name if cur.sprint_id else None,
                labels=tuple(cur.labels),
                updated_at=utc(cur.updated_at),
                status_since=_run_start(rows, lambda r, s=cur.status: r.status == s),
                blocked_since=_run_start(rows, lambda r: blocked_label in r.labels) if blocked else None,
            )
        )

    active = [s for s in sprints.values() if s.status == "active"]
    current = max(active, key=lambda s: s.start) if active else None
    sprint = SprintView(current.name, current.start, current.end) if current else None
    return BoardState(cfg=cfg, now=now, items=items, sprint=sprint)
