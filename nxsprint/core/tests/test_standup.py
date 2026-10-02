from datetime import timedelta

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ConfigError, load_config
from app.db import make_engine
from app.domain.bot_inbound import handle_activity
from app.domain.nudges import run_nudges
from app.domain.standup import build_summary, post_standup_summary, run_standup_prompts
from app.domain.sync import sync_project
from app.integrations.github import ProjectData
from app.models import Base, Event, Member, Outbox, Project, StandupPrompt, TeamsConversation
from app.settings import Mode
from tests.bot_helpers import activity
from tests.helpers import NOW, days_ago, item

PROMPT_NOW = NOW - timedelta(hours=1)  # Thu 10:00 IST, window is 09:45 to 10:30
SUMMARY_NOW = NOW  # 11:00 IST


@pytest.fixture
def env(config_file):
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    cfg = load_config(config_file, Mode.DRY_RUN).projects[0]
    board = [
        item(
            issue_node_id="I_1",
            title="Login page",
            status="In Progress",
            assignee_login="ravi-demo",
            estimate=5.0,
        ),
        item(
            issue_node_id="I_2", title="Export CSV", status="Todo", assignee_login="ravi-demo", estimate=2.0
        ),
        item(issue_node_id="I_3", title="Audit log", status="Todo", assignee_login="asha-demo", estimate=3.0),
        item(issue_node_id="I_4", title="Copy", status="Done", assignee_login="asha-demo", estimate=1.0),
    ]
    with Session(engine) as s:
        sync_project(s, cfg, ProjectData("PVT", board), days_ago(1, PROMPT_NOW))
        yield s, cfg, s.scalar(select(Project))


def prompts(env, now=PROMPT_NOW, use_bot=False, mode=Mode.DRY_RUN):
    s, cfg, project = env
    return run_standup_prompts(s, project, cfg, mode, now, use_bot)


def test_each_active_member_gets_their_own_list(env):
    s = env[0]
    run = prompts(env)
    assert run.prompted == 2 and run.not_due is None
    rows = {o.target: o.body for o in s.scalars(select(Outbox))}
    ravi, asha = rows["demo-ravi"], rows["demo-asha"]
    assert ravi.startswith("Hi Ravi, it is standup time for Demo Project.")
    assert (
        "1. Login page (In Progress)" in ravi and "2. Export CSV (Todo)" in ravi and "Audit log" not in ravi
    )
    assert "1. Audit log (Todo)" in asha and "Copy" not in asha  # done items are not listed
    assert "Done: ..., Doing: ..., Blocked: ..." in ravi


def test_prompt_text_has_no_dashes_of_ours(env):
    s = env[0]
    prompts(env)
    for o in s.scalars(select(Outbox)):
        assert "-" not in o.body and "—" not in o.body, o.body


def test_prompts_go_out_once_per_day(env):
    s = env[0]
    prompts(env)
    again = prompts(env, now=PROMPT_NOW + timedelta(minutes=15))
    assert (again.prompted, again.already_prompted) == (0, 2)
    assert len(s.scalars(select(Outbox)).all()) == 2
    assert prompts(env, now=PROMPT_NOW + timedelta(days=1)).prompted == 2  # next day, a fresh standup


@pytest.mark.parametrize(
    "now,why",
    [
        (PROMPT_NOW - timedelta(minutes=30), "outside the standup window"),  # 09:30
        (SUMMARY_NOW, "outside the standup window"),  # 11:00, summary time, too late to prompt
        (PROMPT_NOW + timedelta(days=2), "not a working day"),  # Saturday
    ],
)
def test_prompts_only_inside_the_window_on_working_days(env, now, why):
    run = prompts(env, now=now)
    assert (run.prompted, run.not_due) == (0, why) and env[0].scalars(select(Outbox)).all() == []


def test_member_in_another_timezone_waits_for_their_own_hours(env):
    s, cfg, project = env
    cfg.members[1].timezone = "America/New_York"
    run = prompts(env)
    assert (run.prompted, run.deferred_outside_hours) == (1, 1)
    assert not s.scalars(select(StandupPrompt).where(StandupPrompt.member_id == 2)).all()  # retried later


def test_bot_is_used_only_where_a_conversation_exists(env):
    s, cfg, project = env
    handle_activity(s, _app_cfg(env), activity("help", who="demo-ravi"), PROMPT_NOW)
    run_standup_prompts(s, project, cfg, Mode.DRY_RUN, PROMPT_NOW, use_bot=True)
    by_target = {o.target: o for o in s.scalars(select(Outbox))}
    assert by_target["demo-ravi"].channel == "teams_bot" and by_target["demo-ravi"].body.endswith(
        "Reply ack when you have seen this."
    )
    assert by_target["demo-asha"].channel == "teams_dm" and "Reply ack" not in by_target["demo-asha"].body


def _app_cfg(env):
    from app.config import AppConfig

    return AppConfig(projects=[env[1]], placeholder=True)


def reply(env, who, text):
    s = env[0]
    handle_activity(s, _app_cfg(env), activity(text, who=who), PROMPT_NOW + timedelta(minutes=5))


def test_summary_waits_until_summary_time_and_needs_prompts(env):
    s, cfg, project = env
    assert (
        post_standup_summary(s, project, cfg, Mode.DRY_RUN, SUMMARY_NOW)
        == "no standup prompts went out today"
    )
    prompts(env)
    assert (
        post_standup_summary(s, project, cfg, Mode.DRY_RUN, PROMPT_NOW + timedelta(minutes=20))
        == "not time yet"
    )
    assert (
        post_standup_summary(s, project, cfg, Mode.DRY_RUN, PROMPT_NOW + timedelta(days=2, hours=1))
        == "not a working day"
    )


def test_summary_content_and_once_a_day(env):
    s, cfg, project = env
    prompts(env)
    reply(env, "demo-ravi", "Done: login form. Doing: csv export. Blocked: none")
    assert post_standup_summary(s, project, cfg, Mode.DRY_RUN, SUMMARY_NOW) is None
    assert post_standup_summary(s, project, cfg, Mode.DRY_RUN, SUMMARY_NOW) == "already posted"
    row = s.scalars(select(Outbox).where(Outbox.channel == "teams_team")).one()
    assert (row.target, row.mode) == ("team", "dry_run")
    text = row.body
    assert text.startswith("Standup summary for Thu 01 Oct, Demo Project.\nReplies: 1 of 2 members.")
    assert "Ravi. Done: login form. Doing: csv export. Blocked: none." in text
    assert "Asha. No reply yet." in text
    assert "Moved to Done since the last working day:" in text
    assert "Nothing on the board carries the blocked label." in text
    assert len(s.scalars(select(Event).where(Event.type == "standup_summary")).all()) == 1


def test_summary_lists_blockers_and_risks_from_the_board_and_rules(env):
    s, cfg, project = env
    sync_project(s, cfg, ProjectData("PVT", [
        item(issue_node_id="I_1", title="Payments", status="In Progress", labels=("blocked",), estimate=8.0),
        item(issue_node_id="I_2", title="Orphan", assignee_login=None, estimate=2.0),
    ]), days_ago(5, PROMPT_NOW))  # fmt: skip
    prompts(env)
    text = build_summary(s, project, cfg, SUMMARY_NOW)
    assert "Blocked on the board: Payments (" in text and "working days)" in text
    assert "Risks:" in text and "Orphan has no owner" in text and "Sprint 12 looks at risk" in text


def test_long_or_messy_reply_is_trimmed_in_the_summary(env):
    s, cfg, project = env
    prompts(env)
    reply(env, "demo-asha", "Done: " + "x " * 300 + "\n\n Doing: y")
    text = build_summary(s, project, cfg, SUMMARY_NOW)
    line = next(line for line in text.splitlines() if line.startswith("Asha."))
    assert len(line) < 320 and "..." in line and "\n" not in line


def test_unlabelled_reply_is_shown_as_written(env):
    s, cfg, project = env
    prompts(env)
    reply(env, "demo-asha", "all quiet on my side")
    assert "Asha. all quiet on my side" in build_summary(s, project, cfg, SUMMARY_NOW)


def test_summary_config_must_come_after_prompt_time(config_file):
    data = yaml.safe_load(config_file.read_text())
    data["projects"][0]["standup_summary_time"] = "09:00"
    config_file.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="later than standup_time"):
        load_config(config_file, Mode.DRY_RUN)


def test_nudges_use_bot_channel_with_footer_only_when_asked_and_possible(env):
    s, cfg, project = env
    handle_activity(s, _app_cfg(env), activity("help", who="demo-ravi"), PROMPT_NOW)
    sync_project(s, cfg, ProjectData("PVT", [item(estimate=None)]), days_ago(2, NOW))
    run = run_nudges(s, project, cfg, Mode.DRY_RUN, NOW, use_bot=True)
    nudge = next(n for n in run.created if n.rule == "NO_ESTIMATE")
    out = s.scalars(select(Outbox).where(Outbox.nudge_id == nudge.id)).one()
    assert (nudge.channel, out.channel) == ("teams_bot", "teams_bot")
    assert out.body == nudge.message + "\n\nReply ack when you have seen this."  # nudge row stays clean
    assert s.scalar(select(Member.id).where(Member.github_login == "ravi-demo")) and s.scalar(
        select(TeamsConversation.id)
    )
    # Bot off: same member, plain webhook.
    again = run_nudges(s, project, cfg, Mode.DRY_RUN, NOW + timedelta(days=2), use_bot=False)
    assert {n.channel for n in again.created} <= {"teams_dm"}
