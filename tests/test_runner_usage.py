"""Tests for what a stdout-reported dispatch's numbers add up to (`runner_usage`).

Every assertion here is about a *total*: which of a provider's fields are disjoint and
which are subsets of another, and therefore what a dispatch is charged. They are taken
through `runner.extract_usage`, which is the dispatcher that picks the reader — a total
is only ever read that way, so asserting through it is asserting on the seam a caller
actually uses.

Split out of `tests/test_runner.py` with the module they cover. The captured probe
fixtures below are duplicated from that file rather than imported across test modules;
each copy carries the evidence for the assertions made against it here.
"""

from __future__ import annotations

import json

import pytest

from basicly import runner
from basicly.runner import (
    BUILTIN_RUNNERS,
    CLAUDE_JSON,
    HANDOFF,
    HEADLESS,
    MANUAL_RUNNER,
    PROMPT_PLACEHOLDER,
    RunnerSpec,
    RunResult,
)


def _claude_spec() -> RunnerSpec:
    return next(s for s in BUILTIN_RUNNERS if s.name == "claude")


def _claude_json_spec() -> RunnerSpec:
    """A consumer pinning the older single-object envelope (still supported)."""
    return _claude_spec().__class__(
        "claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER), usage_format=CLAUDE_JSON
    )


def _codex_spec() -> RunnerSpec:
    return next(s for s in BUILTIN_RUNNERS if s.name == "codex")


def _executed(spec: RunnerSpec, stdout: str, stderr: str = "") -> RunResult:
    return RunResult(
        spec.name, (spec.name,), executed=True, returncode=0, stdout=stdout, stderr=stderr
    )


# Captured from a live `claude -p ... --output-format json` probe (2026-07-22),
# trimmed to the fields extraction reads plus representative noise.
_CLAUDE_RESULT = json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "ok",
    "total_cost_usd": 0.136147,
    "usage": {
        "input_tokens": 2,
        "cache_creation_input_tokens": 5960,
        "cache_read_input_tokens": 15496,
        "output_tokens": 17,
        "server_tool_use": {"web_search_requests": 0},
    },
})

# Two `turn.completed` usage objects captured verbatim from live `codex exec
# --json` probes of codex-cli 0.146.0 (2026-07-31 and 2026-07-29,
# basicly-jr0l.37), each paired with the `total_tokens` codex's own session
# rollout recorded for that same turn. That pairing is the evidence for how the
# fields relate: the identity input_tokens + output_tokens == total_tokens holds
# on both, and on all four turns this machine has recorded. So
# `cached_input_tokens` is a subset of `input_tokens`, and
# `reasoning_output_tokens` a subset of `output_tokens` — 12764 + 155 == 12919
# even though 147 of those 155 output tokens were reasoning, and the visible
# answer really was 4 characters long.
#
# The first probe forced a **non-zero** reasoning count
# (`model_reasoning_effort=high` on multi-step arithmetic). Every earlier sample
# on this machine reported 0, which is why the subset question could not be
# settled from existing data — and why the fixture this replaced, composed from
# the documented shape and never probed, carried no `cache_write_input_tokens`
# and no `reasoning_output_tokens` at all, which is how the dropped-split defect
# survived. Nothing from the prompt or the answer is copied here; the usage
# objects are pure counts.
_CODEX_TURNS = (
    (
        {
            "input_tokens": 12764,
            "cached_input_tokens": 9984,
            "cache_write_input_tokens": 0,
            "output_tokens": 155,
            "reasoning_output_tokens": 147,
        },
        12919,
    ),
    (
        {
            "input_tokens": 16824,
            "cached_input_tokens": 10496,
            "cache_write_input_tokens": 0,
            "output_tokens": 5,
            "reasoning_output_tokens": 0,
        },
        16829,
    ),
)


def _codex_stream(*usages: dict) -> str:
    """A codex `--json` stream carrying *usages*, wrapped in the real event kinds.

    The non-usage events are what a live run interleaves, so the reader has to
    skip them rather than assume a stream of nothing but `turn.completed`.
    """
    lines = ['{"type":"thread.started","thread_id":"t1"}']
    for usage in usages:
        lines.append('{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}')
        lines.append(json.dumps({"type": "turn.completed", "usage": usage}))
    return "\n".join(lines)


_CODEX_EVENTS = _codex_stream(*(usage for usage, _total in _CODEX_TURNS))


def test_extract_usage_claude_reads_tokens_and_cost() -> None:
    """The claude result object yields summed usage tokens plus total_cost_usd."""
    spec = _claude_json_spec()
    usage = runner.extract_usage(spec, _executed(spec, _CLAUDE_RESULT))
    assert usage is not None
    assert usage.tokens == 2 + 5960 + 15496 + 17
    assert usage.cost == pytest.approx(0.136147)
    assert usage.estimated is False


def test_extract_usage_claude_without_cost_field() -> None:
    """A usage block without total_cost_usd still reports tokens, cost null."""
    stdout = json.dumps({"usage": {"input_tokens": 10, "output_tokens": 5}})
    spec = _claude_json_spec()
    usage = runner.extract_usage(spec, _executed(spec, stdout))
    assert usage == runner.Usage(tokens=15, cost=None, estimated=False)


def test_extract_usage_claude_unparseable_falls_back_to_estimate() -> None:
    """Non-JSON output (e.g. an overridden command) degrades to the chars/4 estimate."""
    result = _executed(_claude_spec(), "plain text answer", stderr="warn")
    usage = runner.extract_usage(_claude_spec(), result)
    assert usage == runner.Usage(
        tokens=(len("plain text answer") + len("warn")) // 4, cost=None, estimated=True
    )


def test_extract_usage_claude_json_without_usage_block_estimates() -> None:
    """A parseable object missing the usage block still degrades to the estimate."""
    stdout = json.dumps({"type": "result", "result": "ok"})
    usage = runner.extract_usage(_claude_spec(), _executed(_claude_spec(), stdout))
    assert usage is not None
    assert usage.estimated is True


@pytest.mark.parametrize(("turn", "total_tokens"), _CODEX_TURNS)
def test_extract_usage_codex_total_matches_the_cli_own_total(turn: dict, total_tokens: int) -> None:
    """A single observed turn totals exactly what codex itself accounted for it.

    The identity that settles the summation semantics: codex's session rollout
    recorded `total_tokens` for this very turn, and input + output reproduces it
    to the token. Any addend beyond those two would overshoot it.
    """
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), _codex_stream(turn)))
    assert usage is not None
    assert usage.tokens == total_tokens
    assert usage.estimated is False


def test_extract_usage_codex_records_reasoning_without_adding_it() -> None:
    """`reasoning_output_tokens` lands on the split but never in the total.

    Measured, not assumed (basicly-jr0l.37): the probed turn spent 147 of its 155
    output tokens on reasoning, so summing the two would double-count 147 tokens
    and inflate a 12919-token turn to 13066.
    """
    turn, total_tokens = _CODEX_TURNS[0]
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), _codex_stream(turn)))
    assert usage is not None
    assert usage.reasoning_tokens == 147
    assert usage.tokens == total_tokens
    assert usage.tokens != total_tokens + 147
    # Subset of output, so the residue is the answer plus its framing.
    assert usage.output_tokens is not None and usage.reasoning_tokens <= usage.output_tokens


def test_extract_usage_codex_records_the_cache_split_without_adding_it() -> None:
    """Cached input is the portion *inside* `input_tokens`, not a separate addend.

    `input_tokens` is the superset (the same convention copilot's `inputTokens`
    follows), so the uncached remainder the pricing model needs is derivable as
    input minus cache-read rather than stored a fourth time — and adding
    cache-read back in would report 22903 tokens for a 12919-token turn.
    """
    turn, total_tokens = _CODEX_TURNS[0]
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), _codex_stream(turn)))
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens) == (12764, 155)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (9984, 0)
    assert usage.tokens == total_tokens
    assert usage.tokens != total_tokens + 9984
    # The uncached remainder is derivable, which is why it is not stored a fourth time.
    assert usage.input_tokens is not None and usage.cache_read_tokens is not None
    assert usage.input_tokens - usage.cache_read_tokens == 2780


def test_extract_usage_codex_keeps_cache_write_out_of_the_total() -> None:
    """Cache-write is recorded and, like cache-read, is not added to the total.

    Synthetic on purpose, and the one codex assertion here **not** backed by an
    observed number: every turn recorded on this machine reported
    `cache_write_input_tokens` 0, so the total_tokens identity cannot speak to it.
    The convention comes from the semantics the rest of the mapping follows —
    cache-written tokens are prompt tokens, so they sit inside `input_tokens`,
    which is verified outright on the copilot side (`inputTokens == input +
    cacheRead + cacheWrite` on 15 stores). Pinned so a build that disagrees
    reddens a test instead of silently under-counting a cache-warming turn.
    """
    stream = _codex_stream({
        "input_tokens": 1000,
        "cached_input_tokens": 600,
        "cache_write_input_tokens": 300,
        "output_tokens": 20,
        "reasoning_output_tokens": 8,
    })
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), stream))
    assert usage is not None
    assert usage.cache_write_tokens == 300
    assert usage.tokens == 1000 + 20


def test_extract_usage_codex_sums_the_split_across_turns() -> None:
    """A multi-turn stream meters once, per kind, over every turn's usage."""
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), _CODEX_EVENTS))
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens) == (12764 + 16824, 155 + 5)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (9984 + 10496, 0)
    assert usage.reasoning_tokens == 147 + 0
    assert usage.tokens == 12764 + 155 + 16824 + 5
    assert usage.cost is None and usage.credits is None


def test_extract_usage_codex_leaves_an_unreported_kind_null() -> None:
    """A build that emits no cache or reasoning counts records null, never zero.

    0.146.0 reports a real `reasoning_output_tokens` of 0 for a turn that did no
    reasoning, so a fabricated 0 would be indistinguishable from that measurement.
    """
    stream = _codex_stream({"input_tokens": 100, "output_tokens": 7})
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), stream))
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens) == (100, 7)
    assert usage.cache_read_tokens is None
    assert usage.cache_write_tokens is None
    assert usage.reasoning_tokens is None
    assert usage.tokens == 107


def test_extract_usage_codex_without_usage_events_estimates() -> None:
    """An event stream with no turn.completed usage degrades to the estimate."""
    stdout = '{"type":"thread.started","thread_id":"t1"}\nnot json\n'
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), stdout))
    assert usage is not None
    assert usage.estimated is True


def test_extract_usage_no_format_estimates_over_transcript() -> None:
    """A spec with no usage format meters the transcript at chars/4."""
    spec = RunnerSpec("acme", HEADLESS, ("acme", PROMPT_PLACEHOLDER))
    result = _executed(spec, "x" * 100, stderr="y" * 20)
    assert runner.extract_usage(spec, result) == runner.Usage(tokens=30, cost=None, estimated=True)


def test_extract_usage_none_when_nothing_executed() -> None:
    """A handoff or dry run has no transcript to meter: no usage, not a zero estimate."""
    handoff = RunResult(MANUAL_RUNNER, (), executed=False, handoff=True)
    assert runner.extract_usage(RunnerSpec(MANUAL_RUNNER, HANDOFF), handoff) is None
    dry = RunResult("claude", ("claude",), executed=False)
    assert runner.extract_usage(_claude_spec(), dry) is None
