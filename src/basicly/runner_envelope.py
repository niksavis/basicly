"""Where an agent CLI's own records sit inside the stdout a dispatch captured.

One responsibility, and it is a *source*: the captured transcript. Every function here
takes a dispatch's raw stdout and hands back a record the adapter wrote into it — the
terminating result object, the assistant turns, the reply item, the per-turn usage
blocks. Nothing here sums, prices or judges what it finds.

The boundary is *which source* against *what is read off it*.
:mod:`basicly.copilot_store` covers the other source, the one family that reports out
of band; :mod:`basicly.runner_usage` turns whichever record came back into a total; and
``runner`` decides which of them to ask, because it is the module that knows which
adapter it dispatched.

The usage-format vocabulary is here because a format name is what selects an envelope,
so the constant and the reader it picks belong together — and the dispatch side that
appends the flags then keys on the same spelling the reading side parses back rather
than on a second copy of it.

The token-key tuples are here for a sharper version of that reason: which fields of a
family's usage block are disjoint is a fact about the *record*, not about any one total
taken off it — and two callers take different totals off the very same fields
(``runner_usage`` sums across turns for cost, ``runner.context_occupancy`` reads a
single turn for occupancy). Held in one place, the two cannot come to disagree about
what a provider's fields mean.

Split out of ``runner`` when the module-size ratchet caught that module growing (32,295
tokens against a frozen 31,114). The seam is a responsibility rather than an arithmetic
convenience, and the check is that nothing here imports back: locating a record in a
transcript needs none of the invocation, bounding or recording ``runner`` exists to do.
"""

from __future__ import annotations

import json

# The stdout-reported usage formats (basicly-kjc5.1): how a usage-capturing
# dispatch asks the CLI to report token usage, and so which reader below parses
# what comes back. `copilot-session-store` is the fourth format and is named in
# :mod:`basicly.copilot_store`, because its record is not in stdout at all;
# ``runner.USAGE_FORMATS`` is where all four meet, since dispatching on the
# format is that module's job.
CLAUDE_JSON = "claude-json"  # `--output-format json`: one result object with a usage block
# `--output-format stream-json --verbose`: JSONL events, one per turn, ending in
# the same result object. The only claude envelope that carries *per-turn* usage,
# which is what the context-ceiling meter needs (basicly-kjc5.14).
CLAUDE_STREAM_JSON = "claude-stream-json"
CODEX_JSONL = "codex-jsonl"  # `--json`: JSONL event stream with turn.completed usage

# The codex `--json` events that carry the agent's own reply, as opposed to its
# usage: an `item.completed` whose item is an `agent_message` (probed 0.146.0).
# :func:`runner.result_text` reads the reply out of these so a metered codex
# dispatch still has a parseable answer.
CODEX_ITEM_COMPLETED = "item.completed"
CODEX_AGENT_MESSAGE = "agent_message"
# The codex `--json` event that carries a turn's token usage. Named because both
# the batch reader (:func:`codex_turn_usages`) and the incremental one
# (:func:`runner_usage.codex_turn_usage`) key on it, and they must key on the
# same string.
CODEX_TURN_COMPLETED = "turn.completed"

# How claude marks a stream event forwarded from a nested subagent, and what
# names that subagent. Captured from a real 2.1.226 dispatch: a forwarded event
# is an ordinary `assistant`/`user` event carrying its own `message.usage`, so
# `parent_tool_use_id` is the only thing telling a nested turn from a lane turn.
CLAUDE_PARENT_TOOL_USE_ID = "parent_tool_use_id"
CLAUDE_SUBAGENT_TYPE = "subagent_type"
UNNAMED_SUBAGENT = "subagent"  # a forwarded event that names no type

# Claude usage-block keys: input_tokens excludes the cache fields (Anthropic
# usage semantics), so the total processed is the sum of all four.
CLAUDE_TOKEN_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)
# Codex usage keys summed into the total. Verified against codex-cli 0.146.0's
# own arithmetic by a live probe (2026-07-31, basicly-jr0l.37): a turn reporting
# input_tokens 12764, cached_input_tokens 9984, cache_write_input_tokens 0,
# output_tokens 155 and reasoning_output_tokens 147 is accounted
# total_tokens 12919 in the session rollout, and 12764 + 155 == 12919 exactly.
# So cached_input_tokens is a subset of input_tokens and reasoning_output_tokens
# is a subset of output_tokens (the probe's visible answer was 4 characters, so
# 155 - 147 is the answer plus framing) — adding either would double-count.
CODEX_TOKEN_KEYS = ("input_tokens", "output_tokens")


def stream_object(line: str) -> dict | None:
    """One stream line parsed as a JSON object, or None when it is not one.

    The same tolerance the batch readers have (:func:`stream_events`): a
    dispatch interleaves plain-text warnings with its stream and a killed one ends
    mid-line, so an unparseable line is skipped rather than fatal.
    """
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def stream_events(stdout: str) -> list[dict]:
    """The parseable JSON objects in a JSONL transcript, in order.

    The batch half of :func:`stream_object`, deferring to it so the two cannot
    drift: a line the incremental reader hands a sink is a line this one counts.
    Both adapters share it — claude's ``stream-json`` and codex's ``--json`` are
    the same envelope, and three hand-rolled copies of this loop agreed only by
    accident.
    """
    return [obj for line in stdout.splitlines() if (obj := stream_object(line)) is not None]


def claude_result_object(stdout: str) -> dict | None:
    """Claude's result object, located rather than assumed to be all of *stdout*.

    Both readers of it used to require ``stdout`` to be pure JSON, which made the
    non-streaming envelope intolerant of anything the CLI prints around it. The
    streaming reader never was — :func:`stream_events` skips lines it does
    not recognise — and the noise is not a property of the output format: the
    warning this module's own fixture pins ("no stdin data received in 3s") comes
    from the CLI's stdin handling, so a format that emits one object is exposed to
    it just the same. That was not observed on the ``json`` arm; it is inferred
    from the arm where it *was* observed, and hardened for because of what it
    costs. A leading line there reproduced both halves of basicly-gczc at once —
    the reply unreadable *and* the record estimated, which halts the grant — so
    the tolerant read is the one that cannot fail open.

    Takes the **last** parseable top-level object, matching the streaming
    reader's "last result event" rule, and falls back to parsing the whole
    transcript so a pretty-printed object spanning several lines still reads.
    """
    events = stream_events(stdout)
    if events:
        return events[-1]
    try:
        obj = json.loads(stdout.strip() or "null")
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def claude_result_field(stdout: str) -> str | None:
    """The ``result`` string of claude's result object (one JSON object), or None.

    Shared by both claude envelopes because the streaming one ends in the very
    same object — see :func:`claude_result_event`. None on any parse miss, or on
    a result field that is not a string (an ``is_error`` envelope can carry a
    structured payload there), so :func:`runner.result_text` falls back to the
    transcript. An empty string is a real answer — the agent printed nothing —
    and is returned as one.
    """
    obj = claude_result_object(stdout)
    if obj is None:
        return None
    value = obj.get("result")
    return value if isinstance(value, str) else None


def claude_result_event(stdout: str) -> str:
    """The stream's terminating ``result`` event, re-serialized, or an empty string.

    Lets the cumulative cost/token view reuse the non-streaming parser: the
    stream ends in the very same result object the non-streaming envelope emits,
    so there is one parser for it and no second definition of "total".
    """
    for event in reversed(stream_events(stdout)):
        if event.get("type") == "result":
            return json.dumps(event)
    return ""


def claude_turn_text(message: dict) -> str:
    """The prose in one claude ``message``'s content blocks, joined and stripped.

    Thinking blocks are excluded: each arrives with a signature blob many times the
    length of its prose. "" when the turn carried none, which is the common case — a
    tool-calling turn's content is a ``tool_use`` block with nothing to read.
    """
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    parts = [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    return "\n".join(parts).strip()


def forwarded(event: dict) -> bool:
    """Whether *event* is a nested subagent's turn rather than the lane agent's own.

    The guard every claude token reader takes before counting, so that forwarding
    stays purely additive: it makes nested work visible and moves no total. A
    forwarded turn carries a full ``message.usage`` of its own, so counting one
    would fold a nested agent's tokens into a live sum calibrated without them
    (:data:`supervise.LIVE_OVERREPORT_BOUND`) and read a subagent's context as the
    lane's occupancy — measured on a real 2.1.226 dispatch, the forwarded turn
    reported ``cache_read_input_tokens`` 0 against the lane's 51526.
    """
    return event.get(CLAUDE_PARENT_TOOL_USE_ID) is not None


def claude_last_turn_usage(stdout: str) -> dict | None:
    """The usage block of the stream's **last assistant message**.

    That is the occupancy view (design D8): what the window held on the final
    call. The cumulative result-event sum is not — ``cache_read_input_tokens``
    re-counts the context every turn, so it exceeds the window on any healthy
    multi-turn run.

    The lane's own last message: a dispatch killed while a subagent was mid-reply
    ends on a forwarded turn, and reading that one would report the subagent's
    occupancy as the lane's (:func:`forwarded`).
    """
    for event in reversed(stream_events(stdout)):
        if event.get("type") != "assistant" or forwarded(event):
            continue
        message = event.get("message")
        if isinstance(message, dict) and isinstance(message.get("usage"), dict):
            return message["usage"]
    return None


def codex_agent_message(stdout: str) -> str | None:
    """The text of the **last** ``agent_message`` item in codex's ``--json`` stream.

    The last one, not the concatenation: a multi-turn run emits one per turn, and
    the reply to the prompt is the final one — earlier ones are progress narration
    from before the tool calls. Scanned from the end for that reason, and
    unparseable lines are skipped like everywhere else in this module (a truncated
    final line is normal for a killed dispatch).

    None when no such item parses, so :func:`runner.result_text` falls back to the
    transcript.
    """
    for event in reversed(stream_events(stdout)):
        if event.get("type") != CODEX_ITEM_COMPLETED:
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != CODEX_AGENT_MESSAGE:
            continue
        text = item.get("text")
        if isinstance(text, str):
            return text
    return None


def codex_turn_usages(stdout: str) -> list[dict]:
    """The usage objects of codex's ``turn.completed`` events, in stream order."""
    return [
        event["usage"]
        for event in stream_events(stdout)
        if event.get("type") == CODEX_TURN_COMPLETED and isinstance(event.get("usage"), dict)
    ]
