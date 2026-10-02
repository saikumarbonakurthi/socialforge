"""Static checks on the exported workflows. They cannot prove n8n imports them, only that they are
well formed, authenticated, free of secrets, and call endpoints that really exist."""

import json
import re
from pathlib import Path

import pytest

WF_DIR = Path(__file__).resolve().parents[2] / "n8n" / "workflows"
FILES = sorted(WF_DIR.glob("*.json"))
EXPECTED_SCHEDULE = {
    "cron-sync": ("minutes", 15),
    "cron-nudges": ("hours", 1),
    "deliver-outbox": ("minutes", 1),
    "deliver-bot": ("minutes", 1),
    "cron-standup": ("cronExpression", "*/15 * * * 1-5"),
    "cron-weekly-report": ("cronExpression", "*/30 * * * 1-5"),
    "cron-ceremonies": ("cronExpression", "*/30 * * * 1-5"),
    "escalation-whatsapp": ("minutes", 1),
}


def load(path):
    return json.loads(path.read_text())


def test_expected_workflows_exist():
    assert {p.stem for p in FILES} == set(EXPECTED_SCHEDULE)


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_structure(path):
    wf = load(path)
    names = [n["name"] for n in wf["nodes"]]
    assert len(names) == len(set(names)) and len({n["id"] for n in wf["nodes"]}) == len(names)
    assert wf["active"] is False  # imported switched off, a human turns them on
    for src, outs in wf["connections"].items():
        assert src in names
        for branch in outs["main"]:
            assert all(link["node"] in names for link in branch)
    triggers = [n for n in wf["nodes"] if n["type"].endswith("scheduleTrigger")]
    assert len(triggers) == 1
    rule = triggers[0]["parameters"]["rule"]["interval"][0]
    if rule["field"] == "cronExpression":
        assert (rule["field"], rule["expression"]) == EXPECTED_SCHEDULE[path.stem]
    else:
        assert (rule["field"], rule[f"{rule['field']}Interval"]) == EXPECTED_SCHEDULE[path.stem]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_no_secrets_or_hardcoded_hosts(path):
    text = path.read_text()
    assert "sig=" not in text and "logic.azure.com" not in text and "powerplatform" not in text
    assert not re.search(r"https?://", text)  # every URL comes from env or from core's response
    assert not re.search(r"Bearer [A-Za-z0-9]", text)  # token only via $env


def route_of(url: str) -> str:
    """Turn an n8n URL expression into a route template: literals stay, any other term becomes {}."""
    body = url.removeprefix("=").strip()
    body = re.sub(r"^\{\{\s*\$env\.NXSPRINT_CORE_URL\s*\}\}", "", body)  # '{{ core }}/jobs/sync'
    if body.startswith("{{"):  # '{{ core + '/outbox/' + id + '/sent' }}'
        terms = [t.strip() for t in body.strip("{} ").split(" + ")]
        body = "".join(t.strip("'") if t.startswith("'") else "{}" for t in terms[1:])
    return body.split("?")[0]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_calls_to_core_are_authenticated_and_hit_real_routes(path, client):
    paths = {re.sub(r"\{[^}]*\}", "{}", p) for p in client.get("/openapi.json").json()["paths"]}
    found = 0
    for n in load(path)["nodes"]:
        if not n["type"].endswith("httpRequest") or "NXSPRINT_CORE_URL" not in n["parameters"]["url"]:
            continue  # the Teams post is checked in the wiring test
        headers = {h["name"]: h["value"] for h in n["parameters"]["headerParameters"]["parameters"]}
        assert "$env.NXSPRINT_API_SECRET" in headers["Authorization"]
        assert route_of(n["parameters"]["url"]) in paths, (path.stem, n["parameters"]["url"])
        found += 1
    assert found >= 1


def test_delivery_workflow_wiring():
    wf = load(WF_DIR / "deliver-outbox.json")
    nodes = {n["name"]: n for n in wf["nodes"]}
    post = nodes["Post to Teams"]
    assert post["parameters"]["url"] == "={{ $json.webhook_url }}"  # from core, never stored here
    assert "headerParameters" not in post["parameters"]  # the webhook URL itself is the credential
    assert post["onError"] == "continueErrorOutput"
    success, failure = wf["connections"]["Post to Teams"]["main"]
    assert [x["node"] for x in success] == ["Mark sent"] and [x["node"] for x in failure] == ["Mark failed"]
    assert nodes["Split items"]["parameters"]["fieldToSplitOut"] == "items"
