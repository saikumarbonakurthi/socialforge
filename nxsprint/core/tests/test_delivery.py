from datetime import UTC, timedelta

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import ConfigError
from app.domain.delivery import LEASE, MAX_ATTEMPTS
from app.domain.nudges import run_nudges
from app.domain.sync import sync_project
from app.integrations.github import ProjectData
from app.main import create_app
from app.models import Nudge, Outbox, Project
from app.settings import Mode
from tests.conftest import SECRET
from tests.helpers import NOW, days_ago, item

AUTH = {"Authorization": f"Bearer {SECRET}"}
HOOK = "https://prod-00.example.logic.azure.com/workflows/abc?sig=SECRETSIG"


@pytest.fixture
def live_settings(settings, config_file, monkeypatch):
    data = yaml.safe_load(config_file.read_text())
    data["placeholder"] = False  # live refuses sample config
    config_file.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("TEAMS_WEBHOOK_DEMO_DM", HOOK)
    return settings.model_copy(update={"mode": Mode.LIVE})


def seed_nudges(app, mode=Mode.LIVE):
    cfg = app.state.config.projects[0]
    with app.state.session_factory() as db:
        sync_project(
            db, cfg, ProjectData("PVT", [item(status="In Progress", updated_at=days_ago(4))]), days_ago(6)
        )
        project = db.scalar(select(Project))
        return len(run_nudges(db, project, cfg, mode, NOW).created)


@pytest.fixture
def live(live_settings):
    app = create_app(live_settings)
    with TestClient(app) as c:
        app.state.clock = lambda: NOW
        yield app, c, seed_nudges(app)


def pending(c):
    return c.get("/outbox/pending", headers=AUTH).json()


def test_pending_requires_auth(live):
    _, c, _ = live
    assert c.get("/outbox/pending").status_code == 401
    assert c.post("/outbox/1/sent").status_code == 401


def test_pending_leases_live_rows_with_target_and_hides_nothing_secret_elsewhere(live):
    app, c, n = live
    out = pending(c)
    assert out["mode"] == "live" and len(out["items"]) == n > 0
    first = out["items"][0]
    assert first["webhook_url"] == HOOK
    assert (
        set(first["payload"]) == {"channel", "target", "text"} and first["payload"]["channel"] == "teams_dm"
    )
    assert first["payload"]["target"] in {"demo-asha", "demo-ravi"}
    # The secret URL is only ever in the pending response, not in the viewer endpoints.
    assert "SECRETSIG" not in c.get("/outbox", headers=AUTH).text
    assert "SECRETSIG" not in c.get("/nudges", headers=AUTH).text


def test_leased_rows_are_not_handed_out_twice_until_the_lease_expires(live):
    app, c, n = live
    assert len(pending(c)["items"]) == n
    assert pending(c)["items"] == []
    app.state.clock = lambda: NOW + LEASE + timedelta(seconds=1)  # nobody reported back
    again = pending(c)["items"]
    assert len(again) == n
    assert {r["status"] for r in c.get("/outbox", headers=AUTH).json()} == {"leased"}


def test_sent_marks_delivery_and_nudge_and_stops_redelivery(live):
    app, c, n = live
    items = pending(c)["items"]
    assert c.post(f"/outbox/{items[0]['id']}/sent", headers=AUTH).json()["delivered"] is True
    assert c.post(f"/outbox/{items[0]['id']}/sent", headers=AUTH).status_code == 200  # idempotent
    with app.state.session_factory() as db:
        nudge = db.scalar(
            select(Nudge).join(Outbox, Outbox.nudge_id == Nudge.id).where(Outbox.id == items[0]["id"])
        )
        assert nudge.status == "sent" and nudge.sent_at is not None
    app.state.clock = lambda: NOW + timedelta(hours=1)
    assert items[0]["id"] not in {i["id"] for i in pending(c)["items"]}
    statuses = {r["id"]: r["status"] for r in c.get("/outbox", headers=AUTH).json()}
    assert statuses[items[0]["id"]] == "delivered"


def test_failed_row_backs_off_then_is_retried_and_the_reason_is_recorded(live):
    app, c, _ = live
    item_id = pending(c)["items"][0]["id"]
    assert (
        c.post(f"/outbox/{item_id}/failed", json={"error": "HTTP 500 from Teams"}, headers=AUTH).status_code
        == 200
    )
    assert item_id not in {i["id"] for i in pending(c)["items"]}  # not straight away
    row = next(r for r in c.get("/outbox", headers=AUTH).json() if r["id"] == item_id)
    assert (row["status"], row["last_error"], row["attempts"]) == ("retrying", "HTTP 500 from Teams", 1)
    app.state.clock = lambda: NOW + timedelta(minutes=1, seconds=1)  # first backoff step is one minute
    assert item_id in {i["id"] for i in pending(c)["items"]}
    row = next(r for r in c.get("/outbox", headers=AUTH).json() if r["id"] == item_id)
    assert row["attempts"] == 2


def test_backoff_grows_and_the_row_goes_dead_after_max_attempts(live):
    app, c, _ = live
    waits = []
    clock = NOW
    for _ in range(MAX_ATTEMPTS):
        app.state.clock = lambda clock=clock: clock
        item_id = pending(c)["items"][0]["id"]
        c.post(f"/outbox/{item_id}/failed", json={"error": "down"}, headers=AUTH)
        with app.state.session_factory() as db:
            row = db.get(Outbox, item_id)
            waits.append(
                None
                if row.next_attempt_at is None
                else (row.next_attempt_at.replace(tzinfo=UTC) - clock).seconds // 60
            )
        clock = clock + timedelta(hours=2)
    assert waits == [1, 5, 15, 60, None]  # the fifth failure schedules nothing
    app.state.clock = lambda: clock + timedelta(days=2)
    assert item_id not in {i["id"] for i in pending(c)["items"]}  # never offered again
    rows = {r["id"]: r for r in c.get("/outbox", headers=AUTH).json()}
    assert rows[item_id]["status"] == "dead" and rows[item_id]["attempts"] == MAX_ATTEMPTS


def test_unknown_row_is_404(live):
    _, c, _ = live
    assert c.post("/outbox/9999/sent", headers=AUTH).status_code == 404
    assert c.post("/outbox/9999/failed", json={}, headers=AUTH).status_code == 404


def test_redirect_sends_everything_to_the_test_target(live_settings):
    app = create_app(live_settings.model_copy(update={"delivery_redirect_target": "TEST-ROOM"}))
    with TestClient(app) as c:
        app.state.clock = lambda: NOW
        seed_nudges(app)
        items = pending(c)["items"]
    assert items and {i["payload"]["target"] for i in items} == {"TEST-ROOM"}
    assert all(i["payload"]["text"].startswith("Test redirect, this was meant for demo-") for i in items)
    assert not any("-" in i["payload"]["text"].split(". ", 1)[0].replace("demo-", "") for i in items)


def test_dry_run_app_never_leases_even_if_live_rows_exist(settings, config_file):
    app = create_app(settings)  # dry_run
    with TestClient(app) as c:
        app.state.clock = lambda: NOW
        seed_nudges(app, mode=Mode.LIVE)  # rows left over from some earlier live period
        out = pending(c)
        assert out["mode"] == "dry_run" and out["items"] == []


def test_dry_run_rows_cannot_be_marked_delivered(live):
    app, c, _ = live
    with app.state.session_factory() as db:
        project = db.scalar(select(Project))
        row = Outbox(project_id=project.id, channel="teams_dm", target="t", body="b", mode="dry_run")
        db.add(row)
        db.commit()
        rid = row.id
    assert c.post(f"/outbox/{rid}/sent", headers=AUTH).status_code == 409
    assert c.post(f"/outbox/{rid}/failed", json={}, headers=AUTH).status_code == 409
    assert next(r for r in c.get("/outbox", headers=AUTH).json() if r["id"] == rid)["status"] == "dry_run"


def test_live_mode_refuses_to_start_without_a_webhook(settings, config_file, monkeypatch):
    data = yaml.safe_load(config_file.read_text())
    data["placeholder"] = False
    config_file.write_text(yaml.safe_dump(data))
    monkeypatch.delenv("TEAMS_WEBHOOK_DEMO_DM", raising=False)
    with (
        pytest.raises(ConfigError, match="TEAMS_WEBHOOK_DEMO_DM"),
        TestClient(create_app(settings.model_copy(update={"mode": Mode.LIVE}))),
    ):
        pass


def test_live_mode_refuses_a_non_https_webhook(settings, config_file, monkeypatch):
    data = yaml.safe_load(config_file.read_text())
    data["placeholder"] = False
    config_file.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("TEAMS_WEBHOOK_DEMO_DM", "http://insecure.example/hook")
    with (
        pytest.raises(ConfigError, match="https"),
        TestClient(create_app(settings.model_copy(update={"mode": Mode.LIVE}))),
    ):
        pass


def test_row_for_unknown_project_is_not_posted_and_says_why(live_settings):
    app = create_app(live_settings)
    cfg2 = app.state  # noqa: F841  (config is loaded at startup)
    with TestClient(app) as c:
        app.state.clock = lambda: NOW
        # Two projects configured, so a row without a project cannot be routed.
        app.state.config.projects.append(app.state.config.projects[0].model_copy(update={"name": "Other"}))
        with app.state.session_factory() as db:
            db.add(Outbox(channel="teams_dm", target="t", body="b", mode="live"))
            db.commit()
        assert pending(c)["items"] == []
        row = c.get("/outbox", headers=AUTH).json()[0]
        assert "no webhook" in row["last_error"]
