from datetime import UTC, date, datetime, timedelta

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ConfigError, load_config
from app.db import make_engine
from app.domain import ceremonies as c
from app.domain.board import load_board
from app.domain.rules import Finding, Severity
from app.domain.sync import sync_project
from app.integrations.github import ProjectData
from app.models import Base, Event, Outbox, Project, StandupResponse, TeamsConversation
from app.settings import Mode
from tests.helpers import NOW, days_ago, item

# Dates (Kolkata): NOW is Thu 1 Oct 11:00. Sprint 12 runs Mon 21 Sep to Mon 5 Oct (end is exclusive).
T0 = days_ago(9)  # Tue 22 Sep 11:00, the first time we saw the board


@pytest.fixture
def cfg(config_file):
    return load_config(config_file, Mode.DRY_RUN).projects[0]


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def sync(session, cfg, items, at):
    sync_project(session, cfg, ProjectData("PVT", items), at)
    return session.scalar(select(Project))


def backlog(n, title, priority, est, **kw):
    return item(issue_node_id=n, title=title, sprint_name=None, sprint_start=None, sprint_days=None,
                priority=priority, estimate=est, assignee_login=None, **kw)  # fmt: skip


def plan_board(session, cfg, extra=()):
    items = [
        item(issue_node_id="I_1", title="Login", status="In Progress", estimate=5.0),
        item(issue_node_id="I_2", title="Export", status="Todo", estimate=3.0),
        item(issue_node_id="I_3", title="Copy", status="Done", estimate=2.0),
        backlog("B1", "Urgent thing", "Urgent", 8.0),
        backlog("B2", "Huge thing", "High", 30.0),
        backlog("B3", "Unsized thing", "High", None),
        backlog("B4", "Medium thing", "Medium", 3.0),
        backlog("B5", "Low thing", "Low", 5.0),
        backlog("B6", "Unranked thing", None, 2.0),
        backlog("B7", "Finished thing", "Urgent", 1.0, status="Done"),
        *extra,
    ]
    project = sync(session, cfg, items, NOW)
    return load_board(session, project, cfg, NOW)


# Planning ----------------------------------------------------------------------------------------------
def test_planning_capacity_carryover_and_priority_first_fit(session, cfg):
    plan = c.build_planning(plan_board(session, cfg))
    assert plan.total_capacity == 40 and [n for n, _ in plan.capacity] == ["Asha", "Ravi"]
    assert [i.title for i in plan.carryover] == ["Login", "Export"] and plan.carryover_points == 8
    assert plan.room == 32 and plan.over_by == 0
    # Priority order Urgent, High, Medium, Low, then unranked. The 30 point item cannot fit 24 left.
    assert [i.title for i in plan.suggested] == [
        "Urgent thing",
        "Medium thing",
        "Low thing",
        "Unranked thing",
    ]
    assert plan.suggested_points == 18
    assert [i.title for i in plan.did_not_fit] == ["Huge thing"]
    assert [i.title for i in plan.unestimated] == ["Unsized thing"]


def test_done_backlog_items_and_in_sprint_items_are_never_suggested(session, cfg):
    plan = c.build_planning(plan_board(session, cfg))
    everything = {i.title for i in plan.suggested + plan.did_not_fit + plan.unestimated}
    assert "Finished thing" not in everything and "Login" not in everything


def test_carryover_over_capacity_leaves_no_room(session, cfg):
    big = item(issue_node_id="I_9", title="Giant", status="Todo", estimate=60.0)
    plan = c.build_planning(plan_board(session, cfg, extra=[big]))
    assert plan.over_by == 28 and plan.room == 0 and plan.suggested == []
    text = c.render_planning(plan, cfg)
    assert "Carryover alone is 28 points over capacity" in text and "Nothing from the backlog fits" in text


def test_planning_without_an_active_sprint_is_none(session, cfg):
    project = sync(session, cfg, [backlog("B1", "x", "High", 1.0)], NOW)
    assert c.build_planning(load_board(session, project, cfg, NOW)) is None


def test_planning_text_is_a_proposal_and_follows_house_rules(session, cfg):
    text = c.render_planning(c.build_planning(plan_board(session, cfg)), cfg)
    assert "only a proposal for you to approve, nothing on the board has been changed" in text
    assert "Capacity: Asha 20, Ravi 20. Total 40 points." in text
    assert "Carrying over from Sprint 12 if not finished: 2 items, 8 points." in text
    assert "1. Login (In Progress, 5 points)" in text and "Room left for new work: 32 points." in text
    assert (
        "1. Urgent thing (Urgent, 8 points)" in text
        and "Need an estimate before they can be planned:" in text
    )
    assert "-" not in text  # no dashes of any kind in our wording (the titles here have none)


def test_long_lists_are_capped(session, cfg):
    many = [backlog(f"X{n}", f"Task {n}", "Low", 0.5) for n in range(15)]
    cfg.members[0].capacity_points, cfg.members[1].capacity_points = 100, 100
    plan = c.build_planning(plan_board(session, cfg, extra=many))
    assert len(plan.suggested) > 10
    assert f"and {len(plan.suggested) - 10} more" in c.render_planning(plan, cfg)  # only 10 are listed


# Retro -------------------------------------------------------------------------------------------------
def retro_history(session, cfg):
    sprint = dict(sprint_name="Sprint 12")
    sync(session, cfg, [
        item(issue_node_id="P1", title="Login", status="In Progress", estimate=5.0, **sprint),
        item(issue_node_id="P2", title="Export", status="Todo", estimate=3.0, **sprint),
        item(issue_node_id="P3", title="Copy", status="Todo", estimate=2.0, **sprint),
        item(issue_node_id="P4", title="Webhooks", status="Todo", estimate=1.0, labels=("blocked",), **sprint),
    ], T0)  # fmt: skip
    sync(session, cfg, [
        item(issue_node_id="P1", title="Login", status="Done", estimate=5.0, **sprint),
        item(issue_node_id="P2", title="Export", status="Todo", estimate=3.0, **sprint),
        item(issue_node_id="P3", title="Copy", status="Todo", estimate=2.0, sprint_name=None, sprint_start=None, sprint_days=None),
        item(issue_node_id="P4", title="Webhooks", status="Todo", estimate=1.0, **sprint),
        item(issue_node_id="A1", title="Hotfix", status="Todo", estimate=4.0, **sprint),
    ], T0 + timedelta(days=2))  # fmt: skip
    return sync(session, cfg, [
        item(issue_node_id="P1", title="Login", status="Done", estimate=5.0, **sprint),
        item(issue_node_id="P2", title="Export", status="In Progress", estimate=3.0, **sprint),
        item(issue_node_id="P3", title="Copy", status="Todo", estimate=2.0, sprint_name=None, sprint_start=None, sprint_days=None),
        item(issue_node_id="P4", title="Webhooks", status="Todo", estimate=1.0, **sprint),
        item(issue_node_id="A1", title="Hotfix", status="Done", estimate=4.0, **sprint),
    ], NOW)  # fmt: skip


def test_retro_planned_added_removed_completed(session, cfg):
    project = retro_history(session, cfg)
    r = c.build_retro(session, project, cfg, NOW)
    assert (r.sprint, r.start, r.end) == ("Sprint 12", date(2026, 9, 21), date(2026, 10, 5))
    assert r.observed_from == date(2026, 9, 22)  # we began watching mid sprint, and say so
    assert (r.planned_count, r.planned_points) == (4, 11)  # what we saw at the first sync
    assert (r.planned_done_count, r.planned_done_points) == (1, 5)
    assert r.added == [("Hotfix", 4.0, True)] and r.removed == ["Copy"]
    assert (r.done_total_count, r.done_total_points) == (2, 9)  # Login plus the added hotfix
    assert sorted(r.carryover) == ["Export", "Webhooks"]


def test_retro_cycle_time_counts_only_items_we_saw_start_and_finish(session, cfg):
    project = retro_history(session, cfg)
    r = c.build_retro(session, project, cfg, NOW)
    # Login: In Progress on 22 Sep, Done on 24 Sep, so 2 working days. The hotfix jumped to Done unseen.
    assert (r.cycle_median, r.cycle_samples, r.cycle_longest) == (2.0, 1, ("Login", 2))


def test_retro_blocked_items_with_days(session, cfg):
    project = retro_history(session, cfg)
    r = c.build_retro(session, project, cfg, NOW)
    assert r.blocked == [("Webhooks", 2, False)]  # blocked 22 Sep, label gone by 24 Sep


def test_retro_flags_items_blocked_in_an_earlier_sprint_too(session, cfg):
    early = dict(sprint_name="Sprint 11", sprint_start=date(2026, 9, 7))
    sync(session, cfg, [item(issue_node_id="E1", title="Keys", labels=("blocked",), **early)], days_ago(21))
    project = sync(session, cfg, [item(issue_node_id="E1", title="Keys", labels=("blocked",))], T0)
    r = c.build_retro(session, project, cfg, NOW)
    assert r.blocked and r.blocked[0][0] == "Keys" and r.blocked[0][2] is True
    assert "blocked in an earlier sprint too" in c.render_retro(r, cfg)


def test_retro_prompts_are_data_driven_then_standing(session, cfg):
    r = c.build_retro(session, retro_history(session, cfg), cfg, NOW)
    assert r.prompts[0].startswith("Scope changed during the sprint: 1 added and 1 removed.")
    assert r.prompts[1].startswith("2 items are not finished.") and r.prompts[2].startswith(
        "1 item carried the blocked label."
    )
    empty = c.Retro("S", date(2026, 9, 21), date(2026, 10, 5))
    assert c.retro_prompts(empty) == list(c.GENERIC_PROMPTS)
    only_cycle = c.Retro(
        "S",
        date(2026, 9, 21),
        date(2026, 10, 5),
        cycle_longest=("Login", 4),
        planned_points=10,
        planned_done_points=6,
    )
    assert c.retro_prompts(only_cycle)[:2] == [
        "Login took 4 working days from start to done, the longest this sprint. What made it slow?",
        "We finished 6 of 10 planned points. What would we do differently to make the plan fit?",
    ]


def test_retro_text(session, cfg):
    r = c.build_retro(session, retro_history(session, cfg), cfg, NOW)
    text = c.render_retro(r, cfg)
    assert text.startswith("Sprint review and retro prep for Sprint 12, 21 Sep to 04 Oct.")
    assert "We only started watching this board on 22 Sep" in text
    assert "1 of 4 planned items done, 5 of 11 planned points" in text
    assert "Everything done in the sprint, including added work: 2 items, 9 points." in text
    assert (
        "Scope changes: 1 added, 1 removed." in text
        and "Added: Hotfix (4 points, done)" in text
        and "Removed: Copy" in text
    )
    assert (
        "Cycle time, from In Progress to Done: median 2 working days across 1 item. The longest was Login at 2 working days."
        in text
    )
    assert "Webhooks (2 working days)" in text and "Three questions for the retro:" in text


def test_retro_without_sprint_or_data_is_none(session, cfg):
    project = sync(session, cfg, [backlog("B1", "x", "High", 1.0)], NOW)
    assert c.build_retro(session, project, cfg, NOW) is None


def test_retro_with_no_finished_work_says_so(session, cfg):
    project = sync(session, cfg, [item(issue_node_id="Q", title="Only", status="Todo")], T0)
    text = c.render_retro(c.build_retro(session, project, cfg, NOW), cfg)
    assert "Cycle time: not enough finished items" in text and "No item carried the blocked label." in text
    assert "No scope changes seen." in text


# Weekly ------------------------------------------------------------------------------------------------
def test_top_risks_rank_by_severity_then_rule_order():
    def f(rule, sev, title="t"):
        return Finding(rule, sev, None, "i", title, None, {"working_days_blocked": 5, "sprint": "S"})

    ranked = c.top_risks([f("NO_ESTIMATE", Severity.LOW), f("STALE_IN_PROGRESS", Severity.MEDIUM, "b"),
                          f("BLOCKED_LABEL_AGING", Severity.HIGH), f("SPRINT_AT_RISK", Severity.HIGH),
                          f("UNASSIGNED_IN_SPRINT", Severity.MEDIUM, "a")])  # fmt: skip
    assert [x.rule_id for x in ranked] == ["SPRINT_AT_RISK", "BLOCKED_LABEL_AGING", "UNASSIGNED_IN_SPRINT"]


def test_trend_words():
    assert c._trend([10]).startswith("not enough")
    assert (c._trend([10, 12]), c._trend([12, 10]), c._trend([10, 10])) == ("rising", "falling", "flat")


def weekly_board(session, cfg):
    def done(n, sprint, start, est):
        return item(
            issue_node_id=n, title=n, status="Done", estimate=est, sprint_name=sprint, sprint_start=start
        )

    items = [
        done("a", "Sprint 9", date(2026, 8, 10), 6.0), done("b", "Sprint 9", date(2026, 8, 10), 4.0),
        done("c", "Sprint 10", date(2026, 8, 24), 15.0), done("d", "Sprint 11", date(2026, 9, 7), 12.0),
        item(issue_node_id="V1", title="Payments", status="In Progress", estimate=8.0, labels=("blocked",), updated_at=days_ago(9)),
        item(issue_node_id="U1", title="Orphan", assignee_login=None, estimate=2.0),
    ]  # fmt: skip
    project = sync(session, cfg, items, days_ago(9))
    sync(session, cfg, items, NOW)  # statuses of old sprints update to completed
    return project


def test_velocity_uses_points_done_in_each_completed_sprint(session, cfg):
    project = weekly_board(session, cfg)
    assert c.velocity(session, project, cfg) == [("Sprint 9", 10.0), ("Sprint 10", 15.0), ("Sprint 11", 12.0)]


def test_velocity_keeps_only_the_last_four_sprints(session, cfg):
    items = [item(issue_node_id=f"s{n}", status="Done", estimate=float(n), sprint_name=f"Sprint {n}",
                  sprint_start=date(2026, 1, 1) + timedelta(days=14 * n)) for n in range(1, 7)]  # fmt: skip
    project = sync(session, cfg, items, NOW)
    assert [name for name, _ in c.velocity(session, project, cfg)] == [
        "Sprint 3",
        "Sprint 4",
        "Sprint 5",
        "Sprint 6",
    ]


def test_weekly_report_content(session, cfg):
    project = weekly_board(session, cfg)
    session.add(StandupResponse(project_id=project.id, member_id=2, standup_date=date(2026, 10, 1), raw_text="x",
                                blocked="waiting on keys"))  # fmt: skip
    session.add(
        StandupResponse(
            project_id=project.id, member_id=1, standup_date=date(2026, 10, 1), raw_text="x", blocked="none"
        )
    )
    session.commit()
    text = c.build_weekly(session, project, cfg, NOW)
    assert text.startswith("Weekly report for Thu 01 Oct, Demo Project.\nTop risks:")
    assert "Sprint 12 looks at risk" in text and "Payments has been blocked for" in text
    assert (
        "Who is blocked:" in text
        and "Ravi: Payments (" in text
        and "Ravi said in standup: waiting on keys" in text
    )
    assert "Asha said" not in text  # "none" is not a blocker
    assert "Velocity, points done: Sprint 9 10, Sprint 10 15, Sprint 11 12. Trend: falling." in text
    assert "Decisions needed from you:" in text and "Pick an owner for Orphan." in text
    assert "Decide what to cut or move so Sprint 12 can finish." in text
    assert text.count("\n") < 25  # one page


def test_weekly_report_when_everything_is_calm(session, cfg):
    project = sync(session, cfg, [item(issue_node_id="ok", status="Done", estimate=3.0)], NOW)
    text = c.build_weekly(session, project, cfg, NOW)
    assert "Top risks: none from the rules." in text and "nobody on the board" in text
    assert "Velocity: no completed sprint seen yet." in text and "Decisions needed from you: none." in text


# Scheduling -------------------------------------------------------------------------------------------
def at(day, hh=11, mm=0):
    """Local Kolkata time on a day in Sept or Oct 2026, as UTC."""
    return datetime(2026, 10, 1, tzinfo=UTC).replace(
        month=day[0], day=day[1], hour=hh, minute=mm
    ) - timedelta(hours=5, minutes=30)


@pytest.mark.parametrize(
    "when,due",
    [((9, 28), False), ((9, 29), False), ((9, 30), True), ((10, 1), True), ((10, 2), True)],
)
def test_planning_prep_falls_due_two_working_days_before_the_end(session, cfg, when, due):
    project = sync(session, cfg, [item()], at((9, 22)))
    why = c.run_planning_prep(session, project, cfg, Mode.DRY_RUN, at(when))
    assert (why is None) is due and (due or why == "not due yet")


def test_planning_prep_goes_to_the_lead_once_per_sprint(session, cfg):
    project = sync(session, cfg, [item()], at((9, 22)))
    assert c.run_planning_prep(session, project, cfg, Mode.DRY_RUN, at((9, 30))) is None
    assert (
        c.run_planning_prep(session, project, cfg, Mode.DRY_RUN, at((10, 1)))
        == "already sent for this sprint"
    )
    row = session.scalars(select(Outbox)).one()
    assert (row.target, row.channel, row.mode) == ("demo-asha", "teams_dm", "dry_run")  # Asha is the lead
    assert row.body.startswith("Sprint planning proposal for the sprint after Sprint 12.")


def test_prep_waits_for_the_leads_working_hours_and_leaves_no_trace(session, cfg):
    project = sync(session, cfg, [item()], at((9, 22)))
    assert (
        c.run_planning_prep(session, project, cfg, Mode.DRY_RUN, at((9, 30), 21))
        == "outside the lead's working hours"
    )
    assert session.scalars(select(Event).where(Event.type == "planning_prep")).all() == []
    assert c.run_planning_prep(session, project, cfg, Mode.DRY_RUN, at((9, 30), 11)) is None  # retried later


def test_prep_uses_the_bot_when_the_lead_has_a_conversation(session, cfg):
    project = sync(session, cfg, [item()], at((9, 22)))
    session.add(TeamsConversation(member_id=1, service_url="https://x/", conversation_id="c"))
    session.commit()
    c.run_planning_prep(session, project, cfg, Mode.DRY_RUN, at((9, 30)), use_bot=True)
    assert session.scalars(select(Outbox)).one().channel == "teams_bot"


def test_retro_prep_falls_due_on_the_last_working_day(session, cfg):
    project = sync(session, cfg, [item(issue_node_id="x", status="In Progress")], at((9, 22)))
    assert c.run_retro_prep(session, project, cfg, Mode.DRY_RUN, at((10, 1))) == "not due yet"
    assert c.run_retro_prep(session, project, cfg, Mode.DRY_RUN, at((10, 2))) is None  # Friday, the last one
    assert (
        c.run_retro_prep(session, project, cfg, Mode.DRY_RUN, at((10, 2), 12))
        == "already sent for this sprint"
    )
    assert session.scalars(select(Outbox)).one().body.startswith("Sprint review and retro prep for Sprint 12")


def test_prep_without_an_active_sprint(session, cfg):
    project = sync(session, cfg, [backlog("B", "x", "High", 1.0)], NOW)
    assert c.run_planning_prep(session, project, cfg, Mode.DRY_RUN, NOW) == "no active sprint"
    assert c.run_retro_prep(session, project, cfg, Mode.DRY_RUN, NOW) == "no active sprint"


def test_weekly_report_only_on_its_day_after_its_time_once_a_week(session, cfg):
    project = sync(session, cfg, [item()], at((9, 22)))
    run = lambda when: c.run_weekly_report(session, project, cfg, Mode.DRY_RUN, when)  # noqa: E731
    assert run(at((10, 1), 17)) == "not the report day"  # Thursday
    assert run(at((10, 3), 17)) == "not the report day"  # Saturday
    assert run(at((10, 2), 15)) == "not time yet"  # Friday 15:00, report is from 16:00
    assert run(at((10, 2), 16, 30)) is None
    assert run(at((10, 2), 17)) == "already sent this week"
    row = session.scalars(select(Outbox)).one()
    assert (row.channel, row.target, row.mode) == ("owner_report", "sai", "dry_run")
    assert row.body.startswith("Weekly report for Fri 02 Oct, Demo Project.")
    assert run(at((10, 9), 16, 30)) is None  # next Friday is a new week


def test_weekly_report_respects_holidays(session, cfg):
    cfg.holidays = [date(2026, 10, 2)]
    project = sync(session, cfg, [item()], at((9, 22)))
    assert c.run_weekly_report(session, project, cfg, Mode.DRY_RUN, at((10, 2), 17)) == "not the report day"


# Config ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda p: p.pop("priority_order"), "priority_order"),
        (lambda p: p.update(priority_order=[]), "priority_order"),
        (lambda p: p.update(priority_order=["High", "High"]), "duplicates"),
        (lambda p: p.pop("ceremonies"), "ceremonies"),
        (lambda p: p["ceremonies"].pop("weekly_report_time"), "weekly_report_time"),
        (lambda p: p["ceremonies"].update(weekly_report_weekday=6), "one of working_days"),
        (lambda p: p["ceremonies"].update(retro_prep_working_days_before_sprint_end=-1), "retro_prep"),
    ],
)
def test_ceremony_config_is_required_and_checked(config_file, mutate, match):
    data = yaml.safe_load(config_file.read_text())
    mutate(data["projects"][0])
    config_file.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match=match):
        load_config(config_file, Mode.DRY_RUN)
