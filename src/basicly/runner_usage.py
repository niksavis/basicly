"""What a stdout-reported dispatch's numbers add up to.

One responsibility, and it is the total. :class:`Usage` is the single shape every
family is normalised to, and each function below turns one record into one — summing
the fields that are disjoint and leaving out the ones that are subsets. Which fields
those are is the whole substance here: getting it wrong yields a plausible number
rather than a crash, so every summation carries the probe it was verified against.

The boundary is *the total* against *the record it is taken off*.
:mod:`basicly.runner_envelope` locates the records and knows nothing about arithmetic,
and ``runner`` dispatches on which format an adapter reports in, because it is the
module that knows which adapter it ran. :mod:`basicly.copilot_store` is the one family
that does not come through here — its record is a file it wrote itself, with one shape
and one total, so there is nothing there for this separation to buy.

Totals for cost, not occupancy. ``runner.context_occupancy`` reads the *same* provider
fields and deliberately does not come through here either: this module sums across
turns for what a dispatch is charged, while occupancy reads a single turn for how full
the window was at the end (design D8). Folding the two together is the mistake
:data:`supervise.LIVE_OVERREPORT_BOUND` exists to correct, so they stay apart.

Split out of ``runner`` when the module-size ratchet caught that module growing (32,295
tokens against a frozen 31,114). Nothing here imports back into ``runner``: a total is
a function of a record and of nothing about how the dispatch that produced it was
invoked, which is what makes the seam a real one rather than an arithmetic convenience.
"""

from __future__ import annotations

from dataclasses import dataclass

from .runner_envelope import (
    CLAUDE_TOKEN_KEYS,
    CODEX_TOKEN_KEYS,
    CODEX_TURN_COMPLETED,
    claude_result_object,
    codex_turn_usages,
    forwarded,
)


@dataclass(frozen=True)
class Usage:
    """Token usage for one executed run: adapter-reported, or a chars/4 estimate."""

    # The single summed total processed. Every consumer of the run-record's
    # `tokens` reads it as that (the D3 grant ceiling, sizing calibration, the
    # cost rollups), so the split fields below are siblings, never a redefinition.
    tokens: int
    cost: float | None
    estimated: bool
    # Provider-neutral per-kind split, null for an adapter that reports no split
    # (basicly-2rn9). Each family's own summation semantics are folded in by its
    # extractor, so a reader never has to know whose numbers these were.
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    # AI credits, **not** USD. `cost` is USD (claude's total_cost_usd); copilot
    # meters in AIU. They are different units, so they get different fields —
    # adding them into one number would be a silent accounting defect.
    credits: float | None = None


# codex `turn.completed` usage keys mapped onto Usage's split fields
# (basicly-jr0l.37). `input_tokens` is the **superset**, exactly as copilot's
# `inputTokens` is: it already contains the cached portion, so the uncached
# remainder is `input_tokens - cache_read_tokens` rather than a fourth stored
# number. Same convention for both providers, so a cost model can read the split
# without knowing whose numbers these were.
_CODEX_USAGE_KEYS = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_tokens": "cached_input_tokens",
    "cache_write_tokens": "cache_write_input_tokens",
    "reasoning_tokens": "reasoning_output_tokens",
}


def floor_usage(stdout: str, stderr: str) -> Usage:
    """The chars/4 floor over whatever transcript was captured (design 7.5).

    Takes the two streams rather than the result they came off, because a floor is a
    function of the captured text alone — which is what keeps this module free of any
    knowledge of how a dispatch is invoked or recorded.
    """
    return Usage(tokens=(len(stdout) + len(stderr)) // 4, cost=None, estimated=True)


def claude_json_usage(stdout: str) -> Usage | None:
    """Parse claude's ``--output-format json`` result object (one JSON object).

    Tokens sum the usage block's input/output/cache fields; cost comes from
    ``total_cost_usd``. None on any parse miss so the caller falls back to the
    estimate.
    """
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
    )


def claude_turn_usage(event: dict) -> Usage | None:
    """One claude ``assistant`` event's usage block, summed the way the total is.

    No cost: ``total_cost_usd`` lives only on the terminating result event, so a
    per-turn cost would have to be invented and this reports None instead.

    A forwarded subagent turn reports None too — see
    :func:`runner_envelope.forwarded` for why the tokens it carries are not this
    lane's to count.
    """
    if event.get("type") != "assistant" or forwarded(event):
        return None
    message = event.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
        return None
    usage = message["usage"]
    values = [usage[key] for key in CLAUDE_TOKEN_KEYS if isinstance(usage.get(key), int)]
    if not values:
        return None
    return Usage(tokens=sum(values), cost=None, estimated=False)


def codex_turn_usage(event: dict) -> Usage | None:
    """One codex ``turn.completed`` event's usage, split exactly as the total is."""
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
    """Sum token usage over codex's ``--json`` event stream (JSONL).

    Each ``turn.completed`` event carries a usage object; input and output
    tokens sum across turns onto ``tokens``, and the per-kind counts sum onto the
    split fields (basicly-jr0l.37) so a cost model can price the cached portion
    an order of magnitude cheaper than the uncached one. ``tokens`` stays
    input + output — see :data:`runner_envelope.CODEX_TOKEN_KEYS` for the measured
    reason the cache and reasoning counts are subsets, not addends. Codex reports
    no cost. None when no usage event parses, so the caller falls back to the
    estimate.
    """
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
    """Sum codex's per-kind token counts across turns, leaving an absent kind null.

    A count no turn reported stays None rather than 0, because those are
    different claims: 0.146.0 reports a real ``reasoning_output_tokens`` of 0 for
    a turn that did no reasoning, so a fabricated 0 for a build that omits the
    field would be indistinguishable from that measurement.
    """
    split: dict[str, int | None] = dict.fromkeys(_CODEX_USAGE_KEYS)
    for usage in usages:
        for field, key in _CODEX_USAGE_KEYS.items():
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                split[field] = (split[field] or 0) + value
    return split
