from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import load_config
from app.db import make_engine
from app.domain.bot_inbound import HELP, clean_text, handle_activity, parse_standup
from app.domain.sync import sync_project
from app.integrations.github import ProjectData
from app.models import Base, Member, Nudge, Project, StandupPrompt, StandupResponse, TeamsConversation
from app.settings import Mode
from tests.bot_helpers import activity
from tests.helpers import NOW, item

STANDUP_NOW = NOW - timedelta(hours=1)  # 10:00 in Kolkata, inside the 09:45 to 10:30 window


@pytest.fixture
def env(config_file):

    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    cfg = load_config(config_file, Mode.DRY_RUN)
    with Session(engine) as s:
        sync_project(s, cfg.projects[0], ProjectData("PVT", [item()]), NOW - timedelta(days=1))
        yield s, cfg, cfg.projects[0]


def member(s, login):
    return s.scalar(select(Member).where(Member.github_login == login))


def add_nudge(s, login, status="sent", rule="NO_ESTIMATE", issue="I_1"):
    project = s.scalar(select(Project))
    n = Nudge(project_id=project.id, member_id=member(s, login).id, issue_node_id=issue, rule=rule,
              channel="teams_dm", message="m", status=status)  # fmt: skip
    s.add(n)
    s.commit()
    return n


def say(env, text, who="demo-ravi", now=NOW):
    s, cfg, _ = env
    return handle_activity(s, cfg, activity(text, who=who), now)


def test_clean_text_strips_mentions_and_entities():
    assert clean_text("<at>NxSprint</at>&nbsp; ack   12 ") == "ack 12"


def test_parse_standup_labels_in_any_case_and_order():
    got = parse_standup("DONE: login page. doing: tests. Blocked: none")
    assert got == {"done": "login page", "doing": "tests", "blocked": "none"}
    assert parse_standup("just some words") == {"done": None, "doing": None, "blocked": None}
    assert parse_standup("Blocked: waiting on keys") == {
        "done": None,
        "doing": None,
        "blocked": "waiting on keys",
    }


def test_ack_all_open_nudges_for_this_member_only(env):
    s = env[0]
    mine, other = add_nudge(s, "ravi-demo"), add_nudge(s, "asha-demo")
    done = add_nudge(s, "ravi-demo", status="acked", issue="I_2")
    assert say(env, "ack") == ["Thank you, we have noted 1 item as seen."]
    s.refresh(mine), s.refresh(other), s.refresh(done)
    assert (mine.status, other.status) == ("acked", "sent") and mine.acked_at is not None


def test_ack_a_specific_number_and_not_someone_elses(env):
    s = env[0]
    a, b, theirs = (
        add_nudge(s, "ravi-demo"),
        add_nudge(s, "ravi-demo", issue="I_2"),
        add_nudge(s, "asha-demo"),
    )
    assert "1 item" in say(env, f"ack #{a.id}")[0]
    assert "nothing waiting" in say(env, f"ack {theirs.id}")[0].lower()
    s.refresh(a), s.refresh(b), s.refresh(theirs)
    assert (a.status, b.status, theirs.status) == ("acked", "sent", "sent")


def test_ack_with_nothing_open_is_friendly(env):
    assert "nothing waiting" in say(env, "ok")[0].lower()


def test_help_and_unknown_text_without_open_standup(env):
    assert say(env, "help") == [HELP]
    assert say(env, "what is the weather")[0].startswith("We have no standup open for you right now.")


def test_unknown_account_is_told_and_nothing_is_stored(env):
    s = env[0]
    reply = say(env, "ack", who="stranger")
    assert "do not recognise" in reply[0] and s.scalars(select(TeamsConversation)).all() == []


def test_inactive_member_is_not_recognised(env):
    s = env[0]
    member(s, "ravi-demo").active = False
    s.commit()
    assert "do not recognise" in say(env, "ack")[0]


def prompt(env, login="ravi-demo"):
    s, _, cfg = env
    s.add(StandupPrompt(project_id=s.scalar(select(Project)).id, member_id=member(s, login).id,
                        standup_date=STANDUP_NOW.astimezone(__import__("zoneinfo").ZoneInfo(cfg.timezone)).date()))  # fmt: skip
    s.commit()


def test_standup_reply_is_stored_parsed_and_overwritten(env):
    s = env[0]
    prompt(env)
    assert say(env, "Done: login. Doing: tests. Blocked: none", now=STANDUP_NOW) == [
        "Thank you, we have your standup note."
    ]
    row = s.scalar(select(StandupResponse))
    assert (row.done, row.doing, row.blocked) == ("login", "tests", "none")
    assert say(env, "Done: more. Doing: x. Blocked: y", now=STANDUP_NOW)[0].startswith(
        "Thank you, we have updated"
    )
    assert (
        len(s.scalars(select(StandupResponse)).all()) == 1
        and s.scalar(select(StandupResponse)).done == "more"
    )


def test_unlabelled_reply_is_kept_and_member_is_told_the_format(env):
    s = env[0]
    prompt(env)
    reply = say(env, "mostly fixing bugs today", now=STANDUP_NOW)[0]
    assert "three lines" in reply and s.scalar(select(StandupResponse)).raw_text == "mostly fixing bugs today"


def test_ack_command_wins_over_standup_reply(env):
    s = env[0]
    prompt(env)
    add_nudge(s, "ravi-demo")
    say(env, "ack", now=STANDUP_NOW)
    assert s.scalars(select(StandupResponse)).all() == []


def test_message_stores_conversation_reference_and_refreshes_it(env):
    s = env[0]
    say(env, "help")
    ref = s.scalar(select(TeamsConversation))
    assert (ref.service_url, ref.conversation_id) == ("https://smba.trafficmanager.net/in/", "conv-demo-ravi")
    say(env, "help", now=NOW + timedelta(days=1))
    assert len(s.scalars(select(TeamsConversation)).all()) == 1


def test_install_events_store_the_conversation_and_welcome(env):
    s = env[0]
    out = handle_activity(s, env[1], activity(kind="installationUpdate", action="add"), NOW)
    assert out[0].startswith("Hi Ravi, we are NxSprint") and s.scalar(select(TeamsConversation)) is not None
    out2 = handle_activity(
        s, env[1], activity(kind="conversationUpdate", who="demo-asha", membersAdded=[{"id": "x"}]), NOW
    )
    assert out2[0].startswith("Hi Asha")
    assert handle_activity(s, env[1], activity(kind="installationUpdate", action="remove"), NOW) == []
    assert handle_activity(s, env[1], activity(kind="typing"), NOW) == []
    assert (
        handle_activity(
            s, env[1], activity(kind="conversationUpdate", who="stranger", membersAdded=[{"id": "x"}]), NOW
        )
        == []
    )
