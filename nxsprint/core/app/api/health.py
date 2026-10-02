from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.api.deps import get_db

router = APIRouter()


@router.get("/health")
def health(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
        response.status_code = 503
    return {
        "status": "ok" if db_ok else "degraded",
        "version": __version__,
        "mode": request.app.state.settings.mode.value,
        "db": db_ok,
        "projects": [p.name for p in request.app.state.config.projects],
    }
