from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from basicly import process_confirmation, verify


@pytest.fixture
def evidence(monkeypatch: pytest.MonkeyPatch):
    report = {
        "revision": "current",
        "owed": [],
        "review": {
            "checks": [{"criterion": "Consumer sees the result", "command": ["check", "result"]}]
        },
        "confirmation": {},
    }
    module = SimpleNamespace(
        confirmation_report=lambda *_a, **_k: report,
        closing_owed=lambda *_a, **_k: (
            ()
            if report["confirmation"].get("revision") == report["revision"]
            else ("Completion Confirmation",)
        ),
    )
    module.load = lambda *_a: object()
    events = SimpleNamespace(
        read_events=lambda *_a: ([], []),
        fold=lambda *_a: SimpleNamespace(records={"item": SimpleNamespace(fields={})}),
    )
    monkeypatch.setattr(
        process_confirmation.tracker, "kit", lambda _r, name: events if name == "events" else module
    )
    monkeypatch.setattr(process_confirmation.tracker, "ledger_dir", lambda root: root)
    written: list[list[str]] = []
    monkeypatch.setattr(
        process_confirmation.tracker, "write", lambda _r, args: written.append(args)
    )
    return report, written


def test_matching_actual_output_is_recorded(tmp_path: Path, evidence) -> None:
    _, written = evidence
    actual = verify.CheckResult(
        "check", "pass", 0, output="observed result\n", command=("check", "result")
    )
    assert process_confirmation.confirm(tmp_path, "item", (actual,)) is None
    payload = json.loads(written[0][-1])
    assert payload == {
        "checks": [
            {
                "criterion": "Consumer sees the result",
                "command": ["check", "result"],
                "result": "observed result\n",
                "exit_code": 0,
            }
        ]
    }


@pytest.mark.parametrize(
    "actual",
    [
        verify.CheckResult("check", "pass", 0),
        verify.CheckResult("check", "pass", 0, command=("check", "other")),
        verify.CheckResult("check", "skip", 0, command=("check", "result")),
        verify.CheckResult("check", "fail", 1, output="failed", command=("check", "result")),
    ],
)
def test_missing_actual_command_refuses_without_writes(
    tmp_path: Path, evidence, actual: verify.CheckResult
) -> None:
    _, written = evidence
    reason = process_confirmation.confirm(tmp_path, "item", (actual,))
    assert reason and "Consumer sees the result" in reason and "check" in reason
    assert written == []


def test_invalidated_review_refuses_without_writes(tmp_path: Path, evidence) -> None:
    report, written = evidence
    report["owed"] = ["Confirmation Plan"]
    reason = process_confirmation.confirm(tmp_path, "item")
    assert reason and "Confirmation Plan" in reason
    assert written == []


def test_existing_current_confirmation_needs_no_repeated_execution(
    tmp_path: Path, evidence
) -> None:
    report, written = evidence
    report["confirmation"] = {"revision": "current"}
    assert process_confirmation.confirm(tmp_path, "item") is None
    assert written == []


def test_silent_success_records_actual_exit_status(tmp_path: Path, evidence) -> None:
    _, written = evidence
    actual = verify.CheckResult("check", "pass", 0, command=("check", "result"))
    assert process_confirmation.confirm(tmp_path, "item", (actual,)) is None
    assert "exit code 0" in json.loads(written[0][-1])["checks"][0]["result"]


def test_partial_matching_evidence_records_no_confirmation(tmp_path: Path, evidence) -> None:
    report, written = evidence
    report["review"]["checks"].append({
        "criterion": "Second outcome",
        "command": ["check", "second"],
    })
    actual = verify.CheckResult(
        "check", "pass", 0, output="first result", command=("check", "result")
    )
    reason = process_confirmation.confirm(tmp_path, "item", (actual,))
    assert reason and "Second outcome" in reason and "second" in reason
    assert written == []


def test_refused_confirmation_is_reported(
    tmp_path: Path, evidence, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, written = evidence

    def refused(*_args) -> None:
        raise RuntimeError("review changed concurrently")

    monkeypatch.setattr(process_confirmation.tracker, "write", refused)
    actual = verify.CheckResult("check", "pass", 0, command=("check", "result"))
    reason = process_confirmation.confirm(tmp_path, "item", (actual,))
    assert reason and "review changed concurrently" in reason
    assert written == []


def test_stale_confirmation_does_not_authorize_completion(tmp_path: Path, evidence) -> None:
    report, written = evidence
    report["confirmation"] = {"revision": "old"}
    reason = process_confirmation.confirm(tmp_path, "item")
    assert reason and "Consumer sees the result" in reason
    assert written == []
