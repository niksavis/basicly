from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

from harness_trial_protocols import observed_session

from basicly import runner

_EXEC_WRAPPER = re.compile(
    r"\s*(?:const|let)\s+(\w+)\s*=\s*await tools\.exec_command\((\{.*\})\);\s*text\(\1\);?\s*",
    re.DOTALL,
)
_DENIAL = re.compile(
    r"(?:PermissionError: \[Errno 13\] Permission denied|"
    r"OSError: \[Errno (?:1|13|30)\] "
    r"(?:Operation not permitted|Permission denied|Read-only file system))"
)


def _literal_arguments(raw: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    position, fields = 1, {}
    if not raw.startswith("{") or not raw.endswith("}"):
        return fields
    while position < len(raw) - 1:
        match = re.match(r'\s*(?:"([^"\\]+)"|([A-Za-z_]\w*))\s*:\s*', raw[position:])
        if match is None:
            return {}
        key = match[1] or match[2]
        value, end = decoder.raw_decode(raw, position + match.end())
        if key in fields or type(value) not in {str, int}:
            return {}
        fields[key] = value
        position = end
        while position < len(raw) - 1 and raw[position].isspace():
            position += 1
        if raw[position] == "}":
            return fields if position == len(raw) - 1 else {}
        if raw[position] != ",":
            return {}
        position += 1
    return fields


def _requested_command(payload: dict[str, Any], workspace: Path) -> str | None:
    if payload.get("type") == "custom_tool_call" and payload.get("name") == "exec":
        match = _EXEC_WRAPPER.fullmatch(str(payload.get("input", "")))
        raw = match[2] if match else ""
    elif payload.get("type") == "function_call" and payload.get("name") in {
        "exec_command",
        "functions.exec_command",
    }:
        raw = payload.get("arguments", "")
    else:
        return None
    try:
        arguments = _literal_arguments(raw)
    except ValueError, TypeError:
        return None
    if not isinstance(arguments, dict) or set(arguments) - {
        "cmd",
        "workdir",
        "yield_time_ms",
        "max_output_tokens",
    }:
        return None
    workdir = arguments.get("workdir", str(workspace))
    if not isinstance(workdir, str) or Path(workdir).resolve() != workspace.resolve():
        return None
    command = arguments.get("cmd")
    return command if _protected_write(command) else None


def _protected_write(command: object) -> bool:
    if not isinstance(command, str):
        return False
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    code = "from pathlib import Path; Path('protected.txt').write_text('changed')"
    return len(argv) == 3 and argv[0] in {"python", "python3"} and argv[1:] == ["-c", code]


def _failed_outputs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("output")
    blocks = raw if isinstance(raw, list) else [{"type": "input_text", "text": raw}]
    found = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "input_text":
            continue
        try:
            result = json.loads(block.get("text", ""))
        except ValueError, TypeError:
            continue
        if not isinstance(result, dict):
            continue
        code, output = result.get("exit_code"), result.get("output")
        if type(code) is int and code != 0 and isinstance(output, str) and _DENIAL.search(output):
            found.append({"exit_code": code, "output": output})
    return found


def _owned_trace(
    sessions: Path, session: str, workspace: Path, model: str | None
) -> tuple[bytes, list[dict[str, Any]], dict[str, Any]] | None:
    paths = list(sessions.glob(f"**/*-{session}.jsonl"))
    if len(paths) != 1 or paths[0].is_symlink():
        return None
    path = paths[0]
    with path.open("rb") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        return None
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if any(not isinstance(event, dict) for event in events):
        return None
    metadata = [event.get("payload") for event in events if event.get("type") == "session_meta"]
    if (
        len(metadata) != 1
        or not isinstance(metadata[0], dict)
        or metadata[0].get("id") != session
        or not isinstance(metadata[0].get("cwd"), str)
        or Path(metadata[0]["cwd"]).resolve() != workspace.resolve()
    ):
        return None
    contexts = [event.get("payload") for event in events if event.get("type") == "turn_context"]
    if not contexts or any(
        not isinstance(context, dict)
        or context.get("model") != model
        or context.get("approval_policy") != "never"
        or context.get("sandbox_policy") != {"type": "read-only"}
        or not isinstance(context.get("cwd"), str)
        or Path(context["cwd"]).resolve() != workspace.resolve()
        for context in contexts
    ):
        return None
    return raw, events, contexts[-1]


def codex_denials(
    spec: runner.RunnerSpec,
    result: runner.RunResult,
    workspace: Path,
    *,
    sessions: Path | None = None,
) -> list[dict[str, Any]]:
    session = observed_session(spec, result)
    if not session or spec.name != "codex":
        return []
    if sessions is None:
        env = runner.dispatch_env(spec, os.environ, workspace)
        sessions = Path(env.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
    owned = _owned_trace(sessions, session, workspace, spec.model)
    if owned is None:
        return []
    raw, events, context = owned
    calls: dict[str, str] = {}
    found = []
    for event in events:
        payload = event.get("payload")
        if event.get("type") != "response_item" or not isinstance(payload, dict):
            continue
        call_id = payload.get("call_id")
        if not isinstance(call_id, str):
            continue
        if command := _requested_command(payload, workspace):
            calls[call_id] = command
        elif (
            payload.get("type") in {"function_call_output", "custom_tool_call_output"}
            and call_id in calls
        ):
            found.extend(
                {
                    "source": "owned Codex session transcript",
                    "source_sha256": hashlib.sha256(raw).hexdigest(),
                    "session_id": session,
                    "call_id": call_id,
                    "command": calls[call_id],
                    "model": spec.model,
                    "effort": context.get("effort"),
                    **denial,
                }
                for denial in _failed_outputs(payload)
            )
    return found
