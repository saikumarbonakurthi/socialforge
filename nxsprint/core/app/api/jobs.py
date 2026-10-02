from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_auth
from app.domain.sync import sync_project
from app.integrations.github import GitHubClient, GitHubError

router = APIRouter(prefix="/jobs", dependencies=[Depends(require_auth)])


def default_client_factory(request: Request) -> GitHubClient:
    token = request.app.state.settings.github_token
    if not token:
        raise HTTPException(503, "NXSPRINT_GITHUB_TOKEN is not set")
    return GitHubClient(token)


@router.post("/sync")
def run_sync(request: Request, db: Session = Depends(get_db)) -> dict:
    """Read-only against GitHub, so it is allowed in dry_run too."""
    factory = getattr(request.app.state, "github_client_factory", default_client_factory)
    client = factory(request)
    results = []
    for cfg in request.app.state.config.projects:
        try:
            data = client.fetch_project(cfg.github.org, cfg.github.project_number, cfg.fields)
        except GitHubError as exc:
            raise HTTPException(502, f"{cfg.name}: {exc}") from exc
        r = sync_project(db, cfg, data)
        results.append(r.__dict__)
    return {"projects": results}
