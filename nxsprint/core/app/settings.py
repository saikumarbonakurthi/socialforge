"""Environment settings. Secrets live in env only (rule 5)."""

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Mode(StrEnum):
    DRY_RUN = "dry_run"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NXSPRINT_",
        # `make` targets run from core/ but `make env` writes .env one level up.
        env_file=(".env", "../.env"),
        # .env.example leaves optional values blank; blank must mean unset, not "".
        env_ignore_empty=True,
        extra="ignore",
    )

    mode: Mode = Mode.DRY_RUN  # rule 1: dry-run by default
    api_secret: str = Field(min_length=16)  # shared bearer secret, no default on purpose
    database_url: str = "sqlite:///./nxsprint.db"
    config_path: str = "../config/projects.yaml"
    # Read in Phase 3; never hardcode a model string (section 2).
    llm_model: str | None = Field(default=None, validation_alias="NXSPRINT_MODEL")
    max_daily_usd: float | None = None
    # No built in prices: the model is env driven, so its prices are too (USD per million tokens).
    price_input_per_mtok: float | None = Field(default=None, gt=0)
    price_output_per_mtok: float | None = Field(default=None, gt=0)
    # While set, live delivery sends every message to this target instead of the real person.
    delivery_redirect_target: str | None = None
    # Azure Bot registration (Phase 5). All three or none.
    bot_app_id: str | None = None
    bot_app_password: str | None = None
    bot_tenant_id: str | None = None
    # WhatsApp escalation (Phase 7). Off unless explicitly enabled, and then everything below is required.
    whatsapp_enabled: bool = False
    whatsapp_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_api_version: str | None = None  # e.g. v21.0, set it, Meta retires old versions
    # Delete finished outbox rows, log events (sync, budget alerts, webhooks) and llm_call rows older than this.
    # The once per sprint, week and day markers are never pruned. Unset means keep everything.
    retention_days: int | None = Field(default=None, ge=30)
    github_token: str | None = None  # read-only scope for sync (rule 2)
    github_webhook_secret: str | None = None

    @model_validator(mode="after")
    def _llm_needs_budget(self) -> "Settings":
        if self.llm_model and (
            self.max_daily_usd is None
            or self.price_input_per_mtok is None
            or self.price_output_per_mtok is None
        ):
            raise ValueError(
                "NXSPRINT_MODEL is set, so NXSPRINT_MAX_DAILY_USD, NXSPRINT_PRICE_INPUT_PER_MTOK and "
                "NXSPRINT_PRICE_OUTPUT_PER_MTOK are required (no spending without a ceiling)"
            )
        return self

    @model_validator(mode="after")
    def _whatsapp_needs_credentials(self) -> "Settings":
        needed = (self.whatsapp_token, self.whatsapp_phone_number_id, self.whatsapp_api_version)
        if self.whatsapp_enabled and not all(needed):
            raise ValueError(
                "NXSPRINT_WHATSAPP_ENABLED needs NXSPRINT_WHATSAPP_TOKEN, NXSPRINT_WHATSAPP_PHONE_NUMBER_ID "
                "and NXSPRINT_WHATSAPP_API_VERSION"
            )
        return self

    @property
    def bot_enabled(self) -> bool:
        return bool(self.bot_app_id)

    @model_validator(mode="after")
    def _bot_all_or_none(self) -> "Settings":
        parts = (self.bot_app_id, self.bot_app_password, self.bot_tenant_id)
        if any(parts) and not all(parts):
            raise ValueError(
                "NXSPRINT_BOT_APP_ID, NXSPRINT_BOT_APP_PASSWORD and NXSPRINT_BOT_TENANT_ID go together"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
