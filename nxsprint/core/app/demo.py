"""`make demo`: seed a fake project and run it through the real sync path.

No GitHub, LLM or Teams needed. Nudges and messages arrive in Phase 2, so for now this
shows the board NxSprint would reason about, including how long each item sat in status.
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.config import load_config
from app.db import make_engine, make_session_factory
from app.domain.sync import status_since, sync_project
from app.integrations.github import ProjectData, WorkItem
from app.models import Base, WorkItemSnapshot
from app.settings import Mode

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "projects.example.yaml"


def _item(n, title, status, who, est, sprint, labels=(), updated=None, now=None) -> WorkItem:
    return WorkItem(
        issue_node_id=f"I_demo{n}",
        title=title,
        url=f"https://github.com/sria-demo/demo-app/issues/{n}",
        status=status,
        assignee_login=who,
        estimate=est,
        sprint_name=sprint[0],
        sprint_start=sprint[1],
        sprint_days=14,
        labels=tuple(labels),
        updated_at=updated or now,
    )


def board(now: datetime, moved: bool) -> ProjectData:
    sprint = ("Sprint 12", (now - timedelta(days=9)).date())
    d = lambda n: now - timedelta(days=n)  # noqa: E731
    items = [
        _item(1, "Login page redesign", "In Progress", "asha-demo", 5, sprint, updated=d(4), now=now),
        _item(
            2,
            "Fix invoice rounding",
            "Done" if moved else "In Progress",
            "ravi-demo",
            3,
            sprint,
            updated=d(0 if moved else 3),
            now=now,
        ),
        _item(3, "Export to CSV", "Todo", None, 2, sprint, updated=d(6), now=now),
        _item(4, "Audit log API", "Todo", "ravi-demo", None, sprint, updated=d(2), now=now),
        _item(
            5,
            "Payment webhook retries",
            "In Progress",
            "ravi-demo",
            8,
            sprint,
            labels=["blocked"],
            updated=d(5),
            now=now,
        ),
        _item(6, "Update onboarding copy", "Done", "asha-demo", 1, sprint, updated=d(7), now=now),
    ]
    return ProjectData("PVT_demo", items)


def main() -> int:
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    cfg = load_config(cfg_path, Mode.DRY_RUN).projects[0]
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()

    now = datetime.now(UTC)
    # Two syncs, four days apart, so "days in status" has something to show.
    first = sync_project(session, cfg, board(now, moved=False), now - timedelta(days=4))
    second = sync_project(session, cfg, board(now, moved=True), now)
    print(f"Project: {cfg.name}   (dry run, nothing is sent anywhere)")
    print(f"Sync 1: {first.items_seen} items, {first.snapshots_written} snapshots")
    print(f"Sync 2: {second.items_seen} items, {second.snapshots_written} snapshot changed\n")

    print(f"{'ISSUE':<28}{'STATUS':<13}{'OWNER':<11}{'PTS':<5}{'IN STATUS':<11}LABELS")
    latest = {}
    for s in session.scalars(select(WorkItemSnapshot).order_by(WorkItemSnapshot.id)):
        latest[s.issue_node_id] = s
    for s in latest.values():
        _, since = status_since(session, s.issue_node_id)
        days = (now - since).days
        print(
            f"{s.title[:26]:<28}{s.status:<13}{s.assignee_login or '(none)':<11}"
            f"{'' if s.estimate is None else s.estimate:<5}{f'{days}d+':<11}{','.join(s.labels)}"
        )
    print("\n'N d+' means at least N days: we only know since our first sync.")
    print("Rules that would flag items here (unowned, no estimate, blocked, stale) arrive in Phase 2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
