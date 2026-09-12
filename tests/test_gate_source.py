from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from basicly import gate_source, policy, tracker
from basicly.config import PolicyConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
ISSUE = "gate-0001"

ENGINE_PROVIDER = "basicly-verify"


def _repo(tmp_path: Path) -> Path:

    (tmp_path / tracker.KIT_TRACKER_DIR).mkdir(parents=True, exist_ok=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, tmp_path / tracker.KIT_TRACKER_DIR / source.name)
    (tmp_path / tracker.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / "basicly.toml").write_text('[tracker]\nmode = "owned"\n', encoding="utf-8")
    kit = tracker.kit(tmp_path)
    kit.events.append(
        tracker.ledger_dir(tmp_path),
        [kit.events.Draft(ISSUE, kit.events.KIND_STATUS, {"status": "open"})],
    )
    return tmp_path


def _report(repo: Path, *, gate: str = "verify", status: str = "pass") -> None:
    flags = ["--gate", gate, "--provider", ENGINE_PROVIDER, "--status", status]
    tracker.write(repo, ["gate", "report", *flags, ISSUE])


@pytest.fixture(autouse=True)
def refuse_spawn(monkeypatch: pytest.MonkeyPatch) -> None:

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"the engine spawned a process after the flip: {cmd}")

    monkeypatch.setattr(subprocess, "run", refuse)


def test_the_engine_reads_a_green_gate_off_the_fold(tmp_path: Path) -> None:

    repo = _repo(tmp_path)
    _report(repo)

    assert gate_source.read_gates(repo, ISSUE) == [
        {"gate": "verify", "provider": ENGINE_PROVIDER, "passed": True}
    ]

    status = policy.gate_status(repo, ISSUE, PolicyConfig(required_gates=("verify",), max_rework=2))

    assert status.can_advance
    assert status.required_passed == ("verify",)


def test_a_gate_no_store_recorded_is_not_green(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    status = policy.gate_status(repo, ISSUE, PolicyConfig(required_gates=("verify",), max_rework=2))

    assert not status.can_advance
    assert status.required_missing == ("verify",)


def test_a_failing_gate_reads_back_as_failing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _report(repo, status="fail")

    assert gate_source.read_gates(repo, ISSUE) == [
        {"gate": "verify", "provider": ENGINE_PROVIDER, "passed": False}
    ]


def test_a_tombstoned_record_answers_no_gates(tmp_path: Path) -> None:

    repo = _repo(tmp_path)
    _report(repo)
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo),
        [kit.events.Draft(ISSUE, kit.events.KIND_TOMBSTONE, {})],
    )

    assert gate_source.read_gates(repo, ISSUE) == []


def test_the_store_is_reached_only_from_its_own_seam_modules() -> None:

    root = REPO_ROOT / "src" / "basicly"
    reaching = [
        f"{prefix}{name}("
        for prefix in ("br.", "owned_store.")
        for name in ("owned_record", "tracker_mode", "ledger_dir", "kit")
    ]
    branching = sorted(
        path.name
        for path in sorted(root.glob("*.py"))
        if path.name != "tracker.py"
        if any(name in path.read_text(encoding="utf-8") for name in reaching)
    )

    assert branching == [
        "board_snapshot.py",
        "dependency_graph.py",
        "gate_source.py",
        "label_source.py",
        "owned_write.py",
        "tracker_import.py",
        "tracker_query.py",
    ]
