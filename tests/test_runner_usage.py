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
    return _claude_spec().__class__(
        "claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER), usage_format=CLAUDE_JSON
    )


def _codex_spec() -> RunnerSpec:
    return next(s for s in BUILTIN_RUNNERS if s.name == "codex")


def _executed(spec: RunnerSpec, stdout: str, stderr: str = "") -> RunResult:
    return RunResult(
        spec.name, (spec.name,), executed=True, returncode=0, stdout=stdout, stderr=stderr
    )


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

    lines = ['{"type":"thread.started","thread_id":"t1"}']
    for usage in usages:
        lines.append('{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}')
        lines.append(json.dumps({"type": "turn.completed", "usage": usage}))
    return "\n".join(lines)


_CODEX_EVENTS = _codex_stream(*(usage for usage, _total in _CODEX_TURNS))


def test_extract_usage_claude_reads_tokens_and_cost() -> None:
    spec = _claude_json_spec()
    usage = runner.extract_usage(spec, _executed(spec, _CLAUDE_RESULT))
    assert usage is not None
    assert usage.tokens == 2 + 5960 + 15496 + 17
    assert usage.cost == pytest.approx(0.136147)
    assert usage.estimated is False


def test_extract_usage_claude_without_cost_field() -> None:

    stdout = json.dumps({"usage": {"input_tokens": 10, "output_tokens": 5}})
    spec = _claude_json_spec()
    usage = runner.extract_usage(spec, _executed(spec, stdout))
    assert usage == runner.Usage(
        tokens=15,
        cost=None,
        estimated=False,
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=None,
        cache_write_tokens=None,
    )


def test_extract_usage_claude_carries_the_cache_split() -> None:

    spec = _claude_json_spec()
    usage = runner.extract_usage(spec, _executed(spec, _CLAUDE_RESULT))
    assert usage is not None
    assert usage.cache_read_tokens == 15496
    assert usage.cache_write_tokens == 5960
    assert usage.output_tokens == 17


def test_extract_usage_claude_normalises_input_to_the_superset() -> None:

    spec = _claude_json_spec()
    usage = runner.extract_usage(spec, _executed(spec, _CLAUDE_RESULT))
    assert usage is not None
    assert usage.input_tokens == 2 + 5960 + 15496
    assert usage.input_tokens != 2, "the raw field was stored, not the folded superset"
    assert usage.cache_read_tokens is not None
    assert usage.input_tokens - usage.cache_read_tokens == 2 + 5960


def test_extract_usage_claude_unparseable_falls_back_to_estimate() -> None:
    result = _executed(_claude_spec(), "plain text answer", stderr="warn")
    usage = runner.extract_usage(_claude_spec(), result)
    assert usage == runner.Usage(
        tokens=(len("plain text answer") + len("warn")) // 4, cost=None, estimated=True
    )


def test_extract_usage_claude_json_without_usage_block_estimates() -> None:
    stdout = json.dumps({"type": "result", "result": "ok"})
    usage = runner.extract_usage(_claude_spec(), _executed(_claude_spec(), stdout))
    assert usage is not None
    assert usage.estimated is True


@pytest.mark.parametrize(("turn", "total_tokens"), _CODEX_TURNS)
def test_extract_usage_codex_total_matches_the_cli_own_total(turn: dict, total_tokens: int) -> None:

    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), _codex_stream(turn)))
    assert usage is not None
    assert usage.tokens == total_tokens
    assert usage.estimated is False


def test_extract_usage_codex_records_reasoning_without_adding_it() -> None:

    turn, total_tokens = _CODEX_TURNS[0]
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), _codex_stream(turn)))
    assert usage is not None
    assert usage.reasoning_tokens == 147
    assert usage.tokens == total_tokens
    assert usage.tokens != total_tokens + 147
    assert usage.output_tokens is not None and usage.reasoning_tokens <= usage.output_tokens


def test_extract_usage_codex_records_the_cache_split_without_adding_it() -> None:

    turn, total_tokens = _CODEX_TURNS[0]
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), _codex_stream(turn)))
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens) == (12764, 155)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (9984, 0)
    assert usage.tokens == total_tokens
    assert usage.tokens != total_tokens + 9984
    assert usage.input_tokens is not None and usage.cache_read_tokens is not None
    assert usage.input_tokens - usage.cache_read_tokens == 2780


def test_extract_usage_codex_keeps_cache_write_out_of_the_total() -> None:

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
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), _CODEX_EVENTS))
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens) == (12764 + 16824, 155 + 5)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (9984 + 10496, 0)
    assert usage.reasoning_tokens == 147 + 0
    assert usage.tokens == 12764 + 155 + 16824 + 5
    assert usage.cost is None and usage.credits is None


def test_extract_usage_codex_leaves_an_unreported_kind_null() -> None:

    stream = _codex_stream({"input_tokens": 100, "output_tokens": 7})
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), stream))
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens) == (100, 7)
    assert usage.cache_read_tokens is None
    assert usage.cache_write_tokens is None
    assert usage.reasoning_tokens is None
    assert usage.tokens == 107


def test_extract_usage_codex_without_usage_events_estimates() -> None:
    stdout = '{"type":"thread.started","thread_id":"t1"}\nnot json\n'
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), stdout))
    assert usage is not None
    assert usage.estimated is True


def test_extract_usage_no_format_estimates_over_transcript() -> None:
    spec = RunnerSpec("acme", HEADLESS, ("acme", PROMPT_PLACEHOLDER))
    result = _executed(spec, "x" * 100, stderr="y" * 20)
    assert runner.extract_usage(spec, result) == runner.Usage(tokens=30, cost=None, estimated=True)


def test_extract_usage_none_when_nothing_executed() -> None:
    handoff = RunResult(MANUAL_RUNNER, (), executed=False, handoff=True)
    assert runner.extract_usage(RunnerSpec(MANUAL_RUNNER, HANDOFF), handoff) is None
    dry = RunResult("claude", ("claude",), executed=False)
    assert runner.extract_usage(_claude_spec(), dry) is None
