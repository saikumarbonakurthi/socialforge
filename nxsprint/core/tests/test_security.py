"""Guards that keep the API closed by default."""

import re

from app.main import create_app

PUBLIC = {
    ("GET", "/health"),  # liveness probe, reveals nothing but the mode and project names
    ("POST", "/webhooks/github"),  # authenticated by the GitHub HMAC signature
    ("POST", "/webhooks/teams"),  # authenticated by Microsoft's signed token
}


def test_every_route_but_the_signed_ones_requires_the_bearer_secret(settings):
    app = create_app(settings)
    checked = []
    for path, methods in app.openapi()[
        "paths"
    ].items():  # the schema lists every route, nested routers included
        for method in (m.upper() for m in methods):
            if (method, path) not in PUBLIC:
                checked.append((method, path))
    assert len(checked) >= 25  # the sweep really covers the API, not an empty list

    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        for method, path in checked:
            url = re.sub(r"\{[^}]+\}", "1", path)
            r = c.request(method, url, json={})
            assert r.status_code == 401, f"{method} {path} answered {r.status_code} without credentials"
            wrong = c.request(method, url, json={}, headers={"Authorization": "Bearer not-the-secret-at-all"})
            assert wrong.status_code == 401, f"{method} {path} accepted a wrong secret"


def test_the_signed_webhooks_reject_unsigned_requests(settings):
    from fastapi.testclient import TestClient

    app = create_app(settings.model_copy(update={"github_webhook_secret": "whsec"}))
    with TestClient(app) as c:
        assert c.post("/webhooks/github", content=b"{}").status_code == 401
        assert c.post("/webhooks/teams", content=b"{}").status_code == 503  # bot not configured, never open
