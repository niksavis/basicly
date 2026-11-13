from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml

from basicly import runner


def configure(spec: runner.RunnerSpec, mode: str) -> runner.RunnerSpec:
    settings: dict[str, Any] = {"autoMemoryEnabled": False}
    command = (*spec.command, "--permission-mode", "acceptEdits", "--permission-prompts", "none")
    if mode != "full-basicly":
        settings["claudeMdExcludes"] = ["**"]
        command = (*command, "--disable-slash-commands")
    return dataclasses.replace(spec, command=(*command, "--settings", json.dumps(settings)))


def skill_catalog(workspace: Path) -> dict[str, tuple[str, str]]:
    found = {}
    for path in sorted((workspace / ".claude/skills").glob("*/SKILL.md")):
        parts = path.read_text(encoding="utf-8").split("---", 2)
        if len(parts) != 3 or parts[0].strip():
            raise ValueError("Claude value arm requires skill frontmatter")
        try:
            fields = yaml.safe_load(parts[1])
        except yaml.YAMLError as error:
            raise ValueError("Claude value arm has malformed skill metadata") from error
        if not isinstance(fields, dict):
            raise ValueError("Claude value arm requires skill metadata")
        if fields.get("disable-model-invocation") is True:
            continue
        name, description = fields.get("name"), fields.get("description")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(description, str)
            or not description.strip()
        ):
            raise ValueError("Claude value arm requires named skill descriptions")
        if name in found:
            raise ValueError("Claude value arm has duplicate skill names")
        found[name] = (path.relative_to(workspace).as_posix(), " ".join(description.split()))
    return found


def text_blocks(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list) or any(not isinstance(block, dict) for block in content):
        raise ValueError("malformed Claude context blocks")
    return "\n".join(block["text"] for block in content if isinstance(block.get("text"), str))


def native_events(
    session: str | None, sessions: Path | None
) -> tuple[bytes, list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(session, str) or str(UUID(session)) != session:
        raise ValueError("native Claude loading requires a session UUID")
    if sessions is None:
        sessions = (
            Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "projects"
        )
    paths = list(sessions.glob(f"*/{session}.jsonl"))
    if len(paths) != 1 or any(path.is_symlink() for path in [paths[0], *paths[0].parents]):
        raise ValueError("native Claude loading trace is unavailable or ambiguous")
    with paths[0].open("rb") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("native Claude loading trace exceeds the read limit")
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if any(
        not isinstance(event, dict) or not isinstance(event.get("type"), str) for event in events
    ):
        raise ValueError("malformed native Claude loading trace")
    native = [event for event in events if event.get("type", "").startswith("api-request")]
    if not native or any(event.get("sessionId") != session for event in native):
        raise ValueError("native Claude loading trace has a different session")
    return raw, events, native


def loading_evidence(
    workspace: Path,
    session: str | None,
    result: runner.RunResult,
    *,
    sessions: Path | None = None,
) -> dict[str, Any]:
    raw, events, native = native_events(session, sessions)
    initial = [
        event
        for event in runner.stream_events(result.stdout)
        if (event.get("type") == "system" and event.get("subtype") == "init")
    ]
    users = [event for event in events if event.get("type") == "user"]
    if (
        len(initial) != 1
        or initial[0].get("session_id") != session
        or not isinstance(initial[0].get("cwd"), str)
        or Path(initial[0]["cwd"]).resolve() != workspace.resolve()
        or not users
        or users[0].get("sessionId") != session
        or not isinstance(users[0].get("cwd"), str)
        or Path(users[0]["cwd"]).resolve() != workspace.resolve()
    ):
        raise ValueError("native Claude loading trace does not belong to this trial")
    names = initial[0].get("skills")
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError("native Claude skill evidence is unavailable")
    if any(
        not isinstance(event.get("shapeHash"), str)
        for event in native
        if event["type"] in {"api-request", "api-request-shape"}
    ) or any(
        not isinstance(event.get("hash"), str)
        for event in native
        if event["type"] == "api-request-blob"
    ):
        raise ValueError("native Claude context references are malformed")
    requests = [
        event
        for event in native
        if event.get("type") == "api-request" and event.get("querySource") == "sdk"
    ]
    if not requests or requests[0].get("keep") != 0:
        raise ValueError("native Claude loading requires an initial SDK request")
    shapes = {
        event.get("shapeHash"): event.get("shape")
        for event in native
        if event.get("type") == "api-request-shape"
    }
    blobs = {
        event.get("hash"): event.get("message")
        for event in native
        if event.get("type") == "api-request-blob"
    }
    first = requests[0]
    shape = shapes.get(first.get("shapeHash"))
    tail = first.get("tail")
    if (
        not isinstance(shape, dict)
        or not isinstance(tail, list)
        or not tail
        or any(not isinstance(reference, str) for reference in tail)
    ):
        raise ValueError("native Claude initial context is unavailable")
    messages: list[dict[str, Any]] = []
    for reference in tail:
        message = blobs.get(reference)
        if not isinstance(message, dict):
            raise ValueError("native Claude initial context has missing messages")
        messages.append(message)
    context = (
        text_blocks(shape.get("system"))
        + "\n"
        + "\n".join(text_blocks(message.get("content")) for message in messages)
    )
    instruction = next(
        (
            workspace / name
            for name in [".claude/CLAUDE.md", "CLAUDE.md", "AGENTS.md"]
            if (workspace / name).is_file()
        ),
        None,
    )
    sources = sorted({
        Path(value).resolve().relative_to(workspace.resolve()).as_posix()
        for value in re.findall(r"(?m)^Contents of (.+?) \(project instructions", context)
        if Path(value).resolve().is_relative_to(workspace.resolve())
    })
    catalog = skill_catalog(workspace)
    descriptions = " ".join(context.split())
    loaded = sorted(
        path
        for name, (path, description) in catalog.items()
        if (
            name in names
            and re.search(
                r"(?:^|\s)- " + re.escape(name) + ": " + re.escape(description), descriptions
            )
        )
    )
    parameters: list[dict[str, Any]] = []
    for request in requests:
        params = request.get("params")
        if (
            not isinstance(params, dict)
            or not isinstance(params.get("model"), str)
            or not isinstance(params.get("output_config"), dict)
            or not isinstance(params["output_config"].get("effort"), str)
        ):
            raise ValueError("native Claude model and effort evidence is unavailable")
        parameters.append(params)
    return {
        "trace_sha256": hashlib.sha256(raw).hexdigest(),
        "instructions_loaded": bool(sources),
        "root_instructions_verified": instruction is not None
        and instruction.relative_to(workspace).as_posix() in sources
        and bool(instruction.read_text(encoding="utf-8").strip())
        and instruction.read_text(encoding="utf-8").strip() in context,
        "project_instruction_sources": sources,
        "project_skills_loaded": loaded,
        "expected_project_skills": sorted(path for path, _ in catalog.values()),
        "native_models": sorted({params.get("model", "") for params in parameters}),
        "native_effort": sorted({
            params["output_config"].get("effort", "") for params in parameters
        }),
        "native_permission_mode": initial[0].get("permissionMode"),
    }
