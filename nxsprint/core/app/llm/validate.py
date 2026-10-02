"""Checks on model output. The model never gets the last word: anything failing is discarded."""

import re

from app.domain.rules import Finding

MAX_CHARS = 600
LINK_TOKEN = "{link}"
_DASHES = re.compile(r"[-‐-―−]")
_NUMBERS = re.compile(r"\d+(?:\.\d+)?")


def allowed_numbers(finding: Finding, draft: str) -> set[str]:
    """Numbers that may appear: those in the facts, the title and the draft we wrote ourselves."""
    text = " ".join([finding.title, draft, *map(str, finding.evidence.values())])
    return set(_NUMBERS.findall(text))


def validate_message(text: str, finding: Finding, first_name: str, draft: str) -> list[str]:
    """Return error codes. An empty list means the message is acceptable."""
    errors = []
    if not 20 <= len(text) <= MAX_CHARS:
        errors.append("length")
    if not text.startswith(f"Hi {first_name},"):
        errors.append("greeting")
    if re.search(r"https?://|www\.|@", text):
        errors.append("invented_link_or_mention")
    # Titles are quoted data and may contain hyphens; the placeholder is ours.
    prose = text.replace(finding.title, "").replace(LINK_TOKEN, "")
    if _DASHES.search(prose):
        errors.append("dash")
    expected_links = 1 if finding.url else 0
    if text.count(LINK_TOKEN) != expected_links:
        errors.append("link")
    if finding.url and finding.title not in text:
        errors.append("title_missing")
    if re.search(r"[*`#]|^\s*[•]", text, re.M) or text.count("\n") > 2:
        errors.append("formatting")
    ok = allowed_numbers(finding, draft)
    if any(n not in ok for n in _NUMBERS.findall(prose)):
        errors.append("invented_number")
    return errors
