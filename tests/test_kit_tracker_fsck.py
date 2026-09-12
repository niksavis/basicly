from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
FSCK_SOURCE = KIT_DIR / "fsck.py"
KIT_SOURCES = tuple(sorted(KIT_DIR.glob("*.py")))


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fsck = _load(FSCK_SOURCE, "tracker_fsck")
snapshot = fsck.snapshot
events = fsck.events

RECORD_A = "basicly-aa11"
RECORD_B = "basicly-bb22"
MISSING = "basicly-zz99"

CLOCK = 1_000_000_000.0
NEXT_PERIOD = "2027"


def _append(directory: Path, drafts: list[Any]) -> list[Any]:
    return events.append(directory, drafts, actor="a-lane", clock=lambda: CLOCK)


def _seed(directory: Path) -> Path:
    _append(
        directory,
        [
            events.Draft(RECORD_A, "created", {"title": "a parent"}),
            events.Draft(RECORD_A, "status", {"status": "open"}),
            events.Draft(RECORD_A, "dispatch", {"spend_micros": 1250}),
            events.Draft(RECORD_B, "created", {"title": "a child"}),
            events.Draft(RECORD_B, "comment", {"text": "a note"}),
        ],
    )
    return directory


def _log(directory: Path) -> Path:
    return events.log_paths(directory)[-1]


def _lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8", newline="\n")


def _dumps(obj: dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _of_kind(report: Any, kind: str) -> list[Any]:
    return [found for found in report.findings if found.kind == kind]


def _kinds(report: Any) -> set[str]:
    return {found.kind for found in report.findings}


def _derived_text(directory: Path) -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8") for path in snapshot.derived_paths(directory)
    }


def test_a_healthy_ledger_is_clean_and_every_seeded_case_below_is_measured_against_it(
    tmp_path: Path,
) -> None:
    ledger = _seed(tmp_path / "ledger")
    snapshot.rebuild(ledger)

    report = fsck.check(ledger)

    assert report.findings == ()
    assert report.clean is True
    assert report.exit_code == fsck.EXIT_CLEAN
    assert report.events == 5
    assert report.records == 2


def test_a_ledger_that_does_not_exist_is_inert_rather_than_an_error(tmp_path: Path) -> None:
    report = fsck.check(tmp_path / "no-ledger-here")

    assert report.clean is True
    assert report.events == 0


def test_two_events_claiming_one_sequence_number_name_both_ids_and_fail(
    tmp_path: Path,
) -> None:

    ledger = _seed(tmp_path / "ledger")
    forked = _append(ledger, [events.Draft(RECORD_A, "comment", {"text": "the other branch"})])[0]
    lines = _lines(_log(ledger))
    collided = json.loads(lines[-1])
    collided["seq"] = 2
    _write(_log(ledger), [*lines[:-1], _dumps(collided)])

    report = fsck.check(ledger)

    found = _of_kind(report, fsck.FORKED_SEQUENCE)
    assert len(found) == 1
    assert found[0].subject == RECORD_A
    assert forked.id in found[0].event_ids
    assert len(found[0].event_ids) == 2
    assert report.exit_code == fsck.EXIT_BROKEN


def test_an_edge_whose_target_no_created_event_minted_names_the_edge_and_fails(
    tmp_path: Path,
) -> None:
    ledger = _seed(tmp_path / "ledger")
    edge = _append(
        ledger,
        [events.Draft(RECORD_A, "edge", {"from": RECORD_A, "to": MISSING, "type": "blocks"})],
    )[0]

    report = fsck.check(ledger)

    found = _of_kind(report, fsck.DANGLING_EDGE)
    assert len(found) == 1
    assert found[0].subject == MISSING
    assert found[0].event_ids == (edge.id,)
    assert report.exit_code == fsck.EXIT_BROKEN


def test_both_edge_dialects_are_checked_because_a_blind_one_would_pass_the_log(
    tmp_path: Path,
) -> None:

    ledger = _seed(tmp_path / "ledger")
    _append(
        ledger,
        [
            events.Draft(
                RECORD_A,
                "edge",
                {"target": MISSING, "edge_type": "blocks", "provenance": "EXTRACTED"},
            )
        ],
    )

    report = fsck.check(ledger)

    assert {"to", "target"} <= set(fsck.EDGE_RECORD_KEYS)
    assert [found.subject for found in _of_kind(report, fsck.DANGLING_EDGE)] == [MISSING]


def test_an_event_about_a_record_no_created_event_minted_names_it_and_fails(
    tmp_path: Path,
) -> None:
    ledger = _seed(tmp_path / "ledger")
    orphan = _append(ledger, [events.Draft(MISSING, "comment", {"text": "about nothing"})])[0]

    report = fsck.check(ledger)

    found = _of_kind(report, fsck.DANGLING_RECORD)
    assert len(found) == 1
    assert found[0].subject == MISSING
    assert found[0].event_ids == (orphan.id,)
    assert report.exit_code == fsck.EXIT_BROKEN


def test_a_tombstoned_record_still_exists_so_an_edge_into_one_is_not_dangling(
    tmp_path: Path,
) -> None:
    ledger = _seed(tmp_path / "ledger")
    _append(
        ledger,
        [
            events.Draft(RECORD_B, "tombstone", {}),
            events.Draft(RECORD_A, "edge", {"from": RECORD_A, "to": RECORD_B, "type": "blocks"}),
        ],
    )

    report = fsck.check(ledger)

    assert _of_kind(report, fsck.DANGLING_EDGE) == []
    assert report.clean is True


def test_an_interior_line_the_parser_refuses_is_named_by_file_and_line(tmp_path: Path) -> None:

    ledger = _seed(tmp_path / "ledger")
    lines = _lines(_log(ledger))
    _write(_log(ledger), [lines[0], '{"half an event"', *lines[1:]])

    report = fsck.check(ledger)

    found = _of_kind(report, fsck.UNPARSEABLE)
    assert len(found) == 1
    assert found[0].subject == f"{_log(ledger).name}:2"
    assert found[0].event_ids == ()
    assert report.exit_code == fsck.EXIT_BROKEN
    assert _log(ledger).read_text(encoding="utf-8").count('{"half an event"') == 1


def test_a_known_kind_the_fold_refuses_is_named_instead_of_crashing_the_check(
    tmp_path: Path,
) -> None:

    ledger = _seed(tmp_path / "ledger")
    payload = {"value": "no name to set it on"}
    nameless = {
        "id": events.event_id_for(RECORD_A, "field", payload),
        "record": RECORD_A,
        "seq": 9,
        "kind": "field",
        "actor": "a-lane",
        "ts": "2026-08-07T00:00:00Z",
        "payload": payload,
        "totals": {"events": 4, "attempts": 1, "spend_micros": 1250, "status": "open"},
    }
    _write(_log(ledger), [*_lines(_log(ledger)), _dumps(nameless)])

    report = fsck.check(ledger)

    found = _of_kind(report, fsck.MALFORMED)
    assert len(found) == 1
    assert found[0].event_ids == (nameless["id"],)
    assert report.exit_code == fsck.EXIT_BROKEN
    assert report.records == 2
    assert report.events == 5


def test_carried_totals_the_fold_disagrees_with_name_the_event_and_fail(
    tmp_path: Path,
) -> None:
    ledger = _seed(tmp_path / "ledger")
    lines = _lines(_log(ledger))
    edited = json.loads(lines[2])
    edited["totals"]["spend_micros"] = 99
    _write(_log(ledger), [*lines[:2], _dumps(edited), *lines[3:]])

    report = fsck.check(ledger)

    found = _of_kind(report, fsck.CARRIED_TOTALS)
    assert len(found) == 1
    assert found[0].subject == RECORD_A
    assert found[0].event_ids == (edited["id"],)
    assert report.exit_code == fsck.EXIT_BROKEN


def test_a_fork_reports_itself_and_not_the_totals_findings_it_causes(tmp_path: Path) -> None:

    ledger = _seed(tmp_path / "ledger")
    _append(ledger, [events.Draft(RECORD_A, "comment", {"text": "the other branch"})])
    lines = _lines(_log(ledger))
    collided = json.loads(lines[-1])
    collided["seq"] = 2
    _write(_log(ledger), [*lines[:-1], _dumps(collided)])

    report = fsck.check(ledger)

    assert _kinds(report) == {fsck.FORKED_SEQUENCE}


def _drop_seq(ledger: Path, record: str, seq: int) -> dict[str, Any]:

    lines = _lines(_log(ledger))
    kept, dropped = [], None
    for line in lines:
        event = json.loads(line)
        if event["record"] == record and event["seq"] == seq:
            dropped = event
        else:
            kept.append(line)
    assert dropped is not None, f"no event at {record} seq {seq} to drop"
    _write(_log(ledger), kept)
    return dropped


def test_a_missing_sequence_number_is_named_where_only_its_carried_totals_showed(
    tmp_path: Path,
) -> None:

    ledger = _seed(tmp_path / "ledger")
    _append(ledger, [events.Draft(RECORD_A, "comment", {"text": "before the hole"})])
    _append(ledger, [events.Draft(RECORD_A, "comment", {"text": "after the hole"})])
    dropped = _drop_seq(ledger, RECORD_A, 4)

    report = fsck.check(ledger)

    found = _of_kind(report, fsck.SEQUENCE_GAP)
    assert len(found) == 1
    assert found[0].subject == RECORD_A
    assert found[0].event_ids == ()
    assert "4" in found[0].detail
    assert dropped["id"] not in report.as_dict()["findings"][0]["detail"]
    assert report.exit_code == fsck.EXIT_BROKEN
    assert fsck.sequence_gaps(events.canonical_order(events.read_events(ledger)[0])) == {
        RECORD_A: (4,)
    }


def test_a_sequence_gap_reports_itself_and_not_the_carried_totals_it_causes(
    tmp_path: Path,
) -> None:

    ledger = _seed(tmp_path / "ledger")
    _append(ledger, [events.Draft(RECORD_A, "comment", {"text": "before the hole"})])
    _append(ledger, [events.Draft(RECORD_A, "comment", {"text": "after the hole"})])
    _append(ledger, [events.Draft(RECORD_A, "comment", {"text": "later still"})])
    _drop_seq(ledger, RECORD_A, 4)
    snapshot.rebuild(ledger)

    report = fsck.check(ledger)

    assert _kinds(report) == {fsck.SEQUENCE_GAP}
    assert _of_kind(report, fsck.CARRIED_TOTALS) == []
    folded = events.fold(events.read_events(ledger)[0])
    assert len(folded.mismatched_totals) == 2


def test_one_id_on_lines_that_disagree_about_their_content_fails(tmp_path: Path) -> None:
    ledger = _seed(tmp_path / "ledger")
    lines = _lines(_log(ledger))
    restamped = json.loads(lines[4])
    restamped["seq"] = 7
    _write(_log(ledger), [*lines, _dumps(restamped)])

    report = fsck.check(ledger)

    found = _of_kind(report, fsck.DUPLICATE_ID)
    assert len(found) == 1
    assert found[0].subject == restamped["id"]
    assert found[0].event_ids == (restamped["id"],)
    assert report.exit_code == fsck.EXIT_BROKEN


def test_one_id_on_identical_lines_is_the_union_merge_case_and_stays_clean(
    tmp_path: Path,
) -> None:
    ledger = _seed(tmp_path / "ledger")
    lines = _lines(_log(ledger))
    _write(_log(ledger), [*lines, lines[4]])

    report = fsck.check(ledger)

    assert _of_kind(report, fsck.DUPLICATE_ID) == []
    assert report.clean is True


def test_a_kind_the_fold_applies_no_state_for_warns_and_does_not_fail(tmp_path: Path) -> None:
    ledger = _seed(tmp_path / "ledger")
    newer = _append(ledger, [events.Draft(RECORD_A, "reviewed", {"by": "a newer writer"})])[0]

    report = fsck.check(ledger)

    found = _of_kind(report, fsck.UNFOLDED_KIND)
    assert len(found) == 1
    assert found[0].subject == "reviewed"
    assert found[0].event_ids == (newer.id,)
    assert found[0].severity == fsck.WARNING
    assert report.delegated_kinds == ()
    assert report.clean is True
    assert report.exit_code == fsck.EXIT_CLEAN


def test_a_kind_a_sibling_folds_is_a_census_line_and_not_a_warning(tmp_path: Path) -> None:

    ledger = _seed(tmp_path / "ledger")
    _append(
        ledger,
        [
            events.Draft(RECORD_A, events.KIND_EDGE, {"target": RECORD_B, "edge_type": "blocks"}),
            events.Draft(RECORD_A, events.KIND_GATE, {"gate": "verify", "passed": True}),
            events.Draft(RECORD_B, "reviewed", {"by": "a newer writer"}),
        ],
    )

    report = fsck.check(ledger)

    assert [found.subject for found in _of_kind(report, fsck.UNFOLDED_KIND)] == ["reviewed"]
    assert report.delegated_kinds == (
        (events.KIND_EDGE, 1, "provenance.fold_edges"),
        (events.KIND_GATE, 1, "gates.fold_gates"),
    )
    assert report.as_dict()["delegated_kinds"] == {
        events.KIND_EDGE: {"events": 1, "folded_by": "provenance.fold_edges"},
        events.KIND_GATE: {"events": 1, "folded_by": "gates.fold_gates"},
    }
    assert report.exit_code == fsck.EXIT_CLEAN


def test_a_stale_derivative_is_not_a_finding_because_every_reader_regenerates_it(
    tmp_path: Path,
) -> None:

    ledger = _seed(tmp_path / "ledger")
    snapshot.rebuild(ledger)
    _append(ledger, [events.Draft(RECORD_B, "status", {"status": "closed"})])

    assert snapshot.staleness(ledger).stale is True
    assert fsck.check(ledger).clean is True

    snapshot.snapshot_path(ledger).unlink()
    assert fsck.check(ledger).clean is True


def test_a_derivative_the_cheap_check_calls_fresh_is_caught_only_by_the_fold(
    tmp_path: Path,
) -> None:

    ledger = _seed(tmp_path / "ledger")
    snapshot.rebuild(ledger)
    lines = _lines(snapshot.snapshot_path(ledger))
    edited = json.loads(lines[1])
    edited["status"] = "done"
    _write(snapshot.snapshot_path(ledger), [lines[0], _dumps(edited), *lines[2:]])

    report = fsck.check(ledger)

    assert snapshot.staleness(ledger).stale is False
    found = _of_kind(report, fsck.DERIVED_DISAGREES)
    assert len(found) == 1
    assert found[0].subject == snapshot.SNAPSHOT_NAME
    assert RECORD_A in found[0].detail
    assert report.exit_code == fsck.EXIT_DERIVED


def test_a_checkpoint_that_disagrees_with_the_archive_it_summarises_is_a_finding(
    tmp_path: Path,
) -> None:

    ledger = _seed(tmp_path / "ledger")
    snapshot.rotate(ledger, NEXT_PERIOD)
    _append(ledger, [events.Draft(RECORD_B, "status", {"status": "closed"})])
    snapshot.rebuild(ledger)
    checkpoint = snapshot.latest_checkpoint(ledger)
    lines = _lines(checkpoint)
    edited = json.loads(lines[1])
    edited["max_seq"] = 99
    _write(checkpoint, [lines[0], _dumps(edited), *lines[2:]])

    report = fsck.check(ledger)

    found = _of_kind(report, fsck.DERIVED_DISAGREES)
    assert [one.subject for one in found] == [checkpoint.name]
    assert report.exit_code == fsck.EXIT_DERIVED


def test_a_derivative_that_cannot_be_read_at_all_is_a_finding(tmp_path: Path) -> None:
    ledger = _seed(tmp_path / "ledger")
    snapshot.rebuild(ledger)
    lines = _lines(snapshot.snapshot_path(ledger))
    _write(snapshot.snapshot_path(ledger), [lines[0], "not a record line"])

    report = fsck.check(ledger)

    found = _of_kind(report, fsck.DERIVED_UNREADABLE)
    assert [one.subject for one in found] == [snapshot.SNAPSHOT_NAME]
    assert report.exit_code == fsck.EXIT_DERIVED


def test_a_log_the_fold_refuses_has_no_derivative_to_be_judged_against(tmp_path: Path) -> None:

    ledger = _seed(tmp_path / "ledger")
    snapshot.rebuild(ledger)
    payload = {"value": "no name to set it on"}
    nameless = {
        "id": events.event_id_for(RECORD_A, "field", payload),
        "record": RECORD_A,
        "seq": 9,
        "kind": "field",
        "actor": "a-lane",
        "ts": "2026-08-07T00:00:00Z",
        "payload": payload,
        "totals": {"events": 4, "attempts": 1, "spend_micros": 1250, "status": "open"},
    }
    _write(_log(ledger), [*_lines(_log(ledger)), _dumps(nameless)])

    report = fsck.check(ledger)

    assert _kinds(report) == {fsck.MALFORMED}


def test_a_corrupted_snapshot_and_checkpoint_are_replaced_and_the_check_then_passes(
    tmp_path: Path,
) -> None:

    ledger = _seed(tmp_path / "ledger")
    snapshot.rotate(ledger, NEXT_PERIOD)
    _append(ledger, [events.Draft(RECORD_B, "status", {"status": "closed"})])
    snapshot.rebuild(ledger)
    for derived in snapshot.derived_paths(ledger):
        derived.write_text("this is not a derived file\n", encoding="utf-8")
    assert fsck.check(ledger).exit_code == fsck.EXIT_DERIVED

    rebuilt = fsck.rebuild(ledger)

    assert [path.name for path in rebuilt.written] == [
        snapshot.checkpoint_path(ledger, "0001").name,
        snapshot.SNAPSHOT_NAME,
    ]
    assert fsck.check(ledger).clean is True
    published = snapshot.read_snapshot(snapshot.snapshot_path(ledger))
    assert published.records == events.fold(events.read_events(ledger)[0]).records
    assert published.records[RECORD_B].status == "closed"


def test_rebuild_writes_the_set_the_log_implies_even_when_every_derivative_is_gone(
    tmp_path: Path,
) -> None:

    ledger = _seed(tmp_path / "ledger")
    snapshot.rotate(ledger, NEXT_PERIOD)
    _append(ledger, [events.Draft(RECORD_B, "status", {"status": "closed"})])
    snapshot.rebuild(ledger)
    for derived in snapshot.derived_paths(ledger):
        derived.unlink()

    rebuilt = fsck.rebuild(ledger)

    assert rebuilt.removed == ()
    assert [path.name for path in snapshot.derived_paths(ledger)] == [
        snapshot.SNAPSHOT_NAME,
        snapshot.checkpoint_path(ledger, "0001").name,
    ]
    assert fsck.check(ledger).clean is True


def test_rebuild_reproduces_what_rotation_published_byte_for_byte(tmp_path: Path) -> None:
    ledger = _seed(tmp_path / "ledger")
    snapshot.rotate(ledger, NEXT_PERIOD)
    _append(ledger, [events.Draft(RECORD_B, "status", {"status": "closed"})])
    snapshot.rebuild(ledger)
    before = _derived_text(ledger)

    fsck.rebuild(ledger)
    after = _derived_text(ledger)

    assert after == before


def test_a_derivative_the_log_no_longer_implies_is_removed_rather_than_left(
    tmp_path: Path,
) -> None:

    ledger = _seed(tmp_path / "ledger")
    snapshot.rebuild(ledger)
    orphan = snapshot.checkpoint_path(ledger, "9999")
    shutil.copy2(snapshot.snapshot_path(ledger), orphan)
    assert snapshot.latest_checkpoint(ledger) == orphan

    fsck.rebuild(ledger)

    assert snapshot.latest_checkpoint(ledger) is None
    assert [path.name for path in snapshot.derived_paths(ledger)] == [snapshot.SNAPSHOT_NAME]


def test_a_rebuild_deletes_no_log_and_loses_no_event(tmp_path: Path) -> None:

    ledger = _seed(tmp_path / "ledger")
    snapshot.rotate(ledger, NEXT_PERIOD)
    _append(ledger, [events.Draft(RECORD_A, "comment", {"text": "after the boundary"})])
    snapshot.rebuild(ledger)
    logs = {path.name: path.read_text(encoding="utf-8") for path in events.log_paths(ledger)}
    assert len(logs) > 1, "the control: a rotated ledger holds an archive beside the current file"
    assert set(snapshot.derived_paths(ledger)), "the control: there is a derived set to delete"
    before, _ = events.read_events(ledger)

    fsck.rebuild(ledger)

    after = {path.name: path.read_text(encoding="utf-8") for path in events.log_paths(ledger)}
    assert after == logs, "a rebuild removed or rewrote a log file"
    assert [event.id for event in events.canonical_order(events.read_events(ledger)[0])] == [
        event.id for event in events.canonical_order(before)
    ]


def test_two_rebuilds_of_one_log_are_byte_identical(tmp_path: Path) -> None:
    ledger = _seed(tmp_path / "ledger")
    snapshot.rotate(ledger, NEXT_PERIOD)
    _append(ledger, [events.Draft(RECORD_A, "comment", {"text": "after the boundary"})])

    fsck.rebuild(ledger)
    first = _derived_text(ledger)
    fsck.rebuild(ledger)
    second = _derived_text(ledger)

    assert second == first


def test_rebuild_refuses_a_log_the_fold_cannot_read_rather_than_writing_a_wrong_answer(
    tmp_path: Path,
) -> None:

    ledger = _seed(tmp_path / "ledger")
    payload = {"value": "no name to set it on"}
    nameless = {
        "id": events.event_id_for(RECORD_A, "field", payload),
        "record": RECORD_A,
        "seq": 9,
        "kind": "field",
        "actor": "a-lane",
        "ts": "2026-08-07T00:00:00Z",
        "payload": payload,
        "totals": {"events": 4, "attempts": 1, "spend_micros": 1250, "status": "open"},
    }
    _write(_log(ledger), [*_lines(_log(ledger)), _dumps(nameless)])

    with pytest.raises(events.InvalidEventError):
        fsck.rebuild(ledger)


GUARD_REACH = {
    ("snapshot.py", "rebuild"): "guard",
    ("snapshot.py", "refresh"): "guard",
    ("snapshot.py", "rotate"): "guard",
    ("fsck.py", "rebuild"): "call site",
}


def _calls(function: Any, name: str) -> bool:
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        called = node.func
        if getattr(called, "attr", None) == name or getattr(called, "id", None) == name:
            return True
    return False


def _functions() -> dict[tuple[str, str], Any]:

    found: dict[tuple[str, str], Any] = {}
    for source in KIT_SOURCES:
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found[(source.name, node.name)] = node
    return found


def test_a_rebuild_whose_publish_would_shrink_refuses_and_deletes_nothing(tmp_path: Path) -> None:

    ledger = _seed(tmp_path / "ledger")
    fsck.rebuild(ledger)
    published = _derived_text(ledger)
    for log in events.log_paths(ledger):
        log.unlink()

    with pytest.raises(snapshot.SnapshotError) as refusal:
        fsck.rebuild(ledger)

    assert "0 records over a file holding 2" in str(refusal.value)
    assert _derived_text(ledger) == published


def test_every_publish_site_answers_whether_the_shrink_guard_reaches_it() -> None:

    functions = _functions()
    sites = {key for key, node in functions.items() if _calls(node, "write_snapshot")}

    assert sites == set(GUARD_REACH)
    for site, answer in GUARD_REACH.items():
        assert _calls(functions[site], "shrinkage") is (answer == "call site")


def test_events_naming_no_actor_are_counted_in_both_spellings(tmp_path: Path) -> None:

    ledger = _seed(tmp_path / "ledger")
    lines = _lines(_log(ledger))
    assert len(lines) == 5
    rewritten = []
    for position, line in enumerate(lines):
        event = json.loads(line)
        if position < 2:
            event["actor"] = ""
        elif position < 4:
            event["actor"] = events.UNATTRIBUTED_ACTOR
        rewritten.append(_dumps(event))
    _write(_log(ledger), rewritten)

    report = fsck.check(ledger)
    assert report.events == 5
    assert report.unattributed == 4
    assert report.as_dict()["unattributed"] == 4
    assert report.clean, "an unattributed event is a census, never a finding to fail on"


def test_the_entry_point_exits_by_which_remedy_applies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _seed(tmp_path / "ledger")
    snapshot.rebuild(ledger)

    assert fsck.main([str(ledger)]) == fsck.EXIT_CLEAN
    assert json.loads(capsys.readouterr().out)["clean"] is True

    lines = _lines(snapshot.snapshot_path(ledger))
    edited = json.loads(lines[1])
    edited["status"] = "done"
    _write(snapshot.snapshot_path(ledger), [lines[0], _dumps(edited), *lines[2:]])
    assert fsck.main([str(ledger)]) == fsck.EXIT_DERIVED
    capsys.readouterr()

    _append(ledger, [events.Draft(MISSING, "comment", {"text": "about nothing"})])
    assert fsck.main([str(ledger)]) == fsck.EXIT_BROKEN
    report = json.loads(capsys.readouterr().out)
    assert report["broken"] == 1
    assert report["findings"][0]["kind"] == fsck.DANGLING_RECORD


def test_the_rebuild_flag_repairs_and_then_checks_what_it_produced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _seed(tmp_path / "ledger")
    snapshot.rebuild(ledger)
    snapshot.snapshot_path(ledger).write_text("not a derived file\n", encoding="utf-8")

    code = fsck.main([str(ledger), "--rebuild"])

    report = json.loads(capsys.readouterr().out)
    assert code == fsck.EXIT_CLEAN
    assert report["removed"] == [snapshot.SNAPSHOT_NAME]
    assert report["written"] == [snapshot.SNAPSHOT_NAME]
    assert report["clean"] is True


def test_the_rebuild_flag_reports_a_log_it_refuses_instead_of_raising(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _seed(tmp_path / "ledger")
    payload = {"value": "no name to set it on"}
    nameless = {
        "id": events.event_id_for(RECORD_A, "field", payload),
        "record": RECORD_A,
        "seq": 9,
        "kind": "field",
        "actor": "a-lane",
        "ts": "2026-08-07T00:00:00Z",
        "payload": payload,
        "totals": {"events": 4, "attempts": 1, "spend_micros": 1250, "status": "open"},
    }
    _write(_log(ledger), [*_lines(_log(ledger)), _dumps(nameless)])

    code = fsck.main([str(ledger), "--rebuild"])

    assert code == fsck.EXIT_BROKEN
    assert json.loads(capsys.readouterr().out)["rebuilt"] is False


def test_the_module_imports_nothing_outside_the_standard_library() -> None:

    imported: set[str] = set()
    for node in ast.walk(ast.parse(FSCK_SOURCE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not {name for name in imported if name.split(".")[0] == "basicly"}
    assert imported <= {
        "__future__",
        "argparse",
        "collections.abc",
        "dataclasses",
        "importlib.util",
        "json",
        "pathlib",
        "sys",
        "typing",
    }
    assert "sys.path.insert" not in FSCK_SOURCE.read_text(encoding="utf-8")


def _pruned_env(tmp_path: Path) -> dict[str, str]:

    empty = tmp_path / "empty-path-dir"
    empty.mkdir(exist_ok=True)
    home = tmp_path / "scratch-home"
    home.mkdir(exist_ok=True)
    env = {"PATH": str(empty), "HOME": str(home), "USERPROFILE": str(home)}
    for name in ("SystemRoot", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP"):
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    return env


def test_the_ledger_is_checked_and_repaired_in_a_process_with_no_basicly(
    tmp_path: Path,
) -> None:

    ledger = tmp_path / "their-ledger"
    _seed(ledger)
    snapshot.rebuild(ledger)
    snapshot.snapshot_path(ledger).write_text("not a derived file\n", encoding="utf-8")
    consumer = tmp_path / "consumer" / "kit" / "tracker"
    consumer.mkdir(parents=True)
    for source in KIT_SOURCES:
        shutil.copy2(source, consumer / source.name)

    broken = subprocess.run(
        [sys.executable, "-S", "-I", str(consumer / "fsck.py"), str(ledger)],
        cwd=tmp_path,
        env=_pruned_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    repaired = subprocess.run(
        [sys.executable, "-S", "-I", str(consumer / "fsck.py"), str(ledger), "--rebuild"],
        cwd=tmp_path,
        env=_pruned_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert broken.returncode == fsck.EXIT_DERIVED, broken.stderr
    assert json.loads(broken.stdout)["findings"][0]["kind"] == fsck.DERIVED_UNREADABLE
    assert repaired.returncode == fsck.EXIT_CLEAN, repaired.stderr
    assert json.loads(repaired.stdout)["clean"] is True
    assert snapshot.read_snapshot(snapshot.snapshot_path(ledger)).records[RECORD_A].status == "open"


def test_the_printed_id_list_is_bounded_and_says_how_many_it_dropped(tmp_path: Path) -> None:

    ledger = _seed(tmp_path / "ledger")
    carried = fsck.MAX_EVENT_IDS_REPORTED + 3
    _append(
        ledger,
        [events.Draft(RECORD_A, "reviewed", {"pass": index}) for index in range(carried)],
    )

    report = fsck.check(ledger)

    found = _of_kind(report, fsck.UNFOLDED_KIND)[0]
    assert len(found.event_ids) == carried
    printed = found.as_dict()
    assert len(printed["event_ids"]) == fsck.MAX_EVENT_IDS_REPORTED
    assert printed["event_ids_omitted"] == 3
    assert printed["event_ids"] == list(found.event_ids[: fsck.MAX_EVENT_IDS_REPORTED])
