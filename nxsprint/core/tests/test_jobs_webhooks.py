import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.integrations.github import GitHubClient
from app.main import create_app
from tests.conftest import SECRET

AUTH = {"Authorization": f"Bearer {SECRET}"}
FIX = Path(__file__).parent / "fixtures"


def _fake_factory(request):
    def handler(req):
        after = json.loads(req.content)["variables"]["after"]
        name = "project_page1.json" if after is None else "project_page2.json"
        return httpx.Response(200, json=json.loads((FIX / name).read_text()))

    return GitHubClient("t", transport=httpx.MockTransport(handler))


def test_sync_job_requires_auth(client):
    assert client.post("/jobs/sync").status_code == 401


def test_sync_job_without_token_is_503(client):
    assert client.post("/jobs/sync", headers=AUTH).status_code == 503


def test_sync_job_runs_and_is_idempotent(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        app.state.github_client_factory = _fake_factory
        first = c.post("/jobs/sync", headers=AUTH).json()["projects"][0]
        second = c.post("/jobs/sync", headers=AUTH).json()["projects"][0]
    assert (first["items_seen"], first["snapshots_written"]) == (3, 3)
    assert second["snapshots_written"] == 0


def test_sync_job_github_failure_is_502(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        app.state.github_client_factory = lambda r: GitHubClient(
            "t",
            transport=httpx.MockTransport(lambda q: httpx.Response(401, text="bad")),
            max_retries=0,
        )
        assert c.post("/jobs/sync", headers=AUTH).status_code == 502


@pytest.fixture
def hook_client(settings):
    with TestClient(create_app(settings.model_copy(update={"github_webhook_secret": "whsec"}))) as c:
        yield c


def _sig(body: bytes, secret="whsec"):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_records_event_with_valid_signature(hook_client):
    body = json.dumps(
        {"action": "edited", "repository": {"full_name": "o/r"}, "comment": "secret text"}
    ).encode()
    r = hook_client.post(
        "/webhooks/github",
        content=body,
        headers={"x-hub-signature-256": _sig(body), "x-github-event": "issues"},
    )
    assert r.status_code == 202


def test_webhook_rejects_bad_signature(hook_client):
    body = b"{}"
    assert (
        hook_client.post(
            "/webhooks/github", content=body, headers={"x-hub-signature-256": _sig(body, "wrong")}
        ).status_code
        == 401
    )
    assert hook_client.post("/webhooks/github", content=body).status_code == 401


def test_webhook_without_secret_configured_is_503(client):
    assert client.post("/webhooks/github", content=b"{}").status_code == 503
