"""Tests for the tracker kit's derived record snapshot (basicly-vkh0.14).

Both acceptance criteria are properties of a *derived* file, so each is asserted in the one
way that can fail rather than restated:

- **The header dates the snapshot without folding.** The discriminating case is a snapshot
  whose record lines are garbage: :func:`staleness` still has to answer, which it can only do
  by reading the first line. A hand-edited record line then separates the two read paths —
  a fresh read returns the file (edit and all), and the read after one more append returns
  the log's answer instead, which is what "regenerated lazily on a stale read" means.
- **The glob is a contract and the checkpoint bounds the steady state.** Rotation, then an
  append, then a rebuild: an archive dropped by a narrowed glob loses a record the assertions
  name. The checkpoint is proved to *replace* the archive rather than merely summarise it by
  making the archive unreadable at an unchanged line count — the resumed fold still answers for
  a record idle since before the boundary, while the full-history fold no longer can.

Everything a host would otherwise decide is injected as test data, per this repo's
platform-hermetic rule: the wall clock on every append, the rotation period (the kit reads no
clock at all — asserted from its imports), and the lock holder's pid. The one contention test
uses a zero timeout so it reaches its deadline without sleeping, and it holds the lock under
*this* process's pid so the platform's liveness answer — ``True`` on POSIX, ``None`` on
Windows — refuses the steal either way.
"""

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
    """Load a standalone script by path, the way a consumer without basicly would."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


snapshot = _load(SNAPSHOT_SOURCE, "tracker_snapshot")
# The module object `snapshot.py` itself loaded, not a second copy: two loads mint two
# `RecordState` classes and a dataclass compares unequal across them, so every state
# comparison below would silently be an identity check instead.
events = snapshot.events

RECORD_A = "basicly-aa11"
RECORD_B = "basicly-bb22"
RECORD_C = "basicly-cc33.4"

CLOCK = 1_000_000_000.0
NEXT_PERIOD = "2027"


def _lifecycle() -> list[Any]:
    """Drafts touching every folded field: fields, status, comments, totals, a tombstone."""
    return [
        events.Draft(RECORD_A, "created", {"title": "a parent"}),
        events.Draft(RECORD_A, "status", {"status": "open"}),
        events.Draft(RECORD_B, "created", {"title": "a sibling"}),
        events.Draft(RECORD_B, "dispatch", {"spend_micros": 1250}),
        events.Draft(RECORD_B, "comment", {"text": "a note"}),
        events.Draft(RECORD_C, "created", {"title": "a child"}),
        events.Draft(RECORD_C, "tombstone", {}),
    ]


def _build(directory: Path, drafts: list[Any] | None = None) -> list[Any]:
    """Append *drafts* (the lifecycle by default) under a fixed injected clock."""
    return events.append(
        directory, _lifecycle() if drafts is None else drafts, actor="a-lane", clock=lambda: CLOCK
    )


def _lines(path: Path) -> list[str]:
    """The non-blank lines of a derived file."""
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _code_string_literals(source: str) -> list[str]:
    """Every string literal in *source* that is not a docstring.

    The prose may name ``events-2027.jsonl`` as often as it helps; the *code* may not, because
    a second spelling of the log's name is a second fact that drifts from
    :data:`events.LOG_GLOB` without anything noticing.
    """
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
    """The event id on the last line of the last log file — what a tail read would find."""
    logs = events.log_paths(directory)
    return json.loads(_lines(logs[-1])[-1])["id"]


# --- AC1: the header dates the snapshot without folding ------------------------


def test_the_first_line_carries_the_last_folded_events_id_and_the_event_count(
    tmp_path: Path,
) -> None:
    """The staleness header, read as a reader reads it: line one, and nothing else."""
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
    """The discriminating case: a body no fold could read, and a question still answered.

    A `staleness` that parsed the record lines would raise here. One that folded the log to
    compare would be doing exactly the work the header exists to avoid, and the AC's
    "without folding" would be untestable.
    """
    _build(tmp_path)
    path = snapshot.rebuild(tmp_path) and snapshot.snapshot_path(tmp_path)
    header = _lines(path)[0]
    path.write_text(f"{header}\nnot json at all\n{{]\n", encoding="utf-8")

    assert snapshot.staleness(tmp_path).stale is False

    _build(tmp_path, [events.Draft(RECORD_A, "comment", {"text": "later"})])

    assert snapshot.staleness(tmp_path).stale is True


def test_every_append_after_a_snapshot_makes_it_stale_and_says_why(tmp_path: Path) -> None:
    """Repeated rather than once: a check that only fires on the first divergence is worse.

    The reason is asserted because a hook that prints "rebuilt" and nothing else leaves a
    human unable to tell a merge from a corrupt derivative.
    """
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
    """A tally that only watched the current file would call a grown ledger unchanged.

    A union merge lands another branch's events in the file whose period they belong to,
    which can be an archive — so the scan covers every log the glob finds.
    """
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

    # And the checkpoint must not be trusted here: it folded that archive at a smaller line
    # count, so the shortcut has to give way to the full fold or the merged event is lost
    # behind a header calling the snapshot current.
    grown = snapshot.fold_resumed(tmp_path)

    assert grown.resumed_from is None
    assert [path.name for path in grown.logs] == [events.INITIAL_LOG_NAME, "events-2027.jsonl"]
    assert "merged in" in snapshot.load(tmp_path).records[RECORD_A].comments
    assert snapshot.staleness(tmp_path).stale is False


def test_a_fresh_read_returns_the_file_and_a_stale_read_refolds_the_log(tmp_path: Path) -> None:
    """The two halves of laziness, separated by a value only the file can hold.

    A comment edited into the snapshot is not in the log, so it survives exactly as long as
    the header vouches for the file. One more append and the log's answer replaces it —
    which is the regeneration, observed rather than counted with a spy.
    """
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
    """A derived file's forward compatibility is refusal, not the log's tolerant preservation.

    Half-reading a newer format would serve a field this reader misunderstood; refusing costs
    one fold, because the log is still there.
    """
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
    """A JSON object that happens to sit on line one is some other file, not version 1."""
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
    """The rule that makes a derivative disposable: never repair, always regenerate."""
    _build(tmp_path)
    path = snapshot.rebuild(tmp_path) and snapshot.snapshot_path(tmp_path)
    path.write_text("this is not a snapshot\n", encoding="utf-8")

    loaded = snapshot.load(tmp_path)

    assert loaded.records[RECORD_A].status == "open"
    assert snapshot.read_snapshot(path) is not None
    assert snapshot.staleness(tmp_path).stale is False


def test_two_rebuilds_of_one_log_are_byte_identical(tmp_path: Path) -> None:
    """Fold determinism (§14), which is what lets one derivative be compared with another."""
    _build(tmp_path)

    snapshot.rebuild(tmp_path)
    first = snapshot.snapshot_path(tmp_path).read_bytes()
    snapshot.rebuild(tmp_path)
    second = snapshot.snapshot_path(tmp_path).read_bytes()

    assert first == second


def test_every_folded_field_survives_the_snapshot_round_trip(tmp_path: Path) -> None:
    """A snapshot that dropped `max_seq` would resume folding an item as if it were new."""
    _build(tmp_path)
    folded = events.fold(events.read_events(tmp_path)[0])

    snapshot.rebuild(tmp_path)
    loaded = snapshot.load(tmp_path)

    assert loaded.records == folded.records
    assert loaded.records[RECORD_C].tombstoned is True
    assert loaded.records[RECORD_B].totals.spend_micros == 1250
    assert loaded.records[RECORD_B].max_seq == 3


def test_a_torn_trailing_line_never_makes_the_snapshot_permanently_stale(tmp_path: Path) -> None:
    """The torn-write signature is a line no fold consumed, so no tally may count it.

    Counting it would make every read report a stale snapshot forever and rebuild on each
    one — the failure mode of a check that cannot ever be satisfied.
    """
    _build(tmp_path)
    snapshot.rebuild(tmp_path)
    log = events.log_paths(tmp_path)[-1]
    with log.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write('{"id": "basicly-aa11#ev-tor')

    assert snapshot.staleness(tmp_path).stale is False


def test_interior_garbage_is_counted_once_and_then_the_snapshot_settles(tmp_path: Path) -> None:
    """A quarantined line is a line: the tally counts it so the ledger stops reading as stale."""
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
    """A reader must never see a half-written derivative, and a rebuild must leave no litter."""
    _build(tmp_path)

    snapshot.rebuild(tmp_path)
    snapshot.rebuild(tmp_path)

    assert list(tmp_path.glob("*.tmp")) == []
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        events.INITIAL_LOG_NAME,
        snapshot.SNAPSHOT_NAME,
    ]


def test_every_line_is_utf8_with_a_unix_ending_whatever_the_host_prefers(tmp_path: Path) -> None:
    """Asserted on the bytes, so the Windows answer is checked here rather than by CI."""
    _build(tmp_path, [events.Draft(RECORD_A, "comment", {"text": "naïve — em dash"})])

    snapshot.rebuild(tmp_path)
    raw = snapshot.snapshot_path(tmp_path).read_bytes()

    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    assert "naïve — em dash" in raw.decode("utf-8")


# --- AC1: the hook entry point ------------------------------------------------


def test_the_hook_entry_point_regenerates_a_stale_snapshot_and_is_quiet_when_fresh(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """What `post-merge` and `post-checkout` run: the case laziness cannot cover."""
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
    """So a gate can assert freshness without producing it."""
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
    """`--full` is the escape hatch for a checkpoint nobody trusts, not a different answer."""
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
    """A hook is installed once and runs on every checkout, including before the ledger exists."""
    missing = tmp_path / "no-tracker-here"

    assert snapshot.main([str(missing)]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["ledger"] is False
    assert report["written"] is False
    assert not missing.exists()


def test_an_unusable_header_is_reported_as_stale_rather_than_as_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The hook has exactly two outcomes, because every bad derivative is replaced.

    A negative count cannot have come from any fold here, so the header parser refuses it —
    and `staleness` turns that refusal into a *reason*, which is what keeps a corrupt cache
    from becoming a failing hook.
    """
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


# --- AC2: the glob is a contract, rotation archives, the checkpoint bounds -----


def test_the_log_glob_is_the_contract_and_the_rotation_names_derive_from_it() -> None:
    """A narrowed glob does not merely hide an archive — it renames what rotation creates.

    Nothing in `snapshot.py` spells ``events-`` or ``.jsonl`` a second time, so this is one
    fact with two readers rather than two facts that can drift.
    """
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
    """The full-history fold, across a boundary. A narrowed glob loses `RECORD_A` outright."""
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
    """Archived and never pruned is a property of what rotation *omits*, so the bytes are it."""
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
    """The switch is the new name sorting last, which is all `append_target` looks at."""
    _build(tmp_path)

    rotation = snapshot.rotate(tmp_path, NEXT_PERIOD)
    minted = _build(tmp_path, [events.Draft(RECORD_B, "comment", {"text": "next period"})])

    assert events.append_target(tmp_path) == rotation.log
    assert minted[0].seq == 4
    assert _lines(rotation.log) == [events.to_json(minted[0])]


def test_the_boundary_checkpoint_carries_every_items_totals_including_an_idle_one(
    tmp_path: Path,
) -> None:
    """§4.6's bound is a requirement *on the checkpoint*, so it is asserted on the file."""
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
    """The bound, proved by making the archive unreadable at an unchanged line count.

    Overwriting the archive's lines with garbage is a probe, not a supported operation — it
    is how "never walks the whole archive" becomes an assertion rather than a claim about
    performance. The line count is preserved on purpose, because that is the one thing the
    resumed fold *does* check: this isolates "the archive's bytes are never parsed" from
    "the archive has grown", which is the case the fallback exists for.
    """
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
    """The steady-state shortcut has to be the same answer, or it is a second source of truth."""
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
    """A derivative is disposable in both directions: a bad one is skipped, never repaired."""
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
    """A rotation whose name sorts earlier is a silent no-op: appends keep going to the old file."""
    _build(tmp_path)
    snapshot.rotate(tmp_path, NEXT_PERIOD)

    with pytest.raises(snapshot.SnapshotError, match="does not sort after"):
        snapshot.rotate(tmp_path, "2026")

    assert not snapshot.log_path(tmp_path, "2026").exists()
    assert [path.name for path in snapshot.checkpoint_paths(tmp_path)] == ["checkpoint-0001.jsonl"]


def test_a_malformed_period_or_an_existing_file_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:
    """Refused, and nothing written: a half-rotation would leave two current files."""
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
    """A checkpoint of nothing would be a file whose only content is a claim to have folded."""
    rotation = snapshot.rotate(tmp_path / "fresh", "2026")

    assert rotation.checkpoint is None
    assert rotation.archived == []
    assert rotation.log.read_bytes() == b""


def test_rotation_reports_contention_rather_than_writing_under_another_writer(
    tmp_path: Path,
) -> None:
    """Rotation changes where an append lands, so it takes the writer's lock.

    Hermetic by construction rather than by luck: the timeout is zero, so the acquire loop
    reaches its deadline on the first pass with nothing slept, and the holder's pid is *this*
    process — which POSIX reports alive and Windows reports unknown, and neither answer
    permits a steal.
    """
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


# --- the derived set, and the ignore rule it needs -----------------------------


def test_derived_paths_names_every_derivative_and_never_a_log(tmp_path: Path) -> None:
    """This list is handed to a delete, in a directory that also holds the only truth."""
    _build(tmp_path)
    snapshot.rotate(tmp_path, NEXT_PERIOD)
    snapshot.rebuild(tmp_path)

    derived = [path.name for path in snapshot.derived_paths(tmp_path)]

    assert sorted(derived) == ["checkpoint-0001.jsonl", "snapshot.jsonl"]
    assert not [name for name in derived if name.startswith("events-")]


def test_the_ignore_patterns_can_never_match_a_log_name() -> None:
    """The snapshot is gitignored, so the patterns a deployment ignores are load-bearing.

    An ignore rule that also matched `events-*.jsonl` would leave the ledger untracked — the
    truth dropped to keep a cache out of git.
    """
    assert snapshot.DERIVED_PATTERNS == ("snapshot.jsonl", "checkpoint-*.jsonl")
    for pattern in snapshot.DERIVED_PATTERNS:
        assert not fnmatch(events.INITIAL_LOG_NAME, pattern)
        assert not fnmatch("events-2027.jsonl", pattern)
        assert not fnmatch(".events.lock", pattern)


# --- the seeded fold, which is the only fold ----------------------------------


def test_a_seeded_fold_copies_the_checkpoint_it_resumes_from(tmp_path: Path) -> None:
    """The seam exists so there is one fold, not a second way to apply an event to a state.

    It must therefore not mutate the caller's records: a checkpoint read once and folded
    twice would otherwise accumulate the tail twice.
    """
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


# --- the kit boundary ---------------------------------------------------------


def test_the_module_imports_nothing_outside_the_standard_library() -> None:
    """The kit boundary, read off the source rather than trusted.

    ``time`` and ``datetime`` are absent from the permitted set on purpose: the rotation
    period is an argument (§9.5), so nothing here may ask what year it is. ``Path.replace``
    rather than ``rename`` is asserted for the same reason a platform difference is made test
    data — on Windows a rename onto an existing file fails, which would leave every rebuild
    after the first silently unpublished.
    """
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
    """An environment with no basicly on PATH and nothing pointing at this repo.

    Built from empty rather than filtered, so nothing inherited can smuggle the package back
    in. The few names copied back are what an interpreter needs on its own platform, which
    makes the platform difference test data.
    """
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
    """The three kit files copied out, the way a consumer copies a directory."""
    consumer = tmp_path / "consumer" / "kit" / "tracker"
    consumer.mkdir(parents=True)
    for source in (SNAPSHOT_SOURCE, EVENTS_SOURCE, IDS_SOURCE):
        shutil.copy2(source, consumer / source.name)
    return consumer


def test_the_snapshot_is_derived_in_a_consumer_process_with_no_basicly(tmp_path: Path) -> None:
    """The kit's hard constraint, exercised the way a consumer would exercise it.

    ``-S`` drops site-packages, which is where this repo's own ``basicly`` lives, and ``-I``
    drops ``PYTHONPATH``, the user site directory and the script's own directory. The ledger
    is built here and rotated, folded and published *there*.
    """
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
    """The exact shape a `post-merge` hook uses: one interpreter, one path, one argument."""
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
