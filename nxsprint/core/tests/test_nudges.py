import re
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import load_config
from app.db import make_engine
from app.domain.messages import render
from app.domain.nudges import recipient, run_nudges
from app.domain.rules import Finding, Severity
from app.domain.sync import sync_project
from app.integrations.github import ProjectData
from app.models import Base, Member, Nudge, Outbox, Project
from app.settings import Mode
from tests.helpers import NOW, days_ago, item


@pytest.fixture
def cfg(config_file):
    return load_config(config_file, Mode.DRY_RUN).projects[0]


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def seed(session, cfg, items, at=None):
    sync_project(session, cfg, ProjectData("PVT", items), at or NOW - timedelta(days=6))
    return session.scalar(select(Project))


def messy_board():
    return [
        item(issue_node_id="I_stale", title="Login page", status="In Progress", updated_at=days_ago(4)),
        item(issue_node_id="I_free", title="Export CSV", assignee_login=None),
        item(issue_node_id="I_noest", title="Audit log", estimate=None),
        item(issue_node_id="I_done", title="Copy", status="Done", assignee_login="asha-demo", estimate=7.0),
    ]


def test_creates_nudges_and_outbox_rows_in_dry_run(session, cfg):
    project = seed(session, cfg, messy_board())
    run = run_nudges(session, project, cfg, Mode.DRY_RUN, NOW)
    assert {n.rule for n in run.created} >= {"STALE_IN_PROGRESS", "UNASSIGNED_IN_SPRINT", "NO_ESTIMATE"}
    nudges = session.scalars(select(Nudge)).all()
    outbox = session.scalars(select(Outbox)).all()
    assert len(nudges) == len(outbox) == len(run.created)
    assert {o.mode for o in outbox} == {"dry_run"} and {n.status for n in nudges} == {"queued"}
    assert {o.channel for o in outbox} == {"teams_dm"}


def test_routing_assignee_or_lead(session, cfg):
    project = seed(session, cfg, messy_board())
    run_nudges(session, project, cfg, Mode.DRY_RUN, NOW)
    members = {m.id: m.github_login for m in session.scalars(select(Member))}
    to = {n.rule: members[n.member_id] for n in session.scalars(select(Nudge))}
    assert to["STALE_IN_PROGRESS"] == "ravi-demo"  # the assignee
    assert to["UNASSIGNED_IN_SPRINT"] == "asha-demo"  # no owner, so the lead
    targets = {o.target for o in session.scalars(select(Outbox))}
    assert targets <= {"demo-asha", "demo-ravi"}


def test_cooldown_blocks_repeat_then_allows_after_window(session, cfg):
    project = seed(session, cfg, messy_board())
    first = run_nudges(session, project, cfg, Mode.DRY_RUN, NOW)
    again = run_nudges(session, project, cfg, Mode.DRY_RUN, NOW + timedelta(hours=1))
    assert len(again.created) == 0 and again.skipped_cooldown == len(first.created)
    # 24h cooldown elapsed (next day, same hour, still a working day).
    later = run_nudges(session, project, cfg, Mode.DRY_RUN, NOW + timedelta(hours=25))
    assert len(later.created) > 0


def test_same_issue_different_rules_are_independent(session, cfg):
    project = seed(session, cfg, [item(assignee_login=None, estimate=None)])
    run = run_nudges(session, project, cfg, Mode.DRY_RUN, NOW)
    assert {n.rule for n in run.created} == {"UNASSIGNED_IN_SPRINT", "NO_ESTIMATE"}


@pytest.mark.parametrize(
    "when",
    [
        NOW + timedelta(hours=9),  # 20:30 IST, quiet hours
        NOW - timedelta(hours=3),  # 08:30 IST, before working hours
        NOW + timedelta(days=2),  # Saturday
    ],
)
def test_outside_hours_creates_nothing_and_is_retried_later(session, cfg, when):
    project = seed(session, cfg, messy_board())
    run = run_nudges(session, project, cfg, Mode.DRY_RUN, when)
    assert run.created == [] and run.deferred_outside_hours > 0
    assert session.scalars(select(Nudge)).all() == []
    assert len(run_nudges(session, project, cfg, Mode.DRY_RUN, NOW).created) > 0


def test_member_in_other_timezone_is_judged_in_their_own_clock(session, cfg):
    cfg.members[1].timezone = "America/New_York"  # Ravi: 01:30 local at NOW
    project = seed(session, cfg, [item(estimate=None)])
    run = run_nudges(session, project, cfg, Mode.DRY_RUN, NOW)
    assert run.created == [] and run.deferred_outside_hours == 1


def test_holiday_defers(session, cfg):
    cfg.holidays = [NOW.date()]
    project = seed(session, cfg, [item(estimate=None)])
    assert run_nudges(session, project, cfg, Mode.DRY_RUN, NOW).deferred_outside_hours == 1


def test_inactive_lead_means_no_recipient(session, cfg):
    project = seed(session, cfg, [item(assignee_login=None)])
    cfg.members[0].active = False  # the lead
    run = run_nudges(session, project, cfg, Mode.DRY_RUN, NOW)
    assert run.created == [] and run.skipped_no_recipient == run.findings > 0


def test_departed_assignee_falls_back_to_lead(session, cfg):
    f = Finding("NO_ESTIMATE", Severity.LOW, "gone-person", "I_1", "t", None, {"sprint": "S"})
    assert recipient(f, cfg).github_login == "asha-demo"
    inactive = cfg.model_copy(deep=True)
    inactive.members[1].active = False
    f2 = Finding("NO_ESTIMATE", Severity.LOW, "ravi-demo", "I_1", "t", None, {"sprint": "S"})
    assert recipient(f2, inactive).github_login == "asha-demo"


def test_removed_items_are_not_nudged(session, cfg):
    project = seed(session, cfg, [item(estimate=None)])
    sync_project(session, cfg, ProjectData("PVT", []), NOW - timedelta(days=1))
    assert run_nudges(session, project, cfg, Mode.DRY_RUN, NOW).findings == 0


def test_blocked_item_gets_blocked_nudge_not_stale_nudge(session, cfg):
    board = [item(status="In Progress", updated_at=days_ago(5), labels=("blocked",))]
    project = seed(session, cfg, board)
    rules = {n.rule for n in run_nudges(session, project, cfg, Mode.DRY_RUN, NOW).created}
    assert rules - {"SPRINT_AT_RISK"} == {"BLOCKED_LABEL_AGING"}  # the sprint is also behind here


def test_status_age_is_tracked_across_syncs(session, cfg):
    first = item(status="In Progress", updated_at=days_ago(0))
    project = seed(session, cfg, [first], at=days_ago(6))
    sync_project(session, cfg, ProjectData("PVT", [first]), days_ago(3))  # unchanged, no new row
    run = run_nudges(session, project, cfg, Mode.DRY_RUN, NOW)
    rules = [n.rule for n in run.created if n.rule != "SPRINT_AT_RISK"]
    assert rules == ["STALE_IN_PROGRESS"]  # in status 3+ working days


# Messages: grounded, no dashes, link included
DASHES = re.compile(r"[-‐-―−]")


def _findings():
    link = "https://github.com/sria-demo/demo-app/issues/1"
    return [
        Finding("STALE_IN_PROGRESS", Severity.MEDIUM, "ravi-demo", "I_1", "Login page", link,
                {"working_days_since_update": 3, "working_days_in_status": 4}),
        Finding("STALE_IN_PROGRESS", Severity.MEDIUM, "ravi-demo", "I_1", "Login page", link,
                {"working_days_since_update": 1, "working_days_in_status": 1}),
        Finding("UNASSIGNED_IN_SPRINT", Severity.MEDIUM, None, "I_1", "Export CSV", link,
                {"reason": "no_owner", "sprint": "Sprint 12", "previous_owner": None}),
        Finding("UNASSIGNED_IN_SPRINT", Severity.MEDIUM, None, "I_1", "Export CSV", link,
                {"reason": "owner_not_active_member", "sprint": "Sprint 12", "previous_owner": "gone"}),
        Finding("NO_ESTIMATE", Severity.LOW, "ravi-demo", "I_1", "Audit log", link, {"sprint": "Sprint 12"}),
        Finding("OVERLOADED_MEMBER", Severity.MEDIUM, "ravi-demo", "member:ravi-demo", "w", None,
                {"sprint": "Sprint 12", "points": 34.0, "capacity_points": 20.0,
                 "in_progress_count": 4, "max_parallel_in_progress": 3}),
        Finding("SPRINT_AT_RISK", Severity.HIGH, None, "sprint:S", "Sprint 12", None,
                {"sprint": "Sprint 12", "points_left_pct": 60, "days_left_pct": 30,
                 "no_done_working_days": 1}),
        Finding("PR_WAITING_REVIEW", Severity.MEDIUM, "ravi-demo", "PR_1", "Fix rounding", link,
                {"reason": "review_not_started", "hours": 30}),
        Finding("PR_WAITING_REVIEW", Severity.MEDIUM, "ravi-demo", "PR_1", "Fix rounding", link,
                {"reason": "approved_not_merged", "working_days": 2}),
        Finding("BLOCKED_LABEL_AGING", Severity.MEDIUM, "ravi-demo", "I_1", "Payments", link,
                {"working_days_blocked": 1}),
    ]  # fmt: skip


@pytest.mark.parametrize("finding", _findings(), ids=lambda f: f"{f.rule_id}:{sorted(f.evidence)[0]}")
def test_templates_follow_house_rules(cfg, finding):
    text = render(finding, "Ravi", cfg)
    assert not DASHES.search(text.replace(finding.url or "", "")), text  # rule 7, links excluded
    assert text.startswith("Hi Ravi, ") and text.endswith(("?", finding.url or "?"))
    if finding.url:
        assert finding.url in text
    assert "1 working days" not in text
    # Grounded (rule 3): every number in the text comes from the evidence we passed in.
    values = " ".join(f"{v:g}" if isinstance(v, float) else str(v) for v in finding.evidence.values())
    allowed = set(re.findall(r"\d+", values))
    for num in re.findall(r"\b\d+\b", text.replace(finding.url or "", "")):
        assert num in allowed, f"{num} not grounded in {finding.evidence}"


def test_unknown_rule_has_no_template(cfg):
    with pytest.raises(ValueError):
        render(Finding("NOPE", Severity.LOW, None, "x", "t", None, {}), "A", cfg)


# Engine with the LLM phraser
def test_engine_uses_phraser_and_counts_outcomes(session, cfg):
    from app.llm.phraser import Phraser
    from tests.test_phraser import FakeLLM, result

    project = seed(session, cfg, [item(status="In Progress", updated_at=days_ago(4), labels=())])
    good = (
        "Hi Ravi, we noticed Login page has had no update for 4 working days. "
        "Could you share a quick note? {link}"
    )
    llm = FakeLLM(result(good), TimeoutError("down"), TimeoutError("down"))
    phr = Phraser(llm, "m-test", 5.0, 3.0, 15.0)
    run = run_nudges(session, project, cfg, Mode.DRY_RUN, NOW, phraser=phr)
    by_rule = {n.rule: n.message for n in run.created}
    assert "share a quick note" in by_rule["STALE_IN_PROGRESS"]
    assert "github.com" in by_rule["STALE_IN_PROGRESS"] and "{link}" not in by_rule["STALE_IN_PROGRESS"]
    assert run.phrasing["llm"] == 1 and run.phrasing["template:api_error"] >= 1
    outbox = {o.body for o in session.scalars(select(Outbox))}
    assert by_rule["STALE_IN_PROGRESS"] in outbox  # outbox carries exactly the phrased text
