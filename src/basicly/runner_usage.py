from __future__ import annotations

from dataclasses import dataclass

from .runner_envelope import (
    CLAUDE_TOKEN_KEYS,
    CODEX_TOKEN_KEYS,
    CODEX_TURN_COMPLETED,
    claude_result_object,
    codex_turn_usages,
    forwarded,
    stream_events,
)


@dataclass(frozen=True)
class Usage:
    tokens: int
    cost: float | None
    estimated: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    credits: float | None = None


_SPLIT_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)

_CODEX_USAGE_KEYS = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_tokens": "cached_input_tokens",
    "cache_write_tokens": "cache_write_input_tokens",
    "reasoning_tokens": "reasoning_output_tokens",
}


def floor_usage(stdout: str, stderr: str) -> Usage:

    return Usage(tokens=(len(stdout) + len(stderr)) // 4, cost=None, estimated=True)


def claude_json_usage(stdout: str) -> Usage | None:

    obj = claude_result_object(stdout)
    if obj is None or not isinstance(obj.get("usage"), dict):
        return None
    usage = obj["usage"]
    values = [usage[key] for key in CLAUDE_TOKEN_KEYS if isinstance(usage.get(key), int)]
    if not values:
        return None
    cost = obj.get("total_cost_usd")
    return Usage(
        tokens=sum(values),
        cost=float(cost) if isinstance(cost, int | float) else None,
        estimated=False,
        **_claude_usage_split(usage),
    )


def claude_turn_usage(event: dict) -> Usage | None:

    if event.get("type") != "assistant" or forwarded(event):
        return None
    message = event.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
        return None
    usage = message["usage"]
    values = [usage[key] for key in CLAUDE_TOKEN_KEYS if isinstance(usage.get(key), int)]
    if not values:
        return None
    return Usage(tokens=sum(values), cost=None, estimated=False, **_claude_usage_split(usage))


def claude_stream_usage(stdout: str) -> Usage | None:

    turns = [
        usage for event in stream_events(stdout) if (usage := claude_turn_usage(event)) is not None
    ]
    if not turns:
        return None
    return Usage(
        tokens=sum(turn.tokens for turn in turns), cost=None, estimated=False, **_summed(turns)
    )


def _summed(turns: list[Usage]) -> dict[str, int | None]:

    split: dict[str, int | None] = dict.fromkeys(_SPLIT_FIELDS)
    for turn in turns:
        for field in _SPLIT_FIELDS:
            value = getattr(turn, field)
            if value is not None:
                split[field] = (split[field] or 0) + value
    return split


def codex_turn_usage(event: dict) -> Usage | None:
    if event.get("type") != CODEX_TURN_COMPLETED:
        return None
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return None
    values = [usage[key] for key in CODEX_TOKEN_KEYS if isinstance(usage.get(key), int)]
    if not values:
        return None
    return Usage(tokens=sum(values), cost=None, estimated=False, **_codex_usage_split([usage]))


def codex_jsonl_usage(stdout: str) -> Usage | None:

    total = 0
    found = False
    usages = codex_turn_usages(stdout)
    for usage in usages:
        values = [usage[key] for key in CODEX_TOKEN_KEYS if isinstance(usage.get(key), int)]
        if values:
            total += sum(values)
            found = True
    if not found:
        return None
    split = _codex_usage_split(usages)
    return Usage(
        tokens=total,
        cost=None,
        estimated=False,
        input_tokens=split["input_tokens"],
        output_tokens=split["output_tokens"],
        cache_read_tokens=split["cache_read_tokens"],
        cache_write_tokens=split["cache_write_tokens"],
        reasoning_tokens=split["reasoning_tokens"],
    )


def _codex_usage_split(usages: list[dict]) -> dict[str, int | None]:

    split: dict[str, int | None] = dict.fromkeys(_CODEX_USAGE_KEYS)
    for usage in usages:
        for field, key in _CODEX_USAGE_KEYS.items():
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                split[field] = (split[field] or 0) + value
    return split


def _claude_usage_split(usage: dict) -> dict[str, int | None]:

    def count(key: str) -> int | None:
        value = usage.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    read, written, raw = (
        count("cache_read_input_tokens"),
        count("cache_creation_input_tokens"),
        count("input_tokens"),
    )
    parts = [part for part in (raw, written, read) if part is not None]
    return {
        "input_tokens": sum(parts) if parts else None,
        "output_tokens": count("output_tokens"),
        "cache_read_tokens": read,
        "cache_write_tokens": written,
    }
