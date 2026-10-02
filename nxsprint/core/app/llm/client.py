"""Thin wrapper over the Anthropic SDK. Everything else depends on the `LLMClient` shape only."""

import time
from typing import Protocol

import anthropic

from app.llm.types import LLMResult, PhrasedMessage


class LLMClient(Protocol):
    def complete(self, model: str, system: str, user: str, max_tokens: int) -> LLMResult: ...


class AnthropicLLM:
    def __init__(self, client: anthropic.Anthropic | None = None):
        self._client = client or anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    def complete(self, model: str, system: str, user: str, max_tokens: int) -> LLMResult:
        start = time.perf_counter()
        response = self._client.messages.parse(
            model=model,
            max_tokens=max_tokens,
            # The system prompt is static, so it is the cached prefix. (Caching only engages once the
            # prefix passes the model's minimum length; cache_read_tokens in llm_call shows if it did.)
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_format=PhrasedMessage,
        )
        usage = response.usage
        return LLMResult(
            parsed=response.parsed_output,
            stop_reason=response.stop_reason,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
