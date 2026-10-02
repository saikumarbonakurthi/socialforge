from datetime import timedelta

import pytest

from app.config import load_config
from app.domain.rules import (
    BoardState,
    ItemView,
    PullRequestView,
    Severity,
    SprintView,
    blocked_label_aging,
    no_estimate,
    overloaded_member,
    pr_waiting_review,
    run_rules,
    sprint_at_risk,
    stale_in_progress,
    unassigned_in_sprint,
)
from app.settings import Mode
from tests.helpers import NOW, SPRINT_START, days_ago

SPRINT = SprintView("Sprint 12", SPRINT_START, SPRINT_START + timedelta(days=14))


@pytest.fixture
def cfg(config_file):
    return load_config(config_file, Mode.DRY_RUN).projects[0]


def view(**kw) -> ItemView:
    base = dict(
        issue_node_id="I_1", title="Login page", url="https://x/1", status="Todo",
        assignee_login="ravi-demo", estimate=3.0, sprint_name="Sprint 12", labels=(),
        updated_at=NOW, status_since=NOW, blocked_since=None,
    )  # fmt: skip
    base.update(kw)
    return ItemView(**base)


def state(cfg, items, sprint=SPRINT, prs=()):
    return BoardState(cfg=cfg, now=NOW, items=list(items), sprint=sprint, pull_requests=list(prs))


# STALE_IN_PROGRESS (thresholds in the example config: 2 days quiet, 3 days in status)
def test_stale_by_no_activity(cfg):
    f = stale_in_progress(state(cfg, [view(status="In Progress", updated_at=days_ago(3))]))
    assert [(x.rule_id, x.member, x.severity) for x in f] == [
        ("STALE_IN_PROGRESS", "ravi-demo", Severity.MEDIUM)
    ]
    assert f[0].evidence["working_days_since_update"] == 3


def test_stale_by_status_age_only(cfg):
    f = stale_in_progress(state(cfg, [view(status="In Progress", status_since=days_ago(3))]))
    assert len(f) == 1 and f[0].evidence["working_days_since_update"] == 0


def test_stale_high_severity_when_twice_threshold(cfg):
    f = stale_in_progress(state(cfg, [view(status="In Progress", updated_at=days_ago(6))]))
    assert f[0].severity is Severity.HIGH


def test_stale_ignores_fresh_other_status_and_blocked(cfg):
    items = [
        view(issue_node_id="a", status="In Progress", updated_at=days_ago(1)),
        view(issue_node_id="b", status="Todo", updated_at=days_ago(9)),
        view(issue_node_id="c", status="In Progress", updated_at=days_ago(9), blocked_since=days_ago(5)),
    ]
    assert stale_in_progress(state(cfg, items)) == []


def test_stale_does_not_count_weekend(cfg):
    # Updated Sat 26 Sep, now Thu 1 Oct: working days are Mon, Tue, Wed, Thu = 4, not 5.
    f = stale_in_progress(state(cfg, [view(status="In Progress", updated_at=days_ago(5))]))
    assert f[0].evidence["working_days_since_update"] == 4


# UNASSIGNED_IN_SPRINT
def test_unassigned_no_owner_and_departed_owner(cfg):
    items = [
        view(issue_node_id="a", assignee_login=None),
        view(issue_node_id="b", assignee_login="gone-person"),
        view(issue_node_id="c"),
        view(issue_node_id="d", assignee_login=None, status="Done"),
        view(issue_node_id="e", assignee_login=None, sprint_name="Sprint 11"),
    ]
    f = unassigned_in_sprint(state(cfg, items))
    assert [(x.issue_node_id, x.evidence["reason"]) for x in f] == [
        ("a", "no_owner"),
        ("b", "owner_not_active_member"),
    ]
    assert all(x.member is None for x in f)


def test_sprint_rules_need_an_active_sprint(cfg):
    s = state(cfg, [view(assignee_login=None, estimate=None)], sprint=None)
    assert unassigned_in_sprint(s) == [] and no_estimate(s) == [] and overloaded_member(s) == []
    assert sprint_at_risk(s) == []


# NO_ESTIMATE
def test_no_estimate_only_in_active_sprint_and_not_done(cfg):
    items = [
        view(issue_node_id="a", estimate=None),
        view(issue_node_id="b", estimate=None, status="Done"),
        view(issue_node_id="c", estimate=None, sprint_name=None),
        view(issue_node_id="d", estimate=0.0),
    ]
    f = no_estimate(state(cfg, items))
    assert [(x.issue_node_id, x.member) for x in f] == [("a", "ravi-demo")]


# OVERLOADED_MEMBER (capacity 20, max 3 parallel)
def test_overloaded_by_points(cfg):
    items = [view(issue_node_id="a", estimate=34.0)]
    f = overloaded_member(state(cfg, items))
    assert f[0].member == "ravi-demo" and f[0].issue_node_id == "member:ravi-demo"
    assert f[0].evidence["points"] == 34.0 and "in_progress_count" not in f[0].evidence


def test_overloaded_by_parallel_items(cfg):
    items = [view(issue_node_id=str(n), status="In Progress", estimate=1.0) for n in range(4)]
    f = overloaded_member(state(cfg, items))
    assert f[0].evidence["in_progress_count"] == 4 and "points" not in f[0].evidence


def test_not_overloaded_at_exact_capacity_and_done_work_excluded(cfg):
    items = [
        view(issue_node_id="a", estimate=20.0),
        view(issue_node_id="b", estimate=50.0, status="Done"),
    ]
    assert overloaded_member(state(cfg, items)) == []


def test_inactive_member_is_never_flagged(cfg):
    cfg.members[1].active = False
    assert overloaded_member(state(cfg, [view(estimate=99.0)])) == []


# SPRINT_AT_RISK (gap 25 points, 3 working days without a Done)
def _sprint_items(done_pts, open_pts, done_since):
    return [
        view(issue_node_id="done", status="Done", estimate=done_pts, status_since=done_since),
        view(issue_node_id="open", status="In Progress", estimate=open_pts),
    ]


def test_at_risk_by_points_vs_days(cfg):
    # Thu 1 Oct: 2 of the sprint's 10 working days remain (Thu, Fri), so 20 percent.
    f = sprint_at_risk(state(cfg, _sprint_items(2, 8, days_ago(1))))
    assert len(f) == 1 and f[0].severity is Severity.HIGH and f[0].member is None
    e = f[0].evidence
    assert (e["points_left_pct"], e["days_left_pct"]) == (80, 20)
    assert "no_done_working_days" not in e


def test_at_risk_when_nothing_done_for_a_while(cfg):
    f = sprint_at_risk(state(cfg, _sprint_items(8, 2, days_ago(5))))
    assert f[0].evidence["no_done_working_days"] == 3 and "points_left_pct" not in f[0].evidence


def test_not_at_risk_when_on_track(cfg):
    assert sprint_at_risk(state(cfg, _sprint_items(8, 2, days_ago(1)))) == []


def test_no_done_window_not_applied_before_sprint_has_run_that_long(cfg):
    early = SprintView("Sprint 12", NOW.date() - timedelta(days=1), NOW.date() + timedelta(days=13))
    items = [view(status="In Progress", estimate=5.0)]
    assert sprint_at_risk(state(cfg, items, sprint=early)) == []


def test_not_at_risk_when_everything_done_or_nothing_estimated(cfg):
    assert sprint_at_risk(state(cfg, [view(status="Done", estimate=5.0)])) == []
    assert sprint_at_risk(state(cfg, [view(estimate=None)])) == []


# PR_WAITING_REVIEW (24h review, 2 working days approved)
def pr(**kw):
    base = dict(
        node_id="PR_1", title="Fix", url="https://x/pr/1", author_login="asha-demo",
        reviewer_login="ravi-demo", review_requested_at=None, approved_at=None, merged=False,
    )  # fmt: skip
    base.update(kw)
    return PullRequestView(**base)


def test_pr_review_waiting_goes_to_reviewer(cfg):
    f = pr_waiting_review(state(cfg, [], prs=[pr(review_requested_at=NOW - timedelta(hours=30))]))
    assert (f[0].member, f[0].evidence) == ("ravi-demo", {"reason": "review_not_started", "hours": 30})


def test_pr_approved_not_merged_goes_to_author(cfg):
    f = pr_waiting_review(state(cfg, [], prs=[pr(approved_at=days_ago(3))]))
    assert f[0].member == "asha-demo" and f[0].evidence["reason"] == "approved_not_merged"


def test_pr_fresh_merged_or_unrequested_are_ignored(cfg):
    prs = [
        pr(node_id="a", review_requested_at=NOW - timedelta(hours=5)),
        pr(node_id="b", approved_at=days_ago(9), merged=True),
        pr(node_id="c"),
        pr(node_id="d", approved_at=days_ago(1)),
    ]
    assert pr_waiting_review(state(cfg, [], prs=prs)) == []


# BLOCKED_LABEL_AGING (2 working days)
def test_blocked_label_aging(cfg):
    items = [
        view(issue_node_id="a", blocked_since=days_ago(3)),
        view(issue_node_id="b", blocked_since=days_ago(1)),
        view(issue_node_id="c", blocked_since=days_ago(9), status="Done"),
        view(issue_node_id="d", blocked_since=days_ago(8)),
    ]
    f = blocked_label_aging(state(cfg, items))
    assert [(x.issue_node_id, x.severity) for x in f] == [("a", Severity.MEDIUM), ("d", Severity.HIGH)]


def test_run_rules_collects_all(cfg):
    items = [view(status="In Progress", updated_at=days_ago(3), assignee_login=None, estimate=None)]
    rules = {f.rule_id for f in run_rules(state(cfg, items))}
    assert {"STALE_IN_PROGRESS", "UNASSIGNED_IN_SPRINT", "NO_ESTIMATE"} <= rules
