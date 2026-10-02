from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import load_config
from app.db import make_engine
from app.domain.sync import status_since, sync_project
from app.integrations.github import ProjectData, WorkItem
from app.models import Base, Event, Member, Sprint, WorkItemSnapshot
from app.settings import Mode

T0 = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)


def _item(**kw) -> WorkItem:
    base = dict(
        issue_node_id="I_1",
        title="A",
        url=None,
        status="Todo",
        assignee_login="asha-demo",
        estimate=3.0,
        sprint_name="Sprint 12",
        sprint_start=T0.date() - timedelta(days=3),
        sprint_days=14,
        labels=(),
        updated_at=T0,
    )
    base.update(kw)
    return WorkItem(**base)


def _setup(config_file):
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine), load_config(config_file, Mode.DRY_RUN).projects[0]


def _snaps(s):
    return list(s.scalars(select(WorkItemSnapshot).order_by(WorkItemSnapshot.id)))


def test_first_sync_writes_everything_and_unchanged_resync_writes_nothing(config_file):
    s, cfg = _setup(config_file)
    data = ProjectData("PVT", [_item(), _item(issue_node_id="I_2", title="B")])
    r1 = sync_project(s, cfg, data, T0)
    r2 = sync_project(s, cfg, data, T0 + timedelta(minutes=15))
    assert (r1.snapshots_written, r2.snapshots_written) == (2, 0)
    assert len(_snaps(s)) == 2


def test_change_writes_new_snapshot_and_tracks_status_age(config_file):
    s, cfg = _setup(config_file)
    sync_project(s, cfg, ProjectData("PVT", [_item(status="In Progress")]), T0)
    later = T0 + timedelta(days=2)
    sync_project(s, cfg, ProjectData("PVT", [_item(status="In Progress", labels=("blocked",))]), later)
    sync_project(
        s,
        cfg,
        ProjectData("PVT", [_item(status="In Progress", labels=("blocked",))]),
        T0 + timedelta(days=3),
    )
    assert len(_snaps(s)) == 2
    status, since = status_since(s, "I_1")
    assert status == "In Progress" and since == T0  # first seen In Progress at T0

    sync_project(s, cfg, ProjectData("PVT", [_item(status="Done")]), T0 + timedelta(days=4))
    status, since = status_since(s, "I_1")
    assert status == "Done" and since == T0 + timedelta(days=4)
    assert status_since(s, "nope") is None


def test_removed_items_sprints_members_and_event(config_file):
    s, cfg = _setup(config_file)
    sync_project(s, cfg, ProjectData("PVT", [_item(), _item(issue_node_id="I_2")]), T0)
    r = sync_project(s, cfg, ProjectData("PVT", [_item()]), T0 + timedelta(hours=1))
    assert r.removed == ["I_2"]
    sprint = s.scalar(select(Sprint))
    assert sprint.status == "active" and (sprint.end - sprint.start).days == 14
    assert {m.github_login for m in s.scalars(select(Member))} == {"asha-demo", "ravi-demo"}
    last = s.scalars(select(Event).order_by(Event.id.desc())).first()
    assert last.payload_json == {"items": 1, "written": 0, "removed": ["I_2"]}


def test_item_without_sprint_and_completed_sprint(config_file):
    s, cfg = _setup(config_file)
    old = _item(sprint_start=T0.date() - timedelta(days=30))
    sync_project(s, cfg, ProjectData("PVT", [old, _item(issue_node_id="I_9", sprint_name=None)]), T0)
    assert s.scalar(select(Sprint)).status == "completed"
    assert _snaps(s)[1].sprint_id is None
