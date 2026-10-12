"""Thin wrapper around the Anthropic SDK. Everything else in ``reach.llm`` talks to this."""

from __future__ import annotations

import os
from typing import Protocol, TypeVar

from pydantic import BaseModel

DEFAULT_MODEL = "claude-opus-5-5"
T = TypeVar("T", bound=BaseModel)


class LlmUnavailableError(RuntimeError):
    """The optional LLM features cannot be used in this environment."""


class LlmError(RuntimeError):
    """The model call failed or returned nothing usable."""


class LlmClient(Protocol):
    def complete(self, *, system: str, prompt: str, schema: type[T]) -> T:
        """Return the model's answer parsed into ``schema``."""
        ...


def llm_status() -> tuple[bool, str]:
    """Whether LLM features can run, and why not."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "the 'anthropic' package is not installed (pip install 'reach[llm]')"
    if not any(
        os.environ.get(k)
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")
    ):
        return False, "ANTHROPIC_API_KEY is not set"
    return True, ""


class AnthropicClient:
    """Calls the Messages API with structured outputs."""

    def __init__(self, model: str | None = None, max_tokens: int = 16000) -> None:
        ok, reason = llm_status()
        if not ok:
            raise LlmUnavailableError(reason)
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        self.model = model or os.environ.get("REACH_LLM_MODEL") or DEFAULT_MODEL
        self.max_tokens = max_tokens

    def complete(self, *, system: str, prompt: str, schema: type[T]) -> T:
        anthropic = self._anthropic
        try:
            response = self._client.messages.parse(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
            )
        except anthropic.AuthenticationError as exc:
            raise LlmUnavailableError("the Anthropic API rejected the credentials") from exc
        except anthropic.RateLimitError as exc:
            raise LlmError("rate limited by the Anthropic API; try again in a minute") from exc
        except anthropic.APIStatusError as exc:
            raise LlmError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LlmError("could not reach the Anthropic API") from exc
        if response.stop_reason == "refusal":
            raise LlmError("the model declined this request")
        if response.stop_reason == "max_tokens":
            raise LlmError("the answer was cut off; try a shorter input")
        parsed = response.parsed_output
        if parsed is None:
            raise LlmError("the model returned no structured answer")
        return parsed


def default_client() -> LlmClient:
    return AnthropicClient()
