from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import fail_if_any, get_db, guarded, require_auth
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
    results: list = []
    for cfg in request.app.state.config.projects:
        with guarded(db, cfg.name, results):
            try:
                data = client.fetch_project(cfg.github.org, cfg.github.project_number, cfg.fields)
            except GitHubError as exc:
                results.append({"project": cfg.name, "error": str(exc), "failed": True})
                continue
            results.append(sync_project(db, cfg, data).__dict__)
    fail_if_any(results, status=502)
    return {"projects": results}
