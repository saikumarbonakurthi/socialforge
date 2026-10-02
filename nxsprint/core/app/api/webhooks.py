from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.integrations.github import verify_signature
from app.models import Event

router = APIRouter(prefix="/webhooks")


@router.post("/github", status_code=202)
async def github_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    """Authenticated by HMAC signature, not the bearer secret.

    Phase 1 only records the event. The 15 minute sync job picks up the change.
    """
    secret = request.app.state.settings.github_webhook_secret
    if not secret:
        raise HTTPException(503, "NXSPRINT_GITHUB_WEBHOOK_SECRET is not set")
    body = await request.body()
    if not verify_signature(secret, body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(401, "bad signature")
    payload = await request.json()
    kind = request.headers.get("x-github-event", "unknown")
    # Keep only identifiers, not the whole payload (comments can hold anything).
    db.add(
        Event(
            type=f"github.{kind}",
            payload_json={
                "action": payload.get("action"),
                "repository": (payload.get("repository") or {}).get("full_name"),
            },
        )
    )
    db.commit()
    return {"accepted": True}
