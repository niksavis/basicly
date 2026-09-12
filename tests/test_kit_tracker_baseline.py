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
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


baseline = _load(KIT / "baseline.py", "basicly_tracker_kit_baseline")


@dataclass(frozen=True)
class _Disagreement:
    record: str


def _created(record: str, *, imported: bool, source: str = "beads-export") -> dict:
    payload = {baseline.IMPORT_MARKER: source} if imported else {"title": record}
    return {"record": record, "kind": baseline.KIND_CREATED, "payload": payload}


def _adopted(record: str) -> dict:
    return _created(record, imported=True, source=baseline.ADOPTION_SOURCE)


def _report(disagreements: tuple[_Disagreement, ...] = (), unknown: tuple[str, ...] = ()):
    return SimpleNamespace(disagreements=list(disagreements), unknown=list(unknown))


def test_a_divergence_on_an_imported_record_does_not_make_the_run_unclean() -> None:

    events = [_created("old", imported=True), _created("new", imported=False)]

    scoped = baseline.scope(_report((_Disagreement("old"),)), events, baseline.Baseline())

    assert scoped.clean
    assert scoped.excused == (_Disagreement("old"),)
    assert scoped.disagreements == ()


def test_a_divergence_on_a_post_flip_record_does_make_the_run_unclean() -> None:
    events = [_created("old", imported=True), _created("new", imported=False)]

    scoped = baseline.scope(_report((_Disagreement("new"),)), events, baseline.Baseline())

    assert not scoped.clean
    assert scoped.disagreements == (_Disagreement("new"),)
    assert scoped.excused == ()


def test_an_empty_in_scope_population_is_inconclusive_rather_than_clean() -> None:

    scoped = baseline.scope(_report(), [_created("old", imported=True)], baseline.Baseline())

    assert scoped.clean
    assert not scoped.conclusive
    assert "0 post-flip record(s)" in scoped.summary()


def test_a_declared_record_is_history_and_an_undeclared_one_is_a_finding() -> None:

    declared = baseline.Baseline(frozenset({"gone"}), "2026-08-14")

    scoped = baseline.scope(_report(unknown=("gone", "missed")), [], declared)

    assert scoped.undeclared == ("missed",)
    assert not scoped.clean


def test_a_record_is_classified_by_the_marker_its_producer_wrote(tmp_path: Path) -> None:
    events = [_created("old", imported=True), _created("new", imported=False)]

    assert baseline.imported_records(events) == frozenset({"old"})
    assert baseline.read_baseline(tmp_path) == baseline.Baseline()


def test_an_adopted_hand_write_is_judged_but_never_counted_as_evidence() -> None:

    events = [_created("old", imported=True), _adopted("byhand"), _created("new", imported=False)]

    scoped = baseline.scope(_report((_Disagreement("byhand"),)), events, baseline.Baseline())

    assert baseline.adopted_records(events) == frozenset({"byhand"})
    assert baseline.imported_records(events) == frozenset({"old"})
    assert scoped.adopted == ("byhand",)
    assert set(scoped.in_scope) == {"byhand", "new"}
    assert scoped.disagreements == (_Disagreement("byhand"),)
    assert not scoped.clean


def test_a_scope_of_nothing_but_adopted_records_is_inconclusive() -> None:

    scoped = baseline.scope(_report(), [_adopted("byhand")], baseline.Baseline())

    assert scoped.in_scope == ("byhand",)
    assert scoped.clean
    assert not scoped.conclusive
    assert "0 post-flip record(s)" in scoped.summary()


def test_a_second_declaration_is_refused(tmp_path: Path) -> None:
    baseline.write_baseline(tmp_path, ["a"], "2026-08-14")

    with pytest.raises(baseline.BaselineError, match="already declared"):
        baseline.write_baseline(tmp_path, ["a", "b"], "2026-08-15")

    assert baseline.read_baseline(tmp_path).records == frozenset({"a"})


def test_a_declaration_round_trips_and_an_unreadable_one_raises(tmp_path: Path) -> None:

    written = baseline.write_baseline(tmp_path, ["b", "a"], "2026-08-14")
    assert baseline.read_baseline(tmp_path) == written
    assert json.loads((tmp_path / baseline.BASELINE_FILE).read_text(encoding="utf-8"))["records"]

    (tmp_path / baseline.BASELINE_FILE).write_text("{", encoding="utf-8")
    with pytest.raises(baseline.BaselineError, match="unreadable"):
        baseline.read_baseline(tmp_path)


def test_a_refused_reference_is_never_clean_however_the_scope_falls() -> None:

    events = [_created("new", imported=False)]
    report = _report()
    report.refusals = ["derived-from-owned-ledger: the answers moved"]

    scoped = baseline.scope(report, events, baseline.Baseline())

    assert not scoped.clean
    assert "refused:" in scoped.summary()
