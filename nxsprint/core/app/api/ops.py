"""Operations: status, metrics, dead letters, retention. Everything needs the bearer secret."""

from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import __version__
from app.api.deps import clock, get_db, require_auth
from app.domain.deadletters import dismiss_row, process_dead_letters, prune, retry_row
from app.domain.delivery import STATUSES, DeliveryError, status_clause, status_of
from app.domain.sync import utc
from app.llm.phraser import spent_today
from app.models import Event, Nudge, Outbox, Project

router = APIRouter(dependencies=[Depends(require_auth)])


def collect_stats(request: Request, db: Session) -> dict:
    state, now = request.app.state, clock(request)
    settings = state.settings
    head = {
        "version": __version__,
        "mode": settings.mode.value,
        "features": {
            "claude_wording": bool(settings.llm_model),
            "teams_bot": settings.bot_enabled,
            "whatsapp": settings.whatsapp_enabled,
            "delivery_redirect": bool(settings.delivery_redirect_target),
        },
    }
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db.rollback()  # report the outage instead of failing, so nxsprint_db_up can drop to 0 and alert
        return {
            **head,
            "db": False,
            "projects": [],
            "outbox": {},
            "nudges": {},
            "llm": {"spent_today_usd": 0.0, "ceiling_usd": settings.max_daily_usd},
        }

    projects = []
    for cfg in state.config.projects:
        project = db.scalar(select(Project).where(Project.name == cfg.name))
        last = None
        if project is not None:
            last = db.scalar(
                select(func.max(Event.created_at)).where(Event.type == "sync", Event.project_id == project.id)
            )
        projects.append(
            {
                "name": cfg.name,
                "last_sync_at": utc(last).isoformat() if last else None,
                "seconds_since_sync": int((now - utc(last)).total_seconds()) if last else None,
            }
        )

    # One COUNT per status, in SQL, so a scrape stays cheap however many rows there are.
    outbox = {
        st: db.scalar(select(func.count(Outbox.id)).where(status_clause(st, now))) or 0 for st in STATUSES
    }
    nudges = dict(db.execute(select(Nudge.status, func.count(Nudge.id)).group_by(Nudge.status)).all())
    return {
        **head,
        "db": True,
        "projects": projects,
        "outbox": outbox,
        "nudges": nudges,
        "llm": {"spent_today_usd": round(spent_today(db, now), 6), "ceiling_usd": settings.max_daily_usd},
    }


@router.get("/status")
def status(request: Request, db: Session = Depends(get_db)) -> dict:
    return collect_stats(request, db)


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(request: Request, db: Session = Depends(get_db)) -> str:
    """Prometheus text format. Scrape with a bearer token."""
    s = collect_stats(request, db)
    lines = [
        "# HELP nxsprint_info Build and mode.",
        "# TYPE nxsprint_info gauge",
        f'nxsprint_info{{version="{s["version"]}",mode="{s["mode"]}"}} 1',
        "# HELP nxsprint_db_up 1 if the database answers.",
        "# TYPE nxsprint_db_up gauge",
        f"nxsprint_db_up {int(s['db'])}",
        "# HELP nxsprint_outbox_rows Outbox rows by delivery status (live rows that are open, plus totals).",
        "# TYPE nxsprint_outbox_rows gauge",
    ]
    for status_name, n in sorted(s["outbox"].items()):
        lines.append(f'nxsprint_outbox_rows{{status="{status_name}"}} {n}')
    lines += ["# HELP nxsprint_nudges Nudges by status.", "# TYPE nxsprint_nudges gauge"]
    for status_name, n in sorted(s["nudges"].items()):
        lines.append(f'nxsprint_nudges{{status="{status_name}"}} {n}')
    lines += [
        "# HELP nxsprint_last_sync_age_seconds Seconds since the last successful sync, per project.",
        "# TYPE nxsprint_last_sync_age_seconds gauge",
    ]
    for p in s["projects"]:
        if p["seconds_since_sync"] is not None:
            lines.append(
                f'nxsprint_last_sync_age_seconds{{project="{_label(p["name"])}"}} {p["seconds_since_sync"]}'
            )
    lines += [
        "# HELP nxsprint_llm_spend_usd_today Estimated Claude spend today (UTC).",
        "# TYPE nxsprint_llm_spend_usd_today gauge",
        f"nxsprint_llm_spend_usd_today {s['llm']['spent_today_usd']}",
        "# HELP nxsprint_http_requests_total Requests handled since start, by status class.",
        "# TYPE nxsprint_http_requests_total counter",
    ]
    for cls, n in sorted(getattr(request.app.state, "http_counts", Counter()).items()):
        lines.append(f'nxsprint_http_requests_total{{status_class="{cls}"}} {n}')
    return "\n".join(lines) + "\n"


@router.post("/jobs/dead_letters")
def dead_letters_job(request: Request, db: Session = Depends(get_db)) -> dict:
    """Fail bot messages over to the webhook and tell the owner once about anything that gave up."""
    state = request.app.state
    r = process_dead_letters(db, state.config, state.settings.mode, clock(request))
    return r.__dict__


@router.post("/jobs/prune")
def prune_job(request: Request, db: Session = Depends(get_db)) -> dict:
    days = request.app.state.settings.retention_days
    if days is None:
        return {"enabled": False}
    return {"enabled": True, "retention_days": days, **prune(db, days, clock(request))}


@router.post("/outbox/{outbox_id}/retry")
def retry(outbox_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    row = _call(retry_row, db, outbox_id, clock(request))
    return {"id": row.id, "status": status_of(row, clock(request)), "attempts": row.attempts}


@router.post("/outbox/{outbox_id}/dismiss")
def dismiss(outbox_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    row = _call(dismiss_row, db, outbox_id, clock(request))
    return {"id": row.id, "status": status_of(row, clock(request))}


def _call(fn, *args):
    try:
        return fn(*args)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except DeliveryError as exc:
        raise HTTPException(409, str(exc)) from exc
