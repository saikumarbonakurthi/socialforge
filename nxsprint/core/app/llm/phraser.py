"""Phrase a nudge with Claude, or fall back to the deterministic template (rules 3 and 4)."""

import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import ProjectCfg
from app.domain.messages import render
from app.domain.rules import Finding
from app.llm.client import LLMClient
from app.llm.prompts import NUDGE_PROMPT, load_prompt
from app.llm.types import LLMResult
from app.llm.validate import LINK_TOKEN, validate_message
from app.models import Event, LlmCall, Outbox
from app.settings import Mode

log = logging.getLogger("nxsprint.llm")

MAX_TOKENS = 2048  # thinking models spend part of this before the short reply
PURPOSE = "nudge_phrasing"
# Anthropic's published cache pricing ratios relative to the input price.
CACHE_WRITE_MULT, CACHE_READ_MULT = 1.25, 0.1


@dataclass(frozen=True)
class Phrased:
    message: str
    source: str  # "llm" or "template"
    reason: str | None = None  # why we fell back


def estimate_cost(r: LLMResult, price_in: float, price_out: float) -> float:
    cost = (
        r.input_tokens * price_in
        + r.cache_creation_tokens * price_in * CACHE_WRITE_MULT
        + r.cache_read_tokens * price_in * CACHE_READ_MULT
        + r.output_tokens * price_out
    )
    return cost / 1_000_000


def _day_start(now: datetime) -> datetime:
    return datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)


def spent_today(session: Session, now: datetime) -> float:
    start = _day_start(now)
    return float(
        session.scalar(
            select(func.coalesce(func.sum(LlmCall.cost_estimate), 0.0)).where(LlmCall.created_at >= start)
        )
        or 0.0
    )


def _sanitise(f: Finding) -> Finding:
    """Rule 11: only titles, status, dates and first names go to the model. Not logins."""
    if f.evidence.get("previous_owner"):
        return replace(f, evidence={**f.evidence, "previous_owner": "the previous owner"})
    return f


class Phraser:
    def __init__(
        self,
        client: LLMClient,
        model: str,
        max_daily_usd: float,
        price_in_per_mtok: float,
        price_out_per_mtok: float,
    ):
        self.client, self.model = client, model
        self.max_daily_usd = max_daily_usd
        self.price_in, self.price_out = price_in_per_mtok, price_out_per_mtok

    def phrase(
        self,
        session: Session,
        *,
        project_id: int,
        cfg: ProjectCfg,
        mode: Mode,
        finding: Finding,
        first_name: str,
        now: datetime,
    ) -> Phrased:
        safe = _sanitise(finding)
        fallback = render(finding, first_name, cfg)
        draft = render(safe, first_name, cfg, link=LINK_TOKEN)
        payload = {
            "rule_id": finding.rule_id,
            "recipient_first_name": first_name,
            "issue_title": finding.title if finding.url else "",
            "has_link": bool(finding.url),
            "facts": safe.evidence,
            "draft": draft,
        }
        errors: list[str] = []
        for _attempt in range(2):  # one retry (rule 3)
            if spent_today(session, now) >= self.max_daily_usd:
                self._alert_owner_once(session, project_id, cfg, mode, now)
                return Phrased(fallback, "template", "budget")
            user = json.dumps({**payload, **({"previous_attempt_errors": errors} if errors else {})})
            try:
                result = self.client.complete(self.model, load_prompt(NUDGE_PROMPT), user, MAX_TOKENS)
            except Exception as exc:  # network, auth, rate limit: never block a nudge on the LLM
                log.warning("llm call failed: %s", type(exc).__name__)
                return Phrased(fallback, "template", f"api_error:{type(exc).__name__}")
            self._log_call(session, result, now)
            if result.stop_reason in ("refusal", "max_tokens") or result.parsed is None:
                return Phrased(fallback, "template", f"stop:{result.stop_reason}")
            errors = validate_message(result.parsed.message, safe, first_name, draft)
            if not errors:
                text = result.parsed.message
                return Phrased(text.replace(LINK_TOKEN, finding.url) if finding.url else text, "llm")
        return Phrased(fallback, "template", "validation:" + ",".join(errors))

    def _log_call(self, session: Session, r: LLMResult, now: datetime) -> None:
        session.add(
            LlmCall(
                purpose=PURPOSE,
                model=r.model,
                input_tokens=r.input_tokens + r.cache_read_tokens + r.cache_creation_tokens,
                output_tokens=r.output_tokens,
                cost_estimate=estimate_cost(r, self.price_in, self.price_out),
                latency_ms=r.latency_ms,
                created_at=now,
            )
        )
        session.flush()

    def _alert_owner_once(
        self, session: Session, project_id: int, cfg: ProjectCfg, mode: Mode, now: datetime
    ) -> None:
        already = session.scalar(
            select(Event.id).where(Event.type == "llm_budget_alert", Event.created_at >= _day_start(now))
        )
        if already:
            return
        body = (
            f"We have reached the daily limit of {self.max_daily_usd:g} USD for message wording. "
            "Until tomorrow we are sending the standard templates instead."
        )
        session.add(Event(type="llm_budget_alert", project_id=project_id, payload_json={}, created_at=now))
        session.add(
            Outbox(
                channel="owner_alert",
                target=cfg.channels.owner_report_target,
                body=body,
                mode=mode.value,
                created_at=now,
            )
        )
        session.flush()
