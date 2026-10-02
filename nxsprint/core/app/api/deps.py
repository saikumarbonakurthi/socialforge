import secrets
from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Project

_bearer = HTTPBearer(auto_error=False)


def require_auth(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    expected = request.app.state.settings.api_secret
    if creds is None or not secrets.compare_digest(creds.credentials, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def clock(request: Request) -> datetime:
    """The app clock, replaceable in tests."""
    return getattr(request.app.state, "clock", lambda: datetime.now(UTC))()


def use_bot(request: Request) -> bool:
    """Bot DMs only when the bot is configured and no test redirect is active (the redirect is webhook only)."""
    s = request.app.state.settings
    return s.bot_enabled and not s.delivery_redirect_target


def configured_projects(request: Request, db: Session):
    """Yield (config, stored project or None) for every configured project."""
    for cfg in request.app.state.config.projects:
        yield cfg, db.scalar(select(Project).where(Project.name == cfg.name))
