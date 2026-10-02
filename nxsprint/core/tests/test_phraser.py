import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import load_config
from app.db import make_engine
from app.domain.messages import render
from app.domain.rules import Finding, Severity
from app.llm.client import AnthropicLLM
from app.llm.phraser import Phraser, estimate_cost, spent_today
from app.llm.types import LLMResult, PhrasedMessage
from app.models import Base, Event, LlmCall, Outbox
from app.settings import Mode, Settings
from tests.helpers import NOW

URL = "https://github.com/sria-demo/demo-app/issues/1"
STALE = Finding("STALE_IN_PROGRESS", Severity.MEDIUM, "ravi-demo", "I_1", "Login page", URL,
                {"working_days_since_update": 3, "working_days_in_status": 4})  # fmt: skip
GOOD = "Hi Ravi, we noticed Login page has had no update for 3 working days. Could you share a quick note? {link}"


def result(text=GOOD, *, stop="end_turn", inp=1000, out=100, read=0, create=0, parsed=True) -> LLMResult:
    return LLMResult(
        PhrasedMessage(message=text) if parsed else None, stop, "m-test", inp, out, read, create, 12
    )


class FakeLLM:
    def __init__(self, *script):
        self.script, self.calls = list(script), []

    def complete(self, model, system, user, max_tokens):
        self.calls.append(
            {"model": model, "system": system, "user": json.loads(user), "max_tokens": max_tokens}
        )
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture
def cfg(config_file):
    return load_config(config_file, Mode.DRY_RUN).projects[0]


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def phraser(llm, ceiling=1.0):
    return Phraser(llm, "m-test", ceiling, 3.0, 15.0)


def run(p, session, cfg, f=STALE, now=NOW):
    return p.phrase(session, project_id=1, cfg=cfg, mode=Mode.DRY_RUN, finding=f, first_name="Ravi", now=now)


def test_estimate_cost_counts_cache_tokens_at_their_own_rates():
    r = result(inp=1000, out=200, read=5000, create=1000)
    assert estimate_cost(r, 3.0, 15.0) == pytest.approx(0.01125)


def test_success_substitutes_real_link_and_logs_cost(session, cfg):
    llm = FakeLLM(result(inp=1000, out=200))
    out = run(phraser(llm), session, cfg)
    assert out.source == "llm" and URL in out.message and "{link}" not in out.message
    row = session.scalars(select(LlmCall)).one()
    assert (row.purpose, row.model, row.input_tokens, row.output_tokens) == (
        "nudge_phrasing",
        "m-test",
        1000,
        200,
    )
    assert row.cost_estimate == pytest.approx((1000 * 3 + 200 * 15) / 1e6)
    assert llm.calls[0]["max_tokens"] == 2048


def test_invalid_then_valid_uses_the_single_retry_and_tells_the_model_why(session, cfg):
    bad = "Hi Ravi, Login page is stale for 9 working days. {link}"
    llm = FakeLLM(result(bad), result(GOOD))
    out = run(phraser(llm), session, cfg)
    assert out.source == "llm" and len(llm.calls) == 2
    assert "invented_number" in llm.calls[1]["user"]["previous_attempt_errors"]
    assert len(session.scalars(select(LlmCall)).all()) == 2  # failed attempts cost money too


def test_invalid_twice_falls_back_to_template(session, cfg):
    bad = "Hi Ravi, Login page is stale for 9 working days. {link}"
    llm = FakeLLM(result(bad), result(bad))
    out = run(phraser(llm), session, cfg)
    assert out.source == "template" and out.reason.startswith("validation:") and len(llm.calls) == 2
    assert out.message == render(STALE, "Ravi", cfg)


@pytest.mark.parametrize("step,reason", [
    (result(stop="refusal", parsed=False), "stop:refusal"),
    (result(stop="max_tokens", parsed=False), "stop:max_tokens"),
])  # fmt: skip
def test_refusal_and_truncation_fall_back_without_retry(session, cfg, step, reason):
    llm = FakeLLM(step)
    out = run(phraser(llm), session, cfg)
    assert (out.source, out.reason, len(llm.calls)) == ("template", reason, 1)


def test_api_error_never_blocks_a_nudge(session, cfg):
    out = run(phraser(FakeLLM(TimeoutError("down"))), session, cfg)
    assert out.source == "template" and out.reason == "api_error:TimeoutError"
    assert session.scalars(select(LlmCall)).all() == []


def test_budget_exhausted_skips_the_api_and_alerts_the_owner_once(session, cfg):
    session.add(LlmCall(purpose="x", model="m", input_tokens=1, output_tokens=1, cost_estimate=1.0,
                        latency_ms=1, created_at=NOW - timedelta(hours=1)))  # fmt: skip
    llm = FakeLLM()
    p = phraser(llm, ceiling=1.0)
    first, second = run(p, session, cfg), run(p, session, cfg)
    assert (first.reason, second.reason) == ("budget", "budget") and llm.calls == []
    alerts = session.scalars(select(Outbox).where(Outbox.channel == "owner_alert")).all()
    assert len(alerts) == 1 and alerts[0].target == "sai" and alerts[0].mode == "dry_run"
    assert "-" not in alerts[0].body and "1 USD" in alerts[0].body
    assert len(session.scalars(select(Event).where(Event.type == "llm_budget_alert")).all()) == 1


def test_budget_is_per_day_so_yesterdays_spend_does_not_count(session, cfg):
    session.add(LlmCall(purpose="x", model="m", input_tokens=1, output_tokens=1, cost_estimate=50.0,
                        latency_ms=1, created_at=NOW - timedelta(days=1)))  # fmt: skip
    assert spent_today(session, NOW) == 0.0
    assert run(phraser(FakeLLM(result())), session, cfg).source == "llm"


def test_spend_crossing_the_ceiling_switches_later_nudges_to_templates(session, cfg):
    llm = FakeLLM(result(inp=1_000_000, out=0))  # costs 3 USD at 3 USD per MTok
    p = phraser(llm, ceiling=1.0)
    assert run(p, session, cfg).source == "llm"
    assert run(p, session, cfg).reason == "budget" and len(llm.calls) == 1


def test_only_allowed_data_is_sent_to_the_model(session, cfg):
    f = Finding("UNASSIGNED_IN_SPRINT", Severity.MEDIUM, None, "I_2", "Export CSV", URL,
                {"reason": "owner_not_active_member", "sprint": "Sprint 12", "previous_owner": "gone-person"})  # fmt: skip
    llm = FakeLLM(
        result("Hi Ravi, Export CSV is in Sprint 12 and its previous owner has left. Who can own it? {link}")
    )
    run(phraser(llm), session, cfg, f=f)
    sent = llm.calls[0]["user"]
    blob = json.dumps(sent)
    assert "gone-person" not in blob and URL not in blob and "github.com" not in blob
    assert set(sent) == {"rule_id", "recipient_first_name", "issue_title", "has_link", "facts", "draft"}
    assert "{link}" in sent["draft"] and sent["issue_title"] == "Export CSV"
    assert "whatsapp" not in blob.lower() and "demo-ravi" not in blob  # no ids or numbers of people


def test_aggregate_findings_send_no_title_and_no_link(session, cfg):
    f = Finding("OVERLOADED_MEMBER", Severity.MEDIUM, "ravi-demo", "member:ravi-demo", "workload of Ravi", None,
                {"sprint": "Sprint 12", "points": 34.0, "capacity_points": 20.0})  # fmt: skip
    ok = "Hi Ravi, in Sprint 12 we count 34 points against a capacity of 20 for you. Can we look at it together?"
    llm = FakeLLM(result(ok))
    out = run(phraser(llm), session, cfg, f=f)
    assert out.message == ok and llm.calls[0]["user"]["has_link"] is False
    assert llm.calls[0]["user"]["issue_title"] == ""


def test_static_system_prompt_is_identical_across_calls_so_it_can_be_cached(session, cfg):
    llm = FakeLLM(result(), result())
    p = phraser(llm)
    run(p, session, cfg)
    run(p, session, cfg, now=NOW + timedelta(minutes=5))
    assert llm.calls[0]["system"] == llm.calls[1]["system"]


# The SDK wrapper, against a stand-in for the Anthropic client.
def test_anthropic_wrapper_requests_cached_system_and_structured_output():
    seen = {}

    class FakeMessages:
        def parse(self, **kw):
            seen.update(kw)
            usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=3,
                                    cache_creation_input_tokens=None)  # fmt: skip
            return SimpleNamespace(
                parsed_output=PhrasedMessage(message="x"), stop_reason="end_turn", usage=usage
            )

    r = AnthropicLLM(SimpleNamespace(messages=FakeMessages())).complete("m-test", "SYS", "USER", 99)
    assert seen["model"] == "m-test" and seen["max_tokens"] == 99 and seen["output_format"] is PhrasedMessage
    assert seen["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    assert "tool_choice" not in seen and "thinking" not in seen and "temperature" not in seen
    assert (r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_creation_tokens) == (10, 5, 3, 0)
    assert r.parsed.message == "x"


def test_settings_refuse_a_model_without_ceiling_and_prices(monkeypatch):
    base = {"api_secret": "x" * 16, "NXSPRINT_MODEL": "m-test", "_env_file": None}
    for k in ("NXSPRINT_MODEL", "NXSPRINT_MAX_DAILY_USD"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValueError, match="no spending without a ceiling"):
        Settings(**base)
    ok = Settings(**base, max_daily_usd=2, price_input_per_mtok=1, price_output_per_mtok=5)
    assert ok.llm_model == "m-test"
    assert Settings(api_secret="x" * 16, _env_file=None).llm_model is None  # off by default
