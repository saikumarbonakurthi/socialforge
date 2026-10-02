import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import clock as _now
from app.api.deps import configured_projects as _projects
from app.api.deps import get_db, require_auth
from app.api.deps import use_bot as _use_bot
from app.domain.bot_inbound import handle_activity
from app.domain.delivery import lease_bot_rows, mark_failed, mark_sent
from app.domain.standup import post_standup_summary, run_standup_prompts
from app.integrations.teams_bot import BotAuthError, verify_activity
from app.models import Member, Outbox, TeamsConversation
from app.settings import Mode

log = logging.getLogger("nxsprint.teams")
webhook_router = APIRouter(prefix="/webhooks")
jobs_router = APIRouter(prefix="/jobs", dependencies=[Depends(require_auth)])


@webhook_router.post("/teams")
async def teams_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    """Bot Framework messaging endpoint. Authenticated by Microsoft's signed token, not our bearer secret."""
    state = request.app.state
    if not state.settings.bot_enabled:
        raise HTTPException(503, "the Teams bot is not configured")
    try:
        activity = await request.json()
        service_url, conversation = activity["serviceUrl"], activity["conversation"]["id"]
    except Exception as exc:
        raise HTTPException(400, "not a Bot Framework activity") from exc
    try:
        verify_activity(
            request.headers.get("authorization"), state.settings.bot_app_id, service_url, state.bot_keys
        )
    except BotAuthError as exc:
        log.warning("rejected teams activity: %s", exc)
        raise HTTPException(401, "invalid bot token") from exc

    replies = handle_activity(db, state.config, activity, _now(request))
    for text in replies:
        if state.settings.mode is Mode.LIVE:
            try:
                state.bot.send_text(service_url, conversation, text, activity.get("id"))
            except Exception as exc:  # never fail the webhook, Teams would just redeliver
                log.warning("could not send reply: %s", type(exc).__name__)
        else:
            db.add(
                Outbox(
                    channel="teams_reply",
                    target=conversation,
                    body=text,
                    mode="dry_run",
                    created_at=_now(request),
                )
            )
    db.commit()
    return {"handled": True, "replies": len(replies)}


@jobs_router.post("/standup")
def standup_job(request: Request, db: Session = Depends(get_db)) -> dict:
    out = []
    for cfg, project in _projects(request, db):
        if project is None:
            out.append({"project": cfg.name, "error": "not synced yet, run /jobs/sync first"})
            continue
        r = run_standup_prompts(
            db, project, cfg, request.app.state.settings.mode, _now(request), _use_bot(request)
        )
        out.append({"project": cfg.name, **r.__dict__})
    return {"projects": out}


@jobs_router.post("/standup_summary")
def standup_summary_job(request: Request, db: Session = Depends(get_db)) -> dict:
    out = []
    for cfg, project in _projects(request, db):
        if project is None:
            out.append({"project": cfg.name, "error": "not synced yet, run /jobs/sync first"})
            continue
        why_not = post_standup_summary(db, project, cfg, request.app.state.settings.mode, _now(request))
        out.append({"project": cfg.name, "posted": why_not is None, "why_not": why_not})
    return {"projects": out}


@jobs_router.post("/deliver_bot")
def deliver_bot_job(request: Request, db: Session = Depends(get_db)) -> dict:
    """Send leased live bot rows through the connector. Does nothing in dry_run."""
    state = request.app.state
    if not state.settings.bot_enabled:
        raise HTTPException(503, "the Teams bot is not configured")
    sent = failed = 0
    for row in lease_bot_rows(db, state.settings.mode, _now(request), 20):
        member = db.scalar(select(Member).where(Member.teams_user_id == row.target))
        conv = (
            db.scalar(select(TeamsConversation).where(TeamsConversation.member_id == member.id))
            if member
            else None
        )
        if conv is None:
            mark_failed(db, row.id, "no stored conversation for this member")
            failed += 1
            continue
        try:
            state.bot.send_text(conv.service_url, conv.conversation_id, row.body)
        except Exception as exc:
            mark_failed(db, row.id, f"{type(exc).__name__}: {str(exc)[:200]}")
            failed += 1
        else:
            mark_sent(db, row.id, _now(request))
            sent += 1
    return {"mode": state.settings.mode.value, "sent": sent, "failed": failed}
