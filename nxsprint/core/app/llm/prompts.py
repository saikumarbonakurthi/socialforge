from functools import cache
from pathlib import Path

PROMPT_DIR = Path(__file__).parent / "prompts"
NUDGE_PROMPT = "nudge_v1"  # bump the file name when the prompt changes; golden tests pin it


@cache
def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text()
