import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.api import health, jobs, nudges, webhooks
from app.config import load_config
from app.db import make_engine, make_session_factory
from app.logging import configure_logging, request_id_var
from app.settings import Settings, get_settings

log = logging.getLogger("nxsprint")


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        cfg_settings = settings or get_settings()
        app.state.settings = cfg_settings
        # Fails loudly on bad config (section 5).
        app.state.config = load_config(cfg_settings.config_path, cfg_settings.mode)
        engine = make_engine(cfg_settings.database_url)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        log.info("started mode=%s projects=%d", cfg_settings.mode.value, len(app.state.config.projects))
        yield
        engine.dispose()

    app = FastAPI(title="NxSprint core", lifespan=lifespan)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request_id_var.set(rid)
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        log.info(
            "%s %s %d %dms",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - start) * 1000,
        )
        return response

    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(nudges.router)
    app.include_router(webhooks.router)
    return app


app = create_app()
