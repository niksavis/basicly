from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from . import tracker, verify


def confirm(repo_root: Path, record: str, results: Sequence[verify.CheckResult] = ()) -> str | None:
    events = tracker.kit(repo_root, "events")
    process = tracker.kit(repo_root, "process_evidence")
    found, quarantine = events.read_events(tracker.ledger_dir(repo_root))
    if quarantine:
        return f"confirmation for {record} refuses quarantined tracker evidence"
    state = events.fold(found).records.get(record)
    if state is None:
        return f"confirmation requires existing record {record}"
    template = tracker.kit(repo_root, "templates").load(tracker.ledger_dir(repo_root))
    report = process.confirmation_report(found, record, template=template)
    if report["owed"]:
        return f"confirmation for {record} requires current review: {', '.join(report['owed'])}"
    if not process.closing_owed(found, record, state.fields, template=template):
        return None
    checks, missing = _matched_checks(report["review"]["checks"], results)
    if missing:
        detail = "; ".join(missing)
        return f"confirmation for {record} lacks actual successful check evidence: {detail}"
    return _record_confirmation(repo_root, record, checks)


def _matched_checks(
    planned_checks: Sequence[dict], results: Sequence[verify.CheckResult]
) -> tuple[list[dict], list[str]]:
    checks = []
    missing = []
    for planned in planned_checks:
        actual = next(
            (
                result
                for result in results
                if result.status == "pass"
                and result.returncode == 0
                and list(result.command) == planned["command"]
            ),
            None,
        )
        if actual is None:
            missing.append(f"{planned['criterion']}: {json.dumps(planned['command'])}")
            continue
        checks.append({
            "criterion": planned["criterion"],
            "command": list(actual.command),
            "result": actual.output
            or "Executed verification command returned exit code 0 with no output",
            "exit_code": actual.returncode,
        })
    return checks, missing


def _record_confirmation(repo_root: Path, record: str, checks: Sequence[dict]) -> str | None:
    try:
        tracker.write(repo_root, ["confirm", record, "--evidence", json.dumps({"checks": checks})])
    except RuntimeError as exc:
        return f"confirmation for {record} was refused: {exc}"
    return None
