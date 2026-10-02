import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.api import ceremonies, health, jobs, nudges, teams, webhooks
from app.config import load_config
from app.db import make_engine, make_session_factory
from app.domain.delivery import check_live_ready
from app.domain.escalation import check_whatsapp_ready
from app.integrations.teams_bot import BotClient, default_key_resolver
from app.integrations.whatsapp import WhatsAppClient
from app.llm.client import AnthropicLLM
from app.llm.phraser import Phraser
from app.logging import configure_logging, request_id_var
from app.settings import Mode, Settings, get_settings

log = logging.getLogger("nxsprint")


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        cfg_settings = settings or get_settings()
        app.state.settings = cfg_settings
        # Fails loudly on bad config (section 5).
        app.state.config = load_config(cfg_settings.config_path, cfg_settings.mode)
        if cfg_settings.mode is Mode.LIVE:
            check_live_ready(app.state.config, os.environ)  # fail loudly before anything can be sent
        if cfg_settings.whatsapp_enabled:
            check_whatsapp_ready(app.state.config)  # fail loudly: no half configured WhatsApp
            app.state.whatsapp = WhatsAppClient(
                cfg_settings.whatsapp_token,
                cfg_settings.whatsapp_phone_number_id,
                cfg_settings.whatsapp_api_version,
            )
        # Teams bot (two way). Off unless the Azure registration is configured.
        if cfg_settings.bot_enabled:
            app.state.bot = BotClient(
                cfg_settings.bot_app_id, cfg_settings.bot_app_password, cfg_settings.bot_tenant_id
            )
            app.state.bot_keys = default_key_resolver()
        # Claude wording is off unless NXSPRINT_MODEL is set (Settings then demands a ceiling and prices).
        app.state.phraser = (
            Phraser(
                AnthropicLLM(),
                cfg_settings.llm_model,
                cfg_settings.max_daily_usd,
                cfg_settings.price_input_per_mtok,
                cfg_settings.price_output_per_mtok,
            )
            if cfg_settings.llm_model
            else None
        )
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
    app.include_router(teams.webhook_router)
    app.include_router(teams.jobs_router)
    app.include_router(ceremonies.jobs_router)
    app.include_router(ceremonies.view_router)
    return app


app = create_app()
