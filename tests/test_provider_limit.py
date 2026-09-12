from __future__ import annotations

import json

from basicly import provider_limit, runner
from basicly.runner_envelope import CLAUDE_STREAM_JSON, CODEX_JSONL

SAID = "You've hit your session limit · resets 5:50pm (Europe/Vienna)"

_ZEROS = {
    "input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "output_tokens": 0,
}


def _turn(text: str, *, model: str, tokens: int) -> str:
    usage = dict(_ZEROS, input_tokens=tokens)
    return json.dumps({
        "type": "assistant",
        "message": {
            "model": model,
            "usage": usage,
            "content": [{"type": "text", "text": text}],
        },
    })


def _stream(*events: str) -> str:
    return "\n".join([
        '{"type":"system","subtype":"init","model":"claude-opus-5"}',
        '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed"}}',
        *events,
        '{"type":"result","subtype":"success","result":""}',
    ])


def test_the_captured_session_limit_refusal_is_recognised_with_its_reset_time() -> None:
    stdout = _stream(_turn(SAID, model=provider_limit.SYNTHETIC_MODEL, tokens=0))

    refusal = provider_limit.refusal(CLAUDE_STREAM_JSON, stdout)

    assert refusal is not None
    assert refusal.said == SAID
    assert "5:50pm (Europe/Vienna)" in refusal.detail


def test_a_turn_reporting_no_tokens_is_a_refusal_without_the_synthetic_marker() -> None:

    stdout = _stream(_turn(SAID, model="claude-opus-5", tokens=0))

    assert provider_limit.refusal(CLAUDE_STREAM_JSON, stdout) is not None


def test_a_healthy_dispatch_carrying_a_rate_limit_event_is_not_a_refusal() -> None:
    stdout = _stream(_turn("Reading the module now.", model="claude-opus-5", tokens=4210))

    assert provider_limit.refusal(CLAUDE_STREAM_JSON, stdout) is None


def test_an_agent_writing_about_a_limit_is_not_a_refusal() -> None:
    stdout = _stream(
        _turn("The rework limit is 2, so the lane escalates.", model="claude-opus-5", tokens=980)
    )

    assert provider_limit.refusal(CLAUDE_STREAM_JSON, stdout) is None


def test_a_synthesized_turn_about_something_else_is_not_a_limit_refusal() -> None:
    stdout = _stream(_turn("Prompt is too long", model=provider_limit.SYNTHETIC_MODEL, tokens=0))

    assert provider_limit.refusal(CLAUDE_STREAM_JSON, stdout) is None


def test_an_adapter_whose_refusal_was_never_captured_reports_none() -> None:
    stdout = _stream(_turn(SAID, model=provider_limit.SYNTHETIC_MODEL, tokens=0))

    assert provider_limit.refusal(CODEX_JSONL, stdout) is None
    assert provider_limit.refusal(None, stdout) is None


def test_a_transcript_that_does_not_parse_reports_none() -> None:
    assert provider_limit.refusal(CLAUDE_STREAM_JSON, "") is None
    assert provider_limit.refusal(CLAUDE_STREAM_JSON, 'Warning: no stdin\n{"type":"assis') is None
    assert provider_limit.refusal(CLAUDE_STREAM_JSON, '{"type":"assistant","message":7}') is None


def test_the_dispatched_claude_adapter_reports_in_the_format_the_reader_parses() -> None:

    claude = next(spec for spec in runner.BUILTIN_RUNNERS if spec.name == "claude")

    assert claude.usage_format == CLAUDE_STREAM_JSON
