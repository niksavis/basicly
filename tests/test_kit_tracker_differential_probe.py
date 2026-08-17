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
