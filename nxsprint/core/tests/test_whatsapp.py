import json
from datetime import timedelta

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import ConfigError, load_config
from app.domain.delivery import MAX_ATTEMPTS
from app.domain.sync import sync_project
from app.integrations.github import ProjectData
from app.integrations.whatsapp import WhatsAppClient, WhatsAppError, digits
from app.main import create_app
from app.models import Outbox
from app.settings import Mode, Settings
from tests.conftest import SECRET
from tests.helpers import ist, item

AUTH = {"Authorization": f"Bearer {SECRET}"}
NUMBER = "+919876543210"
HOOK = "https://prod-00.example.logic.azure.com/workflows/abc?sig=S"


# Client ----------------------------------------------------------------------------------------------
def client_with(handler):
    return WhatsAppClient("TOKEN", "1234567890", "v99.0", transport=httpx.MockTransport(handler))


def test_digits_normalises_and_rejects():
    assert digits("+91 98765 43210") == "919876543210"
    for bad in ("12345", "abcdefghijk", "+0123456789", ""):
        with pytest.raises(WhatsAppError):
            digits(bad)


def test_send_template_request_shape():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["auth"], seen["body"] = (
            str(request.url),
            request.headers["authorization"],
            json.loads(request.content),
        )
        return httpx.Response(200, json={"messages": [{"id": "wamid.X"}]})

    msg_id = client_with(handler).send_template(
        "+91 98765 43210", "nxsprint_critical", "en", ["Asha", "Outage", "link"]
    )
    assert msg_id == "wamid.X"
    assert (
        seen["url"] == "https://graph.facebook.com/v99.0/1234567890/messages"
        and seen["auth"] == "Bearer TOKEN"
    )
    assert seen["body"] == {
        "messaging_product": "whatsapp",
        "to": "919876543210",
        "type": "template",
        "template": {
            "name": "nxsprint_critical",
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": t} for t in ("Asha", "Outage", "link")],
                }
            ],
        },
    }


def test_errors_carry_meta_message_but_never_the_number_or_token():
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "Template not approved", "code": 132001}})

    with pytest.raises(WhatsAppError) as exc:
        client_with(handler).send_template(NUMBER, "t", "en", ["x"])
    text = str(exc.value)
    assert "Template not approved" in text and "9876543210" not in text and "TOKEN" not in text
    with pytest.raises(WhatsAppError, match="HTTP 502"):
        client_with(lambda r: httpx.Response(502, text="<html>")).send_template(NUMBER, "t", "en", ["x"])


# Config and settings ----------------------------------------------------------------------------------
def edit(config_file, fn):
    data = yaml.safe_load(config_file.read_text())
    fn(data["projects"][0])
    config_file.write_text(yaml.safe_dump(data))


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda p: p["members"][0].update(whatsapp_number="9876543210"), "whatsapp_number"),
        (lambda p: p["members"][0].update(whatsapp_number="+91 98765 43210"), "whatsapp_number"),
        (lambda p: p["escalation"].update(ack_hours_before_whatsapp=6), "must increase"),
        (lambda p: p["escalation"].pop("ack_hours_before_whatsapp"), "ack_hours_before_whatsapp"),
        (lambda p: p.pop("critical"), "critical"),
        (lambda p: p["critical"].update(not_started_statuses=[]), "not_started_statuses"),
        (lambda p: p["critical"].update(unowned_production_blocker_working_hours=0), "unowned_production"),
        (lambda p: p["cooldowns"]["hours"].pop("GOAL_ITEM_NOT_STARTED"), "GOAL_ITEM_NOT_STARTED"),
        (lambda p: p["whatsapp"].update(max_per_person_per_day=0), "max_per_person_per_day"),
        (lambda p: p["working_hours"].update(start="19:00", end="09:00"), "working_hours"),
    ],
)
def test_critical_and_whatsapp_config_is_checked(config_file, mutate, match):
    edit(config_file, mutate)
    with pytest.raises(ConfigError, match=match):
        load_config(config_file, Mode.DRY_RUN)


def test_whatsapp_block_is_optional_and_a_valid_number_is_accepted(config_file):
    edit(config_file, lambda p: (p.pop("whatsapp"), p["members"][0].update(whatsapp_number=NUMBER)))
    cfg = load_config(config_file, Mode.DRY_RUN).projects[0]
    assert cfg.whatsapp is None and cfg.members[0].whatsapp_number == NUMBER


def test_whatsapp_is_off_by_default_and_needs_all_credentials_when_on():
    assert Settings(api_secret="x" * 16, _env_file=None).whatsapp_enabled is False
    with pytest.raises(ValueError, match="NXSPRINT_WHATSAPP_ENABLED needs"):
        Settings(api_secret="x" * 16, _env_file=None, whatsapp_enabled=True, whatsapp_token="t")
    ok = Settings(api_secret="x" * 16, _env_file=None, whatsapp_enabled=True, whatsapp_token="t",
                  whatsapp_phone_number_id="1", whatsapp_api_version="v1.0")  # fmt: skip
    assert ok.whatsapp_enabled


# API ---------------------------------------------------------------------------------------------------
class FakeWhatsApp:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_template(self, to, template, language, params):
        if self.fail:
            raise WhatsAppError("HTTP 400: Template not approved")
        self.sent.append((to, template, language, params))
        return "wamid.1"


def live_app(settings, config_file, monkeypatch, *, enabled=True):
    def mutate(p):
        p["members"][0]["whatsapp_number"] = NUMBER

    edit(config_file, mutate)
    data = yaml.safe_load(config_file.read_text())
    data["placeholder"] = False
    config_file.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("TEAMS_WEBHOOK_DEMO_DM", HOOK)
    update = {"mode": Mode.LIVE, "whatsapp_enabled": enabled, "whatsapp_token": "t",
              "whatsapp_phone_number_id": "1", "whatsapp_api_version": "v1.0"}  # fmt: skip
    return create_app(settings.model_copy(update=update))


def blocker():
    return item(issue_node_id="PB", title="Checkout outage", assignee_login=None, labels=("production",),
                estimate=3.0, sprint_name=None, sprint_start=None, sprint_days=None)  # fmt: skip


def seed_and_escalate(app, c):
    cfg = app.state.config.projects[0]
    with app.state.session_factory() as db:
        sync_project(db, cfg, ProjectData("PVT", [blocker()]), ist(9, 30, 9, 30))
    for when, path in [(ist(10, 1, 9, 45), "/jobs/nudges"), (ist(10, 1, 14, 0), "/jobs/escalations"),
                       (ist(10, 1, 18, 0), "/jobs/escalations"), (ist(10, 2, 10, 0), "/jobs/escalations")]:  # fmt: skip
        app.state.clock = lambda when=when: when
        assert c.post(path, headers=AUTH).status_code == 200


def test_escalation_job_needs_auth_and_a_synced_project(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        assert c.post("/jobs/escalations").status_code == 401
        assert "not synced" in c.post("/jobs/escalations", headers=AUTH).json()["projects"][0]["error"]


def test_full_ladder_over_the_api_and_the_number_never_leaks(settings, config_file, monkeypatch):
    app = live_app(settings, config_file, monkeypatch)
    with TestClient(app) as c:
        app.state.whatsapp = FakeWhatsApp()
        seed_and_escalate(app, c)
        out = c.get("/outbox", headers=AUTH)
        rows = {r["channel"]: r for r in out.json()}
        assert {"teams_dm", "teams_team", "whatsapp"} <= set(rows)
        assert rows["whatsapp"]["target"] == "...3210" and "9876543210" not in out.text
        assert "9876543210" not in c.get("/nudges", headers=AUTH).text
        job = c.post("/jobs/escalations", headers=AUTH).json()
        assert job["whatsapp_enabled"] is True and job["mode"] == "live"


def test_deliver_whatsapp_sends_the_template_and_marks_delivery(settings, config_file, monkeypatch):
    app = live_app(settings, config_file, monkeypatch)
    with TestClient(app) as c:
        fake = app.state.whatsapp = FakeWhatsApp()
        seed_and_escalate(app, c)
        out = c.post("/jobs/deliver_whatsapp", headers=AUTH).json()
        assert out == {"enabled": True, "mode": "live", "sent": 1, "failed": 0}
        to, template, language, params = fake.sent[0]
        assert (to, template, language) == (NUMBER, "nxsprint_critical", "en")
        assert params[0] == "Asha" and "Checkout outage" in params[1]
        wa = next(r for r in c.get("/outbox", headers=AUTH).json() if r["channel"] == "whatsapp")
        assert wa["status"] == "delivered"
        assert c.post("/jobs/deliver_whatsapp", headers=AUTH).json()["sent"] == 0  # not sent twice
        assert "whatsapp" not in {
            i["payload"]["channel"] for i in c.get("/outbox/pending", headers=AUTH).json()["items"]
        }


def test_whatsapp_failures_retry_then_park_without_leaking_the_number(settings, config_file, monkeypatch):
    app = live_app(settings, config_file, monkeypatch)
    with TestClient(app) as c:
        app.state.whatsapp = FakeWhatsApp(fail=True)
        seed_and_escalate(app, c)
        base = ist(10, 2, 10, 0)
        for i in range(MAX_ATTEMPTS):
            app.state.clock = lambda i=i: base + timedelta(hours=2 * i)
            assert c.post("/jobs/deliver_whatsapp", headers=AUTH).json()["failed"] == 1
        app.state.clock = lambda: base + timedelta(days=2)
        assert c.post("/jobs/deliver_whatsapp", headers=AUTH).json()["failed"] == 0
        wa = next(r for r in c.get("/outbox", headers=AUTH).json() if r["channel"] == "whatsapp")
        assert (
            wa["status"] == "dead"
            and "Template not approved" in wa["last_error"]
            and "9876" not in wa["last_error"]
        )


def test_deliver_whatsapp_is_a_noop_when_the_flag_is_off(settings, config_file, monkeypatch):
    app = live_app(settings, config_file, monkeypatch, enabled=False)
    with TestClient(app) as c:
        seed_and_escalate(app, c)
        assert c.post("/jobs/deliver_whatsapp", headers=AUTH).json() == {
            "enabled": False,
            "sent": 0,
            "failed": 0,
        }
        assert not any(
            r["channel"] == "whatsapp" for r in c.get("/outbox", headers=AUTH).json()
        )  # none were even queued
        again = c.post("/jobs/escalations", headers=AUTH).json()["projects"][0]
        assert again["whatsapp_off"] == 1 and again["level4"] == 0  # reached the last rung, flag says no


def test_deliver_whatsapp_sends_nothing_in_dry_run(settings, config_file):
    edit(config_file, lambda p: p["members"][0].update(whatsapp_number=NUMBER))
    enabled = {"whatsapp_enabled": True, "whatsapp_token": "t", "whatsapp_phone_number_id": "1",
               "whatsapp_api_version": "v1.0"}  # fmt: skip
    app = create_app(settings.model_copy(update=enabled))  # mode stays dry_run
    with TestClient(app) as c:
        fake = app.state.whatsapp = FakeWhatsApp()
        seed_and_escalate(app, c)
        out = c.post("/jobs/deliver_whatsapp", headers=AUTH).json()
        assert out["sent"] == 0 and out["mode"] == "dry_run" and fake.sent == []
        with app.state.session_factory() as db:
            wa = db.scalars(select(Outbox).where(Outbox.channel == "whatsapp")).all()
        assert len(wa) == 1 and wa[0].mode == "dry_run"


def test_startup_refuses_whatsapp_without_a_lead_number(settings, config_file):
    s = settings.model_copy(update={"whatsapp_enabled": True, "whatsapp_token": "t",
                                    "whatsapp_phone_number_id": "1", "whatsapp_api_version": "v1.0"})  # fmt: skip
    with pytest.raises(ConfigError, match="no whatsapp_number"), TestClient(create_app(s)):
        pass
