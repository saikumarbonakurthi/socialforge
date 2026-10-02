"""Deterministic message templates.

Phase 3 will let Claude rephrase these. They remain the fallback when the LLM fails,
the budget is hit, or validation fails (rule 3). Written as SRIA ("we"), and with no dashes
of any kind in the template text (rule 7). Titles and names are inserted verbatim.
"""

from app.config import ProjectCfg
from app.domain.rules import Finding


def _link(f: Finding) -> str:
    return f" {f.url}" if f.url else ""


def _days(n: int) -> str:
    return f"{n} working day" + ("" if n == 1 else "s")


def render(f: Finding, first_name: str, cfg: ProjectCfg) -> str:
    e = f.evidence
    hi = f"Hi {first_name}, "
    if f.rule_id == "STALE_IN_PROGRESS":
        t = cfg.thresholds
        if e["working_days_since_update"] >= t.stale_no_activity_working_days:
            what = f"has had no update for {_days(e['working_days_since_update'])}"
        else:
            what = f"has stayed in {cfg.statuses.in_progress} for {_days(e['working_days_in_status'])}"
        return (
            f"{hi}we noticed {f.title} {what}. "
            f"Could you add a quick note on where it stands, or tell us what is in the way?{_link(f)}"
        )
    if f.rule_id == "UNASSIGNED_IN_SPRINT":
        why = (
            "nobody is assigned"
            if e["reason"] == "no_owner"
            else f"{e['previous_owner']} is no longer an active member of the project"
        )
        return f"{hi}{f.title} is in {e['sprint']} but {why}. Could you pick an owner for it?{_link(f)}"
    if f.rule_id == "NO_ESTIMATE":
        return (
            f"{hi}{f.title} is in {e['sprint']} without an estimate. "
            f"Could you add one so we can plan capacity properly?{_link(f)}"
        )
    if f.rule_id == "OVERLOADED_MEMBER":
        facts = []
        if "points" in e:
            facts.append(f"{e['points']:g} points against a capacity of {e['capacity_points']:g}")
        if "in_progress_count" in e:
            facts.append(
                f"{e['in_progress_count']} items in {cfg.statuses.in_progress} at once "
                f"(we aim for at most {e['max_parallel_in_progress']})"
            )
        return (
            f"{hi}in {e['sprint']} we count {' and '.join(facts)} for you. "
            "Could we look together at what to move or pause?"
        )
    if f.rule_id == "SPRINT_AT_RISK":
        parts = []
        if "points_left_pct" in e:
            parts.append(
                f"{e['points_left_pct']} percent of the points are still open with "
                f"{e['days_left_pct']} percent of the working days left"
            )
        if "no_done_working_days" in e:
            parts.append(f"no item has moved to {cfg.statuses.done} in {_days(e['no_done_working_days'])}")
        return f"{hi}{e['sprint']} looks at risk: {' and '.join(parts)}. Could we decide what to cut or move?"
    if f.rule_id == "PR_WAITING_REVIEW":
        if e["reason"] == "approved_not_merged":
            return (
                f"{hi}the pull request {f.title} was approved {_days(e['working_days'])} ago "
                f"and is not merged yet. Could you merge it or tell us what is holding it?{_link(f)}"
            )
        return (
            f"{hi}a review of the pull request {f.title} was requested {e['hours']} hours ago. "
            f"Could you take a look today?{_link(f)}"
        )
    if f.rule_id == "BLOCKED_LABEL_AGING":
        return (
            f"{hi}{f.title} has carried the {cfg.thresholds.blocked_label} label for "
            f"{_days(e['working_days_blocked'])}. What would unblock it, and who can help?{_link(f)}"
        )
    raise ValueError(f"no template for rule {f.rule_id}")
