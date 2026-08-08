"""The telemetry copilot reports out of band, in the session store it writes itself.

One responsibility, and it is that store. Copilot is the one supported family whose
numbers are not in the output the engine captured: its result event carries
premium-request counts rather than tokens, so a metered copilot dispatch is read back
out of the store the agent process wrote for itself (basicly-2rn9, probed 1.0.75 and
present in 15 of 15 local sessions). Finding that store, reading its one terminating
record, and totalling what the record holds is a single act here.

That is deliberately *not* how the stdout families are split.
:mod:`basicly.runner_envelope` locates their records and :mod:`basicly.runner_usage`
totals them, because there two callers take different totals off the very same fields.
Nothing pulls this record two ways: it is a private file with one shape and one total,
and separating a locator from its only arithmetic would be a boundary with nothing on
either side of it.

The store is also the one source that fails in ways stdout cannot — absent, unreadable,
truncated mid-write — and every one of those degrades to None rather than raising,
because the caller has to be able to fall back to an estimate and telemetry may not
fail a dispatch.

Split out of ``runner`` when the module-size ratchet caught that module growing (32,295
tokens against a frozen 31,114). Nothing here imports back into ``runner``: the one
spec attribute it reads is taken structurally through :class:`SessionStoreSpec`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .runner_usage import Usage

# copilot reports nothing usable on stdout — its result event carries
# premium-request counts, not tokens — but it writes a per-session event store,
# and that store's terminating `session.shutdown` event carries the per-model
# token split and the AI-credit spend. So this format measures out of band:
# `--session-id <uuid>` *sets* the new session's id, which makes the store path
# known before the store exists, and stdout stays plain text — which is what
# keeps the rubric judge's text parser working on a metered dispatch.
COPILOT_SESSION_STORE = "copilot-session-store"

# Where copilot keeps its per-session event stores, and the stream inside one.
# Held unexpanded so no machine-specific path is committed and `Path.home()` is
# never called at import: the reader expands it at the point of use, which also
# lets a test (or `[runner] copilot_session_store`) redirect it to a temp dir.
DEFAULT_COPILOT_SESSION_STORE = Path("~/.copilot/session-state")
COPILOT_EVENTS_FILE = "events.jsonl"
COPILOT_SHUTDOWN_EVENT = "session.shutdown"

# copilot `session.shutdown` per-model usage keys, mapped onto Usage's split
# fields. Summation semantics, verified against 15 local 1.0.75 stores:
# `inputTokens` *includes* both cache fields (inputTokens ==
# tokenDetails.input + cacheReadTokens + cacheWriteTokens held on all 15), so
# the total processed is inputTokens + outputTokens and adding the cache fields
# would double-count — the same subset relationship codex's cached_input_tokens
# has. `reasoningTokens` never exceeded `outputTokens`, so it is read as a
# subset of output too and likewise not added.
_USAGE_KEYS = {
    "input_tokens": "inputTokens",
    "output_tokens": "outputTokens",
    "cache_read_tokens": "cacheReadTokens",
    "cache_write_tokens": "cacheWriteTokens",
    "reasoning_tokens": "reasoningTokens",
}
# copilot meters AI credits in nano-AIU: a `totalNanoAiu` of 6_056_400_000 is
# 6.0564 credits (observed on the probe the test fixture was captured from).
_NANO_AIU_PER_CREDIT = 1_000_000_000


class SessionStoreSpec(Protocol):
    """The one runner attribute a store lookup needs, taken structurally.

    ``RunnerSpec`` is a frozen dataclass two tiers above this module, so importing it
    to annotate :func:`shutdown_data` would put an import back into ``runner`` and turn
    a layering into a cycle. Declared as a read-only property rather than a plain
    attribute for the reason ``plan_gate.PlannedFields`` records: a mutable slot in a
    protocol is one a frozen dataclass can never satisfy.
    """

    @property
    def session_store(self) -> Path | None:
        """Where this runner's agent keeps its session stores, or None for the default."""
        ...


def shutdown_data(spec: SessionStoreSpec, session_id: str | None) -> dict | None:
    """The ``session.shutdown`` payload of one copilot session's store, or None.

    The store lives at ``<session_store>/<session_id>/events.jsonl`` — the
    directory name *is* the session id (checked on 15 of 15 local stores against
    each one's ``session.start``), which is what makes a supplied id a sound
    join. Scanned from the end because the shutdown event terminates the stream,
    and unparseable lines are skipped rather than failing the read: a truncated
    final line is normal for a killed dispatch.

    None for no session id, a store that is absent or unreadable, or a stream
    with no usable shutdown event. Never raises — the caller must be able to
    degrade to the estimate, and telemetry may not fail a dispatch.
    """
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
    """Measured usage for one copilot dispatch, read from its own session store.

    Sums the shutdown event's ``modelMetrics`` blocks, so a dispatch that
    switched model mid-run still meters once: per-kind tokens onto the split
    fields, ``totalNanoAiu`` onto ``credits``. ``tokens`` is input + output only
    (see :data:`_USAGE_KEYS` for why the cache and reasoning counts are
    subsets, not addends), and ``cost`` stays null because copilot bills in AI
    credits and that field is USD.

    None when no model block yields a token count, so the caller falls back to
    the flagged estimate.
    """
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
