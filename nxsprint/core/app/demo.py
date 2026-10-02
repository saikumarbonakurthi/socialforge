"""`make demo`: seed a fake project and run it through the real sync path.

No GitHub, LLM or Teams needed. Nudges and messages arrive in Phase 2, so for now this
shows the board NxSprint would reason about, including how long each item sat in status.
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import AppConfig, load_config
from app.db import make_engine, make_session_factory
from app.domain.board import load_board
from app.domain.bot_inbound import handle_activity
from app.domain.calendar import is_working_day, working_days_between
from app.domain.nudges import run_nudges
from app.domain.standup import post_standup_summary, run_standup_prompts
from app.domain.sync import sync_project
from app.integrations.github import ProjectData, WorkItem
from app.models import Base, Member, Outbox, Project
from app.settings import Mode

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "projects.example.yaml"


def _item(n, title, status, who, est, sprint, labels=(), updated=None, now=None) -> WorkItem:
    return WorkItem(
        issue_node_id=f"I_demo{n}",
        title=title,
        url=f"https://github.com/sria-demo/demo-app/issues/{n}",
        status=status,
        assignee_login=who,
        estimate=est,
        sprint_name=sprint[0],
        sprint_start=sprint[1],
        sprint_days=14,
        labels=tuple(labels),
        updated_at=updated or now,
    )


def board(now: datetime, moved: bool) -> ProjectData:
    sprint = ("Sprint 12", (now - timedelta(days=9)).date())
    d = lambda n: now - timedelta(days=n)  # noqa: E731
    items = [
        _item(1, "Login page redesign", "In Progress", "asha-demo", 5, sprint, updated=d(4), now=now),
        _item(
            2,
            "Fix invoice rounding",
            "Done" if moved else "In Progress",
            "ravi-demo",
            3,
            sprint,
            updated=d(0 if moved else 3),
            now=now,
        ),
        _item(3, "Export to CSV", "Todo", None, 2, sprint, updated=d(6), now=now),
        _item(4, "Audit log API", "Todo", "ravi-demo", None, sprint, updated=d(2), now=now),
        _item(
            5,
            "Payment webhook retries",
            "In Progress",
            "ravi-demo",
            8,
            sprint,
            labels=["blocked"],
            updated=d(5),
            now=now,
        ),
        _item(6, "Update onboarding copy", "Done", "asha-demo", 1, sprint, updated=d(7), now=now),
    ]
    return ProjectData("PVT_demo", items)


def demo_clock(cfg) -> datetime:
    """Latest working day at 11:00 project time, so the demo always lands inside working hours."""
    tz = ZoneInfo(cfg.timezone)
    day = datetime.now(tz).replace(hour=11, minute=0, second=0, microsecond=0)
    while not is_working_day(day.date(), cfg):
        day -= timedelta(days=1)
    return day.astimezone(UTC)


def standup_demo(session, cfg, project, now) -> None:
    """Prompts at the start of the window, one simulated reply from Ravi, then the team summary."""
    day = now.astimezone(ZoneInfo(cfg.timezone)).date()
    at = lambda t: datetime.combine(day, t, tzinfo=ZoneInfo(cfg.timezone)).astimezone(UTC)  # noqa: E731
    prompt_at = at(cfg.standup_time) + timedelta(minutes=15)
    run = run_standup_prompts(session, project, cfg, Mode.DRY_RUN, prompt_at, use_bot=False)
    print(
        f"\nStandup at {prompt_at.astimezone(ZoneInfo(cfg.timezone)):%H:%M}: {run.prompted} prompts queued."
    )
    first = session.scalars(select(Outbox).where(Outbox.body.like("Hi Ravi, it is standup%"))).first()
    print(f"\nPrompt to Ravi:\n{first.body}\n")
    ravi = session.scalar(select(Member).where(Member.github_login == "ravi-demo"))
    activity = {
        "type": "message", "serviceUrl": "https://smba.example/", "conversation": {"id": "demo"},
        "from": {"id": "29:ravi", "aadObjectId": ravi.teams_user_id},
        "text": "Done: fixed invoice rounding. Doing: audit log API. Blocked: waiting on payment sandbox keys",
    }  # fmt: skip
    handle_activity(
        session, AppConfig(projects=[cfg], placeholder=True), activity, prompt_at + timedelta(minutes=5)
    )
    why = post_standup_summary(session, project, cfg, Mode.DRY_RUN, at(cfg.standup_summary_time))
    summary = session.scalars(select(Outbox).where(Outbox.channel == "teams_team")).first()
    print("Team summary (Asha did not reply):\n" + (summary.body if summary else f"not posted: {why}"))


def main() -> int:
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    cfg = load_config(cfg_path, Mode.DRY_RUN).projects[0]
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()

    now = demo_clock(cfg)
    # Two syncs, four days apart, so "days in status" has something to show.
    sync_project(session, cfg, board(now, moved=False), now - timedelta(days=4))
    second = sync_project(session, cfg, board(now, moved=True), now)
    print(f"Project: {cfg.name}   (dry run, nothing is sent anywhere)")
    print(f"Demo clock: {now.astimezone(ZoneInfo(cfg.timezone)):%a %d %b %H:%M} {cfg.timezone}")
    print(f"Sync 2 changed {second.snapshots_written} of {second.items_seen} items\n")

    print(f"{'ISSUE':<28}{'STATUS':<13}{'OWNER':<11}{'PTS':<5}{'IN STATUS':<11}LABELS")
    for item in load_board(session, session.scalar(select(Project)), cfg, now).items:
        days = working_days_between(item.status_since, now, cfg)
        print(
            f"{item.title[:26]:<28}{item.status:<13}{item.assignee_login or '(none)':<11}"
            f"{'' if item.estimate is None else item.estimate:<5}{f'{days} wd+':<11}{','.join(item.labels)}"
        )
    print("\n'N wd+' means at least N working days: we only know since our first sync.\n")

    project = session.scalar(select(Project))
    run = run_nudges(session, project, cfg, Mode.DRY_RUN, now)
    print(f"Rules found {run.findings} issues. Messages NxSprint would send:\n")
    members = {m.id: m for m in session.scalars(select(Member))}
    for n in run.created:
        who = members[n.member_id]
        print(f"[{n.rule}] to {who.name} ({who.role}) via {n.channel}\n  {n.message}\n")
    again = run_nudges(session, project, cfg, Mode.DRY_RUN, now)
    created, skipped = len(again.created), again.skipped_cooldown
    print(f"Running again straight away creates {created} (skipped {skipped} on cooldown).")

    standup_demo(session, cfg, project, now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
