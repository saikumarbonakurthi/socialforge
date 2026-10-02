"""Deterministic detection rules (section 7). Pure: BoardState in, findings out.

Rules decide WHO and WHY. They never phrase anything and never touch the DB or the network.
Each finding carries evidence built only from stored data, so wording can stay grounded.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.config import ProjectCfg
from app.domain.calendar import working_days_between, working_days_in_range


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ItemView:
    issue_node_id: str
    title: str
    url: str | None
    status: str | None
    assignee_login: str | None
    estimate: float | None
    sprint_name: str | None
    labels: tuple[str, ...]
    updated_at: datetime
    status_since: datetime  # lower bound: first time we saw the current status
    blocked_since: datetime | None  # first time we saw the blocked label, if present now
    priority: str | None = None


@dataclass(frozen=True)
class SprintView:
    name: str
    start: date
    end: date


@dataclass(frozen=True)
class PullRequestView:
    node_id: str
    title: str
    url: str | None
    author_login: str
    reviewer_login: str | None
    review_requested_at: datetime | None
    approved_at: datetime | None
    merged: bool


@dataclass
class BoardState:
    cfg: ProjectCfg
    now: datetime
    items: list[ItemView]
    sprint: SprintView | None
    # Not populated yet: the sync does not fetch PR review data (see PR_WAITING_REVIEW).
    pull_requests: list[PullRequestView] = field(default_factory=list)


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    member: str | None  # github login of the person it concerns, None means "route to the lead"
    issue_node_id: str  # real node id, or member:<login> / sprint:<name> for aggregate findings
    title: str
    url: str | None
    evidence: dict


def _active(state: BoardState) -> list[ItemView]:
    """Items in the active sprint that are not done."""
    if state.sprint is None:
        return []
    done = state.cfg.statuses.done
    return [i for i in state.items if i.sprint_name == state.sprint.name and i.status != done]


def stale_in_progress(state: BoardState) -> list[Finding]:
    t, out = state.cfg.thresholds, []
    for i in state.items:
        if i.status != state.cfg.statuses.in_progress:
            continue
        if i.blocked_since is not None:
            continue  # already known to be stuck, BLOCKED_LABEL_AGING covers it
        quiet = working_days_between(i.updated_at, state.now, state.cfg)
        in_status = working_days_between(i.status_since, state.now, state.cfg)
        if quiet < t.stale_no_activity_working_days and in_status < t.stale_status_unchanged_working_days:
            continue
        worse = (
            quiet >= 2 * t.stale_no_activity_working_days
            or in_status >= 2 * t.stale_status_unchanged_working_days
        )
        out.append(
            Finding(
                "STALE_IN_PROGRESS",
                Severity.HIGH if worse else Severity.MEDIUM,
                i.assignee_login,
                i.issue_node_id,
                i.title,
                i.url,
                {"working_days_since_update": quiet, "working_days_in_status": in_status},
            )
        )
    return out


def unassigned_in_sprint(state: BoardState) -> list[Finding]:
    active_logins = {m.github_login for m in state.cfg.members if m.active}
    out = []
    for i in _active(state):
        if i.assignee_login is None:
            reason = "no_owner"
        elif i.assignee_login not in active_logins:
            reason = "owner_not_active_member"
        else:
            continue
        out.append(
            Finding(
                "UNASSIGNED_IN_SPRINT",
                Severity.MEDIUM,
                None,
                i.issue_node_id,
                i.title,
                i.url,
                {"reason": reason, "sprint": state.sprint.name, "previous_owner": i.assignee_login},
            )
        )
    return out


def no_estimate(state: BoardState) -> list[Finding]:
    return [
        Finding(
            "NO_ESTIMATE",
            Severity.LOW,
            i.assignee_login,
            i.issue_node_id,
            i.title,
            i.url,
            {"sprint": state.sprint.name},
        )
        for i in _active(state)
        if i.estimate is None
    ]


def overloaded_member(state: BoardState) -> list[Finding]:
    cfg, out = state.cfg, []
    active = _active(state)
    for m in cfg.members:
        if not m.active:
            continue
        mine = [i for i in active if i.assignee_login == m.github_login]
        points = sum(i.estimate or 0 for i in mine)
        parallel = sum(1 for i in mine if i.status == cfg.statuses.in_progress)
        reasons = {}
        if points > m.capacity_points:
            reasons["points"] = points
            reasons["capacity_points"] = m.capacity_points
        if parallel > cfg.thresholds.max_parallel_in_progress:
            reasons["in_progress_count"] = parallel
            reasons["max_parallel_in_progress"] = cfg.thresholds.max_parallel_in_progress
        if reasons:
            reasons["sprint"] = state.sprint.name
            out.append(
                Finding(
                    "OVERLOADED_MEMBER",
                    Severity.MEDIUM,
                    m.github_login,
                    f"member:{m.github_login}",
                    f"workload of {m.name}",
                    None,
                    reasons,
                )
            )
    return out


def sprint_at_risk(state: BoardState) -> list[Finding]:
    sp, cfg = state.sprint, state.cfg
    if sp is None:
        return []
    t, today = cfg.thresholds, state.now.astimezone(ZoneInfo(cfg.timezone)).date()
    in_sprint = [i for i in state.items if i.sprint_name == sp.name]
    total = sum(i.estimate or 0 for i in in_sprint)
    left = sum(i.estimate or 0 for i in in_sprint if i.status != cfg.statuses.done)
    days_total = working_days_in_range(sp.start, sp.end, cfg)
    if total <= 0 or days_total <= 0 or left <= 0:
        return []
    days_left = working_days_in_range(max(today, sp.start), sp.end, cfg)
    points_left_pct = round(100 * left / total)
    days_left_pct = round(100 * days_left / days_total)

    reasons: dict = {}
    if points_left_pct - days_left_pct >= t.sprint_risk_gap_pct:
        reasons["points_left_pct"], reasons["days_left_pct"] = points_left_pct, days_left_pct
    elapsed = days_total - days_left
    done = [i for i in in_sprint if i.status == cfg.statuses.done]
    last_done = min((working_days_between(i.status_since, state.now, cfg) for i in done), default=None)
    # Only meaningful once the sprint has run for at least the window.
    if elapsed >= t.sprint_risk_no_done_working_days and (
        last_done is None or last_done >= t.sprint_risk_no_done_working_days
    ):
        reasons["no_done_working_days"] = t.sprint_risk_no_done_working_days
    if not reasons:
        return []
    reasons["sprint"] = sp.name
    reasons["points_left"], reasons["points_total"] = left, total
    return [
        Finding(
            "SPRINT_AT_RISK",
            Severity.HIGH,
            None,
            f"sprint:{sp.name}",
            sp.name,
            None,
            reasons,
        )
    ]


def pr_waiting_review(state: BoardState) -> list[Finding]:
    t, out = state.cfg.thresholds, []
    for pr in state.pull_requests:
        if pr.merged:
            continue
        if pr.approved_at is not None:
            days = working_days_between(pr.approved_at, state.now, state.cfg)
            if days >= t.pr_approved_unmerged_working_days:
                out.append(
                    Finding(
                        "PR_WAITING_REVIEW",
                        Severity.MEDIUM,
                        pr.author_login,
                        pr.node_id,
                        pr.title,
                        pr.url,
                        {"reason": "approved_not_merged", "working_days": days},
                    )
                )
        elif pr.review_requested_at is not None:
            hours = int((state.now - pr.review_requested_at).total_seconds() // 3600)
            if hours >= t.pr_review_wait_hours:
                out.append(
                    Finding(
                        "PR_WAITING_REVIEW",
                        Severity.MEDIUM,
                        pr.reviewer_login,
                        pr.node_id,
                        pr.title,
                        pr.url,
                        {"reason": "review_not_started", "hours": hours},
                    )
                )
    return out


def blocked_label_aging(state: BoardState) -> list[Finding]:
    t, out = state.cfg.thresholds, []
    for i in state.items:
        if i.status == state.cfg.statuses.done or i.blocked_since is None:
            continue
        days = working_days_between(i.blocked_since, state.now, state.cfg)
        if days >= t.blocked_label_working_days:
            out.append(
                Finding(
                    "BLOCKED_LABEL_AGING",
                    Severity.HIGH if days >= 2 * t.blocked_label_working_days else Severity.MEDIUM,
                    i.assignee_login,
                    i.issue_node_id,
                    i.title,
                    i.url,
                    {"working_days_blocked": days},
                )
            )
    return out


ALL_RULES: tuple[Callable[[BoardState], list[Finding]], ...] = (
    stale_in_progress,
    unassigned_in_sprint,
    no_estimate,
    overloaded_member,
    sprint_at_risk,
    pr_waiting_review,
    blocked_label_aging,
)


def run_rules(state: BoardState) -> list[Finding]:
    return [f for rule in ALL_RULES for f in rule(state)]
