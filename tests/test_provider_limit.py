"""The provider's own refusal, told apart from a run that failed on its merits.

The refusal fixture is this repo's own captured incident, reassembled from two artifacts
that survived it: `.basicly/usage/lane-logs/.../basicly-seu7rx.jsonl` recorded the event
sequence and the synthesized turn's text verbatim, and `run-records.json` recorded
``observed_models == ["claude-opus-5", "<synthetic>"]`` for the same dispatches. Nothing
here is composed from documentation.
"""

from __future__ import annotations

import json

from basicly import provider_limit, runner
from basicly.runner_envelope import CLAUDE_STREAM_JSON, CODEX_JSONL

# What the CLI said, character for character (35 events over 7 lanes, 2026-08-28 15:43
# to 15:50Z). The middle dot and the curly apostrophe are the adapter's, not a
# transcription: a rule keyed on the exact sentence would have to reproduce both.
SAID = "You've hit your session limit · resets 5:50pm (Europe/Vienna)"

_ZEROS = {
    "input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "output_tokens": 0,
}


def _turn(text: str, *, model: str, tokens: int) -> str:
    """One assistant event: *model* wrote *text* and the turn reports *tokens* input."""
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
    """A dispatch's stdout: the init and rate-limit events every run opens with, then *events*."""
    return "\n".join([
        '{"type":"system","subtype":"init","model":"claude-opus-5"}',
        # Present on a *healthy* dispatch too — pinned that way against a live probe in
        # `tests/test_runner.py` — which is why the detector cannot key on it.
        '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed"}}',
        *events,
        '{"type":"result","subtype":"success","result":""}',
    ])


def test_the_captured_session_limit_refusal_is_recognised_with_its_reset_time() -> None:
    """The reset time is the one fact the operator needs and the harness cannot derive."""
    stdout = _stream(_turn(SAID, model=provider_limit.SYNTHETIC_MODEL, tokens=0))

    refusal = provider_limit.refusal(CLAUDE_STREAM_JSON, stdout)

    assert refusal is not None
    assert refusal.said == SAID
    assert "5:50pm (Europe/Vienna)" in refusal.detail


def test_a_turn_reporting_no_tokens_is_a_refusal_without_the_synthetic_marker() -> None:
    """The fail-safe half: `<synthetic>` is derived from a record, the zero turn is captured.

    Resting on the derivation alone would leave the detector silently inert if the model
    name sits somewhere else in the envelope than this reasoned it does.
    """
    stdout = _stream(_turn(SAID, model="claude-opus-5", tokens=0))

    assert provider_limit.refusal(CLAUDE_STREAM_JSON, stdout) is not None


def test_a_healthy_dispatch_carrying_a_rate_limit_event_is_not_a_refusal() -> None:
    """The bead's stated signal, refuted: an allowed dispatch emits one too."""
    stdout = _stream(_turn("Reading the module now.", model="claude-opus-5", tokens=4210))

    assert provider_limit.refusal(CLAUDE_STREAM_JSON, stdout) is None


def test_an_agent_writing_about_a_limit_is_not_a_refusal() -> None:
    """The word alone must not convict a turn a model actually produced."""
    stdout = _stream(
        _turn("The rework limit is 2, so the lane escalates.", model="claude-opus-5", tokens=980)
    )

    assert provider_limit.refusal(CLAUDE_STREAM_JSON, stdout) is None


def test_a_synthesized_turn_about_something_else_is_not_a_limit_refusal() -> None:
    """The CLI interjects for other reasons; only a limit stops the pass dispatching."""
    stdout = _stream(_turn("Prompt is too long", model=provider_limit.SYNTHETIC_MODEL, tokens=0))

    assert provider_limit.refusal(CLAUDE_STREAM_JSON, stdout) is None


def test_an_adapter_whose_refusal_was_never_captured_reports_none() -> None:
    """Codex meters in the same JSONL shape, and no refusal of its has been observed."""
    stdout = _stream(_turn(SAID, model=provider_limit.SYNTHETIC_MODEL, tokens=0))

    assert provider_limit.refusal(CODEX_JSONL, stdout) is None
    assert provider_limit.refusal(None, stdout) is None


def test_a_transcript_that_does_not_parse_reports_none() -> None:
    """A killed dispatch ends mid-line and a CLI interleaves plain text; neither may raise."""
    assert provider_limit.refusal(CLAUDE_STREAM_JSON, "") is None
    assert provider_limit.refusal(CLAUDE_STREAM_JSON, 'Warning: no stdin\n{"type":"assis') is None
    assert provider_limit.refusal(CLAUDE_STREAM_JSON, '{"type":"assistant","message":7}') is None


def test_the_dispatched_claude_adapter_reports_in_the_format_the_reader_parses() -> None:
    """The seam, pinned: a detector reading a format nothing dispatches is dead in production.

    Every test above hands the reader its own fixture, so all of them would still pass with
    the builtin spec reporting in some other envelope and the refusal never recognised.
    """
    claude = next(spec for spec in runner.BUILTIN_RUNNERS if spec.name == "claude")

    assert claude.usage_format == CLAUDE_STREAM_JSON
