import os
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_auth
from app.domain.board import load_board
from app.domain.delivery import DeliveryError, lease_pending, mark_failed, mark_sent, status_of
from app.domain.nudges import run_nudges
from app.domain.rules import run_rules
from app.domain.sync import utc
from app.models import Nudge, Outbox, Project

router = APIRouter(dependencies=[Depends(require_auth)])


def _project(db: Session, name: str) -> Project | None:
    return db.scalar(select(Project).where(Project.name == name))


def _ts(dt: datetime | None) -> str | None:
    """SQLite hands back naive datetimes; always answer in UTC so clients see one format."""
    return utc(dt).isoformat() if dt else None


def _nudge(n: Nudge) -> dict:
    return {
        "id": n.id,
        "rule": n.rule,
        "issue_node_id": n.issue_node_id,
        "member_id": n.member_id,
        "channel": n.channel,
        "status": n.status,
        "message": n.message,
        "created_at": _ts(n.created_at),
        "acked_at": _ts(n.acked_at),
    }


@router.post("/jobs/nudges")
def run_nudge_job(request: Request, db: Session = Depends(get_db)) -> dict:
    """Detect, apply cooldowns and hours, queue nudges. Writes to our own tables only."""
    state, out = request.app.state, []
    now = getattr(state, "clock", lambda: datetime.now(UTC))()  # tests inject a fixed clock
    for cfg in state.config.projects:
        project = _project(db, cfg.name)
        if project is None:
            out.append({"project": cfg.name, "error": "not synced yet, run /jobs/sync first"})
            continue
        r = run_nudges(db, project, cfg, state.settings.mode, now, getattr(state, "phraser", None))
        out.append(
            {
                "project": cfg.name,
                "findings": r.findings,
                "created": [_nudge(n) for n in r.created],
                "deferred_outside_hours": r.deferred_outside_hours,
                "skipped_cooldown": r.skipped_cooldown,
                "skipped_no_recipient": r.skipped_no_recipient,
                "phrasing": dict(r.phrasing),
            }
        )
    return {"mode": state.settings.mode.value, "projects": out}


@router.get("/outbox")
def get_outbox(request: Request, limit: int = 100, db: Session = Depends(get_db)) -> list[dict]:
    now = getattr(request.app.state, "clock", lambda: datetime.now(UTC))()
    rows = db.scalars(select(Outbox).order_by(Outbox.id.desc()).limit(min(limit, 500)))
    return [
        {
            "id": o.id,
            "channel": o.channel,
            "target": o.target,
            "body": o.body,
            "mode": o.mode,
            "status": status_of(o, now),
            "attempts": o.attempts,
            "last_error": o.last_error,
            "created_at": _ts(o.created_at),
            "delivered_at": _ts(o.delivered_at),
        }
        for o in rows
    ]


@router.get("/outbox/pending")
def outbox_pending(request: Request, limit: int = 20, db: Session = Depends(get_db)) -> dict:
    """For n8n. Leases live rows and returns where to post them. Always empty in dry_run."""
    state = request.app.state
    clock = getattr(state, "clock", lambda: datetime.now(UTC))
    items = lease_pending(
        db,
        state.config,
        state.settings.mode,
        getattr(state, "env", os.environ),
        state.settings.delivery_redirect_target,
        clock(),
        min(limit, 100),
    )
    return {
        "mode": state.settings.mode.value,
        "items": [{"id": i.id, "webhook_url": i.webhook_url, "payload": i.payload} for i in items],
    }


class FailedBody(BaseModel):
    error: str = "delivery failed"


def _outcome(fn, *args) -> dict:
    try:
        row = fn(*args)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except DeliveryError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": row.id, "delivered": row.delivered_at is not None, "attempts": row.attempts}


@router.post("/outbox/{outbox_id}/sent")
def outbox_sent(outbox_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    clock = getattr(request.app.state, "clock", lambda: datetime.now(UTC))
    return _outcome(mark_sent, db, outbox_id, clock())


@router.post("/outbox/{outbox_id}/failed")
def outbox_failed(outbox_id: int, body: FailedBody, db: Session = Depends(get_db)) -> dict:
    return _outcome(mark_failed, db, outbox_id, body.error)


@router.get("/nudges")
def get_nudges(status: str | None = None, limit: int = 100, db: Session = Depends(get_db)) -> list[dict]:
    q = select(Nudge).order_by(Nudge.id.desc()).limit(min(limit, 500))
    if status:
        q = q.where(Nudge.status == status)
    return [_nudge(n) for n in db.scalars(q)]


@router.post("/nudges/{nudge_id}/ack")
def ack_nudge(nudge_id: int, db: Session = Depends(get_db)) -> dict:
    n = db.get(Nudge, nudge_id)
    if n is None:
        raise HTTPException(404, "nudge not found")
    if n.status != "acked":
        n.status, n.acked_at = "acked", datetime.now(UTC)
        db.commit()
    return _nudge(n)


@router.get("/projects/{project_id}/risk")
def project_risk(project_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    """Current findings for a project. Read only: creates no nudges."""
    project = db.get(Project, project_id)
    cfg = next((c for c in request.app.state.config.projects if project and c.name == project.name), None)
    if project is None or cfg is None:
        raise HTTPException(404, "project not found")
    findings = run_rules(load_board(db, project, cfg, datetime.now(UTC)))
    return {
        "project": project.name,
        "findings": [
            {
                "rule": f.rule_id,
                "severity": f.severity.value,
                "member": f.member,
                "issue_node_id": f.issue_node_id,
                "title": f.title,
                "url": f.url,
                "evidence": f.evidence,
            }
            for f in findings
        ],
    }
