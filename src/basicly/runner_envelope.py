from __future__ import annotations

import json

CLAUDE_JSON = "claude-json"
CLAUDE_STREAM_JSON = "claude-stream-json"
CODEX_JSONL = "codex-jsonl"

CODEX_ITEM_COMPLETED = "item.completed"
CODEX_AGENT_MESSAGE = "agent_message"
CODEX_TURN_COMPLETED = "turn.completed"

CLAUDE_PARENT_TOOL_USE_ID = "parent_tool_use_id"
CLAUDE_SUBAGENT_TYPE = "subagent_type"
UNNAMED_SUBAGENT = "subagent"

CLAUDE_TOKEN_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)
CODEX_TOKEN_KEYS = ("input_tokens", "output_tokens")


def stream_object(line: str) -> dict | None:

    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def stream_events(stdout: str) -> list[dict]:

    return [obj for line in stdout.splitlines() if (obj := stream_object(line)) is not None]


def claude_result_object(stdout: str) -> dict | None:

    events = stream_events(stdout)
    if events:
        return events[-1]
    try:
        obj = json.loads(stdout.strip() or "null")
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def claude_result_field(stdout: str) -> str | None:

    obj = claude_result_object(stdout)
    if obj is None:
        return None
    value = obj.get("result")
    return value if isinstance(value, str) else None


def claude_result_event(stdout: str) -> str:

    for event in reversed(stream_events(stdout)):
        if event.get("type") == "result":
            return json.dumps(event)
    return ""


def claude_turn_text(message: dict) -> str:

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

    return event.get(CLAUDE_PARENT_TOOL_USE_ID) is not None


def claude_last_turn_usage(stdout: str) -> dict | None:

    for event in reversed(stream_events(stdout)):
        if event.get("type") != "assistant" or forwarded(event):
            continue
        message = event.get("message")
        if isinstance(message, dict) and isinstance(message.get("usage"), dict):
            return message["usage"]
    return None


def codex_agent_message(stdout: str) -> str | None:

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
    return [
        event["usage"]
        for event in stream_events(stdout)
        if event.get("type") == CODEX_TURN_COMPLETED and isinstance(event.get("usage"), dict)
    ]
