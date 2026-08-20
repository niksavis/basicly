"""Tests for the independence probe's control read (basicly-vkh0.35).

The aspect split `tests/test_kit_tracker_differential.py` could not take: that module owns
the comparison and the audit's declared-snapshot rules, this one owns the single question
of **what movement means**. The probe hands the reference a perturbed event set and reads
it again; the answers moving is evidence only once a read *without* the perturbation is
shown to hold still, because a live source is read twice over a wall-clock window and
anything else writing the tracker moves it in between.

That is not hypothetical: on 2026-08-16 `basicly tracker shadow` refused
`tracker.py:_live_reference` — a source whose `views` callable ignores the event set it is
handed and spawns `br` — as a derivative of the ledger it cannot read.

Nothing here spawns a process or reads a clock: the reference is a callable answering from
authored views, which is the shape the engine's live read presents to the audit.
"""

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
    """Load a standalone kit module by path, the way a consumer without basicly would."""
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
    """A two-record ledger, and its events. The clock is injected (§9.5)."""
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
    """The reference's views, with *status* overriding a record's — a write it saw."""
    held = {OPEN_RECORD: "open", CLOSED_RECORD: "closed"}
    held.update(status)
    return {
        record: differential.RecordView(record=record, status=value)
        for record, value in held.items()
    }


def _reference(*answers: dict[str, Any]) -> tuple[Any, list[int]]:
    """A source answering *answers* in order and ignoring its argument, plus its read log.

    The last answer repeats, so one answer is a source nothing wrote under and two is one
    the tracker moved under between the baseline read and the probe's.
    """
    reads: list[int] = []

    def views(_ledger_events: Any) -> dict[str, Any]:
        reads.append(len(reads))
        return answers[min(len(reads) - 1, len(answers) - 1)]

    return differential.ReferenceSource(views=views), reads


def test_a_reference_the_tracker_moved_under_is_not_called_a_derivative(tmp_path: Path) -> None:
    """The regression: movement the control reproduces is drift, not derivation.

    Both reads inside the audit see a status the baseline did not, which is what a `br`
    write landing mid-run looks like to a source that never touches the owned ledger.
    """
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
    """The positive control: the control read must not make the refusal unreachable.

    A reference that answers out of the owned event log moves under the perturbation and
    only under it, which is the one pattern the probe is built to name.
    """
    ledger_events = _ledger(tmp_path / "ledger")
    source = differential.ReferenceSource(views=differential.views_from_events)
    baseline = dict(differential.views_from_events(ledger_events))

    refusals, unproven = differential.audit_reference(source, ledger_events, baseline, VOCAB)

    assert [refusal.rule for refusal in refusals] == [differential.RULE_DERIVED_FROM_LEDGER]
    assert differential.LOSSY_SNAPSHOT_REASON in refusals[0].detail
    assert unproven == []


def test_a_reference_that_did_not_move_pays_for_no_control_read(tmp_path: Path) -> None:
    """The cost guard: reading the live tracker is a `br` spawn, so the third read is rare."""
    ledger_events = _ledger(tmp_path / "ledger")
    baseline = _views()
    source, reads = _reference(baseline)

    refusals, unproven = differential.audit_reference(source, ledger_events, baseline, VOCAB)

    assert (refusals, unproven) == ([], [])
    assert len(reads) == 1


def test_a_run_against_a_moving_tracker_is_inconclusive_rather_than_refused(
    tmp_path: Path,
) -> None:
    """End to end: such a run licenses no rung of the cutover, and slanders no reference.

    A refusal says the comparison cannot discriminate at all; this says the reference would
    not hold still to be compared, which the next run settles.
    """
    directory = tmp_path / "ledger"
    _ledger(directory)
    source, _ = _reference(_views(), _views(**{OPEN_RECORD: "in_progress"}))

    report = differential.run_differential(directory, source, VOCAB)

    assert report.refusals == []
    assert not report.conclusive
    assert differential.RULE_DERIVED_FROM_LEDGER in [item.subject for item in report.inconclusive]


# --- the edge dialect the fold reads (basicly-oii83r) -------------------------

provenance = _load(KIT_DIR / "provenance.py", "tracker_provenance")

DIALECT_PAIRS = {
    differential.provenance.DIALECT_DECLARED: (provenance.KEY_TARGET, provenance.KEY_TYPE),
    differential.provenance.DIALECT_ENGINE: (provenance.ALT_KEY_TARGET, provenance.ALT_KEY_TYPE),
}
EDGES = (("r-2", "blocks"), ("r-3", "blocks"), ("r-4", "parent-child"), ("r-5", "discovered-from"))


def _edge_events(dialect: str) -> list[Any]:
    """A created record and four edges off it, written in *dialect*'s spelling.

    Authored rather than imported, because `migrate.import_snapshot` writes only the engine
    pair - which is exactly why the declared one went unread for so long: no fixture this
    repo could produce held it.
    """
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
    """It read **zero** of four in the declared spelling, against four in the engine's.

    The record predicted one; zero is what a reader matching neither key returns, and it is
    the same total blindness `provenance.fold_edges` had from the other side before
    `basicly-svct4w` fixed it there (basicly-oii83r).
    """
    events = _edge_events(dialect)
    views = differential.views_from_events(events)
    assert len(views["r-1"].dependencies) == len(EDGES)


@pytest.mark.parametrize("dialect", sorted(DIALECT_PAIRS))
def test_the_fold_reports_which_dialect_it_read(dialect: str) -> None:
    """An empty edge set is the same answer for no edges and for none it could parse."""
    assert differential.edge_dialects(_edge_events(dialect)) == (dialect,)


def test_a_payload_in_neither_dialect_is_dropped_rather_than_invented() -> None:
    """The second acceptance criterion: unreadable is refused, never guessed into an edge."""
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
