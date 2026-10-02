from datetime import UTC, date, datetime, timedelta

from app.integrations.github import WorkItem

# Thursday 1 Oct 2026, 11:00 in Asia/Kolkata. Sprint 12 runs Mon 21 Sep to Mon 5 Oct.
NOW = datetime(2026, 10, 1, 5, 30, tzinfo=UTC)
SPRINT_START = date(2026, 9, 21)


def days_ago(n: int, now: datetime = NOW) -> datetime:
    return now - timedelta(days=n)


def item(**kw) -> WorkItem:
    base = dict(
        issue_node_id="I_1",
        title="Login page",
        url="https://github.com/sria-demo/demo-app/issues/1",
        status="Todo",
        assignee_login="ravi-demo",
        estimate=3.0,
        sprint_name="Sprint 12",
        sprint_start=SPRINT_START,
        sprint_days=14,
        labels=(),
        updated_at=NOW,
    )
    base.update(kw)
    return WorkItem(**base)
