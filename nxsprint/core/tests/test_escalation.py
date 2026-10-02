import json
from dataclasses import replace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AppConfig, ConfigError, load_config
from app.db import make_engine
from app.domain.board import load_board
from app.domain.bot_inbound import handle_activity
from app.domain.calendar import working_hours_between
from app.domain.escalation import check_whatsapp_ready, run_escalations
from app.domain.nudges import run_nudges
from app.domain.rules import goal_item_not_started, production_blocker_unowned
from app.domain.sync import sync_project
from app.integrations.github import ProjectData
from app.models import Base, Escalation, Nudge, Outbox, Project
from app.settings import Mode
from tests.bot_helpers import activity
from tests.helpers import NOW, SPRINT_START, days_ago, ist, item

LEAD_NUMBER = "+919876543210"
THU_0945 = ist(10, 1, 9, 45)  # nudges are created here, the ladder is then measured from this moment


@pytest.fixture
def cfg(config_file):
    c = load_config(config_file, Mode.DRY_RUN).projects[0]
    c.members[0].whatsapp_number = LEAD_NUMBER  # Asha is the lead
    return c


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def blocker(n="PB", title="Checkout outage", **kw):
    """A production blocker with no owner, outside any sprint so no other rule fires for it."""
    base = dict(issue_node_id=n, title=title, assignee_login=None, labels=("production",), estimate=3.0,
                sprint_name=None, sprint_start=None, sprint_days=None)  # fmt: skip
    base.update(kw)
    return item(**base)


def start(session, cfg, items, synced=None, nudged=THU_0945):
    synced = synced or ist(9, 30, 9, 30)
    sync_project(session, cfg, ProjectData("PVT", items), synced)
    project = session.scalar(select(Project))
    run_nudges(session, project, cfg, Mode.DRY_RUN, nudged)
    return project


def esc(session, project, cfg, when, **kw):
    kw.setdefault("whatsapp_enabled", True)
    return run_escalations(session, project, cfg, Mode.DRY_RUN, when, **kw)


def rows(session, channel):
    return list(session.scalars(select(Outbox).where(Outbox.channel == channel).order_by(Outbox.id)))


def levels(session, rule):
    nudge = session.scalar(select(Nudge).where(Nudge.rule == rule))
    return sorted(e.level for e in session.scalars(select(Escalation).where(Escalation.nudge_id == nudge.id)))


def nudge(session, rule):
    return session.scalar(select(Nudge).where(Nudge.rule == rule))


# Working hours maths ----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "a,b,hours",
    [
        (ist(10, 1, 9, 30), ist(10, 1, 11, 0), 1.5),
        (ist(9, 30, 16, 0), ist(10, 1, 11, 0), 4.0),  # 2.5 left on Wednesday plus 1.5 on Thursday
        (ist(10, 1, 7, 0), ist(10, 1, 10, 0), 0.5),  # only the time inside working hours counts
        (ist(10, 1, 19, 0), ist(10, 2, 9, 30), 0.0),  # overnight is not working time
        (ist(10, 2, 17, 0), ist(10, 5, 10, 30), 2.5),  # Friday 1.5 plus Monday 1.0, the weekend is skipped
        (ist(10, 1, 12, 0), ist(10, 1, 11, 0), 0.0),
    ],
)
def test_working_hours_between(cfg, a, b, hours):
    assert working_hours_between(a, b, cfg) == pytest.approx(hours)


# Critical rules ---------------------------------------------------------------------------------------
def board(session, cfg, items, synced, now=NOW):
    sync_project(session, cfg, ProjectData("PVT", items), synced)
    return load_board(session, session.scalar(select(Project)), cfg, now)


def test_unowned_production_blocker_needs_four_working_hours(session, cfg):
    b = board(session, cfg, [blocker()], synced=ist(9, 30, 16, 0))  # exactly 4.0 working hours by Thu 11:00
    (f,) = production_blocker_unowned(b)
    assert (f.rule_id, f.severity.value, f.member, f.evidence) == (
        "PRODUCTION_BLOCKER_UNOWNED", "critical", None, {"working_hours_unowned": 4.0},
    )  # fmt: skip


def test_unowned_production_blocker_not_yet(session, cfg):
    assert production_blocker_unowned(board(session, cfg, [blocker()], synced=ist(9, 30, 16, 1))) == []


def test_unowned_clock_restarts_if_someone_owned_it_in_between(session, cfg):
    sync_project(session, cfg, ProjectData("PVT", [blocker()]), ist(9, 29, 9, 30))
    sync_project(session, cfg, ProjectData("PVT", [blocker(assignee_login="ravi-demo")]), ist(9, 29, 15, 0))
    b = board(session, cfg, [blocker()], synced=ist(10, 1, 10, 0))  # unowned again for one hour only
    assert production_blocker_unowned(b) == []


def test_production_rule_ignores_owned_done_and_unlabelled(session, cfg):
    items = [blocker("a", assignee_login="ravi-demo"), blocker("b", status="Done"), blocker("c", labels=())]
    assert production_blocker_unowned(board(session, cfg, items, synced=ist(9, 28, 9, 30))) == []


def goal(n="G1", **kw):
    return item(issue_node_id=n, title="Rate limit login", labels=("goal",), assignee_login="ravi-demo", **kw)


def test_goal_item_not_started_near_the_end_of_the_sprint(session, cfg):
    (f,) = goal_item_not_started(
        board(session, cfg, [goal()], synced=ist(9, 30, 9, 30))
    )  # Thu: 1 working day left
    assert (f.rule_id, f.severity.value, f.member) == ("GOAL_ITEM_NOT_STARTED", "critical", "ravi-demo")
    assert f.evidence == {"sprint": "Sprint 12", "status": "Todo", "working_days_left": 1}


def test_goal_item_rule_is_quiet_early_and_for_started_or_unlabelled_items(session, cfg):
    early = ist(9, 30, 11, 0)  # Wednesday, two working days left, one more than allowed
    assert goal_item_not_started(board(session, cfg, [goal()], synced=ist(9, 29, 9, 0), now=early)) == []
    items = [goal("a", status="In Progress"), goal("b", status="Done"), item(issue_node_id="c", labels=()),
             goal("d", sprint_name="Sprint 11", sprint_start=SPRINT_START)]  # fmt: skip
    assert goal_item_not_started(board(session, cfg, items, synced=ist(9, 30, 9, 30))) == []


# The ladder -------------------------------------------------------------------------------------------
def stale_board(days, **kw):
    """One stale In Progress item, plus finished work so the sprint itself is not flagged at risk."""
    stale = item(
        issue_node_id="S1", title="Login page", status="In Progress", updated_at=days_ago(days), **kw
    )
    return [stale, item(issue_node_id="D", title="Done work", status="Done", estimate=30.0)]


def test_level_two_after_the_configured_hours_names_the_assignee(session, cfg):
    project = start(session, cfg, stale_board(3))  # medium severity
    assert nudge(session, "STALE_IN_PROGRESS").status == "queued"
    assert esc(session, project, cfg, ist(10, 1, 13, 40)).level2 == 0  # 3h55m, under the 4h limit
    run = esc(session, project, cfg, ist(10, 1, 13, 45))
    assert run.level2 == 1 and run.checked == 1
    (row,) = rows(session, "teams_team")
    assert row.target == "team" and row.mode == "dry_run"
    assert row.body.startswith("Reminder: Login page has had no update for 3 working days. Ravi, we asked")
    assert "4 hours ago" in row.body and "-" not in row.body.replace(
        row.body.split()[-1], ""
    )  # our wording, not the link
    assert nudge(session, "STALE_IN_PROGRESS").status == "escalated"


def test_each_level_happens_once_and_medium_stops_at_two(session, cfg):
    project = start(session, cfg, stale_board(3))
    esc(session, project, cfg, ist(10, 1, 14, 0))
    for when in (
        ist(10, 1, 14, 5),
        ist(10, 1, 14, 50),
        ist(10, 1, 18, 0),
    ):  # long past the 8h lead mark, still medium today
        again = esc(session, project, cfg, when)
        assert (again.level2, again.level3, again.level4) == (0, 0, 0)
    assert levels(session, "STALE_IN_PROGRESS") == [2] and len(rows(session, "teams_team")) == 1
    assert rows(session, "teams_dm") == [] or all("Reminder" not in r.body for r in rows(session, "teams_dm"))


def test_high_severity_goes_to_the_lead_after_the_lead_hours(session, cfg):
    project = start(session, cfg, stale_board(5))  # 4 working days quiet is twice the threshold, so high
    esc(session, project, cfg, ist(10, 1, 14, 0))
    assert esc(session, project, cfg, ist(10, 1, 17, 40)).level3 == 0  # 7h55m
    run = esc(session, project, cfg, ist(10, 1, 18, 0))  # 8h15m
    assert (run.level3, run.level4) == (1, 0)
    dms = [r for r in rows(session, "teams_dm") if r.target == "demo-asha" and "step in" in r.body]
    assert len(dms) == 1
    assert dms[0].body.startswith(
        "Hi Asha, Login page has had no update for 4 working days. We asked Ravi 8 hours ago"
    )
    assert levels(session, "STALE_IN_PROGRESS") == [2, 3]


def test_lead_is_not_messaged_again_about_their_own_item(session, cfg):
    project = start(session, cfg, stale_board(5, assignee_login="asha-demo"))
    esc(session, project, cfg, ist(10, 1, 14, 0))
    before = len(rows(session, "teams_dm"))
    run = esc(session, project, cfg, ist(10, 1, 18, 0))
    assert run.level3 == 1 and len(rows(session, "teams_dm")) == before  # recorded, nothing sent
    assert [e.channel for e in session.scalars(select(Escalation).where(Escalation.level == 3))] == ["none"]


def critical_ladder(session, cfg, **kw):
    """Walks a production blocker (critical, goes to the lead) up to the point where WhatsApp is next."""
    project = start(session, cfg, [blocker()], synced=ist(9, 30, 9, 30))
    esc(session, project, cfg, ist(10, 1, 14, 0), **kw)  # level 2
    esc(session, project, cfg, ist(10, 1, 18, 0), **kw)  # level 3, skipped as the lead owns it
    return project


def test_critical_reaches_whatsapp_on_the_next_working_morning(session, cfg):
    project = critical_ladder(session, cfg)
    assert esc(session, project, cfg, ist(10, 1, 21, 45)).deferred_outside_hours == 1  # 12h, but it is night
    run = esc(session, project, cfg, ist(10, 2, 10, 0))
    assert run.level4 == 1
    (row,) = rows(session, "whatsapp")
    payload = json.loads(row.body)
    assert (row.target, row.mode) == (LEAD_NUMBER, "dry_run")
    assert payload["template"] == "nxsprint_critical" and payload["language"] == "en"
    assert payload["params"][0] == "Asha" and "has had no owner for" in payload["params"][1]
    assert payload["params"][2] == "https://github.com/sria-demo/demo-app/issues/1"
    assert levels(session, "PRODUCTION_BLOCKER_UNOWNED") == [2, 3, 4]
    assert all(
        "\n" not in p and "-" not in p.replace("sria-demo", "").replace("demo-app", "")
        for p in payload["params"]
    )


def test_non_critical_never_reaches_whatsapp(session, cfg):
    project = start(session, cfg, stale_board(5))  # high, never critical
    for when in (ist(10, 1, 14, 0), ist(10, 1, 18, 0), ist(10, 2, 10, 0), ist(10, 2, 15, 0)):
        esc(session, project, cfg, when)
    assert rows(session, "whatsapp") == []


def test_whatsapp_flag_off_means_no_whatsapp(session, cfg):
    project = critical_ladder(session, cfg, whatsapp_enabled=False)
    run = esc(session, project, cfg, ist(10, 2, 10, 0), whatsapp_enabled=False)
    assert (run.level4, run.whatsapp_off) == (0, 1) and rows(session, "whatsapp") == []
    assert (
        esc(session, project, cfg, ist(10, 2, 10, 5)).level4 == 1
    )  # switching the flag on later still works


def test_a_test_redirect_blocks_whatsapp(session, cfg):
    project = critical_ladder(session, cfg)
    run = esc(session, project, cfg, ist(10, 2, 10, 0), redirect_active=True)
    assert (run.level4, run.whatsapp_blocked_by_redirect) == (0, 1) and rows(session, "whatsapp") == []


def test_whatsapp_is_capped_per_person_per_day(session, cfg):
    items = [blocker(f"PB{n}", f"Outage {n}") for n in range(3)]
    project = start(session, cfg, items, synced=ist(9, 30, 9, 30))
    esc(session, project, cfg, ist(10, 1, 14, 0))
    esc(session, project, cfg, ist(10, 1, 18, 0))
    run = esc(session, project, cfg, ist(10, 2, 10, 0))
    assert (run.level4, run.whatsapp_capped) == (2, 1)  # config says 2 per person per day
    assert len(rows(session, "whatsapp")) == 2
    again = esc(session, project, cfg, ist(10, 2, 15, 0))
    assert again.level4 == 0 and again.whatsapp_capped == 1  # still capped the same day
    nxt = esc(session, project, cfg, ist(10, 5, 10, 0))  # next working day, a fresh allowance
    assert nxt.level4 == 1 and len(rows(session, "whatsapp")) == 3


def test_goal_item_critical_goes_to_lead_by_dm_then_whatsapp(session, cfg):
    project = start(session, cfg, [goal(estimate=3.0), item(issue_node_id="D", status="Done", estimate=30.0)],
                    synced=ist(9, 30, 9, 30))  # fmt: skip
    esc(session, project, cfg, ist(10, 1, 14, 0))
    esc(session, project, cfg, ist(10, 1, 18, 0))
    assert levels(session, "GOAL_ITEM_NOT_STARTED") == [2, 3]
    dm = next(r for r in rows(session, "teams_dm") if "step in" in r.body)
    assert "We asked Ravi 8 hours ago" in dm.body and dm.target == "demo-asha"
    assert esc(session, project, cfg, ist(10, 2, 10, 0)).level4 == 1


def test_outside_hours_does_nothing_and_leaves_no_trace(session, cfg):
    project = start(session, cfg, stale_board(3))
    run = esc(session, project, cfg, ist(10, 1, 20, 0))
    assert run.deferred_outside_hours == 1 and rows(session, "teams_team") == []
    assert esc(session, project, cfg, ist(10, 2, 10, 0)).level2 == 1  # picked up at the next opportunity


def test_ack_stops_the_ladder_and_closes_open_escalations(session, cfg):
    project = start(session, cfg, stale_board(5))
    esc(session, project, cfg, ist(10, 1, 14, 0))
    n = nudge(session, "STALE_IN_PROGRESS")
    assert n.status == "escalated"
    # Ravi replies "ack" to the bot: an escalated nudge can still be acknowledged.
    from tests.bot_helpers import activity as act

    reply = handle_activity(
        session, AppConfig(projects=[cfg], placeholder=True), act("ack", who="demo-ravi"), ist(10, 1, 15, 0)
    )
    assert "noted 1 item" in reply[0]
    run = esc(session, project, cfg, ist(10, 1, 18, 30))
    assert (run.checked, run.level3) == (0, 0)  # acked nudges are not walked up
    assert all(e.resolved_at is not None for e in session.scalars(select(Escalation)))


def test_problem_going_away_suppresses_the_nudge(session, cfg):
    project = start(session, cfg, stale_board(5))
    esc(session, project, cfg, ist(10, 1, 14, 0))
    sync_project(
        session,
        cfg,
        ProjectData("PVT", [replace(stale_board(5)[0], status="Done")]),
        ist(10, 1, 15, 0),
    )
    run = esc(session, project, cfg, ist(10, 1, 18, 0))
    assert run.resolved == 1 and run.level3 == 0
    assert nudge(session, "STALE_IN_PROGRESS").status == "suppressed"
    assert all(e.resolved_at is not None for e in session.scalars(select(Escalation)))


def test_lead_escalation_uses_the_bot_when_the_lead_has_a_conversation(session, cfg):
    project = start(session, cfg, stale_board(5))
    handle_activity(
        session, AppConfig(projects=[cfg], placeholder=True), activity("help", who="demo-asha"), THU_0945
    )
    esc(session, project, cfg, ist(10, 1, 14, 0), use_bot=True)
    esc(session, project, cfg, ist(10, 1, 18, 0), use_bot=True)
    assert len(rows(session, "teams_bot")) == 1 and "step in" in rows(session, "teams_bot")[0].body


# Startup checks ----------------------------------------------------------------------------------------
def test_whatsapp_ready_check(cfg):
    check_whatsapp_ready(AppConfig(projects=[cfg], placeholder=True))
    cfg.members[0].whatsapp_number = None
    with pytest.raises(ConfigError, match="no whatsapp_number"):
        check_whatsapp_ready(AppConfig(projects=[cfg], placeholder=True))
    cfg.members[0].whatsapp_number = LEAD_NUMBER
    cfg.whatsapp = None
    with pytest.raises(ConfigError, match="no whatsapp block"):
        check_whatsapp_ready(AppConfig(projects=[cfg], placeholder=True))
