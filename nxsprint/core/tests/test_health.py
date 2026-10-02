from fastapi import Depends
from fastapi.testclient import TestClient

from app.api.deps import require_auth
from app.main import create_app
from tests.conftest import SECRET


def test_health_ok_and_dry_run(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["mode"] == "dry_run" and body["db"] is True
    assert body["projects"] == ["Demo Project"]
    assert r.headers["x-request-id"]


def test_request_id_is_echoed(client):
    assert client.get("/health", headers={"x-request-id": "abc"}).headers["x-request-id"] == "abc"


def test_auth_dependency(settings):
    app = create_app(settings)

    @app.get("/secret", dependencies=[Depends(require_auth)])
    def secret():
        return {"ok": True}

    with TestClient(app) as c:
        assert c.get("/secret").status_code == 401
        assert c.get("/secret", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert c.get("/secret", headers={"Authorization": f"Bearer {SECRET}"}).status_code == 200


def test_app_refuses_to_start_with_missing_config(settings):
    import pytest

    from app.config import ConfigError

    bad = settings.model_copy(update={"config_path": "/nonexistent.yaml"})
    with pytest.raises(ConfigError), TestClient(create_app(bad)):
        pass
