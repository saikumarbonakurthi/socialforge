"""Golden set: recorded good and bad model outputs per rule, checked against our validator.

Properties pinned (section 11): length, no invented ids or numbers, no dashes, includes the issue link.
Offline by design. See test_live.py to run the same cases against the real model.
"""

import json
from pathlib import Path

import pytest

from app.domain.messages import render
from app.domain.rules import Finding, Severity
from app.llm.prompts import NUDGE_PROMPT, load_prompt
from app.llm.validate import MAX_CHARS, validate_message

CASES = json.loads((Path(__file__).parent / "cases.json").read_text())


def finding(case) -> Finding:
    f = case["finding"]
    return Finding(f["rule_id"], Severity(f["severity"]), f["member"], f["issue_node_id"], f["title"],
                   f["url"], f["evidence"])  # fmt: skip


@pytest.fixture
def cfg(config_file):
    from app.config import load_config
    from app.settings import Mode

    return load_config(config_file, Mode.DRY_RUN).projects[0]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_good_outputs_pass(case, cfg):
    f = finding(case)
    draft = render(f, case["first_name"], cfg, link="{link}")
    for text in case["good"]:
        assert validate_message(text, f, case["first_name"], draft) == [], text
        assert len(text) <= MAX_CHARS


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_bad_outputs_are_caught(case, cfg):
    f = finding(case)
    draft = render(f, case["first_name"], cfg, link="{link}")
    for bad in case["bad"]:
        errors = validate_message(bad["text"], f, case["first_name"], draft)
        assert set(bad["expect"]) <= set(errors), (bad["text"], errors)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_our_own_template_always_passes_validation(case, cfg):
    """The fallback must satisfy the same rules as the model, or the rules are wrong."""
    f = finding(case)
    draft = render(f, case["first_name"], cfg, link="{link}")
    assert validate_message(draft, f, case["first_name"], draft) == []


def test_every_rule_has_a_golden_case():
    from app.config import RULE_IDS

    covered = {c["finding"]["rule_id"] for c in CASES}
    # PR_WAITING_REVIEW is dormant until the sync fetches PR data.
    assert set(RULE_IDS) - covered == {"NO_ESTIMATE", "PR_WAITING_REVIEW"}


def test_prompt_is_pinned_and_states_the_house_rules():
    assert NUDGE_PROMPT == "nudge_v1"
    text = load_prompt(NUDGE_PROMPT)
    for must in ("{link}", "dashes", "we", "JSON only", "recipient_first_name", "previous_attempt_errors"):
        assert must in text, must
    assert "gpt" not in text.lower() and "claude-" not in text.lower()  # no model strings anywhere
