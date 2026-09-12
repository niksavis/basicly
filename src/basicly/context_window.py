from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from .models import same_model
from .runner_envelope import claude_result_event, forwarded, stream_events

DEFAULT_CONTEXT_WINDOW = 128_000

ADAPTER_WINDOW = "adapter default"
FALLBACK_WINDOW = "conservative fallback"
AGENT_WINDOW = "agent context_window"
DECLARED_WINDOW = "[runner] context_windows"
OBSERVED_WINDOW = "adapter reported"
UNMETERED = "unmetered — nothing declared a window and the adapter reported none"

CHOSEN_SOURCES = (AGENT_WINDOW, DECLARED_WINDOW)

RECHECK_DAYS = 180
RECHECK_PROBE = "claude -p '.' --output-format stream-json --verbose | tail -1"


@dataclass(frozen=True)
class AdapterWindow:
    tokens: int
    checked: date
    evidence: str


ADAPTER_WINDOWS: dict[str, AdapterWindow] = {
    "claude": AdapterWindow(
        1_000_000,
        date(2026, 8, 15),
        "modelUsage['claude-opus-5[1m]'].contextWindow, claude 2.1.233",
    ),
}


def _final_turn_model(stdout: str) -> str | None:

    for event in reversed(stream_events(stdout)):
        if event.get("type") != "assistant" or forwarded(event):
            continue
        message = event.get("message")
        model = message.get("model") if isinstance(message, dict) else None
        if isinstance(model, str) and model:
            return model
    return None


def _reported_windows(stdout: str) -> list[tuple[str, int]]:
    try:
        obj = json.loads(claude_result_event(stdout).strip() or "null")
    except json.JSONDecodeError:
        return []
    usage_by_model = obj.get("modelUsage") if isinstance(obj, dict) else None
    if not isinstance(usage_by_model, dict):
        return []
    found: list[tuple[str, int]] = []
    for key, block in usage_by_model.items():
        window = block.get("contextWindow") if isinstance(block, dict) else None
        if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
            continue
        canonical = block.get("canonicalModel")
        found.append((canonical if isinstance(canonical, str) and canonical else key, window))
    return found


def reported_window(stdout: str) -> int | None:

    windows = _reported_windows(stdout)
    if not windows:
        return None
    model = _final_turn_model(stdout)
    if model is not None:
        for name, window in windows:
            if same_model(model, name) or same_model(name, model):
                return window
    return windows[0][1] if len(windows) == 1 else None


def resolve(*, declared: int, source: str | None, reported: int | None) -> tuple[int | None, str]:

    if source in CHOSEN_SOURCES:
        return declared, source
    if reported is not None:
        return reported, OBSERVED_WINDOW
    if source == ADAPTER_WINDOW:
        return declared, ADAPTER_WINDOW
    return None, UNMETERED


def stale_declarations(shipped: Mapping[str, int], *, today: date) -> list[str]:

    problems: list[str] = []
    for name, tokens in sorted(shipped.items()):
        entry = ADAPTER_WINDOWS.get(name)
        if entry is None:
            problems.append(
                f"runner {name!r} ships a context window of {tokens:,} tokens with nothing "
                f"recording who checked it or when; add an ADAPTER_WINDOWS entry naming the "
                f"probe, or ship no window and let the dispatch record {UNMETERED!r}"
            )
            continue
        if entry.tokens != tokens:
            problems.append(
                f"runner {name!r} ships {tokens:,} tokens while its recorded probe read "
                f"{entry.tokens:,} on {entry.checked.isoformat()} ({entry.evidence}); the "
                f"figure and its evidence have to be the same number"
            )
            continue
        age = (today - entry.checked).days
        if age > RECHECK_DAYS:
            problems.append(
                f"runner {name!r} last read its context window from the adapter on "
                f"{entry.checked.isoformat()}, {age} days ago and past the {RECHECK_DAYS}-day "
                f"bound; re-read it with `{RECHECK_PROBE}` and update the ADAPTER_WINDOWS entry"
            )
    return problems
