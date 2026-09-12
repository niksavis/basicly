from __future__ import annotations

from dataclasses import dataclass

from .runner_envelope import (
    CLAUDE_STREAM_JSON,
    CLAUDE_TOKEN_KEYS,
    claude_turn_text,
    stream_events,
)

SYNTHETIC_MODEL = "<synthetic>"

LIMIT_WORD = "limit"

LIMIT_QUESTION = (
    "the provider's own usage limit refused this dispatch, so nothing about the lane was "
    "tried: re-dispatch after the reset, or park it"
)


@dataclass(frozen=True)
class LimitRefusal:
    said: str

    @property
    def detail(self) -> str:
        return f"provider usage limit refused the dispatch: {self.said}"


def refusal(usage_format: str | None, stdout: str) -> LimitRefusal | None:

    if usage_format != CLAUDE_STREAM_JSON:
        return None
    for event in stream_events(stdout):
        said = _synthesized_text(event)
        if said and LIMIT_WORD in said.casefold():
            return LimitRefusal(said=said)
    return None


def _synthesized_text(event: dict) -> str:

    if event.get("type") != "assistant":
        return ""
    message = event.get("message")
    if not isinstance(message, dict):
        return ""
    if message.get("model") != SYNTHETIC_MODEL and _turn_tokens(message) > 0:
        return ""
    return claude_turn_text(message)


def _turn_tokens(message: dict) -> int:
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return 0
    return sum(usage[key] for key in CLAUDE_TOKEN_KEYS if isinstance(usage.get(key), int))
