from datetime import UTC, datetime, timedelta

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.sync import sync_project
from app.integrations.github import ProjectData
from app.main import create_app
from app.models import WorkItemSnapshot
from app.settings import Mode
from tests.conftest import SECRET
from tests.helpers import NOW, days_ago, item

AUTH = {"Authorization": f"Bearer {SECRET}"}
HOOK = "https://prod-00.example.logic.azure.com/workflows/abc?sig=S"
PATHS = ["/jobs/planning_prep", "/jobs/retro_prep", "/jobs/weekly_report"]


def local(month, day, hh):
    return datetime(2026, month, day, hh, tzinfo=UTC) - timedelta(hours=5, minutes=30)


@pytest.fixture
def app(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        cfg = app.state.config.projects[0]
        with app.state.session_factory() as db:
            sync_project(
                db, cfg, ProjectData("PVT", [item(status="In Progress", estimate=5.0)]), local(9, 22, 11)
            )
        app.state.clock = lambda: NOW
        yield app, c


def test_jobs_need_auth_and_a_synced_project(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        for path in PATHS:
            assert c.post(path).status_code == 401
            assert "not synced" in c.post(path, headers=AUTH).json()["projects"][0]["error"]
        for path in ("planning", "retro", "weekly"):
            assert c.get(f"/projects/1/{path}").status_code == 401


def test_planning_job_queues_for_the_lead_once(app):
    app, c = app  # Thursday 11:00, two working days or fewer to go: due
    first = c.post("/jobs/planning_prep", headers=AUTH).json()
    assert first["mode"] == "dry_run" and first["projects"][0] == {
        "project": "Demo Project",
        "queued": True,
        "why_not": None,
    }
    assert (
        c.post("/jobs/planning_prep", headers=AUTH).json()["projects"][0]["why_not"]
        == "already sent for this sprint"
    )
    out = c.get("/outbox", headers=AUTH).json()
    assert [(o["channel"], o["target"]) for o in out] == [("teams_dm", "demo-asha")]


def test_retro_and_weekly_jobs_report_why_not_yet(app):
    app, c = app
    assert (
        c.post("/jobs/retro_prep", headers=AUTH).json()["projects"][0]["why_not"] == "not due yet"
    )  # Thursday
    assert (
        c.post("/jobs/weekly_report", headers=AUTH).json()["projects"][0]["why_not"] == "not the report day"
    )
    app.state.clock = lambda: local(10, 2, 11)  # Friday morning
    assert c.post("/jobs/retro_prep", headers=AUTH).json()["projects"][0]["queued"] is True
    assert c.post("/jobs/weekly_report", headers=AUTH).json()["projects"][0]["why_not"] == "not time yet"
    app.state.clock = lambda: local(10, 2, 17)
    assert c.post("/jobs/weekly_report", headers=AUTH).json()["projects"][0]["queued"] is True


def test_views_return_text_without_recording_anything(app):
    app, c = app
    plan = c.get("/projects/1/planning", headers=AUTH).json()["text"]
    retro = c.get("/projects/1/retro", headers=AUTH).json()["text"]
    weekly = c.get("/projects/1/weekly", headers=AUTH).json()["text"]
    assert plan.startswith("Sprint planning proposal") and retro.startswith("Sprint review and retro prep")
    assert weekly.startswith("Weekly report for Thu 01 Oct")
    assert c.get("/outbox", headers=AUTH).json() == []
    assert c.get("/projects/99/weekly", headers=AUTH).status_code == 404


def test_owner_report_reaches_n8n_through_the_dm_webhook(settings, config_file, monkeypatch):
    data = yaml.safe_load(config_file.read_text())
    data["placeholder"] = False
    config_file.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("TEAMS_WEBHOOK_DEMO_DM", HOOK)
    app = create_app(settings.model_copy(update={"mode": Mode.LIVE}))
    with TestClient(app) as c:
        cfg = app.state.config.projects[0]
        with app.state.session_factory() as db:
            sync_project(db, cfg, ProjectData("PVT", [item()]), local(9, 22, 11))
        app.state.clock = lambda: local(10, 2, 17)
        assert c.post("/jobs/weekly_report", headers=AUTH).json()["projects"][0]["queued"] is True
        items = c.get("/outbox/pending", headers=AUTH).json()["items"]
    assert len(items) == 1 and items[0]["webhook_url"] == HOOK
    assert items[0]["payload"]["channel"] == "owner_report" and items[0]["payload"]["target"] == "sai"
    assert items[0]["payload"]["text"].startswith("Weekly report for Fri 02 Oct")


def test_priority_is_stored_and_a_change_writes_a_new_snapshot(app):
    app, c = app
    cfg = app.state.config.projects[0]
    with app.state.session_factory() as db:
        sync_project(db, cfg, ProjectData("PVT", [item(priority="High")]), days_ago(1))
        sync_project(db, cfg, ProjectData("PVT", [item(priority="High")]), days_ago(0))
        sync_project(db, cfg, ProjectData("PVT", [item(priority="Urgent")]), NOW)
        rows = db.scalars(select(WorkItemSnapshot).where(WorkItemSnapshot.issue_node_id == "I_1")).all()
    assert [r.priority for r in rows][-2:] == ["High", "Urgent"] and len(rows) == 3
