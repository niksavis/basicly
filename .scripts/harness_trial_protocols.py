from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess  # nosec B404 # Evaluator-owned argv in isolated trial workspaces.
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID

from basicly import runner

_BINDING_FIELDS = frozenset({
    "session_id",
    "task",
    "worktree",
    "family",
    "model",
    "configuration",
    "revision",
})
_CONTINUATION_FLAGS = frozenset({
    "--resume",
    "--continue",
    "--last",
    "--fork-session",
    "--session-id",
    "resume",
    "fork",
})


class Checkpoint(NamedTuple):
    path: Path
    nonce: str


def configuration_inputs(paths: list[Path]) -> dict[str, str]:
    result = {}
    for path in paths:
        if any(
            part.casefold().startswith(".env")
            for part in (*path.absolute().parts, *path.resolve().parts)
        ):
            raise ValueError("protected host configuration input")
        if path.is_symlink():
            raise ValueError("symlinked host configuration input")
        if not path.exists():
            result[str(path.absolute())] = "absent"
            continue
        before = path.stat()
        if not path.is_file() or before.st_size > 2 * 1024 * 1024:
            raise ValueError("unsupported host configuration input")
        content = path.read_bytes()
        after = path.stat()
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("host configuration changed while reading")
        result[str(path.absolute())] = hashlib.sha256(content).hexdigest()
    return result


def configuration_identity(spec: runner.RunnerSpec, workspace: Path, paths: list[Path]) -> str:
    binary = shutil.which(spec.binary) if spec.binary else None
    if binary is None:
        raise ValueError(f"cannot identify host executable for {spec.name}")
    local = [
        workspace / name
        for name in (".codex/config.toml", ".claude/settings.json", ".claude/settings.local.json")
    ]
    payload = {
        "specification": asdict(spec),
        "adapter_version": runner.adapter_version(spec),
        "executable": hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        "configuration": configuration_inputs([*paths, *local]),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _guard_checkpoint_location(path: Path, workspace: Path) -> None:
    if not path.resolve().is_relative_to(workspace.resolve()) or any(
        parent.is_symlink() for parent in [path, *path.parents] if parent != workspace.resolve()
    ):
        raise ValueError("symlinked interruption checkpoint")


def _checkpoint_path(workspace: Path, checkpoint: Checkpoint) -> Path:
    relative = checkpoint.path
    if (
        relative.is_absolute()
        or relative == Path()
        or any(
            part in {"..", ".git"} or part.casefold().startswith(".env") for part in relative.parts
        )
        or not checkpoint.nonce
    ):
        raise ValueError("unsafe interruption checkpoint")
    path = workspace.resolve() / relative
    _guard_checkpoint_location(path, workspace)
    if path.exists():
        raise ValueError("interruption checkpoint already exists")
    return path


def _checkpoint_ready(path: Path, nonce: str, workspace: Path) -> bool:
    _guard_checkpoint_location(path, workspace)
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError, json.JSONDecodeError:
        return False
    return isinstance(content, dict) and content.get("checkpoint") == nonce


def _await_checkpoint(
    process: subprocess.Popen[str],
    finished: threading.Event,
    workspace: Path,
    checkpoint: Checkpoint,
    timeout: float,
) -> dict[str, bool]:
    deadline = time.monotonic() + timeout
    while not finished.is_set():
        if _checkpoint_ready(workspace / checkpoint.path, checkpoint.nonce, workspace):
            stopped = process.poll() is None
            if stopped:
                runner._kill_tree(process)
            return {"checkpoint_observed": True, "stop_requested": stopped, "timed_out": False}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            runner._kill_tree(process)
            return {"checkpoint_observed": False, "stop_requested": True, "timed_out": True}
        finished.wait(min(0.05, remaining))
    return {"checkpoint_observed": False, "stop_requested": False, "timed_out": False}


def interrupt_at_checkpoint(
    spec: runner.RunnerSpec,
    prompt: str,
    workspace: Path,
    checkpoint: Checkpoint,
    timeout: float,
) -> tuple[runner.RunResult, dict[str, bool]]:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("interruption timeout must be positive and finite")
    _checkpoint_path(workspace, checkpoint)
    command = runner.format_command(spec, prompt, capture_usage=True)
    environment = runner.dispatch_env(spec, os.environ, workspace)
    new_session, creationflags = runner._process_isolation(os.name)
    finished = threading.Event()
    streams: list[tuple[str, str]] = []
    errors: list[OSError | ValueError] = []
    started = time.monotonic()
    with subprocess.Popen(  # nosec B603 # Explicit runner argv in its own process group.
        command,
        cwd=workspace,
        env=environment,
        stdin=subprocess.PIPE if spec.prompt_via == "stdin" else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        start_new_session=new_session,
        creationflags=creationflags,
    ) as process:

        def capture() -> None:
            try:
                streams.append(process.communicate(prompt if spec.prompt_via == "stdin" else None))
            except (OSError, ValueError) as error:
                errors.append(error)
            finally:
                finished.set()

        reader = threading.Thread(target=capture, daemon=True)
        reader.start()
        try:
            fact = _await_checkpoint(process, finished, workspace, checkpoint, timeout)
        finally:
            if process.poll() is None:
                runner._kill_tree(process)
            reader.join(runner.KILL_GRACE_S)
        if reader.is_alive() or not streams:
            if errors:
                raise ValueError("cannot capture interrupted host output") from errors[0]
            raise ValueError("interrupted host output did not drain")
        stdout, stderr = streams[0]
        result = runner.RunResult(
            spec.name,
            tuple(command),
            True,
            process.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_s=time.monotonic() - started,
            timed_out=fact["timed_out"],
        )
    return result, fact


def observed_session(spec: runner.RunnerSpec, result: runner.RunResult) -> str | None:
    observed = runner.observed_session(spec, result.stdout)
    if result.session_id is None:
        return observed
    try:
        bound = str(UUID(result.session_id))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("invalid native session identifier") from error
    if observed is not None and bound != observed:
        raise ValueError("conflicting native session identifiers")
    return bound


def bind_resume(
    spec: runner.RunnerSpec,
    recorded: Mapping[str, str],
    current: Mapping[str, str],
    workspace: Path,
) -> runner.RunnerSpec:
    if recorded.keys() != _BINDING_FIELDS or current.keys() != _BINDING_FIELDS:
        raise ValueError("incomplete resume binding")
    if recorded != current:
        changed = sorted(key for key in _BINDING_FIELDS if recorded[key] != current[key])
        raise ValueError("resume binding changed: " + ", ".join(changed))
    try:
        session = str(UUID(recorded["session_id"]))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("invalid bound session identifier") from error
    if (
        any(not isinstance(value, str) or not value.strip() for value in recorded.values())
        or recorded["family"] != spec.name
        or recorded["model"] != spec.model
        or recorded["worktree"] != str(workspace.resolve())
        or any(
            len(recorded[key]) != 64
            or any(char not in "0123456789abcdef" for char in recorded[key])
            for key in ("configuration", "revision")
        )
    ):
        raise ValueError("invalid resume binding")
    if _CONTINUATION_FLAGS.intersection(spec.command):
        raise ValueError("runner command already contains session continuation")
    if spec.name == "codex" and spec.command[1:2] == ("exec",):
        command = (*spec.command[:2], "resume", session, *spec.command[2:])
    elif spec.name == "claude" and spec.command:
        command = (spec.command[0], "--resume", session, *spec.command[1:])
    else:
        raise ValueError(f"unsupported bound resume family: {spec.name}")
    return replace(spec, command=command)


def native_denials(spec: runner.RunnerSpec, result: runner.RunResult) -> list[dict[str, Any]]:
    found = []
    for event in runner.stream_events(result.stdout):
        if spec.usage_format in {runner.CLAUDE_JSON, runner.CLAUDE_STREAM_JSON}:
            if event.get("type") == "system" and event.get("subtype") == "permission_denied":
                found.append(event)
            if event.get("type") == "result":
                denied = event.get("permission_denials")
                if isinstance(denied, list):
                    found.extend(row for row in denied if isinstance(row, dict) and row)
        elif spec.usage_format == runner.CODEX_JSONL and event.get("type") == "item.completed":
            item = event.get("item")
            if not isinstance(item, dict) or item.get("type") != "command_execution":
                continue
            code, output = item.get("exit_code"), item.get("aggregated_output")
            if (
                item.get("status") == "failed"
                and type(code) is int
                and code != 0
                and isinstance(output, str)
                and any(
                    text in output.casefold()
                    for text in (
                        "permission denied",
                        "read-only file system",
                        "operation not permitted",
                        "access is denied",
                        "sandbox denied",
                    )
                )
            ):
                found.append(event)
    unique, seen = [], set()
    for event in found:
        call_id = event.get("tool_use_id")
        if isinstance(call_id, str) and call_id:
            if call_id in seen:
                continue
            seen.add(call_id)
        unique.append(event)
    return unique
