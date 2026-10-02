"""SQLAlchemy models, section 4 of the spec."""

from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "project"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    github_project_id: Mapped[str | None] = mapped_column(String(100))
    team_channel_ref: Mapped[str | None] = mapped_column(String(200))
    sprint_length_days: Mapped[int] = mapped_column(Integer)
    working_hours: Mapped[str] = mapped_column(String(50))
    timezone: Mapped[str] = mapped_column(String(64))


class Member(Base):
    __tablename__ = "member"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    github_login: Mapped[str] = mapped_column(String(100), unique=True)
    teams_user_id: Mapped[str | None] = mapped_column(String(200))
    whatsapp_number: Mapped[str | None] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(20))
    timezone: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Sprint(Base):
    __tablename__ = "sprint"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    start: Mapped[date] = mapped_column(Date)
    end: Mapped[date] = mapped_column(Date)
    goal: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))


class WorkItemSnapshot(Base):
    __tablename__ = "work_item_snapshot"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), index=True)
    issue_node_id: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(100))
    priority: Mapped[str | None] = mapped_column(String(100))
    assignee_login: Mapped[str | None] = mapped_column(String(100))
    estimate: Mapped[float | None] = mapped_column(Float)
    sprint_id: Mapped[int | None] = mapped_column(ForeignKey("sprint.id"))
    labels: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Event(Base):
    __tablename__ = "event"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(100), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"))
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Nudge(Base):
    __tablename__ = "nudge"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("member.id"), index=True)
    issue_node_id: Mapped[str] = mapped_column(String(100), index=True)
    rule: Mapped[str] = mapped_column(String(50))
    channel: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(Text)
    # queued | sent | acked | escalated | suppressed
    status: Mapped[str] = mapped_column(String(20), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"))
    nudge_id: Mapped[int | None] = mapped_column(ForeignKey("nudge.id"), index=True)
    channel: Mapped[str] = mapped_column(String(30))
    target: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Delivery tracking (Phase 4). Only live rows are ever leased for delivery.
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class Escalation(Base):
    __tablename__ = "escalation"
    id: Mapped[int] = mapped_column(primary_key=True)
    nudge_id: Mapped[int] = mapped_column(ForeignKey("nudge.id"), index=True)
    level: Mapped[int] = mapped_column(Integer)
    channel: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LlmCall(Base):
    __tablename__ = "llm_call"
    id: Mapped[int] = mapped_column(primary_key=True)
    purpose: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_estimate: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TeamsConversation(Base):
    """Where the bot can reach a member. Learned from a verified inbound activity, never typed in."""

    __tablename__ = "teams_conversation"
    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("member.id"), unique=True)
    service_url: Mapped[str] = mapped_column(Text)
    conversation_id: Mapped[str] = mapped_column(String(300))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class StandupPrompt(Base):
    __tablename__ = "standup_prompt"
    __table_args__ = (UniqueConstraint("member_id", "standup_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("member.id"))
    standup_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class StandupResponse(Base):
    __tablename__ = "standup_response"
    __table_args__ = (UniqueConstraint("member_id", "standup_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("member.id"))
    standup_date: Mapped[date] = mapped_column(Date)
    raw_text: Mapped[str] = mapped_column(Text)
    done: Mapped[str | None] = mapped_column(Text)
    doing: Mapped[str | None] = mapped_column(Text)
    blocked: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
