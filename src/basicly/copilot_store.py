from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .runner_usage import Usage

COPILOT_SESSION_STORE = "copilot-session-store"

DEFAULT_COPILOT_SESSION_STORE = Path("~/.copilot/session-state")
COPILOT_EVENTS_FILE = "events.jsonl"
COPILOT_SHUTDOWN_EVENT = "session.shutdown"

_USAGE_KEYS = {
    "input_tokens": "inputTokens",
    "output_tokens": "outputTokens",
    "cache_read_tokens": "cacheReadTokens",
    "cache_write_tokens": "cacheWriteTokens",
    "reasoning_tokens": "reasoningTokens",
}
_NANO_AIU_PER_CREDIT = 1_000_000_000


class SessionStoreSpec(Protocol):
    @property
    def session_store(self) -> Path | None: ...


def shutdown_data(spec: SessionStoreSpec, session_id: str | None) -> dict | None:

    if not session_id:
        return None
    base = spec.session_store or DEFAULT_COPILOT_SESSION_STORE
    events = base.expanduser() / session_id / COPILOT_EVENTS_FILE
    try:
        text = events.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == COPILOT_SHUTDOWN_EVENT:
            data = event.get("data")
            if isinstance(data, dict):
                return data
    return None


def store_usage(spec: SessionStoreSpec, session_id: str | None) -> Usage | None:

    data = shutdown_data(spec, session_id)
    metrics = data.get("modelMetrics") if data is not None else None
    if not isinstance(metrics, dict):
        return None
    split = dict.fromkeys(_USAGE_KEYS, 0)
    nano_aiu: float | None = None
    measured = False
    for entry in metrics.values():
        if not isinstance(entry, dict):
            continue
        usage = entry.get("usage")
        if isinstance(usage, dict):
            for field, key in _USAGE_KEYS.items():
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    split[field] += value
                    measured = True
        aiu = entry.get("totalNanoAiu")
        if isinstance(aiu, int | float) and not isinstance(aiu, bool):
            nano_aiu = (nano_aiu or 0.0) + float(aiu)
    if not measured:
        return None
    return Usage(
        tokens=split["input_tokens"] + split["output_tokens"],
        cost=None,
        estimated=False,
        input_tokens=split["input_tokens"],
        output_tokens=split["output_tokens"],
        cache_read_tokens=split["cache_read_tokens"],
        cache_write_tokens=split["cache_write_tokens"],
        reasoning_tokens=split["reasoning_tokens"],
        credits=None if nano_aiu is None else nano_aiu / _NANO_AIU_PER_CREDIT,
    )
