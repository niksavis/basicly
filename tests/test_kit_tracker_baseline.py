"""Tests for the flip boundary the shadow differential is judged on (basicly-c357).

Four properties carry the bead, and the last two are the ones that keep this from being a
way to make a red run green:

* **A pre-flip divergence does not make the run unclean, a post-flip one does.** That pair
  is the bead's own acceptance, and neither half means anything without the other: a
  boundary that excused everything would report clean on a broken dual write.
* **An empty in-scope population is inconclusive.** Scoping makes the population empty
  until the flip happens, so without this the bead would hand the next rung a green light
  computed over nothing — the failure `differential.py` separates clean from conclusive to
  prevent.
* **A record is classified by the marker its own producer wrote**, never by absence.
* **A second declaration is refused**, because widening the baseline after a dual write has
  begun absorbs a real failure into history.

The events are built as plain mappings rather than through `events.py`: what a given event
shape produces is the observable behaviour, and constructing a real ledger would test the
event log instead of the boundary.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).parent.parent
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _load(path: Path, name: str) -> ModuleType:
    """Load a kit module by path, the way `owned_store.kit` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


baseline = _load(KIT / "baseline.py", "basicly_tracker_kit_baseline")


@dataclass(frozen=True)
class _Disagreement:
    """The one field the boundary reads off a `differential.Disagreement`."""

    record: str


def _created(record: str, *, imported: bool) -> dict:
    """A created event, carrying the import marker only when it was extracted."""
    payload = {baseline.IMPORT_MARKER: "beads-export"} if imported else {"title": record}
    return {"record": record, "kind": baseline.KIND_CREATED, "payload": payload}


def _report(disagreements: tuple[_Disagreement, ...] = (), unknown: tuple[str, ...] = ()):
    """The two fields of a `DifferentialReport` the boundary consumes."""
    return SimpleNamespace(disagreements=list(disagreements), unknown=list(unknown))


def test_a_divergence_on_an_imported_record_does_not_make_the_run_unclean() -> None:
    """The bead's first acceptance half: history is excused, not judged.

    The 375 live disagreements are all this shape — the owned side reads `missing` for a
    gate the export carries no field for, so the import had nothing to read. That is an
    absence of evidence rather than a dual-write disagreement, and no amount of
    re-importing would close it.
    """
    events = [_created("old", imported=True), _created("new", imported=False)]

    scoped = baseline.scope(_report((_Disagreement("old"),)), events, baseline.Baseline())

    assert scoped.clean
    assert scoped.excused == (_Disagreement("old"),)
    assert scoped.disagreements == ()


def test_a_divergence_on_a_post_flip_record_does_make_the_run_unclean() -> None:
    """The other half, without which the boundary is a way to pass a broken dual write."""
    events = [_created("old", imported=True), _created("new", imported=False)]

    scoped = baseline.scope(_report((_Disagreement("new"),)), events, baseline.Baseline())

    assert not scoped.clean
    assert scoped.disagreements == (_Disagreement("new"),)
    assert scoped.excused == ()


def test_an_empty_in_scope_population_is_inconclusive_rather_than_clean() -> None:
    """Nothing was compared, so agreement is the absence of evidence.

    This is the state the repository is actually in today — `mode = "external"`, so no
    record has ever been created natively — and reporting it as clean would license the
    flip on a comparison that discriminated nothing.
    """
    scoped = baseline.scope(_report(), [_created("old", imported=True)], baseline.Baseline())

    assert scoped.clean  # nothing in scope disagreed...
    assert not scoped.conclusive  # ...because nothing in scope was compared
    assert "0 post-flip record(s)" in scoped.summary()


def test_a_declared_record_is_history_and_an_undeclared_one_is_a_finding() -> None:
    """The `unknown` class, which no marker can classify.

    A record the reference holds and the ledger does not has no ledger event at all, so
    the baseline is the only thing that can say whether it predates the dual write.
    """
    declared = baseline.Baseline(frozenset({"gone"}), "2026-08-14")

    scoped = baseline.scope(_report(unknown=("gone", "missed")), [], declared)

    assert scoped.undeclared == ("missed",)
    assert not scoped.clean


def test_a_record_is_classified_by_the_marker_its_producer_wrote(tmp_path: Path) -> None:
    """Absence alone never classifies, so a native record is the one without the marker."""
    events = [_created("old", imported=True), _created("new", imported=False)]

    assert baseline.imported_records(events) == frozenset({"old"})
    assert baseline.read_baseline(tmp_path) == baseline.Baseline()


def test_a_second_declaration_is_refused(tmp_path: Path) -> None:
    """Widening the baseline is how a real dual-write failure would be absorbed."""
    baseline.write_baseline(tmp_path, ["a"], "2026-08-14")

    with pytest.raises(baseline.BaselineError, match="already declared"):
        baseline.write_baseline(tmp_path, ["a", "b"], "2026-08-15")

    assert baseline.read_baseline(tmp_path).records == frozenset({"a"})


def test_a_declaration_round_trips_and_an_unreadable_one_raises(tmp_path: Path) -> None:
    """An unreadable baseline must not read as "no history declared".

    Defaulting to empty would turn every historical record into a finding, which is the
    failure this module exists to remove — arriving by a different route.
    """
    written = baseline.write_baseline(tmp_path, ["b", "a"], "2026-08-14")
    assert baseline.read_baseline(tmp_path) == written
    assert json.loads((tmp_path / baseline.BASELINE_FILE).read_text(encoding="utf-8"))["records"]

    (tmp_path / baseline.BASELINE_FILE).write_text("{", encoding="utf-8")
    with pytest.raises(baseline.BaselineError, match="unreadable"):
        baseline.read_baseline(tmp_path)


def test_a_refused_reference_is_never_clean_however_the_scope_falls() -> None:
    """A refusal voids the comparison, and scoping is not a route around it.

    The boundary decides which records are judged; it says nothing about whether the
    reference was the live tracker. Dropping refusals on the way through would let a
    differential run against a derivative of the owned ledger report clean — the exact
    outcome §5.1 says a differential must not be able to produce, reached by a new path.
    """
    events = [_created("new", imported=False)]
    report = _report()
    report.refusals = ["derived-from-owned-ledger: the answers moved"]

    scoped = baseline.scope(report, events, baseline.Baseline())

    assert not scoped.clean
    assert "refused:" in scoped.summary()
