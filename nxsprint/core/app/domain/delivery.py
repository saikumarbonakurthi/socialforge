"""Outbox delivery: lease live rows for n8n, record the outcome. Never posts anything itself."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import AppConfig, ConfigError, ProjectCfg
from app.domain.channels import BOT_CHANNEL, WEBHOOK_CHANNELS, webhook_env_for
from app.domain.sync import utc
from app.models import Nudge, Outbox, Project
from app.settings import Mode

MAX_ATTEMPTS = 5  # then the row is dead until someone looks at it (dead letter, Phase 8)
LEASE = timedelta(minutes=10)  # a leased row is handed out again if nobody reports back in this time


@dataclass(frozen=True)
class DeliveryItem:
    id: int
    webhook_url: str
    payload: dict


def status_of(row: Outbox, now: datetime) -> str:
    if row.mode != "live":
        return "dry_run"
    if row.delivered_at is not None:
        return "delivered"
    if row.attempts >= MAX_ATTEMPTS and (row.leased_at is None or utc(row.leased_at) < now - LEASE):
        return "dead"
    if row.leased_at is not None and utc(row.leased_at) >= now - LEASE:
        return "leased"
    return "pending"


def check_live_ready(config: AppConfig, env: Mapping[str, str]) -> None:
    """In live mode every project's DM webhook must be configured, and must be https."""
    for p in config.projects:
        name = p.channels.dm_webhook_env
        url = env.get(name, "")
        if not url.startswith("https://"):
            raise ConfigError(f"live mode needs env var {name} (project {p.name}) set to an https:// URL")


def _cfg_for(row: Outbox, projects: dict[int, ProjectCfg], single: ProjectCfg | None) -> ProjectCfg | None:
    return projects.get(row.project_id) if row.project_id is not None else single


def _lease_rows(session: Session, channels: frozenset[str], now: datetime, limit: int) -> list[Outbox]:
    rows = session.scalars(
        select(Outbox)
        .where(
            Outbox.mode == "live",
            Outbox.channel.in_(channels),
            Outbox.delivered_at.is_(None),
            Outbox.attempts < MAX_ATTEMPTS,
            or_(Outbox.leased_at.is_(None), Outbox.leased_at < now - LEASE),
        )
        .order_by(Outbox.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for row in rows:
        row.leased_at = now
        row.attempts += 1
    return list(rows)


def lease_pending(
    session: Session,
    config: AppConfig,
    mode: Mode,
    env: Mapping[str, str],
    redirect_target: str | None,
    now: datetime,
    limit: int,
) -> list[DeliveryItem]:
    """Hand out live webhook rows and mark them leased. Empty unless the app itself is live."""
    if mode is not Mode.LIVE:
        return []  # a dry_run app never delivers, even if old live rows exist
    by_name = {p.name: p for p in config.projects}
    projects = {r.id: by_name[r.name] for r in session.scalars(select(Project)) if r.name in by_name}
    single = config.projects[0] if len(config.projects) == 1 else None

    items = []
    for row in _lease_rows(session, WEBHOOK_CHANNELS, now, limit):
        cfg = _cfg_for(row, projects, single)
        url = env.get(webhook_env_for(cfg, row.channel), "") if cfg else ""
        if not url.startswith("https://"):
            row.leased_at = None
            row.last_error = "no webhook configured for this row's project"
            continue
        target, text = row.target, row.body
        if redirect_target:
            target = redirect_target
            text = f"Test redirect, this was meant for {row.target}. {row.body}"
        items.append(DeliveryItem(row.id, url, {"channel": row.channel, "target": target, "text": text}))
    session.commit()
    return items


def lease_bot_rows(session: Session, mode: Mode, now: datetime, limit: int) -> list[Outbox]:
    """Live rows that the bot (not n8n) must send. Same lease and retry rules as webhook rows."""
    if mode is not Mode.LIVE:
        return []
    rows = _lease_rows(session, frozenset({BOT_CHANNEL}), now, limit)
    session.commit()
    return rows


class DeliveryError(ValueError):
    pass


def _live_row(session: Session, outbox_id: int) -> Outbox:
    row = session.get(Outbox, outbox_id)
    if row is None:
        raise LookupError("outbox row not found")
    if row.mode != "live":
        raise DeliveryError("dry_run rows are never delivered, so they cannot be marked")
    return row


def mark_sent(session: Session, outbox_id: int, now: datetime) -> Outbox:
    row = _live_row(session, outbox_id)
    if row.delivered_at is None:
        row.delivered_at, row.last_error = now, None
        nudge = session.get(Nudge, row.nudge_id) if row.nudge_id else None
        if nudge is not None and nudge.status == "queued":
            nudge.status, nudge.sent_at = "sent", now
        session.commit()
    return row


def mark_failed(session: Session, outbox_id: int, error: str) -> Outbox:
    row = _live_row(session, outbox_id)
    if row.delivered_at is None:
        row.leased_at = None  # eligible again straight away, until MAX_ATTEMPTS
        row.last_error = error[:500]
        session.commit()
    return row
