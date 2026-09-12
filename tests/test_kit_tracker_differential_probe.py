from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


differential = _load(KIT_DIR / "differential.py", "tracker_differential")
migrate = differential.migrate

SOURCE = "beads/issues.jsonl"
CLOCK = 1_000_000_000.0
VOCAB = differential.DEFAULT_VOCABULARY

OPEN_RECORD = "basicly-aa11"
CLOSED_RECORD = "basicly-bb22"


def _ledger(directory: Path) -> list[Any]:
    text = "".join(
        json.dumps({"id": record, "title": record, "status": status}) + "\n"
        for record, status in ((OPEN_RECORD, "open"), (CLOSED_RECORD, "closed"))
    )
    report = migrate.import_snapshot(
        directory, migrate.parse_snapshot(text, name=SOURCE), clock=lambda: CLOCK
    )
    assert report.rejected == [], report.rejected
    return differential.read_ledger(directory)


def _views(**status: str) -> dict[str, Any]:
    held = {OPEN_RECORD: "open", CLOSED_RECORD: "closed"}
    held.update(status)
    return {
        record: differential.RecordView(record=record, status=value)
        for record, value in held.items()
    }


def _reference(*answers: dict[str, Any]) -> tuple[Any, list[int]]:

    reads: list[int] = []

    def views(_ledger_events: Any) -> dict[str, Any]:
        reads.append(len(reads))
        return answers[min(len(reads) - 1, len(answers) - 1)]

    return differential.ReferenceSource(views=views), reads


def test_a_reference_the_tracker_moved_under_is_not_called_a_derivative(tmp_path: Path) -> None:

    ledger_events = _ledger(tmp_path / "ledger")
    baseline = _views()
    moved = _views(**{OPEN_RECORD: "in_progress"})
    source, reads = _reference(moved)

    refusals, unproven = differential.audit_reference(source, ledger_events, baseline, VOCAB)

    assert refusals == []
    assert [item.subject for item in unproven] == [differential.RULE_DERIVED_FROM_LEDGER]
    assert "two reads of the *unperturbed* ledger" in unproven[0].reason
    assert len(reads) == 2, "the probe read, then the control"


def test_a_derivative_that_holds_still_without_the_probe_is_still_refused(
    tmp_path: Path,
) -> None:

    ledger_events = _ledger(tmp_path / "ledger")
    source = differential.ReferenceSource(views=differential.views_from_events)
    baseline = dict(differential.views_from_events(ledger_events))

    refusals, unproven = differential.audit_reference(source, ledger_events, baseline, VOCAB)

    assert [refusal.rule for refusal in refusals] == [differential.RULE_DERIVED_FROM_LEDGER]
    assert differential.LOSSY_SNAPSHOT_REASON in refusals[0].detail
    assert unproven == []


def test_a_reference_that_did_not_move_pays_for_no_control_read(tmp_path: Path) -> None:
    ledger_events = _ledger(tmp_path / "ledger")
    baseline = _views()
    source, reads = _reference(baseline)

    refusals, unproven = differential.audit_reference(source, ledger_events, baseline, VOCAB)

    assert (refusals, unproven) == ([], [])
    assert len(reads) == 1


def test_a_run_against_a_moving_tracker_is_inconclusive_rather_than_refused(
    tmp_path: Path,
) -> None:

    directory = tmp_path / "ledger"
    _ledger(directory)
    source, _ = _reference(_views(), _views(**{OPEN_RECORD: "in_progress"}))

    report = differential.run_differential(directory, source, VOCAB)

    assert report.refusals == []
    assert not report.conclusive
    assert differential.RULE_DERIVED_FROM_LEDGER in [item.subject for item in report.inconclusive]


provenance = _load(KIT_DIR / "provenance.py", "tracker_provenance")

DIALECT_PAIRS = {
    differential.provenance.DIALECT_DECLARED: (provenance.KEY_TARGET, provenance.KEY_TYPE),
    differential.provenance.DIALECT_ENGINE: (provenance.ALT_KEY_TARGET, provenance.ALT_KEY_TYPE),
}
EDGES = (("r-2", "blocks"), ("r-3", "blocks"), ("r-4", "parent-child"), ("r-5", "discovered-from"))


def _edge_events(dialect: str) -> list[Any]:

    events = differential.events
    target_key, type_key = DIALECT_PAIRS[dialect]
    created = events.Event(
        id="r-1#ev-0",
        record="r-1",
        seq=0,
        kind=events.KIND_CREATED,
        ts="2026-01-01T00:00:00Z",
        actor="",
        payload={"title": "t"},
        totals={},
    )
    return [
        created,
        *(
            events.Event(
                id=f"r-1#ev-{index}",
                record="r-1",
                seq=index,
                kind=events.KIND_EDGE,
                ts=f"2026-01-0{index}T00:00:00Z",
                actor="",
                payload={target_key: target, type_key: edge_type},
                totals={},
            )
            for index, (target, edge_type) in enumerate(EDGES, start=1)
        ),
    ]


@pytest.mark.parametrize("dialect", sorted(DIALECT_PAIRS))
def test_the_fold_counts_every_edge_in_either_dialect(dialect: str) -> None:

    events = _edge_events(dialect)
    views = differential.views_from_events(events)
    assert len(views["r-1"].dependencies) == len(EDGES)


@pytest.mark.parametrize("dialect", sorted(DIALECT_PAIRS))
def test_the_fold_reports_which_dialect_it_read(dialect: str) -> None:
    assert differential.edge_dialects(_edge_events(dialect)) == (dialect,)


def test_a_payload_in_neither_dialect_is_dropped_rather_than_invented() -> None:
    events = differential.events
    created = events.Event(
        id="r-1#ev-0",
        record="r-1",
        seq=0,
        kind=events.KIND_CREATED,
        ts="2026-01-01T00:00:00Z",
        actor="",
        payload={"title": "t"},
        totals={},
    )
    stray = events.Event(
        id="r-1#ev-1",
        record="r-1",
        seq=1,
        kind=events.KIND_EDGE,
        ts="2026-01-02T00:00:00Z",
        actor="",
        payload={"nonsense": "x"},
        totals={},
    )
    assert differential.views_from_events([created, stray])["r-1"].dependencies == ()
