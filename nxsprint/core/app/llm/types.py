from dataclasses import dataclass

from pydantic import BaseModel, Field


class PhrasedMessage(BaseModel):
    """The only shape we accept from the model (rule 3)."""

    message: str = Field(min_length=1)


@dataclass(frozen=True)
class LLMResult:
    parsed: PhrasedMessage | None
    stop_reason: str | None
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    latency_ms: int
