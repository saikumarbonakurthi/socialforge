import logging
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.ops import _label
from app.domain.channels import BOT_FOOTER
from app.domain.delivery import MAX_ATTEMPTS
from app.domain.sync import sync_project
from app.integrations.github import GitHubError, ProjectData
from app.logging import configure_logging
from app.main import create_app
from app.models import Event, LlmCall, Nudge, Outbox, Project, WorkItemSnapshot
from app.settings import Mode, Settings
from tests.conftest import SECRET
from tests.helpers import NOW, days_ago, item

AUTH = {"Authorization": f"Bearer {SECRET}"}


@pytest.fixture
def app(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        app.state.clock = lambda: NOW
        cfg = app.state.config.projects[0]
        with app.state.session_factory() as db:
            sync_project(db, cfg, ProjectData("PVT", [item()]), NOW - timedelta(minutes=20))
        yield app, c


@pytest.fixture
def live(app):
    """The same app, switched to live mode after startup (dead letter handling only acts when live)."""
    app, c = app
    app.state.settings = app.state.settings.model_copy(update={"mode": Mode.LIVE})
    return app, c


def add(app, **kw):
    base = dict(project_id=1, channel="teams_dm", target="demo-ravi", body="hello", mode="live", attempts=MAX_ATTEMPTS,
                created_at=days_ago(1))  # fmt: skip
    base.update(kw)
    with app.state.session_factory() as db:
        row = Outbox(**base)
        db.add(row)
        db.commit()
        return row.id


def get(app, id_):
    with app.state.session_factory() as db:
        return db.get(Outbox, id_)


# Status and metrics ---------------------------------------------------------------------------------
def test_status_and_metrics_need_auth(app):
    _, c = app
    for path in ("/status", "/metrics"):
        assert c.get(path).status_code == 401
    assert c.get("/health").status_code == 200  # the liveness probe stays open


def test_status_reports_sync_age_outbox_and_features(app):
    app, c = app
    add(app)  # dead
    add(app, attempts=1)  # pending live
    add(app, mode="dry_run", attempts=0)
    s = c.get("/status", headers=AUTH).json()
    assert (s["mode"], s["db"]) == ("dry_run", True)
    assert s["features"] == {
        "claude_wording": False,
        "teams_bot": False,
        "whatsapp": False,
        "delivery_redirect": False,
    }
    assert s["projects"] == [
        {"name": "Demo Project", "last_sync_at": s["projects"][0]["last_sync_at"], "seconds_since_sync": 1200}
    ]
    assert (s["outbox"]["dead"], s["outbox"]["pending"], s["outbox"]["dry_run"]) == (1, 1, 1)
    assert s["llm"] == {"spent_today_usd": 0.0, "ceiling_usd": None}


def test_metrics_are_valid_prometheus_text(app):
    app, c = app
    add(app)
    c.get("/health")  # something to count
    r = c.get("/metrics", headers=AUTH)
    assert r.headers["content-type"].startswith("text/plain")
    text = r.text
    assert 'nxsprint_info{version="0.1.0",mode="dry_run"} 1' in text and "nxsprint_db_up 1" in text
    assert 'nxsprint_outbox_rows{status="dead"} 1' in text
    assert 'nxsprint_last_sync_age_seconds{project="Demo Project"} 1200' in text
    assert (
        "nxsprint_llm_spend_usd_today 0.0" in text
        and 'nxsprint_http_requests_total{status_class="2xx"}' in text
    )
    for line in text.splitlines():  # every line is a comment, a blank, or "name{labels} number"
        assert line.startswith("#") or line.rsplit(" ", 1)[1].replace(".", "", 1).isdigit(), line


def test_label_escaping():
    assert _label('Say "hi"\\ now\nok') == 'Say \\"hi\\"\\\\ now\\nok'


def test_http_request_counter_counts_errors_too(app):
    app, c = app
    c.get("/nope")  # 404
    assert 'status_class="4xx"' in c.get("/metrics", headers=AUTH).text


# Dead letters -----------------------------------------------------------------------------------------
def test_dead_rows_are_listed_by_status(app):
    app, c = app
    dead = add(app)
    add(app, attempts=1)
    rows = c.get("/outbox?status=dead", headers=AUTH).json()
    assert [r["id"] for r in rows] == [dead] and rows[0]["status"] == "dead"


def test_owner_is_told_once_about_dead_letters(live):
    app, c = live
    a, b = add(app), add(app, channel="whatsapp", target="+919876543210")
    out = c.post("/jobs/dead_letters", headers=AUTH).json()
    assert out == {"dead": 2, "failed_over": 0, "alerted": 1}
    alert = next(r for r in c.get("/outbox", headers=AUTH).json() if r["channel"] == "owner_alert")
    assert alert["target"] == "sai" and alert["mode"] == "live"
    assert alert["body"] == (
        "NxSprint could not deliver 2 messages after several attempts: 1 teams_dm, 1 whatsapp. "
        "Please look at the dead letters in the runbook."
    )
    assert "-" not in alert["body"] and "9876" not in alert["body"]  # no dashes, no numbers
    again = c.post("/jobs/dead_letters", headers=AUTH).json()
    assert again["alerted"] == 0  # the same rows are not reported twice
    assert get(app, a).dead_alerted_at is not None and get(app, b).dead_alerted_at is not None
    add(app)  # a new failure is a new alert
    assert c.post("/jobs/dead_letters", headers=AUTH).json()["alerted"] == 1


def test_a_dead_alert_never_triggers_another_alert(live):
    app, c = live
    add(app, channel="owner_alert", target="sai")
    assert c.post("/jobs/dead_letters", headers=AUTH).json() == {"dead": 1, "failed_over": 0, "alerted": 0}


def test_dry_run_delivered_dismissed_and_recent_failures_are_not_dead_letters(live):
    app, c = live
    add(app, mode="dry_run")
    add(app, delivered_at=NOW)
    add(app, dismissed_at=NOW)
    add(app, attempts=2)
    add(app, leased_at=NOW)  # still leased, a delivery is in flight
    assert c.post("/jobs/dead_letters", headers=AUTH).json()["dead"] == 0


def test_bot_messages_fail_over_to_the_webhook_instead_of_alerting(live):
    app, c = live
    with app.state.session_factory() as db:
        project = db.scalar(select(Project))
        nudge = Nudge(project_id=project.id, member_id=2, issue_node_id="I_1", rule="NO_ESTIMATE", channel="teams_bot",
                      message="m", status="sent")  # fmt: skip
        db.add(nudge)
        db.commit()
        nudge_id = nudge.id
    bot_row = add(app, channel="teams_bot", body=f"Hi Ravi, please look.\n\n{BOT_FOOTER}", nudge_id=nudge_id,
                  last_error="RuntimeError: connector down")  # fmt: skip
    out = c.post("/jobs/dead_letters", headers=AUTH).json()
    assert out == {"dead": 1, "failed_over": 1, "alerted": 0}
    assert get(app, bot_row).dismissed_at is not None and "webhook instead" in get(app, bot_row).last_error
    with app.state.session_factory() as db:
        new = db.scalars(select(Outbox).where(Outbox.channel == "teams_dm", Outbox.id != bot_row)).one()
        assert (new.body, new.target, new.mode, new.attempts, new.nudge_id) == (
            "Hi Ravi, please look.",
            "demo-ravi",
            "live",
            0,
            nudge_id,
        )
        assert db.get(Nudge, nudge_id).channel == "teams_dm"
    assert c.post("/jobs/dead_letters", headers=AUTH).json()["failed_over"] == 0  # only once


# Retry and dismiss ---------------------------------------------------------------------------------------
def test_retry_gives_a_dead_row_a_fresh_set_of_attempts(app):
    app, c = app
    rid = add(app, last_error="boom", dead_alerted_at=NOW)
    out = c.post(f"/outbox/{rid}/retry", headers=AUTH).json()
    assert out == {"id": rid, "status": "pending", "attempts": 0}
    row = get(app, rid)
    assert (row.attempts, row.dead_alerted_at, row.next_attempt_at) == (
        0,
        None,
        None,
    ) and row.last_error == "boom"


def test_dismiss_stops_a_row_for_good_and_is_idempotent(app):
    app, c = app
    rid = add(app)
    assert c.post(f"/outbox/{rid}/dismiss", headers=AUTH).json()["status"] == "dismissed"
    first = get(app, rid).dismissed_at
    assert (
        c.post(f"/outbox/{rid}/dismiss", headers=AUTH).status_code == 200
        and get(app, rid).dismissed_at == first
    )
    assert c.post("/jobs/dead_letters", headers=AUTH).json()["dead"] == 0
    assert (
        c.post(f"/outbox/{rid}/retry", headers=AUTH).json()["status"] == "pending"
    )  # a dismissal can be undone


def test_retry_and_dismiss_refuse_the_wrong_rows(app):
    app, c = app
    dry, done = add(app, mode="dry_run"), add(app, delivered_at=NOW)
    for path in (f"/outbox/{dry}/retry", f"/outbox/{dry}/dismiss", f"/outbox/{done}/retry"):
        assert c.post(path, headers=AUTH).status_code == 409
    assert c.post("/outbox/9999/retry", headers=AUTH).status_code == 404
    assert c.post("/outbox/9999/dismiss", headers=AUTH).status_code == 404
    assert c.post(f"/outbox/{done}/retry").status_code == 401


# Retention ----------------------------------------------------------------------------------------------
def test_prune_is_off_until_a_retention_is_set(app):
    _, c = app
    assert c.post("/jobs/prune", headers=AUTH).json() == {"enabled": False}


def test_retention_must_be_at_least_thirty_days():
    with pytest.raises(ValueError):
        Settings(api_secret="x" * 16, _env_file=None, retention_days=7)
    assert Settings(api_secret="x" * 16, _env_file=None, retention_days=30).retention_days == 30


def test_prune_deletes_old_bookkeeping_and_keeps_history_and_open_work(settings):
    app = create_app(settings.model_copy(update={"retention_days": 30}))
    with TestClient(app) as c:
        app.state.clock = lambda: NOW
        cfg = app.state.config.projects[0]
        with app.state.session_factory() as db:
            sync_project(db, cfg, ProjectData("PVT", [item()]), days_ago(90))  # old snapshots and sync event
            sync_project(db, cfg, ProjectData("PVT", [item(title="Changed")]), days_ago(1))
            db.add(
                LlmCall(
                    purpose="x",
                    model="m",
                    input_tokens=1,
                    output_tokens=1,
                    cost_estimate=0.1,
                    latency_ms=1,
                    created_at=days_ago(60),
                )
            )
            db.add(
                LlmCall(
                    purpose="x",
                    model="m",
                    input_tokens=1,
                    output_tokens=1,
                    cost_estimate=0.1,
                    latency_ms=1,
                    created_at=days_ago(2),
                )
            )
            db.commit()
        old_dry = add(app, mode="dry_run", attempts=0, created_at=days_ago(60))
        old_done = add(app, delivered_at=days_ago(59), created_at=days_ago(60))
        old_open = add(app, attempts=1, created_at=days_ago(60))  # still waiting to be delivered
        old_dead = add(app, created_at=days_ago(60))  # dead, not yet dealt with
        recent = add(app, mode="dry_run", attempts=0, created_at=days_ago(2))
        out = c.post("/jobs/prune", headers=AUTH).json()
        assert (
            out["enabled"]
            and out["retention_days"] == 30
            and out["outbox"] == 2
            and out["llm_calls"] == 1
            and out["events"] == 1
        )
        with app.state.session_factory() as db:
            kept = {r.id for r in db.scalars(select(Outbox))}
            assert kept == {old_open, old_dead, recent} and old_dry not in kept and old_done not in kept
            assert db.scalars(select(WorkItemSnapshot)).all().__len__() == 2  # history untouched
            assert len(db.scalars(select(Event).where(Event.type == "sync")).all()) == 1


# One project failing must not stop the others -----------------------------------------------------------
def two_projects(app):
    cfg = app.state.config.projects[0]
    other = cfg.model_copy(deep=True, update={"name": "Other"})
    app.state.config.projects.append(other)
    with app.state.session_factory() as db:
        sync_project(db, other, ProjectData("PVT2", [item(estimate=None)]), NOW - timedelta(minutes=20))
        sync_project(db, cfg, ProjectData("PVT", [item(estimate=None)]), NOW - timedelta(minutes=20))


def test_a_failing_project_does_not_stop_the_next_one(app, monkeypatch):
    app, c = app
    two_projects(app)
    import app.api.nudges as mod

    real = mod.run_nudges

    def flaky(db, project, cfg, *args, **kw):
        if cfg.name == "Demo Project":
            raise RuntimeError("boom")
        return real(db, project, cfg, *args, **kw)

    monkeypatch.setattr(mod, "run_nudges", flaky)
    r = c.post("/jobs/nudges", headers=AUTH)
    assert r.status_code == 500  # so n8n marks the execution failed
    projects = r.json()["detail"]["projects"]
    assert {"project": "Demo Project", "error": "failed, see the logs", "failed": True} in projects
    assert next(p for p in projects if p["project"] == "Other")["created"]  # the healthy project still ran
    with app.state.session_factory() as db:
        assert db.scalars(select(Nudge)).all()  # and its nudges were saved


def test_sync_continues_after_a_github_error_and_reports_it(app):
    app, c = app
    two_projects(app)

    class Client:
        def fetch_project(self, org, number, fields):
            if number == 1:  # the first configured project
                raise GitHubError("project not found")
            return ProjectData("PVT2", [item()])

    app.state.github_client_factory = lambda request: Client()
    app.state.config.projects[1].github.project_number = 2
    r = c.post("/jobs/sync", headers=AUTH)
    assert r.status_code == 502
    projects = r.json()["detail"]["projects"]
    assert projects[0]["failed"] and "project not found" in projects[0]["error"]
    assert projects[1]["project"] == "Other" and projects[1]["items_seen"] == 1


# Logging ------------------------------------------------------------------------------------------------
def test_http_libraries_are_kept_quiet_so_urls_do_not_reach_the_logs():
    configure_logging()
    for name in ("httpx", "httpx2", "httpcore", "anthropic"):
        assert logging.getLogger(name).level == logging.WARNING


def test_dry_run_dead_letter_job_changes_nothing(app):
    app, c = app
    add(app)
    assert c.post("/jobs/dead_letters", headers=AUTH).json() == {"dead": 1, "failed_over": 0, "alerted": 0}
    assert get(app, 1).dead_alerted_at is None  # the one alert is saved for when it can actually be sent
    assert [r["channel"] for r in c.get("/outbox", headers=AUTH).json()] == ["teams_dm"]
