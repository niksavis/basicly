"""When the provider's own allowance refused a dispatch, rather than the work failing.

A dispatch the seat allowance turns away exits nonzero with an empty diff, which at the
routing layer is indistinguishable from an agent that tried and failed — so the pass
charged it a rework attempt and re-dispatched into the same wall. Measured on this repo's
records: 110 of 702 runs, every one ``failed`` at returncode 1 with 0 tokens in 2.0-3.7s,
70 of them inside 20 minutes on 2026-08-28 before a human stopped the session.

The boundary is *whose refusal* against *what is done about it*:
:mod:`basicly.runner_envelope` locates records and judges none, :mod:`basicly.supervise`
decides what a routed lane costs, and this says only whether the provider refused.
"""

# comment-density-waiver: cohesion: what this module *is* is a discriminator plus the
# evidence that fixes it - which vendor sentence was captured and how many times, which of
# the two marks is derived rather than observed, and why the event the record was filed
# against discriminates nothing. Measured at 64.6% against 380 tokens of code: the code is
# four short total functions, and cutting to 50% means deleting the provenance that is the
# only reason anyone can check the rule against a future CLI. Correct and permanent.

from __future__ import annotations

from dataclasses import dataclass

from .runner_envelope import (
    CLAUDE_STREAM_JSON,
    CLAUDE_TOKEN_KEYS,
    claude_turn_text,
    stream_events,
)

# The model name claude puts on a turn its CLI synthesized rather than a model producing
# it. Derived, not captured: the refusal records carry ``observed_models ==
# ["<pin>", "<synthetic>"]`` in that order, and only the `system` init event can have
# supplied the pin (:func:`runner._claude_observed_models`).
SYNTHETIC_MODEL = "<synthetic>"

# What makes a synthesized turn a *limit* refusal and not one of the CLI's other
# interjections. The word, not the vendor's sentence: the captured text is "You've hit
# your session limit · resets 5:50pm (Europe/Vienna)" (35 events, 7 lanes, 2026-08-28),
# and pinning that spelling makes the detector fail silently at the next rewording.
LIMIT_WORD = "limit"

# The queue question a limit-refused lane asks. No numbers in it, for the reason
# :data:`supervise.PASS_SPEND_QUESTION` carries: items key on (issue, kind, question), so
# a reset time spelled in here queues a fresh item per refusal.
LIMIT_QUESTION = (
    "the provider's own usage limit refused this dispatch, so nothing about the lane was "
    "tried: re-dispatch after the reset, or park it"
)


@dataclass(frozen=True)
class LimitRefusal:
    """A dispatch the provider's allowance turned away, and what it said about it."""

    # Verbatim, because it carries the reset time — the one fact an operator needs here
    # and the only one the harness cannot derive.
    said: str

    @property
    def detail(self) -> str:
        """The refusal as a routed outcome and a queue item report it."""
        return f"provider usage limit refused the dispatch: {self.said}"


def refusal(usage_format: str | None, stdout: str) -> LimitRefusal | None:
    """The provider's limit refusal in *stdout*, or None when it holds none.

    Claude's stream only: codex and copilot have their own allowances, but nothing here
    has captured one of their refusals, and a rule written from no observation would
    either miss it or catch a healthy run.

    Deliberately *not* keyed on the ``rate_limit_event`` this record was filed against —
    a healthy dispatch emits one too, carrying ``{"status": "allowed"}``, pinned that way
    in this suite's own live-probe fixture, so its presence discriminates nothing.
    """
    if usage_format != CLAUDE_STREAM_JSON:
        return None
    for event in stream_events(stdout):
        said = _synthesized_text(event)
        if said and LIMIT_WORD in said.casefold():
            return LimitRefusal(said=said)
    return None


def _synthesized_text(event: dict) -> str:
    """The text of an assistant turn no model produced, or "" for any other event.

    Two marks of one and either suffices: the turn names :data:`SYNTHETIC_MODEL`, or it
    reports no tokens — and a turn a model wrote always has output tokens. Either alone
    would do; both are read because the first is derived rather than captured, and a
    detector resting on it would go quietly inert if that derivation is wrong.
    """
    if event.get("type") != "assistant":
        return ""
    message = event.get("message")
    if not isinstance(message, dict):
        return ""
    if message.get("model") != SYNTHETIC_MODEL and _turn_tokens(message) > 0:
        return ""
    return claude_turn_text(message)


def _turn_tokens(message: dict) -> int:
    """What the turn's own usage block sums to; 0 when it carries none."""
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return 0
    return sum(usage[key] for key in CLAUDE_TOKEN_KEYS if isinstance(usage.get(key), int))
