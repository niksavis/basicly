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
    return _claude_spec().__class__(
        "claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER), usage_format=CLAUDE_JSON
    )


def _codex_spec() -> RunnerSpec:
    return next(s for s in BUILTIN_RUNNERS if s.name == "codex")


def test_result_text_takes_codex_last_agent_message() -> None:

    stream = "\n".join([
        '{"type":"thread.started","thread_id":"t1"}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"looking into it"}}',
        '{"type":"item.completed","item":{"type":"reasoning","text":"not a message"}}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"the answer"}}',
        '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":2}}',
    ])
    assert runner.result_text(_codex_spec(), stream) == "the answer"


def test_result_text_falls_back_to_the_transcript_with_no_parseable_envelope() -> None:

    for spec in (_claude_json_spec(), _claude_spec(), _codex_spec()):
        assert runner.result_text(spec, "error: not logged in") == "error: not logged in"
    no_result, no_message = '{"type":"result"}', '{"type":"turn.completed"}'
    assert runner.result_text(_claude_json_spec(), no_result) == no_result
    assert runner.result_text(_codex_spec(), no_message) == no_message
