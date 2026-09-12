from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
SNAPSHOT_SOURCE = KIT_DIR / "snapshot.py"
EVENTS_SOURCE = KIT_DIR / "events.py"
IDS_SOURCE = KIT_DIR / "ids.py"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


snapshot = _load(SNAPSHOT_SOURCE, "tracker_snapshot")
events = snapshot.events

RECORD_A = "basicly-aa11"
RECORD_B = "basicly-bb22"
RECORD_C = "basicly-cc33.4"

CLOCK = 1_000_000_000.0
NEXT_PERIOD = "2027"


def _lifecycle() -> list[Any]:
    return [
        events.Draft(RECORD_A, "created", {"title": "a parent"}),
        events.Draft(RECORD_A, "status", {"status": "open"}),
        events.Draft(RECORD_A, "checkpoint", {"checkpoint": "classify", "approved_by": "owner"}),
        events.Draft(RECORD_A, "artifact", {"artifact": "plan", "body": {"units": 2}}),
        events.Draft(RECORD_B, "created", {"title": "a sibling"}),
        events.Draft(RECORD_B, "dispatch", {"spend_micros": 1250}),
        events.Draft(RECORD_B, "comment", {"text": "a note"}),
        events.Draft(RECORD_C, "created", {"title": "a child"}),
        events.Draft(RECORD_C, "tombstone", {}),
    ]


def _build(directory: Path, drafts: list[Any] | None = None) -> list[Any]:
    return events.append(
        directory, _lifecycle() if drafts is None else drafts, actor="a-lane", clock=lambda: CLOCK
    )


def _lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _code_string_literals(source: str) -> list[str]:

    tree = ast.parse(source)
    documented = {
        ast.get_docstring(node, clean=False)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in documented
    ]


def _log_tip_id(directory: Path) -> str:
    logs = events.log_paths(directory)
    return json.loads(_lines(logs[-1])[-1])["id"]


def test_the_first_line_carries_the_last_folded_events_id_and_the_event_count(
    tmp_path: Path,
) -> None:
    minted = _build(tmp_path)

    snapshot.rebuild(tmp_path)

    first = json.loads(_lines(snapshot.snapshot_path(tmp_path))[0])
    assert first["last_event_id"] == _log_tip_id(tmp_path)
    assert first["event_count"] == len(minted)
    assert first["log_lines"] == len(minted)
    assert first["version"] == snapshot.SNAPSHOT_VERSION


def test_staleness_is_answered_from_the_header_alone_without_folding_the_records(
    tmp_path: Path,
) -> None:

    _build(tmp_path)
    path = snapshot.rebuild(tmp_path) and snapshot.snapshot_path(tmp_path)
    header = _lines(path)[0]
    path.write_text(f"{header}\nnot json at all\n{{]\n", encoding="utf-8")

    assert snapshot.staleness(tmp_path).stale is False

    _build(tmp_path, [events.Draft(RECORD_A, "comment", {"text": "later"})])

    assert snapshot.staleness(tmp_path).stale is True


def test_every_append_after_a_snapshot_makes_it_stale_and_says_why(tmp_path: Path) -> None:

    _build(tmp_path)
    for index in range(3):
        snapshot.rebuild(tmp_path)
        assert snapshot.staleness(tmp_path).stale is False

        _build(tmp_path, [events.Draft(RECORD_A, "comment", {"text": f"note {index}"})])
        state = snapshot.staleness(tmp_path)

        assert state.stale is True
        assert state.reason is not None
        assert "lines" in state.reason


def test_a_merge_that_appended_to_an_archive_is_detected_too(tmp_path: Path) -> None:

    _build(tmp_path)
    archive = events.log_paths(tmp_path)[0]
    snapshot.rotate(tmp_path, NEXT_PERIOD)
    _build(tmp_path, [events.Draft(RECORD_B, "comment", {"text": "in the new file"})])
    snapshot.rebuild(tmp_path)
    assert snapshot.staleness(tmp_path).stale is False

    with archive.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(
            json.dumps({
                "id": "basicly-aa11#ev-fromtheotherside",
                "record": RECORD_A,
                "seq": 9,
                "kind": "comment",
                "actor": "another-branch",
                "ts": "2026-01-01T00:00:00Z",
                "payload": {"text": "merged in"},
                "totals": {"events": 9, "attempts": 0, "spend_micros": 0, "status": "open"},
            })
            + "\n"
        )

    assert snapshot.staleness(tmp_path).stale is True

    grown = snapshot.fold_resumed(tmp_path)

    assert grown.resumed_from is None
    assert [path.name for path in grown.logs] == [events.INITIAL_LOG_NAME, "events-2027.jsonl"]
    assert "merged in" in snapshot.load(tmp_path).records[RECORD_A].comments
    assert snapshot.staleness(tmp_path).stale is False


def test_a_fresh_read_returns_the_file_and_a_stale_read_refolds_the_log(tmp_path: Path) -> None:

    _build(tmp_path)
    snapshot.rebuild(tmp_path)
    path = snapshot.snapshot_path(tmp_path)
    lines = _lines(path)
    edited = [
        line.replace('"a note"', '"only in the snapshot"') if RECORD_B in line else line
        for line in lines
    ]
    path.write_text("\n".join(edited) + "\n", encoding="utf-8")

    assert snapshot.load(tmp_path).records[RECORD_B].comments == ["only in the snapshot"]

    _build(tmp_path, [events.Draft(RECORD_B, "comment", {"text": "appended"})])

    assert snapshot.load(tmp_path).records[RECORD_B].comments == ["a note", "appended"]
    assert snapshot.staleness(tmp_path).stale is False


def test_a_snapshot_from_a_newer_format_version_is_refused_and_rebuilt(tmp_path: Path) -> None:

    _build(tmp_path)
    snapshot.rebuild(tmp_path)
    path = snapshot.snapshot_path(tmp_path)
    lines = _lines(path)
    header = json.loads(lines[0])
    header["version"] = snapshot.SNAPSHOT_VERSION + 1
    path.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n", encoding="utf-8")

    state = snapshot.staleness(tmp_path)

    assert state.stale is True
    assert state.reason is not None
    assert "newer" in state.reason
    with pytest.raises(snapshot.SnapshotError, match="newer"):
        snapshot.read_header(path)

    assert snapshot.load(tmp_path).header.version == snapshot.SNAPSHOT_VERSION


def test_a_first_line_with_no_format_version_is_not_taken_for_this_one(tmp_path: Path) -> None:
    _build(tmp_path)
    path = snapshot.rebuild(tmp_path) and snapshot.snapshot_path(tmp_path)
    header = json.loads(_lines(path)[0])
    del header["version"]
    path.write_text(json.dumps(header) + "\n", encoding="utf-8")

    with pytest.raises(snapshot.SnapshotError, match="no snapshot format version"):
        snapshot.read_header(path)

    assert snapshot.staleness(tmp_path).stale is True
    assert snapshot.load(tmp_path).records[RECORD_A].status == "open"


def test_a_corrupt_snapshot_is_replaced_from_the_log_rather_than_repaired(tmp_path: Path) -> None:
    _build(tmp_path)
    path = snapshot.rebuild(tmp_path) and snapshot.snapshot_path(tmp_path)
    path.write_text("this is not a snapshot\n", encoding="utf-8")

    loaded = snapshot.load(tmp_path)

    assert loaded.records[RECORD_A].status == "open"
    assert snapshot.read_snapshot(path) is not None
    assert snapshot.staleness(tmp_path).stale is False


def test_two_rebuilds_of_one_log_are_byte_identical(tmp_path: Path) -> None:
    _build(tmp_path)

    snapshot.rebuild(tmp_path)
    first = snapshot.snapshot_path(tmp_path).read_bytes()
    snapshot.rebuild(tmp_path)
    second = snapshot.snapshot_path(tmp_path).read_bytes()

    assert first == second


def test_every_folded_field_survives_the_snapshot_round_trip(tmp_path: Path) -> None:
    _build(tmp_path)
    folded = events.fold(events.read_events(tmp_path)[0])

    snapshot.rebuild(tmp_path)
    loaded = snapshot.load(tmp_path)

    assert loaded.records == folded.records
    assert loaded.records[RECORD_C].tombstoned is True
    assert loaded.records[RECORD_B].totals.spend_micros == 1250
    assert loaded.records[RECORD_B].max_seq == 3
    assert loaded.records[RECORD_A].checkpoints == {"classify": "owner"}
    assert loaded.records[RECORD_A].artifacts == {"plan": {"units": 2}}


def test_a_torn_trailing_line_never_makes_the_snapshot_permanently_stale(tmp_path: Path) -> None:

    _build(tmp_path)
    snapshot.rebuild(tmp_path)
    log = events.log_paths(tmp_path)[-1]
    with log.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write('{"id": "basicly-aa11#ev-tor')

    assert snapshot.staleness(tmp_path).stale is False


def test_interior_garbage_is_counted_once_and_then_the_snapshot_settles(tmp_path: Path) -> None:
    _build(tmp_path)
    snapshot.rebuild(tmp_path)
    log = events.log_paths(tmp_path)[-1]
    with log.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write("interior garbage\n")

    assert snapshot.staleness(tmp_path).stale is True
    snapshot.rebuild(tmp_path)

    assert snapshot.staleness(tmp_path).stale is False
    assert len(snapshot.fold_all(tmp_path).quarantined) == 1


def test_publication_is_atomic_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    _build(tmp_path)

    snapshot.rebuild(tmp_path)
    snapshot.rebuild(tmp_path)

    assert list(tmp_path.glob("*.tmp")) == []
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        events.INITIAL_LOG_NAME,
        snapshot.SNAPSHOT_NAME,
    ]


def test_every_line_is_utf8_with_a_unix_ending_whatever_the_host_prefers(tmp_path: Path) -> None:
    _build(tmp_path, [events.Draft(RECORD_A, "comment", {"text": "naïve — em dash"})])

    snapshot.rebuild(tmp_path)
    raw = snapshot.snapshot_path(tmp_path).read_bytes()

    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    assert "naïve — em dash" in raw.decode("utf-8")


def test_the_hook_entry_point_regenerates_a_stale_snapshot_and_is_quiet_when_fresh(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _build(tmp_path)

    assert snapshot.main([str(tmp_path)]) == 0
    first = json.loads(capsys.readouterr().out)

    assert first["written"] is True
    assert first["stale"] is True
    assert first["records"] == 3
    assert snapshot.staleness(tmp_path).stale is False

    assert snapshot.main([str(tmp_path)]) == 0
    second = json.loads(capsys.readouterr().out)

    assert second["written"] is False
    assert second["stale"] is False


def test_check_mode_reports_a_stale_snapshot_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _build(tmp_path)

    assert snapshot.main([str(tmp_path), "--check"]) == 1
    report = json.loads(capsys.readouterr().out)

    assert report["stale"] is True
    assert report["written"] is False
    assert not snapshot.snapshot_path(tmp_path).exists()

    snapshot.rebuild(tmp_path)
    capsys.readouterr()

    assert snapshot.main([str(tmp_path), "--check"]) == 0


def test_the_full_flag_folds_the_whole_history_and_publishes_the_same_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _build(tmp_path)
    snapshot.rotate(tmp_path, NEXT_PERIOD)
    _build(tmp_path, [events.Draft(RECORD_B, "comment", {"text": "after the boundary"})])

    assert snapshot.main([str(tmp_path)]) == 0
    resumed = snapshot.snapshot_path(tmp_path).read_bytes()
    assert snapshot.main([str(tmp_path), "--full"]) == 0
    full = snapshot.snapshot_path(tmp_path).read_bytes()

    assert json.loads(capsys.readouterr().out.splitlines()[-1])["written"] is True
    assert resumed == full


def test_the_hook_is_inert_in_a_repository_with_no_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "no-tracker-here"

    assert snapshot.main([str(missing)]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["ledger"] is False
    assert report["written"] is False
    assert not missing.exists()


def test_an_unusable_header_is_reported_as_stale_rather_than_as_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    _build(tmp_path)
    snapshot.rebuild(tmp_path)
    path = snapshot.snapshot_path(tmp_path)
    lines = _lines(path)
    header = json.loads(lines[0])
    header["log_lines"] = -1
    path.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n", encoding="utf-8")

    assert snapshot.main([str(tmp_path), "--check"]) == 1
    report = json.loads(capsys.readouterr().out)

    assert report["reason"] is not None
    assert "unusable" in report["reason"]
    assert snapshot.main([str(tmp_path)]) == 0
    assert snapshot.staleness(tmp_path).stale is False


def test_the_log_glob_is_the_contract_and_the_rotation_names_derive_from_it() -> None:

    assert events.LOG_GLOB == "events-*.jsonl"
    assert snapshot.log_path(Path("ledger"), NEXT_PERIOD).name == "events-2027.jsonl"
    assert snapshot.checkpoint_path(Path("ledger"), NEXT_PERIOD).name == "checkpoint-2027.jsonl"
    assert snapshot.SNAPSHOT_NAME == "snapshot.jsonl"
    assert snapshot.period_of(Path("ledger/events-2027.jsonl")) == NEXT_PERIOD
    assert snapshot.period_of(Path("ledger/checkpoint-0001.jsonl")) == "0001"
    literals = _code_string_literals(SNAPSHOT_SOURCE.read_text(encoding="utf-8"))
    assert not [text for text in literals if "events-" in text]
    assert not [text for text in literals if ".jsonl" in text]


def test_a_rebuild_folds_a_rotated_archive_and_fails_if_the_glob_narrows(tmp_path: Path) -> None:
    _build(tmp_path)
    snapshot.rotate(tmp_path, NEXT_PERIOD)
    _build(tmp_path, [events.Draft(RECORD_B, "comment", {"text": "after the boundary"})])

    published = snapshot.rebuild(tmp_path)

    assert published.records[RECORD_A].status == "open"
    assert published.records[RECORD_A].fields["title"] == "a parent"
    assert published.records[RECORD_B].comments == ["a note", "after the boundary"]
    assert published.header.event_count == len(_lifecycle()) + 1
    assert [path.name for path in snapshot.fold_all(tmp_path).logs] == [
        events.INITIAL_LOG_NAME,
        "events-2027.jsonl",
    ]


def test_rotation_archives_every_earlier_file_byte_for_byte_and_prunes_nothing(
    tmp_path: Path,
) -> None:
    _build(tmp_path)
    before = {path.name: path.read_bytes() for path in events.log_paths(tmp_path)}

    rotation = snapshot.rotate(tmp_path, NEXT_PERIOD)

    assert [path.name for path in rotation.archived] == sorted(before)
    assert {path.name: path.read_bytes() for path in events.log_paths(tmp_path)} == {
        **before,
        "events-2027.jsonl": b"",
    }
    assert rotation.log.name == "events-2027.jsonl"
    assert rotation.checkpoint is not None
    assert rotation.checkpoint.name == "checkpoint-0001.jsonl"


def test_rotation_switches_the_append_target_and_the_sequence_continues(tmp_path: Path) -> None:
    _build(tmp_path)

    rotation = snapshot.rotate(tmp_path, NEXT_PERIOD)
    minted = _build(tmp_path, [events.Draft(RECORD_B, "comment", {"text": "next period"})])

    assert events.append_target(tmp_path) == rotation.log
    assert minted[0].seq == 4
    assert _lines(rotation.log) == [events.to_json(minted[0])]


def test_the_boundary_checkpoint_carries_every_items_totals_including_an_idle_one(
    tmp_path: Path,
) -> None:
    _build(tmp_path)
    at_boundary = events.fold(events.read_events(tmp_path)[0])

    rotation = snapshot.rotate(tmp_path, NEXT_PERIOD)
    _build(tmp_path, [events.Draft(RECORD_B, "comment", {"text": "only b moves"})])

    assert rotation.checkpoint is not None
    checkpoint = snapshot.read_snapshot(rotation.checkpoint)
    assert checkpoint is not None
    assert checkpoint.records == at_boundary.records
    assert checkpoint.header.event_count == len(_lifecycle())
    assert checkpoint.records[RECORD_C].totals.events == 2


def test_steady_state_folds_the_checkpoint_and_the_current_file_only(tmp_path: Path) -> None:

    _build(tmp_path)
    snapshot.rotate(tmp_path, NEXT_PERIOD)
    _build(tmp_path, [events.Draft(RECORD_B, "comment", {"text": "only b moves"})])
    resumed = snapshot.fold_resumed(tmp_path)

    assert [path.name for path in resumed.logs] == ["events-2027.jsonl"]
    assert resumed.resumed_from is not None
    assert resumed.resumed_from.name == "checkpoint-0001.jsonl"

    archive = tmp_path / events.INITIAL_LOG_NAME
    archive.write_text("not an event\n" * len(_lifecycle()), encoding="utf-8")

    unreadable = snapshot.fold_resumed(tmp_path)

    assert unreadable.resumed_from == resumed.resumed_from
    assert unreadable.result.records[RECORD_A].status == "open"
    assert unreadable.result.records[RECORD_B].comments == ["a note", "only b moves"]
    assert unreadable.quarantined == []
    assert RECORD_A not in snapshot.fold_all(tmp_path).result.records
    assert len(snapshot.fold_all(tmp_path).quarantined) == len(_lifecycle())


def test_the_resumed_fold_and_the_full_history_fold_agree(tmp_path: Path) -> None:
    _build(tmp_path)
    snapshot.rotate(tmp_path, "2028")
    _build(tmp_path, [events.Draft(RECORD_A, "status", {"status": "in_progress"})])
    snapshot.rotate(tmp_path, "2029")
    _build(tmp_path, [events.Draft(RECORD_B, "dispatch", {"spend_micros": 40})])

    resumed = snapshot.fold_resumed(tmp_path)
    full = snapshot.fold_all(tmp_path)

    assert resumed.result.records == full.result.records
    assert resumed.event_count == full.event_count
    assert [path.name for path in resumed.logs] == ["events-2029.jsonl"]
    snapshot.refresh(tmp_path)
    from_resumed = snapshot.snapshot_path(tmp_path).read_bytes()
    snapshot.rebuild(tmp_path)
    assert snapshot.snapshot_path(tmp_path).read_bytes() == from_resumed


def test_a_corrupt_checkpoint_costs_the_shortcut_and_not_correctness(tmp_path: Path) -> None:
    _build(tmp_path)
    rotation = snapshot.rotate(tmp_path, NEXT_PERIOD)
    _build(tmp_path, [events.Draft(RECORD_B, "comment", {"text": "after the boundary"})])
    assert rotation.checkpoint is not None
    rotation.checkpoint.write_text("not a checkpoint\n", encoding="utf-8")

    with pytest.raises(snapshot.SnapshotError):
        snapshot.fold_resumed(tmp_path)

    snapshot.refresh(tmp_path)
    from_fallback = snapshot.snapshot_path(tmp_path).read_bytes()
    snapshot.rebuild(tmp_path)

    assert snapshot.snapshot_path(tmp_path).read_bytes() == from_fallback
    assert snapshot.load(tmp_path).records[RECORD_A].status == "open"


def test_a_period_that_would_not_sort_after_the_current_file_is_refused(tmp_path: Path) -> None:
    _build(tmp_path)
    snapshot.rotate(tmp_path, NEXT_PERIOD)

    with pytest.raises(snapshot.SnapshotError, match="does not sort after"):
        snapshot.rotate(tmp_path, "2026")

    assert not snapshot.log_path(tmp_path, "2026").exists()
    assert [path.name for path in snapshot.checkpoint_paths(tmp_path)] == ["checkpoint-0001.jsonl"]


def test_a_malformed_period_or_an_existing_file_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:
    _build(tmp_path)

    with pytest.raises(snapshot.SnapshotError, match="must match"):
        snapshot.rotate(tmp_path, "2027-q1")
    with pytest.raises(snapshot.SnapshotError, match="must match"):
        snapshot.rotate(tmp_path, "../escape")

    snapshot.rotate(tmp_path, NEXT_PERIOD)
    with pytest.raises(snapshot.SnapshotError, match="already exists"):
        snapshot.rotate(tmp_path, NEXT_PERIOD)

    assert [path.name for path in events.log_paths(tmp_path)] == [
        events.INITIAL_LOG_NAME,
        "events-2027.jsonl",
    ]


def test_rotating_a_ledger_with_no_history_writes_no_checkpoint(tmp_path: Path) -> None:
    rotation = snapshot.rotate(tmp_path / "fresh", "2026")

    assert rotation.checkpoint is None
    assert rotation.archived == []
    assert rotation.log.read_bytes() == b""


def test_rotation_reports_contention_rather_than_writing_under_another_writer(
    tmp_path: Path,
) -> None:

    _build(tmp_path)
    holder = events.LedgerLock(tmp_path, pid=os.getpid())
    holder.acquire()
    try:
        with pytest.raises(events.LockUnavailableError):
            snapshot.rotate(tmp_path, NEXT_PERIOD, lock_timeout_s=0.0)
    finally:
        holder.release()

    assert not snapshot.log_path(tmp_path, NEXT_PERIOD).exists()
    assert snapshot.checkpoint_paths(tmp_path) == []

    rotation = snapshot.rotate(tmp_path, NEXT_PERIOD, held_lock=holder.acquire())
    holder.release()

    assert rotation.log.exists()


def test_derived_paths_names_every_derivative_and_never_a_log(tmp_path: Path) -> None:
    _build(tmp_path)
    snapshot.rotate(tmp_path, NEXT_PERIOD)
    snapshot.rebuild(tmp_path)

    derived = [path.name for path in snapshot.derived_paths(tmp_path)]

    assert sorted(derived) == ["checkpoint-0001.jsonl", "snapshot.jsonl"]
    assert not [name for name in derived if name.startswith("events-")]


def test_the_ignore_patterns_can_never_match_a_log_name() -> None:

    assert snapshot.DERIVED_PATTERNS == ("snapshot.jsonl", "checkpoint-*.jsonl")
    for pattern in snapshot.DERIVED_PATTERNS:
        assert not fnmatch(events.INITIAL_LOG_NAME, pattern)
        assert not fnmatch("events-2027.jsonl", pattern)
        assert not fnmatch(".events.lock", pattern)


def test_a_seeded_fold_copies_the_checkpoint_it_resumes_from(tmp_path: Path) -> None:

    _build(tmp_path)
    rotation = snapshot.rotate(tmp_path, NEXT_PERIOD)
    _build(tmp_path, [events.Draft(RECORD_B, "comment", {"text": "after the boundary"})])
    assert rotation.checkpoint is not None
    base = snapshot.read_snapshot(rotation.checkpoint)
    assert base is not None
    tail, _ = events.read_log(events.log_paths(tmp_path)[-1])

    first = events.fold(tail, seed=base.records)
    second = events.fold(tail, seed=base.records)

    assert base.records[RECORD_B].comments == ["a note"]
    assert base.records[RECORD_B].totals.events == 3
    assert first.records == second.records
    assert first.records[RECORD_B] is not base.records[RECORD_B]


def test_a_resumed_fold_carries_the_typed_machine_state_across_the_boundary(
    tmp_path: Path,
) -> None:

    _build(tmp_path)
    rotation = snapshot.rotate(tmp_path, NEXT_PERIOD)
    _build(tmp_path, [events.Draft(RECORD_B, "note", {"text": "after the boundary"})])
    assert rotation.checkpoint is not None

    resumed = snapshot.fold_resumed(tmp_path)

    assert resumed.resumed_from == rotation.checkpoint
    assert resumed.result.records[RECORD_A].checkpoints == {"classify": "owner"}
    assert resumed.result.records[RECORD_A].artifacts == {"plan": {"units": 2}}
    assert resumed.result.records == snapshot.fold_all(tmp_path).result.records


def test_a_snapshot_written_before_the_typed_kinds_reads_back_with_neither() -> None:

    older = {
        "record": RECORD_A,
        "status": "open",
        "fields": {"title": "a parent"},
        "comments": ["a note"],
        "tombstoned": False,
        "totals": {"events": 2, "attempts": 0, "spend_micros": 0, "status": "open"},
        "max_seq": 2,
    }

    state = snapshot.record_from_dict(older)

    assert (state.checkpoints, state.artifacts) == ({}, {})
    assert state.comments == ["a note"]


def test_a_folded_records_typed_state_is_refused_when_it_cannot_be_read(tmp_path: Path) -> None:
    _build(tmp_path)
    snapshot.rebuild(tmp_path)
    line = _lines(snapshot.snapshot_path(tmp_path))[1]
    broken = json.loads(line)
    broken["checkpoints"] = {"classify": True}

    with pytest.raises(snapshot.SnapshotError, match="name to an approver"):
        snapshot.record_from_dict(broken)
    with pytest.raises(snapshot.SnapshotError, match="artifacts must be an object"):
        snapshot.record_from_dict({**json.loads(line), "artifacts": ["plan"]})


def _fewer(published: Any, keep: int) -> Any:
    return snapshot.Snapshot(
        header=published.header, records=dict(sorted(published.records.items())[:keep])
    )


def test_a_shrinking_publish_is_refused_and_reports_both_counts(tmp_path: Path) -> None:
    _build(tmp_path)
    published = snapshot.rebuild(tmp_path)
    path = snapshot.snapshot_path(tmp_path)
    before = path.read_bytes()

    with pytest.raises(snapshot.SnapshotError) as raised:
        snapshot.write_snapshot(path, _fewer(published, 1))

    assert "1 records" in str(raised.value) and "holding 3" in str(raised.value)
    loss = snapshot.shrinkage(path, _fewer(published, 1))
    assert (loss.refused, loss.existing, loss.proposed) == (True, 3, 1)
    assert path.read_bytes() == before


def test_a_shrink_the_caller_declares_intentional_is_published(tmp_path: Path) -> None:
    _build(tmp_path)
    published = snapshot.rebuild(tmp_path)
    path = snapshot.snapshot_path(tmp_path)

    snapshot.write_snapshot(path, _fewer(published, 1), allow_shrink=True)

    assert len(_lines(path)) == 2


@pytest.mark.parametrize("mtime", [0.0, CLOCK * 2])
def test_the_shrink_comparison_reads_records_and_never_a_timestamp(
    tmp_path: Path, mtime: float
) -> None:

    _build(tmp_path)
    published = snapshot.rebuild(tmp_path)
    path = snapshot.snapshot_path(tmp_path)
    lines = _lines(path)
    path.write_text(
        "\n".join([json.dumps({**json.loads(lines[0]), "event_count": 0}), *lines[1:]]) + "\n",
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))

    loss = snapshot.shrinkage(path, _fewer(published, 2))

    assert (loss.refused, loss.existing, loss.proposed) == (True, 3, 2)
    assert snapshot.shrinkage(path, published).refused is False


def test_a_shrink_check_with_no_usable_file_publishes_and_says_which(tmp_path: Path) -> None:
    _build(tmp_path)
    smaller = _fewer(snapshot.rebuild(tmp_path), 1)
    path = snapshot.snapshot_path(tmp_path)

    path.unlink()
    absent = snapshot.shrinkage(path, smaller)
    snapshot.write_snapshot(path, smaller)
    path.write_text("this is not a snapshot\n", encoding="utf-8")
    unparseable = snapshot.shrinkage(path, smaller)

    assert (absent.refused, absent.existing) == (False, None)
    assert "no file was there" in str(absent.reason)
    assert (unparseable.refused, unparseable.existing) == (False, None)
    assert "unparseable" in str(unparseable.reason)
    assert len(snapshot.write_snapshot(path, smaller).records) == 1


def test_a_rebuild_whose_log_vanished_shrinks_and_is_refused_rather_than_reported_clean(
    tmp_path: Path,
) -> None:
    _build(tmp_path)
    snapshot.rebuild(tmp_path)
    for log in events.log_paths(tmp_path):
        log.unlink()

    with pytest.raises(snapshot.SnapshotError):
        snapshot.rebuild(tmp_path)

    assert len(_lines(snapshot.snapshot_path(tmp_path))) == 4


def test_the_module_imports_nothing_outside_the_standard_library() -> None:

    source = SNAPSHOT_SOURCE.read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
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
        "fnmatch",
        "importlib.util",
        "json",
        "os",
        "pathlib",
        "re",
        "sys",
        "typing",
    }
    assert "sys.path.insert" not in source
    assert ".rename(" not in source
    assert ".replace(file_path)" in source


_DRIVER = """
import importlib.util
import json
import shutil
import sys
from pathlib import Path

assert importlib.util.find_spec("basicly") is None, "basicly is importable"
assert shutil.which("basicly") is None, "basicly is on PATH"

spec = importlib.util.spec_from_file_location("tracker_snapshot", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules["tracker_snapshot"] = module
spec.loader.exec_module(module)

ledger = Path(sys.argv[2])
module.rotate(ledger, "2027")
loaded = module.load(ledger)
state = loaded.records["consumer-zz99"]
print(json.dumps({
    "status": state.status,
    "totals": state.totals.as_dict(),
    "event_count": loaded.header.event_count,
    "resumed_from": None if module.latest_checkpoint(ledger) is None
                    else module.latest_checkpoint(ledger).name,
    "stale": module.staleness(ledger).stale,
}))
"""


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


def _consumer_kit(tmp_path: Path) -> Path:
    consumer = tmp_path / "consumer" / "kit" / "tracker"
    consumer.mkdir(parents=True)
    for source in (SNAPSHOT_SOURCE, EVENTS_SOURCE, IDS_SOURCE):
        shutil.copy2(source, consumer / source.name)
    return consumer


def test_the_snapshot_is_derived_in_a_consumer_process_with_no_basicly(tmp_path: Path) -> None:

    ledger = tmp_path / "their-ledger"
    events.append(
        ledger,
        [
            events.Draft("consumer-zz99", "created", {"title": "theirs"}),
            events.Draft("consumer-zz99", "status", {"status": "open"}),
            events.Draft("consumer-zz99", "dispatch", {"spend_micros": 7}),
        ],
        clock=lambda: CLOCK,
    )
    consumer = _consumer_kit(tmp_path)
    driver = tmp_path / "drive.py"
    driver.write_text(_DRIVER, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-S", "-I", str(driver), str(consumer / "snapshot.py"), str(ledger)],
        cwd=tmp_path,
        env=_pruned_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "status": "open",
        "totals": {"events": 3, "attempts": 1, "spend_micros": 7, "status": "open"},
        "event_count": 3,
        "resumed_from": "checkpoint-0001.jsonl",
        "stale": False,
    }
    assert (ledger / snapshot.SNAPSHOT_NAME).exists()


def test_the_hook_command_runs_the_module_as_a_script_with_no_basicly(tmp_path: Path) -> None:
    ledger = tmp_path / "their-ledger"
    events.append(
        ledger,
        [events.Draft("consumer-zz99", "created", {"title": "theirs"})],
        clock=lambda: CLOCK,
    )
    consumer = _consumer_kit(tmp_path)

    result = subprocess.run(
        [sys.executable, "-S", "-I", str(consumer / "snapshot.py"), str(ledger)],
        cwd=tmp_path,
        env=_pruned_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["written"] is True
    assert (ledger / snapshot.SNAPSHOT_NAME).exists()
