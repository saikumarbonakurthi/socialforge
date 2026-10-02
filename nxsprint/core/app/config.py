"""Project config (config/projects.yaml), section 5.

Everything that can message a human is required with no default (section 17).
Validation failure raises at startup.
"""

from datetime import date, time
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.settings import Mode

RULE_IDS = (
    "STALE_IN_PROGRESS",
    "UNASSIGNED_IN_SPRINT",
    "NO_ESTIMATE",
    "OVERLOADED_MEMBER",
    "SPRINT_AT_RISK",
    "PR_WAITING_REVIEW",
    "BLOCKED_LABEL_AGING",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_tz(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone {value!r}") from exc
    return value


class GitHubRef(_Strict):
    org: str
    repo: str
    project_number: int = Field(gt=0)


class FieldMappings(_Strict):
    """Names of the Projects v2 fields. Must match the board exactly."""

    status: str
    estimate: str
    sprint: str
    priority: str


class StatusNames(_Strict):
    """Board column names that rules depend on."""

    in_progress: str
    done: str


class Role(StrEnum):
    MEMBER = "member"
    LEAD = "lead"


class MemberCfg(_Strict):
    name: str
    github_login: str
    teams_user_id: str
    whatsapp_number: str | None = None
    role: Role
    timezone: str
    capacity_points: float = Field(gt=0)
    active: bool = True

    _tz = field_validator("timezone")(_check_tz)


class Window(_Strict):
    start: time
    end: time


class Cooldowns(_Strict):
    """Hours before the same nudge for the same issue may be sent again."""

    hours: dict[str, int]

    @field_validator("hours")
    @classmethod
    def _all_rules(cls, v: dict[str, int]) -> dict[str, int]:
        missing, unknown = set(RULE_IDS) - set(v), set(v) - set(RULE_IDS)
        if missing or unknown:
            raise ValueError(
                f"cooldowns need exactly the rule ids; "
                f"missing={sorted(missing)} unknown={sorted(unknown)}"
            )
        if any(h <= 0 for h in v.values()):
            raise ValueError("cooldown hours must be positive")
        return v


class Thresholds(_Strict):
    stale_no_activity_working_days: int = Field(gt=0)
    stale_status_unchanged_working_days: int = Field(gt=0)
    pr_review_wait_hours: int = Field(gt=0)
    pr_approved_unmerged_working_days: int = Field(gt=0)
    blocked_label_working_days: int = Field(gt=0)
    max_parallel_in_progress: int = Field(gt=0)
    sprint_risk_no_done_working_days: int = Field(gt=0)
    blocked_label: str


class Escalation(_Strict):
    ack_hours_before_channel: int = Field(gt=0)
    ack_hours_before_lead: int = Field(gt=0)


class ChannelRouting(_Strict):
    # Names of env vars holding webhook URLs. The URLs themselves are secrets.
    team_webhook_env: str
    dm_webhook_env: str
    owner_report_target: str


class ProjectCfg(_Strict):
    name: str
    github: GitHubRef
    fields: FieldMappings
    statuses: StatusNames
    sprint_length_days: int = Field(gt=0)
    timezone: str
    working_days: list[int]  # ISO weekday, 1 = Monday
    working_hours: Window
    quiet_hours: Window
    holidays: list[date]  # explicit, may be empty
    standup_time: time
    members: list[MemberCfg] = Field(min_length=1)
    cooldowns: Cooldowns
    thresholds: Thresholds
    escalation: Escalation
    channels: ChannelRouting

    _tz = field_validator("timezone")(_check_tz)

    @field_validator("working_days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if not v or any(d < 1 or d > 7 for d in v) or len(set(v)) != len(v):
            raise ValueError("working_days must be unique ISO weekdays 1 to 7")
        return v

    @model_validator(mode="after")
    def _one_lead(self) -> "ProjectCfg":
        logins = [m.github_login for m in self.members]
        if len(set(logins)) != len(logins):
            raise ValueError("duplicate github_login in members")
        if sum(1 for m in self.members if m.role is Role.LEAD) != 1:
            raise ValueError("exactly one member must have role 'lead'")
        return self


class AppConfig(_Strict):
    # True means sample values. Refused in live mode so placeholders never message a human.
    placeholder: bool = False
    projects: list[ProjectCfg] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_names(self) -> "AppConfig":
        names = [p.name for p in self.projects]
        if len(set(names)) != len(names):
            raise ValueError("duplicate project name")
        return self


class ConfigError(RuntimeError):
    pass


def load_config(path: str | Path, mode: Mode) -> AppConfig:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(
            f"config file not found: {p}. "
            "Copy config/projects.example.yaml to config/projects.yaml and fill it in."
        )
    try:
        cfg = AppConfig.model_validate(yaml.safe_load(p.read_text()) or {})
    except Exception as exc:
        raise ConfigError(f"invalid config {p}: {exc}") from exc
    if cfg.placeholder and mode is Mode.LIVE:
        raise ConfigError("config is marked placeholder: true; refusing to run in live mode")
    return cfg
