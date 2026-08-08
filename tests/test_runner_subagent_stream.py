"""Forwarded subagent turns: passed on the argv, surfaced, and never counted.

`--forward-subagent-text` makes a lane's nested subagent work reach the same
stream the harness already reads (basicly-u2hl.7). The hazard it brings with it
is the reason these live in their own module: a forwarded turn is an ordinary
``assistant`` event carrying its own ``message.usage``, so the readers that meter
a lane would happily count a nested agent's tokens as the lane's. Token
accounting gates spend, and a wrong number there is worse than no forwarding —
so the regression these pin is that forwarding moves *no* total.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

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

# Captured from a real dispatch, not composed from documentation: Claude Code
# 2.1.226 on 2026-08-08, `claude -p "<prompt spawning one subagent>"
# --output-format stream-json --verbose --forward-subagent-text`. Every field the
# readers under test key on is verbatim from that capture — `type`,
# `parent_tool_use_id`, `subagent_type`, `message.content` and every key of
# `message.usage`. Dropped for size, and read by nothing here: the envelope's
# identifiers (uuid, timestamp, session_id, request_id, model), the `thinking`
# blocks and their signature blobs, and the result event's `modelUsage` map.
_TOOL_USE_ID = "toolu_01K53mB2SguD6493Ah6sTeiG"
_SUBAGENT = "general-purpose"


def _usage(inputs: int, creation: int, read: int, output: int) -> dict:
    """A claude turn's usage block, with the four keys the token readers sum."""
    return {
        "input_tokens": inputs,
        "cache_creation_input_tokens": creation,
        "cache_read_input_tokens": read,
        "output_tokens": output,
        "service_tier": "standard",
    }


# The lane's own turn that spawns the subagent: a `tool_use` block, no text.
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

# The subagent's prompt, forwarded as a `user` event. Carries no usage block.
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

# The subagent's reply, forwarded as an `assistant` event — and it carries a full
# usage block of its own. This is the event that would corrupt the meters.
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

# The lane's own final answer, and the terminating result object.
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
# The same dispatch as it reached the harness before the flag: identical lane
# turns, with the two forwarded events simply absent.
UNFORWARDED = (LANE_SPAWN, LANE_REPLY, RESULT)


def _transcript(events: tuple[dict, ...]) -> str:
    """*events* as the JSONL stdout a dispatch would have produced."""
    return "".join(json.dumps(event) + "\n" for event in events)


def _claude() -> RunnerSpec:
    """The shipped claude adapter, whose usage format is the streaming one."""
    return next(spec for spec in BUILTIN_RUNNERS if spec.name == "claude")


def _executed(spec: RunnerSpec, stdout: str) -> RunResult:
    """A finished dispatch of *spec* whose stdout was *stdout*."""
    return RunResult(spec.name, (spec.name,), executed=True, returncode=0, stdout=stdout)


def _emitting(transcript: str) -> RunnerSpec:
    """A claude-shaped spec whose "CLI" writes *transcript* to stdout and exits."""
    body = f"import sys\nsys.stdout.write({transcript!r})\n"
    return RunnerSpec(
        "claude",
        HEADLESS,
        (sys.executable, "-c", body, PROMPT_PLACEHOLDER),
        usage_format=CLAUDE_STREAM_JSON,
    )


# --- the flag reaches the argv, and only claude's ------------------------------


def test_forward_subagent_text_reaches_a_metered_claude_dispatch() -> None:
    """The flag is only legal beside `--print` and `--output-format=stream-json`.

    So it rides with the usage flags rather than the command template: it is
    appended exactly when the dispatch that makes it legal is being built.
    """
    argv = format_command(_claude(), "go", capture_usage=True)

    assert FORWARD_FLAG in argv
    assert "--output-format" in argv and "stream-json" in argv and "--verbose" in argv


def test_forward_subagent_text_is_absent_from_an_unmetered_claude_dispatch() -> None:
    """No stream was asked for, so there is nothing legal to forward into."""
    assert FORWARD_FLAG not in format_command(_claude(), "go")


def test_forward_subagent_text_is_not_passed_to_a_runner_that_is_not_claude() -> None:
    """It is a claude flag; every other CLI would reject the argv outright."""
    others = [spec for spec in BUILTIN_RUNNERS if spec.name != "claude" and spec.command]

    assert others, "expected built-in adapters besides claude"
    for spec in others:
        argv = format_command(spec, "go", capture_usage=True, session_id="s")
        assert FORWARD_FLAG not in argv, spec.name


# --- the forwarded work is surfaced -------------------------------------------


def test_a_forwarded_subagent_reply_surfaces_as_a_progress_line(tmp_path: Path) -> None:
    """The point of forwarding: nested work becomes visible instead of silent.

    The sink is handed the subagent's own prose and the name of the subagent that
    produced it, so a watcher can tell delegation from a wedge.
    """
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
    """A lane turn is not attributed to a subagent, and a tool call has no prose."""
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


# --- and it moves no total ----------------------------------------------------


def test_a_forwarded_turn_moves_no_token_total(tmp_path: Path) -> None:
    """The regression that matters: forwarding is additive, never accounted.

    The forwarded reply carries a full usage block, so every reader here would
    have counted it. Each is asserted against the same dispatch without the
    forwarded events — the totals the harness recorded before the flag existed.
    """
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
    """A dispatch killed mid-delegation ends on a forwarded turn.

    Reading that one would report the subagent's window — 16283 tokens here — as
    the lane's occupancy, and the ceiling meter divides by the lane's window.
    """
    spec = _claude()
    killed = _transcript((LANE_SPAWN, FORWARDED_PROMPT, FORWARDED_REPLY))

    occupancy = context_occupancy(spec, _executed(spec, killed))

    assert occupancy == 10 + 920 + 25303 + 1


def test_a_subagents_model_is_not_read_as_the_lanes_own() -> None:
    """`observed_models` feeds `model_mismatch`, which compares against the pin.

    A subagent runs its own tier's model, so counting the model named on a
    forwarded turn would report a mismatch for a dispatch that honoured its pin.
    The turn scan only runs when the result event carried no `modelUsage` — a
    killed dispatch — which is exactly when a forwarded turn may be the last one.
    """
    spec = _claude()
    lane = {**LANE_REPLY, "message": {**LANE_REPLY["message"], "model": "claude-lane-1"}}
    nested = {**FORWARDED_REPLY, "message": {**FORWARDED_REPLY["message"], "model": "sub-2"}}

    seen = observed_models(spec, _executed(spec, _transcript((lane, nested))))

    assert seen == ("claude-lane-1",)


# --- an unexpected payload shape breaks nothing --------------------------------


def test_an_unexpected_payload_shape_still_reaches_the_sink(tmp_path: Path) -> None:
    """A later release's event shape must not silence the reader or move a total.

    The readers are total over any JSON object rather than guarded by a catch,
    and this is what that has to mean in practice: an `assistant` event whose
    `message` is not an object and whose `parent_tool_use_id` is not a string
    yields no text and no usage, and the reader carries on to the next line.
    """
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
