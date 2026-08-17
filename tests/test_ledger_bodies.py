"""Tests for the gate that names a record the owned ledger cannot describe.

The defect it was built for (basicly-vkh0.41): nine records reached the ledger with a work
log, a status, edges and gate rows and **no** ``created`` event, which is the only carrier
of a title, description, type, priority and acceptance criteria. The shadow differential
reported ``clean`` throughout, because its three queries read status, comments, edges and
gate rows and none of them reads a body.

``test_the_fold_cannot_see_what_the_check_reports`` is the reason this file exists rather
than an extra assertion in the differential's own tests: it asserts that the reader the
differential uses answers a full status and a full comment list for a record with no body,
so the gap is invisible from there by construction and not by oversight.

Every ledger here is written by the kit itself into ``tmp_path``, so the fixtures are real
logs in the real format rather than hand-assembled JSON. The gate is driven as a
subprocess, because its **exit code** is half of what it promises and an in-process call
would not exercise it. Nothing reads or writes the host's own ledger.
"""

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

# The two event-kind shapes the nine real records were found in (basicly-vkh0.41): four of
# them carried a comment, an edge and a status; five also carried a field and a gate row.
WORK_LOG_KINDS = ("status", "comment", "edge")
GATED_KINDS = ("status", "comment", "edge", "field", "gate")


@pytest.fixture(scope="module")
def kit() -> Any:
    """The kit's event log, loaded by path under the kit's own ``sys.modules`` name."""
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
    """A payload the ledger will accept for *kind*, shaped as its own writer writes it."""
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
    """Append one event per kind for each record in *records*, in the order given."""
    directory.mkdir(parents=True, exist_ok=True)
    drafts = [
        kit.Draft(record, kind, _payload_for(kind, record))
        for record, kinds in records.items()
        for kind in kinds
    ]
    kit.append(directory, drafts)


def _run_check(ledger: Path, repo: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    """Run the gate, never raising: a non-zero exit is the answer, not a failure."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), "--ledger", str(ledger)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_record_with_a_work_log_and_no_created_event_is_named(kit: Any, tmp_path: Path) -> None:
    """Both real shapes are reported by id, and the run exits non-zero.

    The two kind sets are the ones measured on the real ledger, so a record that carries
    *five* kinds of evidence about its work and nothing about its identity is still named —
    which is the case a reader of ``clean: yes`` was most likely to assume was covered.
    """
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
    """The positive control: one difference, and it is the one the gate is about.

    The same records and the same work log, plus a ``created`` event each. Without this the
    non-zero above is ambiguous between "the gate found the gap" and "the gate fails on any
    ledger", and the reported population is asserted so a clean verdict over zero records
    cannot stand in for a clean verdict over these two.
    """
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
    """An empty ledger passes, and says how many records it passed over.

    A gate that printed only "clean" here would report the absence of a population as
    agreement, which is the failure mode the whole cutover keeps paying for.
    """
    completed = _run_check(tmp_path / "empty")

    assert completed.returncode == 0
    assert "all 0 record(s)" in completed.stdout


def test_a_host_with_no_kit_is_an_error_rather_than_a_pass(tmp_path: Path) -> None:
    """No kit means no answer, and no answer exits non-zero.

    The fail-open shape is the one that matters: a gate that could not read the ledger and
    returned 0 would look exactly like a ledger with nothing wrong.
    """
    ledger = tmp_path / "ledger"
    ledger.mkdir()

    completed = _run_check(ledger, repo=tmp_path)

    assert completed.returncode == 1
    assert "no tracker kit" in completed.stderr


def test_the_fold_cannot_see_what_the_check_reports(kit: Any, tmp_path: Path) -> None:
    """The differential's own reader answers fully for a record with no body.

    This is the finding behind the bead, asserted rather than argued: ``events.fold``
    returns a status and the comment list for a bodyless record, so every input
    ``differential.RecordView`` carries is present and the three queries have nothing to
    disagree about. The gap is therefore unreachable from the differential's reader, which
    is why the check reads the raw events instead.
    """
    ledger = tmp_path / "ledger"
    _write_ledger(kit, ledger, {"basicly-aaaa.1": WORK_LOG_KINDS})

    found, _ = kit.read_events(ledger)
    state = kit.fold(found).records["basicly-aaaa.1"]

    assert state.status == "closed"
    assert state.comments == ["a comment on basicly-aaaa.1"]
    assert "title" not in state.fields


def test_the_check_leaves_the_ledger_byte_identical(kit: Any, tmp_path: Path) -> None:
    """It is a read of an append-only log and writes nothing, not even on a finding."""
    ledger = tmp_path / "ledger"
    _write_ledger(kit, ledger, {"basicly-aaaa.1": WORK_LOG_KINDS})
    before = {path.name: path.read_bytes() for path in sorted(ledger.iterdir())}

    assert _run_check(ledger).returncode == 1

    assert {path.name: path.read_bytes() for path in sorted(ledger.iterdir())} == before
