"""Opt in: runs the golden cases through the real model. Costs real money.

    NXSPRINT_LIVE_LLM=1 NXSPRINT_MODEL=... NXSPRINT_MAX_DAILY_USD=1 \
    NXSPRINT_PRICE_INPUT_PER_MTOK=... NXSPRINT_PRICE_OUTPUT_PER_MTOK=... ANTHROPIC_API_KEY=... \
    pytest tests/golden/test_live.py -s
"""

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.config import load_config
from app.db import make_engine
from app.llm.client import AnthropicLLM
from app.llm.phraser import Phraser, spent_today
from app.models import Base
from app.settings import Mode, Settings
from tests.golden.test_golden import CASES, finding

pytestmark = pytest.mark.skipif(
    os.environ.get("NXSPRINT_LIVE_LLM") != "1", reason="live model test is opt in"
)


def test_live_model_phrases_every_case_without_falling_back(config_file):
    s = Settings(api_secret="x" * 16)
    cfg = load_config(config_file, Mode.DRY_RUN).projects[0]
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    phraser = Phraser(
        AnthropicLLM(), s.llm_model, s.max_daily_usd, s.price_input_per_mtok, s.price_output_per_mtok
    )
    now = datetime.now(UTC)
    with Session(engine) as session:
        for case in CASES:
            out = phraser.phrase(
                session, project_id=1, cfg=cfg, mode=Mode.DRY_RUN, finding=finding(case),
                first_name=case["first_name"], now=now,
            )  # fmt: skip
            print(f"[{case['id']}] {out.source} {out.reason or ''}\n  {out.message}")
            assert out.source == "llm", (case["id"], out.reason)
        print(f"spent: {spent_today(session, now):.5f} USD")
