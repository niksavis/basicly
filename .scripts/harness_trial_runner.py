from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import random
import subprocess  # nosec B404 # Run trusted fixture commands without shell parsing.
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from harness_trial_protocols import (
    Checkpoint,
    bind_resume,
    configuration_identity,
    interrupt_at_checkpoint,
    native_denials,
    observed_session,
)
from harness_trial_transcripts import codex_denials

from basicly import runner


def schedule(
    cases: list[str], arms: list[str], repetitions: int, *, seed: int
) -> list[tuple[int, str, str]]:
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    if not cases or not arms or len(set(cases)) != len(cases) or len(set(arms)) != len(arms):
        raise ValueError("cases and arms must be nonempty and unique")
    generator = random.Random(seed)  # nosec B311 # Reproducible trial ordering, not security.
    result = []
    for repetition in range(repetitions):
        ordered_cases = list(cases)
        generator.shuffle(ordered_cases)
        for case_id in ordered_cases:
            ordered_arms = list(arms)
            generator.shuffle(ordered_arms)
            result.extend((repetition, case_id, arm) for arm in ordered_arms)
    return result


def validate_paths(names: list[str]) -> None:
    for name in names:
        path = PurePosixPath(name)
        if (
            not name
            or path.as_posix() != name
            or name == "."
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or ":" in name
            or any(part.startswith(".env") for part in path.parts)
            or ".git" in path.parts
            or path == PurePosixPath(".basicly/ledger/redirect")
        ):
            raise ValueError(f"unsafe fixture path: {name}")


def fingerprint(workspace: Path, names: list[str]) -> dict[str, str]:
    validate_paths(names)
    result = {}
    for name in names:
        root = workspace / name
        if any(parent.is_symlink() for parent in [root, *root.parents] if parent != workspace):
            raise ValueError(f"preserved path contains a symlink: {name}")
        paths = sorted(root.rglob("*")) if root.is_dir() else [root]
        result[name] = "directory" if root.is_dir() else "file"
        for path in paths:
            relative = path.relative_to(workspace).as_posix()
            validate_paths([relative])
            if path.is_symlink():
                raise ValueError(f"preserved path contains a symlink: {relative}")
            result[relative] = (
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "directory"
            )
    return result


def write_fixture(workspace: Path, files: dict[str, str]) -> None:
    validate_paths(list(files))
    workspace.mkdir(parents=True, exist_ok=False)
    for name, text in files.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def grade(workspace: Path, command: list[str], timeout: float) -> dict[str, Any]:
    if not command:
        raise ValueError("a trial requires an independent acceptance command")
    argv = [
        arg.replace("{workspace}", str(workspace)).replace("{python}", sys.executable)
        for arg in command
    ]
    try:
        with tempfile.TemporaryDirectory(prefix="harness-grader-cache-") as cache:
            env = runner.sanitised_git_env(os.environ)
            env.update(PYTHONPYCACHEPREFIX=cache, PYTHONDONTWRITEBYTECODE="1")
            result = subprocess.run(  # nosec B603 # Evaluator-owned argv without shell parsing.
                argv,
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"outcome": "infrastructure_error", "error": str(error)}
    return {
        "outcome": {0: "passed", 1: "failed"}.get(result.returncode, "infrastructure_error"),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def grade_preserved(
    workspace: Path, case: dict[str, Any], preserved: dict[str, str], timeout: float
) -> dict[str, Any]:
    checked = grade(workspace, case["check"], timeout)
    try:
        unchanged = preserved == fingerprint(workspace, case.get("preserve", []))
    except OSError, ValueError:
        unchanged = False
    checked["preserved_files_unchanged"] = unchanged
    if not unchanged:
        checked["outcome"] = "failed"
    return checked


def prepare_case(
    workspace: Path, case: dict[str, Any], report: dict[str, Any], timeout: float
) -> bool:
    report["setup"] = []
    for command in case.get("setup", []):
        checked = grade(workspace, command, timeout)
        report["setup"].append(checked)
        if checked["outcome"] != "passed":
            report["error"] = "fixture setup failed; agent was not dispatched"
            return False
    files = case.get("guidance_files", {})
    validate_paths(list(files))
    for name, text in files.items():
        path = workspace / name
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent != workspace):
            raise ValueError(f"guidance path contains a symlink: {name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return True


def _resume_binding(
    spec: runner.RunnerSpec,
    case: dict[str, Any],
    workspace: Path,
    session: str,
) -> dict[str, str]:
    names = set(case["files"]) | set(case.get("guidance_files", {})) | set(case.get("preserve", []))
    names.add(case["protocol"]["checkpoint"])
    revision = hashlib.sha256(
        json.dumps(fingerprint(workspace, sorted(names)), sort_keys=True).encode()
    ).hexdigest()
    return {
        "session_id": session,
        "task": case["id"],
        "worktree": str(workspace.resolve()),
        "family": spec.name,
        "model": spec.model or "",
        "configuration": configuration_identity(
            spec, workspace, [Path(name) for name in case.get("configuration_files", [])]
        ),
        "revision": revision,
    }


def _resume_trial(
    spec: runner.RunnerSpec,
    case: dict[str, Any],
    directory: Path,
    report: dict[str, Any],
    timeout: float,
) -> runner.RunResult:
    if not spec.model or spec.name not in {"codex", "claude"}:
        raise ValueError("resume trials require an explicit native family and model")
    workspace = directory / "workspace"
    protocol = case["protocol"]
    checkpoint = Checkpoint(Path(protocol["checkpoint"]), str(uuid4()))
    initial = (
        protocol["initial_prompt"]
        .replace("{checkpoint}", checkpoint.path.as_posix())
        .replace("{nonce}", checkpoint.nonce)
    )
    configured = configuration_identity(
        spec, workspace, [Path(name) for name in case.get("configuration_files", [])]
    )
    first, fact = interrupt_at_checkpoint(spec, initial, workspace, checkpoint, timeout)
    (directory / "initial.stdout").write_text(first.stdout, encoding="utf-8")
    (directory / "initial.stderr").write_text(first.stderr, encoding="utf-8")
    report["protocol"] = {"kind": "resume", "satisfied": False, **fact}
    report["stages"] = [
        {"command": first.command, "returncode": first.returncode, "duration_s": first.duration_s}
    ]
    result = first
    try:
        session = observed_session(spec, first)
        if (
            not fact["checkpoint_observed"]
            or not fact["stop_requested"]
            or fact["timed_out"]
            or not session
        ):
            return first
        report["protocol"]["session_id"] = session
        checkpoint_state = fingerprint(workspace, [checkpoint.path.as_posix()])
        recorded = _resume_binding(spec, case, workspace, session)
        if recorded["configuration"] != configured:
            report["protocol"]["error"] = "host configuration changed during the interrupted stage"
            return first
        (directory / "resume-binding.json").write_text(json.dumps(recorded, indent=2) + "\n")
        resumed = bind_resume(
            spec, recorded, _resume_binding(spec, case, workspace, session), workspace
        )
        result = runner.run(resumed, case["prompt"], workspace, capture_usage=True, timeout=timeout)
        report["stages"].append({
            "command": result.command,
            "returncode": result.returncode,
            "duration_s": result.duration_s,
        })
        report["protocol"]["satisfied"] = (
            observed_session(spec, result) == session
            and result.returncode == 0
            and not result.timed_out
            and fingerprint(workspace, [checkpoint.path.as_posix()]) == checkpoint_state
        )
        report["protocol"]["usage_scope"] = (
            "Final dispatch only; whole-trial usage is not yet reconciled."
        )
    except (OSError, ValueError) as error:
        report["protocol"]["error"] = str(error)
    return result


def _run_trial_agent(
    spec: runner.RunnerSpec,
    case: dict[str, Any],
    directory: Path,
    report: dict[str, Any],
    timeout: float,
) -> runner.RunResult:
    protocol = case.get("protocol")
    if protocol is None:
        return runner.run(
            spec, case["prompt"], directory / "workspace", capture_usage=True, timeout=timeout
        )
    if not isinstance(protocol, dict):
        raise ValueError("trial protocol must be an object")
    if protocol.get("kind") == "resume":
        return _resume_trial(spec, case, directory, report, timeout)
    if protocol.get("kind") != "permission-denial":
        raise ValueError(f"unknown trial protocol: {protocol.get('kind')}")
    if spec.name == "codex":
        selected = dataclasses.replace(spec, sandbox="read-only", approval="never")
    elif spec.name == "claude":
        selected = dataclasses.replace(
            spec, command=(*spec.command, "--permission-prompts", "none")
        )
    else:
        raise ValueError("permission trials require an explicit native family")
    result = runner.run(
        selected, case["prompt"], directory / "workspace", capture_usage=True, timeout=timeout
    )
    denied = native_denials(selected, result)
    if not denied and selected.name == "codex":
        denied = codex_denials(selected, result, directory / "workspace")
    report["protocol"] = {
        "kind": "permission-denial",
        "satisfied": bool(denied),
        "native_evidence": denied,
    }
    return result


def _save_acceptance(
    workspace: Path,
    case: dict[str, Any],
    report: dict[str, Any],
    preserved: dict[str, str],
    timeout: float,
) -> None:
    checked = grade_preserved(workspace, case, preserved, timeout)
    if "protocol" in report:
        checked["protocol_satisfied"] = report["protocol"]["satisfied"]
        if not checked["protocol_satisfied"]:
            checked["outcome"] = "failed"
    report["acceptance"] = checked
    (workspace.parent / "grading.json").write_text(json.dumps(checked, indent=2) + "\n")


def _save_execution(
    spec: runner.RunnerSpec,
    result: runner.RunResult,
    directory: Path,
    report: dict[str, Any],
) -> None:
    (directory / "agent.stdout").write_text(result.stdout, encoding="utf-8")
    (directory / "agent.stderr").write_text(result.stderr, encoding="utf-8")
    report.update(agent_duration_s=result.duration_s, returncode=result.returncode)
    if stages := report.get("stages"):
        times = [stage["duration_s"] for stage in stages]
        report["agent_duration_s"] = (
            sum(times) if all(value is not None for value in times) else None
        )
    if error := report.get("protocol", {}).get("error"):
        raise ValueError(error)
    report["session_id"] = observed_session(spec, result)
    report["model_resolution"] = (
        dataclasses.asdict(result.model_resolution) if result.model_resolution else None
    )
    usage = runner.extract_usage(spec, result)
    report["usage"] = dataclasses.asdict(usage) if usage is not None else None
    if usage is not None and not usage.estimated:
        report.update(tokens=usage.tokens, cost=usage.cost)
    report["cost_source"] = "client-side CLI estimate" if report["cost"] is not None else "unknown"
    if report.get("protocol", {}).get("kind") == "resume":
        report.update(tokens=None, cost=None, cost_source="unreconciled multi-stage usage")


def execute_trial(
    spec: runner.RunnerSpec,
    case: dict[str, Any],
    directory: Path,
    identity: dict[str, str],
    *,
    timeout: float = 600,
) -> dict[str, Any]:
    if not case.get("check"):
        raise ValueError("a trial requires an independent acceptance command")
    preparation_started = time.perf_counter()
    directory.mkdir(parents=True, exist_ok=False)
    workspace = directory / "workspace"
    write_fixture(workspace, case["files"])
    subprocess.run(  # nosec B603 B607 # Fixed Git command in a newly created fixture.
        ["git", "init", "-q", str(workspace)],
        env=runner.sanitised_git_env(os.environ),
        check=True,
        capture_output=True,
    )
    started = time.perf_counter()
    report: dict[str, Any] = {
        **identity,
        "case": case["id"],
        "outcome": "infrastructure_error",
        "tokens": None,
        "cost": None,
    }
    result = None
    preserved = None
    try:
        if not prepare_case(workspace, case, report, timeout):
            return report
        preserved = fingerprint(workspace, case.get("preserve", []))
        report["setup_duration_s"] = time.perf_counter() - preparation_started
        started = time.perf_counter()
        result = _run_trial_agent(spec, case, directory, report, timeout)
        _save_execution(spec, result, directory, report)
        if result.executed and not result.handoff:
            _save_acceptance(workspace, case, report, preserved, timeout)
        if result.timed_out:
            report["outcome"] = "timed_out"
        elif not result.executed or result.handoff or result.returncode != 0:
            report["outcome"] = "infrastructure_error"
        else:
            report["outcome"] = report["acceptance"]["outcome"]
    except (OSError, ValueError) as error:
        report["outcome"] = "infrastructure_error"
        report["error"] = str(error)
        observed = result is not None and result.executed and not result.handoff
        if (
            (observed or isinstance(error, ValueError))
            and preserved is not None
            and "acceptance" not in report
        ):
            _save_acceptance(workspace, case, report, preserved, timeout)
    except KeyboardInterrupt:
        report["outcome"] = "interrupted"
        raise
    finally:
        report.setdefault("setup_duration_s", time.perf_counter() - preparation_started)
        report["duration_s"] = time.perf_counter() - started
        report["total_duration_s"] = time.perf_counter() - preparation_started
        (directory / "trial.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report
