from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from .verify_receipt import CachedCheck, CheckReceipt

RUN_ARTIFACT = Path(".basicly/usage/verify-run.json")


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    returncode: int
    detail: str = ""
    output: str = ""
    command: tuple[str, ...] = ()
    duration_s: float = 0.0
    reused_from: str = ""
    reuse_reason: str = ""
    source_duration_s: float = 0.0
    input_fingerprint: str = ""
    infrastructure_error: bool = False


@dataclass(frozen=True)
class VerifyReport:
    mode: str
    results: tuple[CheckResult, ...]
    run_id: str = ""
    receipts: tuple[CheckReceipt, ...] = ()
    duration_s: float = 0.0
    selection: dict | None = None
    subject_fingerprint: str = ""

    @property
    def passed(self) -> bool:
        return not any(r.status in {"fail", "blocked"} for r in self.results)

    @property
    def failures(self) -> tuple[str, ...]:
        return tuple(r.name for r in self.results if r.status == "fail")


def execution_failure(name: str, command: Sequence[str], error: OSError) -> CheckResult:
    missing = isinstance(error, FileNotFoundError)
    detail = (
        f"command not found: {command[0]} — install it or edit [[verify.checks]] in basicly.toml"
        if missing
        else f"cannot run {command[0]} ({error.strerror or error}) — check "
        "[[verify.checks]] in basicly.toml"
    )
    return CheckResult(
        name,
        "fail",
        127 if missing else 126,
        detail,
        command=tuple(command),
        infrastructure_error=True,
    )


def infrastructure_failure(report: VerifyReport) -> str:
    failed = [result for result in report.results if result.status == "fail"]
    if not failed or not all(result.infrastructure_error for result in failed):
        return ""
    return "verification environment unavailable: " + "; ".join(
        f"{result.name}: {result.detail}" for result in failed
    )


def blocked_result(name: str, command: tuple[str, ...], failed: str) -> CheckResult:
    return CheckResult(name, "blocked", 0, f"not run after failed check {failed}", command=command)


def receipt_result(
    name: str, command: tuple[str, ...], mode: str, inputs: str, source: VerifyReport | None
) -> CheckResult | None:
    if source is None or not source.passed or not inputs:
        return None
    receipt = next((r for r in source.receipts if r.name == name), None)
    if receipt != CheckReceipt(
        name, command, mode, inputs, source.run_id, receipt.components if receipt else ()
    ):
        return None
    original = next((r for r in source.results if r.name == name), None)
    if (
        original is None
        or original.status != "pass"
        or original.returncode != 0
        or original.command != command
        or original.input_fingerprint != inputs
    ):
        return None
    return replace(
        original,
        duration_s=0.0,
        reused_from=original.reused_from or source.run_id,
        reuse_reason="matching same-landing inputs",
        source_duration_s=original.source_duration_s
        if original.reused_from
        else original.duration_s,
        input_fingerprint=inputs,
    )


@runtime_checkable
class CheckOutcome(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def returncode(self) -> int: ...

    @property
    def detail(self) -> str: ...

    @property
    def command(self) -> tuple[str, ...]: ...

    @property
    def duration_s(self) -> float: ...

    @property
    def reused_from(self) -> str: ...

    @property
    def reuse_reason(self) -> str: ...

    @property
    def source_duration_s(self) -> float: ...

    @property
    def input_fingerprint(self) -> str: ...


@runtime_checkable
class RunVerdict(Protocol):
    @property
    def mode(self) -> str: ...

    @property
    def passed(self) -> bool: ...

    @property
    def results(self) -> tuple[CheckOutcome, ...]: ...

    @property
    def run_id(self) -> str: ...

    @property
    def duration_s(self) -> float: ...

    @property
    def receipts(self) -> tuple[CheckReceipt, ...]: ...


def recorded_detail(outcome: CheckOutcome) -> str:

    if outcome.status != "fail" or outcome.detail:
        return outcome.detail
    if not outcome.command:
        return "the check failed and reported nothing"
    return f"output streamed rather than captured; reproduce with: {' '.join(outcome.command)}"


def write_run_artifact(repo_root: Path, report: RunVerdict) -> Path | None:

    path = repo_root / RUN_ARTIFACT
    payload = {
        "mode": report.mode,
        "recorded_at": datetime.now(UTC).isoformat(),
        "passed": report.passed,
        "selection": getattr(report, "selection", None),
        "subject_fingerprint": getattr(report, "subject_fingerprint", ""),
        "run_id": report.run_id,
        "duration_s": report.duration_s,
        "receipts": [
            {
                "name": r.name,
                "command": list(r.command),
                "mode": r.mode,
                "inputs": r.inputs,
                "origin": r.origin,
                **({"components": dict(r.components)} if r.components else {}),
            }
            for r in report.receipts
        ],
        "checks": [
            {
                "name": r.name,
                "command": list(r.command),
                "status": r.status,
                "returncode": r.returncode,
                "detail": recorded_detail(r),
                **(
                    {"infrastructure_error": True}
                    if getattr(r, "infrastructure_error", False)
                    else {}
                ),
                "disposition": "reused"
                if r.reused_from
                else "not-run"
                if r.status == "blocked"
                else "skipped"
                if r.status == "skip"
                else "executed",
                "duration_s": r.duration_s,
                "reused_from": r.reused_from,
                "reuse_reason": r.reuse_reason,
                "source_duration_s": r.source_duration_s,
                "input_fingerprint": r.input_fingerprint,
            }
            for r in report.results
        ],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        gitignore = path.parent / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n", encoding="utf-8")
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return None
    return path


def cached_result(
    name: str, command: tuple[str, ...], identity: tuple[str, str], cached: tuple[CachedCheck, ...]
) -> CheckResult | None:
    mode, inputs = identity
    previous = next(
        (
            entry
            for entry in cached
            if (entry.name, entry.command, entry.mode, entry.inputs)
            == (name, command, mode, inputs)
        ),
        None,
    )
    if previous is None:
        return None
    return CheckResult(
        name,
        "pass",
        0,
        command=command,
        reused_from=previous.origin,
        reuse_reason="matching local cache inputs",
        source_duration_s=previous.duration_s,
        input_fingerprint=inputs,
    )
