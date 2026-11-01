from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from basicly import runner

VALUE_MODES = frozenset({"plain", "gates-only", "full-basicly"})


def select_setup(case: dict[str, Any], mode: str) -> dict[str, Any]:
    if mode == "guidance":
        return case
    if mode not in VALUE_MODES:
        raise ValueError(f"unknown comparison mode: {mode}")
    if "setup" in case or not all(name in case for name in ["task_setup", "harness_setup"]):
        raise ValueError(
            "value trials require separate task_setup and harness_setup lists; remove setup"
        )
    for name in ["task_setup", "harness_setup"]:
        commands = case[name]
        if not isinstance(commands, list) or any(
            not isinstance(command, list)
            or not command
            or any(not isinstance(arg, str) for arg in command)
            for command in commands
        ):
            raise ValueError(f"{name} requires command argument lists")
    return {
        **case,
        "setup": [*case["task_setup"], *(case["harness_setup"] if mode != "plain" else [])],
    }


def configure_value_arm(
    spec: runner.RunnerSpec, mode: str, workspace: Path
) -> tuple[runner.RunnerSpec, dict[str, Any]]:
    if mode == "guidance":
        return spec, {}
    if spec.name != "codex":
        raise ValueError("value trials currently require Codex; other hosts need loading controls")
    surfaces = ["AGENTS.md", "AGENTS.override.md", "CLAUDE.md", ".basicly", ".agents", ".claude"]
    if mode == "plain":
        if any((workspace / name).exists() for name in surfaces):
            raise ValueError("plain arm contains harness files")
        if any(
            p.is_file() and not p.name.endswith(".sample")
            for p in (workspace / ".git/hooks").glob("*")
        ):
            raise ValueError("plain arm contains active Git hooks")
        return spec, {"mode": mode, "installed": False, "repository_instructions": False}
    if mode not in VALUE_MODES:
        raise ValueError(f"unknown comparison mode: {mode}")
    hooks = workspace / ".git/hooks"
    required = ["pre-commit", "commit-msg"]
    if not all((hooks / name).is_file() for name in required):
        raise ValueError("installed arm requires active pre-commit and commit-msg hooks")
    config = tomllib.loads((workspace / "basicly.toml").read_text())
    if not config.get("verify", {}).get("checks"):
        raise ValueError("installed arm requires configured verification checks")
    skills = sorted((workspace / ".agents/skills").glob("*/SKILL.md"))
    if not (workspace / "AGENTS.md").is_file() or not skills:
        raise ValueError("installed arm requires projected instructions and skills")
    evidence = {
        "mode": mode,
        "installed": True,
        "repository_instructions": mode == "full-basicly",
        "instructions_sha256": hashlib.sha256((workspace / "AGENTS.md").read_bytes()).hexdigest(),
        "configuration_sha256": hashlib.sha256(
            (workspace / "basicly.toml").read_bytes()
        ).hexdigest(),
        "hook_sha256": {
            name: hashlib.sha256((hooks / name).read_bytes()).hexdigest() for name in required
        },
        "skill_sha256": {
            path.relative_to(workspace).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in skills
        },
        "loading_verified": False,
    }
    if mode == "full-basicly":
        return spec, evidence
    disabled = ",".join("{path=" + json.dumps(str(path)) + ",enabled=false}" for path in skills)
    command = (*spec.command, "-c", "project_doc_max_bytes=0", "-c", f"skills.config=[{disabled}]")
    return dataclasses.replace(spec, command=command), evidence


def loading_evidence(
    workspace: Path, session: str | None, model: str, *, sessions: Path | None = None
) -> dict[str, Any]:
    if not isinstance(session, str) or str(UUID(session)) != session:
        raise ValueError("native loading requires a session UUID")
    if sessions is None:
        sessions = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
    paths = list(sessions.glob(f"**/*-{session}.jsonl"))
    if len(paths) != 1 or paths[0].is_symlink():
        raise ValueError("native loading trace is unavailable or ambiguous")
    with paths[0].open("rb") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("native loading trace exceeds the read limit")
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if any(
        not isinstance(event, dict) or not isinstance(event.get("payload", {}), dict)
        for event in events
    ):
        raise ValueError("malformed native loading trace")
    metadata = [event.get("payload", {}) for event in events if event.get("type") == "session_meta"]
    if (
        len(metadata) != 1
        or metadata[0].get("id") != session
        or Path(metadata[0].get("cwd", "")).resolve() != workspace.resolve()
    ):
        raise ValueError("native loading trace does not belong to this trial")
    initial = []
    developer = []
    for event in events:
        payload = event.get("payload", {})
        if event.get("type") == "response_item" and payload.get("type") == "message":
            content = payload.get("content", [])
            if not isinstance(content, list) or any(
                not isinstance(item, dict) or not isinstance(item.get("text", ""), str)
                for item in content
            ):
                raise ValueError("malformed native context message")
            if payload.get("role") == "assistant":
                break
            if payload.get("role") == "developer":
                developer.extend(
                    item.get("text", "")
                    for item in payload.get("content", [])
                    if isinstance(item, dict)
                )
            initial.extend(
                item.get("text", "")
                for item in payload.get("content", [])
                if isinstance(item, dict)
            )
    text = "\n".join(initial)
    catalog = "\n".join(developer)
    roots = re.findall(r"(?m)^- `([^`]+)` = `([^`]+)`$", catalog)
    skill_root = workspace / ".agents/skills"
    aliases = [alias for alias, root in roots if Path(root).resolve() == skill_root.resolve()]
    instructions = workspace / "AGENTS.md"
    contexts = [event.get("payload", {}) for event in events if event.get("type") == "turn_context"]
    if any(not isinstance(context.get("sandbox_policy"), dict) for context in contexts):
        raise ValueError("malformed native sandbox context")
    return {
        "trace_sha256": hashlib.sha256(raw).hexdigest(),
        "instructions_loaded": instructions.is_file() and instructions.read_text().strip() in text,
        "project_skills_loaded": sorted(
            path.relative_to(workspace).as_posix()
            for path in (workspace / ".agents/skills").glob("*/SKILL.md")
            if str(path) in catalog
            or any(
                f"(file: {alias}/{path.relative_to(skill_root).as_posix()})" in catalog
                for alias in aliases
            )
        ),
        "native_model_verified": bool(contexts)
        and all(context.get("model") == model for context in contexts),
        "native_approval": sorted({str(context.get("approval_policy")) for context in contexts}),
        "native_effort": sorted({str(context.get("effort")) for context in contexts}),
        "native_sandbox": [
            json.loads(value)
            for value in sorted({
                json.dumps(context.get("sandbox_policy"), sort_keys=True) for context in contexts
            })
        ],
    }


def verify_value_loading(spec: runner.RunnerSpec, workspace: Path, report: dict[str, Any]) -> None:
    if conditions := report.get("arm_conditions"):
        loading = loading_evidence(workspace, report["session_id"], spec.model or "")
        conditions["loading"] = loading
        expected = conditions["mode"] == "full-basicly"
        conditions["loading_verified"] = (
            loading["native_model_verified"]
            and loading["native_effort"] == [report["effort"]]
            and loading["native_approval"] == [spec.approval]
            and bool(loading["native_sandbox"])
            and all(
                policy.get("type") == spec.sandbox and policy.get("network_access") is False
                for policy in loading["native_sandbox"]
            )
            and loading["instructions_loaded"] == expected
            and bool(loading["project_skills_loaded"]) == expected
        )
        if not conditions["loading_verified"]:
            raise ValueError("native loading differs from the declared comparison arm")
