from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.domain.sync import sync_project
from app.integrations.github import ProjectData
from app.main import create_app
from tests.conftest import SECRET
from tests.helpers import NOW, days_ago, item

AUTH = {"Authorization": f"Bearer {SECRET}"}


@pytest.fixture
def app(settings):
    return create_app(settings)


def seeded(app):
    """Sync a messy board through the real sync path, with controlled timestamps."""
    cfg = app.state.config.projects[0]
    board = [
        item(issue_node_id="I_1", status="In Progress", updated_at=days_ago(4)),
        item(issue_node_id="I_2", assignee_login=None, estimate=None, title="Idea"),
    ]
    with app.state.session_factory() as db:
        sync_project(db, cfg, ProjectData("PVT", board), NOW - timedelta(days=6))


def test_endpoints_require_auth(app):
    with TestClient(app) as c:
        for method, path in [("post", "/jobs/nudges"), ("get", "/outbox"), ("get", "/nudges"),
                             ("post", "/nudges/1/ack"), ("get", "/projects/1/risk")]:  # fmt: skip
            assert getattr(c, method)(path).status_code == 401, path


def test_nudge_job_before_sync_says_so(app):
    with TestClient(app) as c:
        out = c.post("/jobs/nudges", headers=AUTH).json()
    assert "not synced" in out["projects"][0]["error"]


def test_full_flow_nudge_outbox_list_ack(app):
    with TestClient(app) as c:
        seeded(app)
        app.state.clock = lambda: NOW
        out = c.post("/jobs/nudges", headers=AUTH).json()
        assert out["mode"] == "dry_run"
        proj = out["projects"][0]
        assert proj["findings"] >= 3 and len(proj["created"]) == proj["findings"]

        # Same call again is idempotent inside the cooldown window.
        again = c.post("/jobs/nudges", headers=AUTH).json()["projects"][0]
        assert again["created"] == [] and again["skipped_cooldown"] == proj["findings"]

        outbox = c.get("/outbox", headers=AUTH).json()
        assert len(outbox) == proj["findings"] and {o["mode"] for o in outbox} == {"dry_run"}

        queued = c.get("/nudges?status=queued", headers=AUTH).json()
        assert len(queued) == proj["findings"]
        nid = queued[0]["id"]
        acked = c.post(f"/nudges/{nid}/ack", headers=AUTH).json()
        assert acked["status"] == "acked" and acked["acked_at"]
        assert c.post(f"/nudges/{nid}/ack", headers=AUTH).json()["acked_at"] == acked["acked_at"]
        assert len(c.get("/nudges?status=acked", headers=AUTH).json()) == 1
        assert c.post("/nudges/9999/ack", headers=AUTH).status_code == 404


def test_risk_is_read_only(app):
    with TestClient(app) as c:
        seeded(app)
        r = c.get("/projects/1/risk", headers=AUTH).json()
        rules = {f["rule"] for f in r["findings"]}
        assert {"STALE_IN_PROGRESS", "UNASSIGNED_IN_SPRINT", "NO_ESTIMATE"} <= rules
        assert c.get("/nudges", headers=AUTH).json() == []
        assert c.get("/projects/99/risk", headers=AUTH).status_code == 404
