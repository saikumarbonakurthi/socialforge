"""What to do with an inbound Teams activity: remember the conversation, acks, standup replies."""

import html
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AppConfig, ProjectCfg
from app.models import Member, Nudge, Project, StandupPrompt, StandupResponse, TeamsConversation

HELP = (
    "We are NxSprint, the scrum assistant for SRIA. Reply ack to tell us you have seen our nudges, "
    "or ack followed by a number for one of them. At standup time, reply with three lines: "
    "Done: ..., Doing: ..., Blocked: ... and write none where it does not apply."
)
_LABELS = re.compile(r"\b(done|doing|blocked)\s*:", re.I)
_ACK = re.compile(r"^(?:ack|acknowledge|got it|ok)(?:\s+all|\s+#?(\d+))?$", re.I)


def clean_text(raw: str) -> str:
    no_mentions = re.sub(r"<at>.*?</at>", "", raw or "", flags=re.I | re.S)
    return " ".join(html.unescape(no_mentions).split())


def parse_standup(text: str) -> dict[str, str | None]:
    """Pull Done, Doing and Blocked out of a reply. Anything unlabelled stays in the raw text only."""
    out: dict[str, str | None] = {"done": None, "doing": None, "blocked": None}
    marks = list(_LABELS.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m.group(1).lower()] = text[m.end() : end].strip(" .,;") or None
    return out


def find_member(session: Session, sender: dict) -> Member | None:
    ids = {v for v in (sender.get("aadObjectId"), sender.get("id")) if v}
    if not ids:
        return None
    return session.scalar(select(Member).where(Member.teams_user_id.in_(ids), Member.active.is_(True)))


def project_for(session: Session, config: AppConfig, member: Member) -> tuple[Project, ProjectCfg] | None:
    for cfg in config.projects:
        if any(m.github_login == member.github_login for m in cfg.members):
            project = session.scalar(select(Project).where(Project.name == cfg.name))
            if project is not None:
                return project, cfg
    return None


def remember_conversation(session: Session, member: Member, activity: dict, now: datetime) -> None:
    row = session.scalar(select(TeamsConversation).where(TeamsConversation.member_id == member.id))
    if row is None:
        row = TeamsConversation(member_id=member.id)
        session.add(row)
    row.service_url = activity["serviceUrl"]
    row.conversation_id = activity["conversation"]["id"]
    row.updated_at = now


def _ack(session: Session, member: Member, number: int | None, now: datetime) -> str:
    query = select(Nudge).where(Nudge.member_id == member.id, Nudge.status.in_(("queued", "sent")))
    if number is not None:
        query = query.where(Nudge.id == number)
    nudges = session.scalars(query).all()
    for n in nudges:
        n.status, n.acked_at = "acked", now
    if not nudges:
        return "There is nothing waiting for acknowledgement. Thank you."
    word = "item" if len(nudges) == 1 else "items"
    return f"Thank you, we have noted {len(nudges)} {word} as seen."


def _standup_reply(
    session, member: Member, project: Project, cfg: ProjectCfg, text: str, now: datetime
) -> str | None:
    today = now.astimezone(ZoneInfo(cfg.timezone)).date()
    prompted = session.scalar(
        select(StandupPrompt.id).where(
            StandupPrompt.member_id == member.id, StandupPrompt.standup_date == today
        )
    )
    if not prompted:
        return None
    parsed = parse_standup(text)
    row = session.scalar(
        select(StandupResponse).where(
            StandupResponse.member_id == member.id, StandupResponse.standup_date == today
        )
    )
    updated = row is not None
    if row is None:
        row = StandupResponse(project_id=project.id, member_id=member.id, standup_date=today)
        session.add(row)
    row.raw_text, row.created_at = text, now
    row.done, row.doing, row.blocked = parsed["done"], parsed["doing"], parsed["blocked"]
    msg = (
        "Thank you, we have updated your standup note."
        if updated
        else "Thank you, we have your standup note."
    )
    if not any(parsed.values()):
        msg += " Next time please use three lines starting Done, Doing and Blocked so the team summary can use it."
    return msg


def handle_activity(session: Session, config: AppConfig, activity: dict, now: datetime) -> list[str]:
    """Process one verified activity and return the reply texts to send back."""
    kind = activity.get("type")
    member = find_member(session, activity.get("from") or {})
    if kind in ("conversationUpdate", "installationUpdate"):
        added = kind == "installationUpdate" and activity.get("action") == "add"
        added = added or (kind == "conversationUpdate" and activity.get("membersAdded"))
        if member is None or not added:
            return []
        remember_conversation(session, member, activity, now)
        session.commit()
        first = member.name.split()[0]
        return [
            f"Hi {first}, we are NxSprint, the scrum assistant for SRIA. We will message you here. {HELP}"
        ]
    if kind != "message":
        return []
    if member is None:
        return [
            "We do not recognise this Teams account as a member of a project, so we cannot act on messages yet."
        ]

    remember_conversation(session, member, activity, now)
    text = clean_text(activity.get("text", ""))
    reply: str | None
    if m := _ACK.match(text):
        reply = _ack(session, member, int(m.group(1)) if m.group(1) else None, now)
    elif text.lower() in ("help", "?", ""):
        reply = HELP
    else:
        found = project_for(session, config, member)
        reply = _standup_reply(session, member, *found, text, now) if found else None
        reply = reply or f"We have no standup open for you right now. {HELP}"
    session.commit()
    return [reply]
