import json
from datetime import timedelta

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.nudges import run_nudges
from app.domain.sync import sync_project
from app.integrations.github import ProjectData
from app.main import create_app
from app.models import Member, Nudge, Outbox, Project, StandupPrompt, StandupResponse, TeamsConversation
from app.settings import Mode, Settings
from tests.bot_helpers import APP_ID, OTHER_KEY, SERVICE_URL, activity, make_token, resolver
from tests.conftest import SECRET
from tests.helpers import NOW, days_ago, item

AUTH = {"Authorization": f"Bearer {SECRET}"}
HOOK = "https://prod-00.example.logic.azure.com/workflows/abc?sig=S"


class FakeBot:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_text(self, service_url, conversation_id, text, reply_to_id=None):
        if self.fail:
            raise RuntimeError("connector down")
        self.sent.append((service_url, conversation_id, text, reply_to_id))


def bot_settings(settings, **kw):
    return settings.model_copy(
        update={"bot_app_id": APP_ID, "bot_app_password": "pw", "bot_tenant_id": "tenant", **kw}
    )


def post(c, body, token=None, **headers):
    hdr = {"Authorization": f"Bearer {token}"} if token else {}
    return c.post("/webhooks/teams", content=json.dumps(body), headers={**hdr, **headers})


@pytest.fixture
def app(settings):
    app = create_app(bot_settings(settings))
    with TestClient(app) as c:
        app.state.bot_keys = resolver  # stand in for Microsoft's published keys
        app.state.bot = FakeBot()
        app.state.clock = lambda: NOW
        cfg = app.state.config.projects[0]
        with app.state.session_factory() as db:
            sync_project(db, cfg, ProjectData("PVT", [item(estimate=None)]), days_ago(2))
        yield app, c


def test_endpoint_is_503_when_bot_not_configured(client):
    assert post(client, activity("ack"), make_token()).status_code == 503


def test_rejects_missing_wrong_and_replayed_tokens(app):
    app, c = app
    body = activity("ack")
    assert post(c, body).status_code == 401
    assert post(c, body, make_token(aud="other")).status_code == 401
    assert post(c, body, make_token(key=OTHER_KEY)).status_code == 401
    assert post(c, body, make_token(serviceurl="https://evil.example/")).status_code == 401
    assert c.post("/webhooks/teams", content="nope").status_code == 400
    assert app.state.bot.sent == []


def test_dry_run_records_the_reply_in_the_outbox_and_sends_nothing(app):
    app, c = app
    r = post(c, activity("help"), make_token())
    assert r.status_code == 200 and r.json() == {"handled": True, "replies": 1}
    assert app.state.bot.sent == []
    with app.state.session_factory() as db:
        row = db.scalars(select(Outbox).where(Outbox.channel == "teams_reply")).one()
        assert (row.mode, row.target) == ("dry_run", "conv-demo-ravi") and row.body.startswith(
            "We are NxSprint"
        )
        assert db.scalar(select(TeamsConversation)) is not None


def test_ack_over_the_wire_updates_the_nudge(app):
    app, c = app
    with app.state.session_factory() as db:
        project = db.scalar(select(Project))
        run_nudges(db, project, app.state.config.projects[0], Mode.DRY_RUN, NOW)
        before = db.scalars(select(Nudge).join(Member).where(Member.github_login == "ravi-demo")).all()
        assert before and all(n.status == "queued" for n in before)
    assert post(c, activity("<at>NxSprint</at> ack"), make_token()).status_code == 200
    with app.state.session_factory() as db:
        after = db.scalars(select(Nudge).join(Member).where(Member.github_login == "ravi-demo")).all()
        assert all(n.status == "acked" for n in after)


def test_live_mode_replies_through_the_bot_and_survives_a_send_failure(settings, config_file, monkeypatch):
    data = yaml.safe_load(config_file.read_text())
    data["placeholder"] = False
    config_file.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("TEAMS_WEBHOOK_DEMO_DM", HOOK)
    app = create_app(bot_settings(settings, mode=Mode.LIVE))
    with TestClient(app) as c:
        app.state.bot_keys, app.state.clock = resolver, lambda: NOW
        app.state.bot = FakeBot()
        with app.state.session_factory() as db:
            sync_project(db, app.state.config.projects[0], ProjectData("PVT", [item()]), days_ago(2))
        assert post(c, activity("help"), make_token()).status_code == 200
        assert (
            app.state.bot.sent[0][:2] == (SERVICE_URL, "conv-demo-ravi")
            and app.state.bot.sent[0][3] == "act-1"
        )
        app.state.bot = FakeBot(fail=True)
        assert post(c, activity("help"), make_token()).status_code == 200  # Teams must not be made to retry


def test_standup_jobs_end_to_end(app):
    app, c = app
    assert c.post("/jobs/standup").status_code == 401
    app.state.clock = lambda: NOW - timedelta(hours=1)  # 10:00 IST
    out = c.post("/jobs/standup", headers=AUTH).json()["projects"][0]
    assert out["prompted"] == 2
    assert post(c, activity("Done: a. Doing: b. Blocked: none"), make_token()).status_code == 200
    app.state.clock = lambda: NOW  # 11:00 IST
    assert c.post("/jobs/standup_summary", headers=AUTH).json()["projects"][0] == {
        "project": "Demo Project", "posted": True, "why_not": None,
    }  # fmt: skip
    again = c.post("/jobs/standup_summary", headers=AUTH).json()["projects"][0]
    assert (again["posted"], again["why_not"]) == (False, "already posted")
    with app.state.session_factory() as db:
        assert (
            db.scalar(select(StandupResponse)).done == "a"
            and len(db.scalars(select(StandupPrompt)).all()) == 2
        )
    bodies = {o["channel"]: o["body"] for o in c.get("/outbox", headers=AUTH).json()}
    assert "Replies: 1 of 2 members." in bodies["teams_team"]


def test_standup_jobs_before_sync_say_so(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        for path in ("/jobs/standup", "/jobs/standup_summary"):
            assert "not synced" in c.post(path, headers=AUTH).json()["projects"][0]["error"]


# Delivery of bot rows by core itself.
@pytest.fixture
def live(settings, config_file, monkeypatch):
    data = yaml.safe_load(config_file.read_text())
    data["placeholder"] = False
    config_file.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("TEAMS_WEBHOOK_DEMO_DM", HOOK)
    monkeypatch.setenv("TEAMS_WEBHOOK_DEMO_TEAM", HOOK)
    app = create_app(bot_settings(settings, mode=Mode.LIVE))
    with TestClient(app) as c:
        app.state.bot_keys, app.state.clock, app.state.bot = resolver, lambda: NOW, FakeBot()
        cfg = app.state.config.projects[0]
        with app.state.session_factory() as db:
            sync_project(db, cfg, ProjectData("PVT", [item(estimate=None)]), days_ago(2))
        assert post(c, activity("help"), make_token()).status_code == 200  # Ravi talks to the bot
        app.state.bot.sent.clear()
        with app.state.session_factory() as db:
            run_nudges(db, db.scalar(select(Project)), cfg, Mode.LIVE, NOW, use_bot=True)
        yield app, c


def test_bot_rows_are_not_offered_to_n8n_but_are_sent_by_core(live):
    app, c = live
    with app.state.session_factory() as db:
        channels = {o.channel for o in db.scalars(select(Outbox).where(Outbox.mode == "live"))}
    assert channels == {"teams_bot"}  # the only nudge belongs to Ravi, who has a conversation
    assert c.get("/outbox/pending", headers=AUTH).json()["items"] == []  # n8n sees nothing
    out = c.post("/jobs/deliver_bot", headers=AUTH).json()
    assert (out["sent"], out["failed"]) == (1, 0)
    service_url, conv, text, _ = app.state.bot.sent[0]
    assert (service_url, conv) == (SERVICE_URL, "conv-demo-ravi") and text.endswith(
        "Reply ack when you have seen this."
    )
    rows = c.get("/outbox", headers=AUTH).json()
    assert [r["status"] for r in rows if r["channel"] == "teams_bot"] == ["delivered"]
    assert c.post("/jobs/deliver_bot", headers=AUTH).json() == {"mode": "live", "sent": 0, "failed": 0}


def test_bot_send_failure_is_retried_then_parked(live):
    app, c = live
    app.state.bot = FakeBot(fail=True)
    for i in range(5):  # two hours apart, longer than any backoff step
        app.state.clock = lambda i=i: NOW + timedelta(hours=2 * i)
        assert c.post("/jobs/deliver_bot", headers=AUTH).json()["failed"] == 1
    app.state.clock = lambda: NOW + timedelta(days=2)
    assert c.post("/jobs/deliver_bot", headers=AUTH).json()["failed"] == 0  # attempts used up
    row = next(r for r in c.get("/outbox", headers=AUTH).json() if r["channel"] == "teams_bot")
    assert row["status"] == "dead" and row["attempts"] == 5 and "connector down" in row["last_error"]


def test_bot_row_without_a_stored_conversation_fails_cleanly(live):
    app, c = live
    with app.state.session_factory() as db:
        for conv in db.scalars(select(TeamsConversation)):
            db.delete(conv)
        db.commit()
    assert c.post("/jobs/deliver_bot", headers=AUTH).json()["failed"] == 1


def test_deliver_bot_is_a_noop_in_dry_run_and_503_without_bot(settings, client):
    assert client.post("/jobs/deliver_bot", headers=AUTH).status_code == 503
    app = create_app(bot_settings(settings))
    with TestClient(app) as c:
        app.state.bot = FakeBot()
        assert c.post("/jobs/deliver_bot", headers=AUTH).json() == {"mode": "dry_run", "sent": 0, "failed": 0}


def test_team_summary_goes_to_n8n_through_the_team_webhook(live):
    app, c = live
    cfg = app.state.config.projects[0]
    with app.state.session_factory() as db:
        project = db.scalar(select(Project))
        db.add(
            Outbox(
                project_id=project.id,
                channel="teams_team",
                target="team",
                body="Standup summary",
                mode="live",
            )
        )
        db.commit()
    items = c.get("/outbox/pending", headers=AUTH).json()["items"]
    assert [i["payload"] for i in items] == [
        {"channel": "teams_team", "target": "team", "text": "Standup summary"}
    ]
    assert items[0]["webhook_url"] == HOOK and cfg.channels.team_webhook_env == "TEAMS_WEBHOOK_DEMO_TEAM"


def test_redirect_keeps_nudges_off_the_bot(settings):
    app = create_app(bot_settings(settings, delivery_redirect_target="TEST-ROOM"))
    with TestClient(app) as c:
        app.state.bot_keys, app.state.clock, app.state.bot = resolver, lambda: NOW, FakeBot()
        with app.state.session_factory() as db:
            sync_project(
                db, app.state.config.projects[0], ProjectData("PVT", [item(estimate=None)]), days_ago(2)
            )
        post(c, activity("help"), make_token())
        c.post("/jobs/nudges", headers=AUTH)
        assert {o["channel"] for o in c.get("/outbox", headers=AUTH).json()} <= {"teams_dm", "teams_reply"}


def test_bot_settings_are_all_or_none():
    with pytest.raises(ValueError, match="go together"):
        Settings(api_secret="x" * 16, _env_file=None, bot_app_id="a")
    ok = Settings(
        api_secret="x" * 16, _env_file=None,
        bot_app_id="a", bot_app_password="b", bot_tenant_id="c",
    )  # fmt: skip
    assert ok.bot_enabled and not Settings(api_secret="x" * 16, _env_file=None).bot_enabled
