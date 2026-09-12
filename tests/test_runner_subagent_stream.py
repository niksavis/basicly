from __future__ import annotations

import json
import sys
from pathlib import Path

from basicly import runner
from basicly.runner import (
    BUILTIN_RUNNERS,
    CLAUDE_STREAM_JSON,
    HEADLESS,
    PROMPT_PLACEHOLDER,
    RunnerSpec,
    RunResult,
    context_occupancy,
    extract_usage,
    format_command,
    observed_models,
    run,
)

FORWARD_FLAG = "--forward-subagent-text"

_TOOL_USE_ID = "toolu_01K53mB2SguD6493Ah6sTeiG"
_SUBAGENT = "general-purpose"


def _usage(inputs: int, creation: int, read: int, output: int) -> dict:
    return {
        "input_tokens": inputs,
        "cache_creation_input_tokens": creation,
        "cache_read_input_tokens": read,
        "output_tokens": output,
        "service_tier": "standard",
    }


LANE_SPAWN = {
    "type": "assistant",
    "message": {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": _TOOL_USE_ID,
                "name": "Agent",
                "input": {"description": "Reply with BANANA only"},
            }
        ],
        "usage": _usage(10, 920, 25303, 1),
    },
    "parent_tool_use_id": None,
}

FORWARDED_PROMPT = {
    "type": "user",
    "message": {
        "role": "user",
        "content": [{"type": "text", "text": "Your entire task is to reply BANANA."}],
    },
    "parent_tool_use_id": _TOOL_USE_ID,
    "subagent_type": _SUBAGENT,
    "task_description": "Reply with BANANA only",
}

FORWARDED_REPLY = {
    "type": "assistant",
    "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": "BANANA"}],
        "usage": _usage(10, 16273, 0, 8),
    },
    "parent_tool_use_id": _TOOL_USE_ID,
    "subagent_type": _SUBAGENT,
    "task_description": "Reply with BANANA only",
}

LANE_REPLY = {
    "type": "assistant",
    "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": "DONE"}],
        "usage": _usage(8, 400, 26223, 4),
    },
    "parent_tool_use_id": None,
}
RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "DONE",
    "total_cost_usd": 0.08309785,
    "usage": _usage(28, 26623, 51526, 504),
}

FORWARDED = (LANE_SPAWN, FORWARDED_PROMPT, FORWARDED_REPLY, LANE_REPLY, RESULT)
UNFORWARDED = (LANE_SPAWN, LANE_REPLY, RESULT)


def _transcript(events: tuple[dict, ...]) -> str:
    return "".join(json.dumps(event) + "\n" for event in events)


def _claude() -> RunnerSpec:
    return next(spec for spec in BUILTIN_RUNNERS if spec.name == "claude")


def _executed(spec: RunnerSpec, stdout: str) -> RunResult:
    return RunResult(spec.name, (spec.name,), executed=True, returncode=0, stdout=stdout)


def _emitting(transcript: str) -> RunnerSpec:
    body = f"import sys\nsys.stdout.write({transcript!r})\n"
    return RunnerSpec(
        "claude",
        HEADLESS,
        (sys.executable, "-c", body, PROMPT_PLACEHOLDER),
        usage_format=CLAUDE_STREAM_JSON,
    )


def test_forward_subagent_text_reaches_a_metered_claude_dispatch() -> None:

    argv = format_command(_claude(), "go", capture_usage=True)

    assert FORWARD_FLAG in argv
    assert "--output-format" in argv and "stream-json" in argv and "--verbose" in argv


def test_forward_subagent_text_is_absent_from_an_unmetered_claude_dispatch() -> None:
    assert FORWARD_FLAG not in format_command(_claude(), "go")


def test_forward_subagent_text_is_not_passed_to_a_runner_that_is_not_claude() -> None:
    others = [spec for spec in BUILTIN_RUNNERS if spec.name != "claude" and spec.command]

    assert others, "expected built-in adapters besides claude"
    for spec in others:
        argv = format_command(spec, "go", capture_usage=True, session_id="s")
        assert FORWARD_FLAG not in argv, spec.name


def test_a_forwarded_subagent_reply_surfaces_as_a_progress_line(tmp_path: Path) -> None:

    seen: list = []

    run(
        _emitting(_transcript(FORWARDED)),
        "go",
        tmp_path,
        capture_usage=True,
        on_event=seen.append,
        timeout=60.0,
    )

    forwarded = [event for event in seen if event.subagent is not None]
    assert [event.text for event in forwarded] == ["Your entire task is to reply BANANA.", "BANANA"]
    assert {event.subagent for event in forwarded} == {_SUBAGENT}


def test_the_lane_agents_own_progress_is_surfaced_without_a_subagent(tmp_path: Path) -> None:
    seen: list = []

    run(
        _emitting(_transcript(FORWARDED)),
        "go",
        tmp_path,
        capture_usage=True,
        on_event=seen.append,
        timeout=60.0,
    )

    own = [event for event in seen if event.data is not None and event.subagent is None]
    assert [event.text for event in own] == [None, "DONE", None]


def test_a_forwarded_turn_moves_no_token_total(tmp_path: Path) -> None:

    spec = _claude()
    forwarded, plain = _transcript(FORWARDED), _transcript(UNFORWARDED)

    assert extract_usage(spec, _executed(spec, forwarded)) == extract_usage(
        spec, _executed(spec, plain)
    )
    assert context_occupancy(spec, _executed(spec, forwarded)) == context_occupancy(
        spec, _executed(spec, plain)
    )

    def live(transcript: str) -> int:
        seen: list = []
        run(
            _emitting(transcript),
            "go",
            tmp_path,
            capture_usage=True,
            on_event=seen.append,
            timeout=60.0,
        )
        return sum(event.usage.tokens for event in seen if event.usage is not None)

    assert live(forwarded) == live(plain)


def test_occupancy_reads_the_lane_not_a_subagent_left_last() -> None:

    spec = _claude()
    killed = _transcript((LANE_SPAWN, FORWARDED_PROMPT, FORWARDED_REPLY))

    occupancy = context_occupancy(spec, _executed(spec, killed))

    assert occupancy == 10 + 920 + 25303 + 1


def test_a_subagents_model_is_not_read_as_the_lanes_own() -> None:

    spec = _claude()
    lane = {**LANE_REPLY, "message": {**LANE_REPLY["message"], "model": "claude-lane-1"}}
    nested = {**FORWARDED_REPLY, "message": {**FORWARDED_REPLY["message"], "model": "sub-2"}}

    seen = observed_models(spec, _executed(spec, _transcript((lane, nested))))

    assert seen == ("claude-lane-1",)


def test_an_unexpected_payload_shape_still_reaches_the_sink(tmp_path: Path) -> None:

    odd = {"type": "assistant", "message": "not an object", "parent_tool_use_id": []}
    seen: list = []

    result = run(
        _emitting(_transcript((odd, LANE_REPLY, RESULT))),
        "go",
        tmp_path,
        capture_usage=True,
        on_event=seen.append,
        timeout=60.0,
    )

    assert len(seen) == 3, "the reader kept going past the unexpected event"
    assert seen[0].data == odd and seen[0].usage is None and seen[0].text is None
    assert seen[1].text == "DONE" and seen[1].usage is not None
    assert result.returncode == 0


def test_tool_names_are_read_off_a_real_captured_turn() -> None:

    spec = _claude()
    assert runner.event_tools(spec, LANE_SPAWN) == ("Agent",)


def test_a_turn_calling_no_tool_reports_none_rather_than_an_empty_name() -> None:
    spec = _claude()
    text_turn = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
    assert runner.event_tools(spec, text_turn) == ()


def test_tool_names_keep_the_order_the_turn_emitted_them() -> None:
    spec = _claude()
    turn = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "name": "Read", "id": "a"},
                {"type": "text", "text": "then"},
                {"type": "tool_use", "name": "Edit", "id": "b"},
            ]
        },
    }
    assert runner.event_tools(spec, turn) == ("Read", "Edit")


def test_a_non_list_content_reports_no_tools_rather_than_raising() -> None:

    spec = _claude()
    assert runner.event_tools(spec, {"type": "user", "message": {"content": "tool result"}}) == ()
    assert runner.event_tools(spec, {}) == ()


def test_a_non_claude_family_reports_no_tools() -> None:
    codex = next(s for s in runner.BUILTIN_RUNNERS if s.name == "codex")
    assert runner.event_tools(codex, LANE_SPAWN) == ()
