from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "ledger_bodies.py"
KIT_RELATIVE = Path(".basicly") / "core" / "kit" / "tracker"

WORK_LOG_KINDS = ("status", "comment", "edge")
GATED_KINDS = ("status", "comment", "edge", "field", "gate")


@pytest.fixture(scope="module")
def kit() -> Any:
    source = REPO_ROOT / KIT_RELATIVE / "events.py"
    name = "basicly_tracker_kit_events"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _payload_for(kind: str, record: str) -> dict[str, object]:
    if kind == "status":
        return {"status": "closed"}
    if kind == "comment":
        return {"text": f"a comment on {record}"}
    if kind == "edge":
        return {"to": "basicly-root", "type": "parent-child"}
    if kind == "field":
        return {"name": "external_ref", "value": "worktree:lane:branch"}
    if kind == "gate":
        return {"gate": "verify", "provider": "basicly-verify", "passed": True}
    return {"title": f"the body of {record}", "description": "what the work was"}


def _write_ledger(kit: Any, directory: Path, records: dict[str, tuple[str, ...]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    drafts = [
        kit.Draft(record, kind, _payload_for(kind, record))
        for record, kinds in records.items()
        for kind in kinds
    ]
    kit.append(directory, drafts)


def _run_check(ledger: Path, repo: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), "--ledger", str(ledger)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_record_with_a_work_log_and_no_created_event_is_named(kit: Any, tmp_path: Path) -> None:

    ledger = tmp_path / "ledger"
    _write_ledger(
        kit,
        ledger,
        {"basicly-aaaa.1": WORK_LOG_KINDS, "basicly-aaaa.2": GATED_KINDS},
    )

    completed = _run_check(ledger)

    assert completed.returncode == 1
    assert "basicly-aaaa.1" in completed.stderr
    assert "basicly-aaaa.2" in completed.stderr
    assert "2 of 2 record(s)" in completed.stderr


def test_the_same_ledger_with_a_created_event_is_clean(kit: Any, tmp_path: Path) -> None:

    ledger = tmp_path / "ledger"
    _write_ledger(
        kit,
        ledger,
        {
            "basicly-aaaa.1": ("created", *WORK_LOG_KINDS),
            "basicly-aaaa.2": ("created", *GATED_KINDS),
        },
    )

    completed = _run_check(ledger)

    assert completed.returncode == 0
    assert "all 2 record(s)" in completed.stdout


def test_a_ledger_with_no_log_reports_its_population_of_zero(tmp_path: Path) -> None:

    completed = _run_check(tmp_path / "empty")

    assert completed.returncode == 0
    assert "all 0 record(s)" in completed.stdout


def test_a_host_with_no_kit_is_an_error_rather_than_a_pass(tmp_path: Path) -> None:

    ledger = tmp_path / "ledger"
    ledger.mkdir()

    completed = _run_check(ledger, repo=tmp_path)

    assert completed.returncode == 1
    assert "no tracker kit" in completed.stderr


def test_the_fold_cannot_see_what_the_check_reports(kit: Any, tmp_path: Path) -> None:

    ledger = tmp_path / "ledger"
    _write_ledger(kit, ledger, {"basicly-aaaa.1": WORK_LOG_KINDS})

    found, _ = kit.read_events(ledger)
    state = kit.fold(found).records["basicly-aaaa.1"]

    assert state.status == "closed"
    assert state.comments == ["a comment on basicly-aaaa.1"]
    assert "title" not in state.fields


def test_the_check_leaves_the_ledger_byte_identical(kit: Any, tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    _write_ledger(kit, ledger, {"basicly-aaaa.1": WORK_LOG_KINDS})
    before = {path.name: path.read_bytes() for path in sorted(ledger.iterdir())}

    assert _run_check(ledger).returncode == 1

    assert {path.name: path.read_bytes() for path in sorted(ledger.iterdir())} == before
