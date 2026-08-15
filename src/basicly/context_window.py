"""The window a dispatch is metered against, and where that figure came from.

The context window is the denominator of the ceiling meter, and it is the one input
to that meter that is a claim about a *vendor's model* rather than a choice this repo
made. basicly-23ep is what a stale one costs: the shipped `claude` figure said 200_000
while lanes recorded occupancies up to 223_221, which put the 0.6 trigger at a fifth of
its intended point and spun eighteen follow-up beads off healthy lanes.

The remedy is not a bigger constant. Probed 2026-08-15 against claude 2.1.233, a single
dispatch reported **two** windows on its own stream — `claude-haiku-4-5` at 200_000 and
`claude-opus-5[1m]` at 1_000_000 — so the window is a property of the model, not of the
adapter, and no per-adapter constant can be right for both. :func:`resolve` is therefore
an order of preference ending in a refusal, and :data:`UNMETERED` is a real answer.

The same probe day settled the other two families, and neither reports a window at all:
codex 0.146.0's `--json` stream carries only thread/turn/item events (positive control —
its `turn.completed` usage block *is* there), and copilot's `session.shutdown` carries
`modelMetrics` with no window key on 6 of 6 local stores (control: the metrics
themselves are present). Neither gets a figure in :data:`ADAPTER_WINDOWS`, because
nothing in this engine could refute one if it rotted.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from .models import same_model
from .runner_envelope import claude_result_event, forwarded, stream_events

# The window for an agent nobody declared one for: the smallest of the big three, so
# a meter that runs at all errs toward observing rather than toward silence.
DEFAULT_CONTEXT_WINDOW = 128_000

# Provenance labels for a resolved window, recorded verbatim beside it. The
# distinction that matters is *chosen* versus *defaulted*: reading a defaulted window
# back as if someone had picked it is how basicly-23ep survived for months.
ADAPTER_WINDOW = "adapter default"  # a dated ADAPTER_WINDOWS probe below
FALLBACK_WINDOW = "conservative fallback"  # a figure nobody checked, for an agent that reports none
AGENT_WINDOW = "agent context_window"  # [[runner.agents]] context_window
DECLARED_WINDOW = "[runner] context_windows"  # the per-agent declaration, most specific
OBSERVED_WINDOW = "adapter reported"  # modelUsage.<id>.contextWindow, this dispatch's own
UNMETERED = "unmetered — nothing declared a window and the adapter reported none"

# The two sources that mean a human in the consuming repo picked the number.
CHOSEN_SOURCES = (AGENT_WINDOW, DECLARED_WINDOW)

# How long a shipped default may go without being re-read from the adapter.
RECHECK_DAYS = 180
RECHECK_PROBE = "claude -p '.' --output-format stream-json --verbose | tail -1"


@dataclass(frozen=True)
class AdapterWindow:
    """A shipped default window, the probe that read it, and the day it was read."""

    tokens: int
    checked: date
    evidence: str


# Only families whose own report can refute the figure appear here (see the module
# docstring): a default nothing can contradict is the defect, not the fix.
ADAPTER_WINDOWS: dict[str, AdapterWindow] = {
    "claude": AdapterWindow(
        1_000_000,
        date(2026, 8, 15),
        "modelUsage['claude-opus-5[1m]'].contextWindow, claude 2.1.233",
    ),
}


def _final_turn_model(stdout: str) -> str | None:
    """The model of the last turn the lane itself took, which is the metered one.

    Paired with :func:`runner.context_occupancy`, which measures that same turn: a
    forwarded turn is a subagent's, and reading its model would pick the subagent's
    window as the lane's.
    """
    for event in reversed(stream_events(stdout)):
        if event.get("type") != "assistant" or forwarded(event):
            continue
        message = event.get("message")
        model = message.get("model") if isinstance(message, dict) else None
        if isinstance(model, str) and model:
            return model
    return None


def _reported_windows(stdout: str) -> list[tuple[str, int]]:
    """Every (model, window) pair the terminating result event reports."""
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
    """The window the adapter itself reported for this dispatch, or None.

    None means *unreported*, never "the usual figure": an envelope that did not parse,
    a family that reports no window, and a run that ended before its result event all
    have to stay distinguishable from a measurement.

    A dispatch reporting several models is resolved by the final turn, because that is
    the turn the occupancy is measured on — the two figures observed together were
    200_000 and 1_000_000, so taking either the first or the largest would be wrong
    five times out of ten. A run whose final turn cannot be named yields nothing unless
    exactly one window was reported, where there is nothing to pick between.
    """
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
    """The window this dispatch was metered against, and its provenance.

    *declared* and *source* are the spec's, *reported* is :func:`reported_window`.
    A declaration wins over the adapter's own report so the record still explains the
    threshold the engine acted on; the report wins over every default, because a
    default is a figure about a model the dispatch may not even have run.
    """
    if source in CHOSEN_SOURCES:
        return declared, source
    if reported is not None:
        return reported, OBSERVED_WINDOW
    if source == ADAPTER_WINDOW:
        return declared, ADAPTER_WINDOW
    return None, UNMETERED


def stale_declarations(shipped: Mapping[str, int], *, today: date) -> list[str]:
    """Every shipped adapter window with no dated probe behind it, or one past the bound.

    The falsifier for the defaults themselves. `window_violations` catches a window
    the ledger has already outgrown, which needs a lane to record the contradiction
    first; this one fires on the calendar, before any lane pays for it.
    """
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
