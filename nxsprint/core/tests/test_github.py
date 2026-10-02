import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.config import FieldMappings
from app.integrations.github import GitHubClient, GitHubError, verify_signature

FIX = Path(__file__).parent / "fixtures"
FIELDS = FieldMappings(status="Status", estimate="Estimate", sprint="Sprint", priority="Priority")


def _client(handler, sleeps=None):
    return GitHubClient(
        "tok",
        transport=httpx.MockTransport(handler),
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
        max_retries=3,
    )


def _pages(request):
    after = json.loads(request.content)["variables"]["after"]
    name = "project_page1.json" if after is None else "project_page2.json"
    return httpx.Response(200, json=json.loads((FIX / name).read_text()))


def test_pagination_and_parsing():
    data = _client(_pages).fetch_project("o", 1, FIELDS)
    assert data.project_node_id == "PVT_1"
    # Item with null content is skipped; draft falls back to the item id.
    assert [i.issue_node_id for i in data.items] == ["I_1", "PVTI_2", "PR_3"]
    first = data.items[0]
    assert (first.status, first.estimate, first.assignee_login) == ("In Progress", 5.0, "asha-demo")
    assert (first.sprint_name, first.sprint_start, first.sprint_days) == (
        "Sprint 12",
        date(2026, 9, 21),
        14,
    )
    assert first.labels == ("blocked", "ui")
    assert first.updated_at.isoformat() == "2026-09-30T08:30:00+00:00"
    assert data.items[1].assignee_login is None and data.items[1].estimate is None


def test_backoff_on_429_then_success():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"retry-after": "7"})
        return _pages(request)

    _client(handler, sleeps).fetch_project("o", 1, FIELDS)
    assert sleeps[:2] == [7.0, 7.0]


def test_gives_up_after_max_retries():
    with pytest.raises(GitHubError, match="after retries"):
        _client(lambda r: httpx.Response(503)).fetch_project("o", 1, FIELDS)


def test_rate_limited_403_backs_off_but_plain_403_does_not():
    sleeps = []
    calls = []

    def limited(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0", "retry-after": "3"})
        return _pages(request)

    _client(limited, sleeps).fetch_project("o", 1, FIELDS)
    assert sleeps == [3.0]

    sleeps.clear()
    with pytest.raises(GitHubError, match="HTTP 403"):
        _client(lambda r: httpx.Response(403, text="no access"), sleeps).fetch_project("o", 1, FIELDS)
    assert sleeps == []


def test_graphql_rate_limited_error_retries():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(200, json={"errors": [{"type": "RATE_LIMITED"}]})
        return _pages(request)

    _client(handler, sleeps).fetch_project("o", 1, FIELDS)
    assert len(sleeps) == 1


def test_missing_project_and_graphql_error():
    def none(request):
        return httpx.Response(200, json={"data": {"organization": {"projectV2": None}}})

    with pytest.raises(GitHubError, match="not found"):
        _client(none).fetch_project("o", 1, FIELDS)

    def err(request):
        return httpx.Response(200, json={"errors": [{"type": "NOPE", "message": "x"}]})

    with pytest.raises(GitHubError, match="GraphQL"):
        _client(err).fetch_project("o", 1, FIELDS)


def test_signature():
    import hashlib
    import hmac

    body = b'{"a":1}'
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert verify_signature("s3cret", body, good)
    assert not verify_signature("s3cret", body, good[:-1] + "0")
    assert not verify_signature("s3cret", body, None)
    assert not verify_signature("s3cret", body, "md5=abc")
