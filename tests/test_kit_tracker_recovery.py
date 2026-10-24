from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, events, fsck

recovery = fsck.recovery
RECORD = "demo-a"


def _ledger(directory: Path) -> Path:
    events.append(
        directory,
        [
            events.Draft(RECORD, "created", {"title": "history"}),
            events.Draft(RECORD, "comment", {"text": "lost"}),
            events.Draft(RECORD, "comment", {"text": "surviving"}),
            events.Draft(RECORD, "status", {"status": "closed"}),
        ],
        writer="",
    )
    _drop(directory, 2)
    return directory


def _drop(directory: Path, seq: int) -> None:
    path = directory / events.INITIAL_LOG_NAME
    lines = path.read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line)["seq"] == seq for line in lines)
    path.write_text(
        "".join(line + "\n" for line in lines if json.loads(line)["seq"] != seq),
        encoding="utf-8",
    )


def _acknowledge(directory: Path) -> list[Any]:
    return recovery.acknowledge(
        directory, RECORD, recovery.Request((2,), "original content unavailable", ("git:abc123",))
    )


def test_acknowledgment_appends_a_visible_loss_without_inventing_history(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "ledger")
    path = ledger / events.INITIAL_LOG_NAME
    before = path.read_bytes()
    assert fsck.check(ledger).exit_code == fsck.EXIT_BROKEN

    appended = _acknowledge(ledger)

    assert path.read_bytes().startswith(before)
    assert appended[0].seq == 5
    report = fsck.check(ledger)
    assert report.exit_code == fsck.EXIT_CLEAN
    assert report.clean
    assert [(one.kind, one.severity) for one in report.findings] == [
        (recovery.ACKNOWLEDGED_LOSS, fsck.WARNING)
    ]
    assert "original content unavailable" in report.findings[0].detail
    assert "git:abc123" in report.findings[0].detail
    assert fsck.sequence_gaps(events.read_events(ledger)[0]) == {RECORD: (2,)}
    assert events.fold(events.read_events(ledger)[0]).mismatched_totals
    assert not any(one.kind == fsck.CARRIED_TOTALS for one in report.findings)


@pytest.mark.parametrize(
    "loss_request",
    [
        recovery.Request((3,), "reason", ("git:abc",)),
        recovery.Request((2, 3), "reason", ("git:abc",)),
        recovery.Request((2, 2), "reason", ("git:abc",)),
        recovery.Request((0,), "reason", ("git:abc",)),
        recovery.Request((True,), "reason", ("git:abc",)),
        recovery.Request((2,), " ", ("git:abc",)),
        recovery.Request((2,), "reason", ()),
        recovery.Request((2,), "reason", (" ",)),
    ],
)
def test_invalid_loss_requests_leave_the_ledger_unchanged(
    tmp_path: Path, loss_request: Any
) -> None:
    ledger = _ledger(tmp_path / "ledger")
    path = ledger / events.INITIAL_LOG_NAME
    before = path.read_bytes()

    with pytest.raises(events.InvalidEventError):
        recovery.acknowledge(ledger, RECORD, loss_request)

    assert path.read_bytes() == before
    assert fsck.check(ledger).exit_code == fsck.EXIT_BROKEN


def test_an_unknown_record_cannot_acknowledge_another_records_loss(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "ledger")

    with pytest.raises(events.InvalidEventError, match="no created record"):
        recovery.acknowledge(ledger, "demo-absent", recovery.Request((2,), "reason", ("git:abc",)))


@pytest.mark.parametrize(
    "changed",
    [
        {"record": "demo-other"},
        {"missing_sequences": [3]},
        {"history_sha256": "sha256:wrong"},
        {"boundary_seq": 50},
        {"boundary_seq": True},
        {"evidence": []},
        {"reason": ""},
    ],
)
def test_malformed_or_stale_acknowledgments_leave_the_gap_blocking(
    tmp_path: Path, changed: dict[str, object]
) -> None:
    ledger = _ledger(tmp_path / "ledger")
    original = _acknowledge(ledger)[0]
    body = {**original.payload["body"], **changed}
    events.append(
        ledger,
        [events.Draft(RECORD, "artifact", {"artifact": recovery.LOSS_ARTIFACT, "body": body})],
        writer="",
    )

    report = fsck.check(ledger)

    assert report.exit_code == fsck.EXIT_BROKEN
    assert {one.kind for one in report.findings} >= {
        fsck.SEQUENCE_GAP,
        recovery.INVALID_ACKNOWLEDGMENT,
    }


def test_changed_surviving_history_invalidates_its_loss_acknowledgment(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "ledger")
    _acknowledge(ledger)
    path = ledger / events.INITIAL_LOG_NAME
    rewritten = []
    for event in events.read_events(ledger)[0]:
        kept = event
        if event.seq == 3:
            payload = {"text": "changed surviving history"}
            kept = replace(
                event, payload=payload, id=events.event_id_for(RECORD, event.kind, payload)
            )
        rewritten.append(events.to_json(kept))
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    report = fsck.check(ledger)

    assert report.exit_code == fsck.EXIT_BROKEN
    assert any(one.kind == recovery.INVALID_ACKNOWLEDGMENT for one in report.findings)


def test_a_later_gap_is_not_covered_by_an_earlier_acknowledgment(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "ledger")
    _acknowledge(ledger)
    events.append(
        ledger,
        [
            events.Draft(RECORD, "comment", {"text": "later lost"}),
            events.Draft(RECORD, "comment", {"text": "later surviving"}),
        ],
        writer="",
    )
    _drop(ledger, 6)

    report = fsck.check(ledger)

    assert report.exit_code == fsck.EXIT_BROKEN
    gap = next(one for one in report.findings if one.kind == fsck.SEQUENCE_GAP)
    assert "sequence 6" in gap.detail
    assert any(one.kind == recovery.ACKNOWLEDGED_LOSS for one in report.findings)


def test_a_copied_kit_acknowledges_loss_without_site_packages(tmp_path: Path) -> None:
    kit = tmp_path / "kit"
    shutil.copytree(REPO_ROOT / KIT_RELATIVE, kit)
    ledger = _ledger(tmp_path / "ledger")
    command = [sys.executable, "-I", "-S", str(kit / "cli.py")]
    acknowledged = subprocess.run(
        [
            *command,
            "acknowledge-loss",
            str(ledger),
            RECORD,
            "--sequence",
            "2",
            "--reason",
            "historical loss",
            "--evidence",
            "git:abc123",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert acknowledged.returncode == 0, acknowledged.stderr
    assert json.loads(acknowledged.stdout)["appended"]
    checked = subprocess.run(
        [*command, "fsck", str(ledger)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert checked.returncode == 0, checked.stderr
    report = json.loads(checked.stdout)
    assert report["warnings"] == 1
    assert report["findings"][0]["kind"] == recovery.ACKNOWLEDGED_LOSS
