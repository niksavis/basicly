"""Tests for where an agent CLI's records sit in captured stdout (`runner_envelope`).

Both are asserted through `runner.result_text`, the envelope reader's only caller: the
subject is which record the locator picks out of a transcript — the *last* agent
message rather than the concatenation of them, and nothing at all when the adapter did
not emit the shape its format declares.

Split out of `tests/test_runner.py` with the module they cover.
"""

from __future__ import annotations

from basicly import runner
from basicly.runner import (
    BUILTIN_RUNNERS,
    CLAUDE_JSON,
    HEADLESS,
    PROMPT_PLACEHOLDER,
    RunnerSpec,
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


def test_result_text_takes_codex_last_agent_message() -> None:
    """A multi-turn codex run answers in its final message, not its narration.

    The earlier `agent_message` events are progress notes from before the tool
    calls; concatenating them would prepend commentary to a reply a caller is
    about to parse as one JSON object.
    """
    stream = "\n".join([
        '{"type":"thread.started","thread_id":"t1"}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"looking into it"}}',
        '{"type":"item.completed","item":{"type":"reasoning","text":"not a message"}}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"the answer"}}',
        '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":2}}',
    ])
    assert runner.result_text(_codex_spec(), stream) == "the answer"


def test_result_text_falls_back_to_the_transcript_with_no_parseable_envelope() -> None:
    """An adapter that did not emit its declared shape has no reply hidden elsewhere.

    Blanking the transcript instead would throw away the only text there is —
    including the CLI's own error message, which is what a caller shows a human.
    Both callers fail closed on it anyway: an envelope is not a parseable answer
    to either of them.
    """
    for spec in (_claude_json_spec(), _claude_spec(), _codex_spec()):
        assert runner.result_text(spec, "error: not logged in") == "error: not logged in"
    # Parseable JSON, but not the envelope: no `result` field, and no agent_message.
    no_result, no_message = '{"type":"result"}', '{"type":"turn.completed"}'
    assert runner.result_text(_claude_json_spec(), no_result) == no_result
    assert runner.result_text(_codex_spec(), no_message) == no_message
