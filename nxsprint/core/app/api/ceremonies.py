from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import clock, configured_projects, get_db, require_auth, use_bot
from app.domain import ceremonies as c
from app.domain.board import load_board
from app.models import Project

jobs_router = APIRouter(prefix="/jobs", dependencies=[Depends(require_auth)])
view_router = APIRouter(dependencies=[Depends(require_auth)])


def _run(request: Request, db: Session, fn, *, bot: bool) -> dict:
    out = []
    mode = request.app.state.settings.mode
    for cfg, project in configured_projects(request, db):
        if project is None:
            out.append({"project": cfg.name, "error": "not synced yet, run /jobs/sync first"})
            continue
        args = (db, project, cfg, mode, clock(request)) + ((use_bot(request),) if bot else ())
        why_not = fn(*args)
        out.append({"project": cfg.name, "queued": why_not is None, "why_not": why_not})
    return {"mode": mode.value, "projects": out}


@jobs_router.post("/planning_prep")
def planning_prep_job(request: Request, db: Session = Depends(get_db)) -> dict:
    return _run(request, db, c.run_planning_prep, bot=True)


@jobs_router.post("/retro_prep")
def retro_prep_job(request: Request, db: Session = Depends(get_db)) -> dict:
    return _run(request, db, c.run_retro_prep, bot=True)


@jobs_router.post("/weekly_report")
def weekly_report_job(request: Request, db: Session = Depends(get_db)) -> dict:
    return _run(request, db, c.run_weekly_report, bot=False)


def _for_view(request: Request, db: Session, project_id: int):
    project = db.get(Project, project_id)
    cfg = next((x for x in request.app.state.config.projects if project and x.name == project.name), None)
    if project is None or cfg is None:
        raise HTTPException(404, "project not found")
    return project, cfg


@view_router.get("/projects/{project_id}/planning")
def view_planning(project_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    """The planning proposal as it would be sent, without sending or recording anything."""
    project, cfg = _for_view(request, db, project_id)
    plan = c.build_planning(load_board(db, project, cfg, clock(request)))
    return {"text": c.render_planning(plan, cfg) if plan else "No active sprint."}


@view_router.get("/projects/{project_id}/retro")
def view_retro(project_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    project, cfg = _for_view(request, db, project_id)
    retro = c.build_retro(db, project, cfg, clock(request))
    return {"text": c.render_retro(retro, cfg) if retro else "No active sprint."}


@view_router.get("/projects/{project_id}/weekly")
def view_weekly(project_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    project, cfg = _for_view(request, db, project_id)
    return {"text": c.build_weekly(db, project, cfg, clock(request))}
