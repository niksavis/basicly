from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

RUN_ARTIFACT = Path(".basicly/usage/verify-run.json")


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


@runtime_checkable
class RunVerdict(Protocol):
    @property
    def mode(self) -> str: ...

    @property
    def passed(self) -> bool: ...

    @property
    def results(self) -> tuple[CheckOutcome, ...]: ...


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
        "checks": [
            {
                "name": r.name,
                "status": r.status,
                "returncode": r.returncode,
                "detail": recorded_detail(r),
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
