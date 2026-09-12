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

from tests import tracker_corpus

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
MIGRATE_SOURCE = KIT_DIR / "migrate.py"
EVENTS_SOURCE = KIT_DIR / "events.py"
IDS_SOURCE = KIT_DIR / "ids.py"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


migrate = _load(MIGRATE_SOURCE, "tracker_migrate")
events = migrate.events

SOURCE = "the committed ledger"

RECORD_A = "basicly-aa11"
RECORD_B = "basicly-bb22"
RECORD_C = "basicly-cc33.4"

CLOCK = 1_000_000_000.0


def _record(record_id: str, **overrides: Any) -> dict[str, Any]:
    record = {
        "id": record_id,
        "title": f"the record {record_id}",
        "status": "open",
        "priority": 2,
        "issue_type": "task",
        "created_at": "2026-08-01T10:00:00Z",
        "created_by": "niksa",
        "labels": ["phase-6"],
    }
    record.update(overrides)
    return record


def _export(*records: dict[str, Any]) -> str:
    return "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)


def _snapshot(*records: dict[str, Any], name: str = SOURCE) -> Any:
    return migrate.parse_snapshot(_export(*records), name=name)


def _import(ledger: Path, snapshot: Any, **kwargs: Any) -> Any:
    return migrate.import_snapshot(ledger, snapshot, clock=lambda: CLOCK, **kwargs)


def _fold(ledger: Path) -> Any:
    found, quarantined = events.read_events(ledger)
    assert quarantined == []
    return events.fold(found)


def _kinds(minted: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in minted:
        counts[event.kind] = counts.get(event.kind, 0) + 1
    return counts


def test_the_live_trackers_records_and_edges_all_arrive_with_import_provenance(
    tmp_path: Path,
) -> None:

    snapshot = migrate.parse_snapshot(tracker_corpus.snapshot_text(), name=SOURCE)
    assert snapshot.unreadable == ()
    assert len(snapshot.records) > 500, "the corpus should hold the whole tracker"

    report = _import(tmp_path, snapshot)

    assert report.rejected == []
    assert report.unreadable == []
    assert report.diverged == []
    assert report.absent == []
    assert sorted(report.imported) == sorted(str(record["id"]) for record in snapshot.records)

    folded = _fold(tmp_path)
    assert set(folded.records) == {str(record["id"]) for record in snapshot.records}
    for record, state in folded.records.items():
        assert state.fields[migrate.PROVENANCE_KEY] == migrate.EXTRACTED, record
        assert state.fields[migrate.SOURCE_KEY] == SOURCE, record
        assert state.fields[migrate.DIGEST_KEY] == snapshot.digest, record

    imported_edges = set()
    for event in report.events:
        if event.kind != migrate.KIND_EDGE:
            continue
        assert event.payload[migrate.PROVENANCE_KEY] == migrate.EXTRACTED
        assert event.payload[migrate.SOURCE_KEY] == SOURCE
        imported_edges.add((
            event.payload[migrate.EDGE_FROM],
            event.payload[migrate.EDGE_TO],
            event.payload[migrate.EDGE_TYPE],
        ))
    source_edges = {
        (str(record["id"]), edge["depends_on_id"], edge["type"])
        for record in snapshot.records
        for edge in record.get("dependencies") or ()
    }
    assert source_edges, "the live export should hold dependency edges to import"
    assert imported_edges == source_edges


def test_every_kind_the_importer_writes_carries_the_same_provenance_label(
    tmp_path: Path,
) -> None:
    first = _snapshot(
        _record(RECORD_A, comments=[{"id": 7, "author": "niksa", "text": "a note"}]),
        _record(
            RECORD_B,
            dependencies=[
                {
                    "issue_id": RECORD_B,
                    "depends_on_id": RECORD_A,
                    "type": "blocks",
                    "created_by": "niksa",
                }
            ],
        ),
    )
    _import(tmp_path, first)

    later = _import(tmp_path, _snapshot(_record(RECORD_B)), deleted=[RECORD_A])

    kinds = {event.kind for event in _fold_events(tmp_path)}
    assert kinds == {"created", "status", "comment", migrate.KIND_EDGE, "tombstone"}
    for event in _fold_events(tmp_path):
        assert event.payload[migrate.PROVENANCE_KEY] == migrate.EXTRACTED, event.kind
        assert event.payload[migrate.SOURCE_KEY] == SOURCE, event.kind
    assert later.tombstoned == [RECORD_A]


def _fold_events(ledger: Path) -> list[Any]:
    found, quarantined = events.read_events(ledger)
    assert quarantined == []
    return events.canonical_order(found)


def test_the_digest_is_recorded_on_the_created_event_and_nowhere_else(tmp_path: Path) -> None:
    snapshot = _snapshot(
        _record(RECORD_A, comments=[{"id": 1, "text": "a note"}]),
        _record(RECORD_B),
    )

    report = _import(tmp_path, snapshot)

    carriers = {event.kind for event in report.events if migrate.DIGEST_KEY in event.payload}
    assert carriers == {"created"}


def test_re_importing_the_same_facts_from_a_reserialised_export_appends_nothing(
    tmp_path: Path,
) -> None:

    records = (
        _record(RECORD_A, comments=[{"id": 1, "text": "a note"}]),
        _record(
            RECORD_B,
            dependencies=[{"issue_id": RECORD_B, "depends_on_id": RECORD_A, "type": "blocks"}],
        ),
    )
    first = migrate.parse_snapshot(_export(*records), name=SOURCE)
    reserialised = migrate.parse_snapshot(
        "".join(json.dumps(record, indent=None, sort_keys=False) + "\n" for record in records),
        name=SOURCE,
    )
    assert reserialised.digest != first.digest

    landed = _import(tmp_path, first).events
    replayed = _import(tmp_path, reserialised)

    assert len(landed) == 6
    assert replayed.events == []
    assert replayed.imported == []
    assert replayed.diverged == []
    assert replayed.absent == []


def test_a_replayed_import_of_the_same_export_appends_nothing(tmp_path: Path) -> None:
    snapshot = _snapshot(_record(RECORD_A), _record(RECORD_C))

    _import(tmp_path, snapshot)
    again = _import(tmp_path, snapshot)

    assert again.events == []
    assert _kinds(_fold_events(tmp_path)) == {"created": 2, "status": 2}


def test_the_source_field_set_is_split_into_events_and_record_fields(tmp_path: Path) -> None:
    snapshot = _snapshot(
        _record(
            RECORD_A,
            status="closed",
            comments=[{"id": 1, "text": "shipped"}],
            dependencies=[{"issue_id": RECORD_A, "depends_on_id": RECORD_B, "type": "blocks"}],
        )
    )

    _import(tmp_path, snapshot)

    state = _fold(tmp_path).records[RECORD_A]
    assert state.status == "closed"
    assert state.comments == ["shipped"]
    assert set(state.fields) == {
        "title",
        "priority",
        "issue_type",
        "created_at",
        "created_by",
        "labels",
        migrate.PROVENANCE_KEY,
        migrate.SOURCE_KEY,
        migrate.DIGEST_KEY,
    }
    assert state.fields["labels"] == ["phase-6"]


def test_the_edge_event_is_recorded_on_the_dependent_record(tmp_path: Path) -> None:
    snapshot = _snapshot(
        _record(RECORD_A),
        _record(
            RECORD_B,
            dependencies=[
                {
                    "issue_id": RECORD_B,
                    "depends_on_id": RECORD_A,
                    "type": "parent-child",
                    "created_by": "niksa",
                    "created_at": "2026-07-01T09:00:00Z",
                }
            ],
        ),
    )

    report = _import(tmp_path, snapshot)

    edge = next(event for event in report.events if event.kind == migrate.KIND_EDGE)
    assert edge.record == RECORD_B
    assert edge.payload[migrate.EDGE_FROM] == RECORD_B
    assert edge.payload[migrate.EDGE_TO] == RECORD_A
    assert edge.payload[migrate.EDGE_TYPE] == "parent-child"
    assert edge.payload[migrate.ASSERTED_BY_KEY] == "niksa"
    assert edge.payload[migrate.ASSERTED_AT_KEY] == "2026-07-01T09:00:00Z"


def test_an_imported_edge_is_a_kind_the_current_fold_carries_without_folding(
    tmp_path: Path,
) -> None:

    snapshot = _snapshot(
        _record(RECORD_A),
        _record(
            RECORD_B,
            dependencies=[{"issue_id": RECORD_B, "depends_on_id": RECORD_A, "type": "blocks"}],
        ),
    )

    _import(tmp_path, snapshot)

    folded = _fold(tmp_path)
    assert folded.delegated_kinds == {migrate.KIND_EDGE: 1}
    assert folded.mismatched_totals == []
    assert folded.records[RECORD_B].totals.events == 3


def test_a_record_missing_from_a_later_export_is_reported_absent_and_not_deleted(
    tmp_path: Path,
) -> None:

    _import(tmp_path, _snapshot(_record(RECORD_A), _record(RECORD_B), _record(RECORD_C)))

    report = _import(tmp_path, _snapshot(_record(RECORD_A), _record(RECORD_C)))

    assert report.absent == [RECORD_B]
    assert report.events == []
    assert report.tombstoned == []
    assert _kinds(_fold_events(tmp_path)) == {"created": 3, "status": 3}
    state = _fold(tmp_path).records[RECORD_B]
    assert state.tombstoned is False
    assert state.fields["title"] == f"the record {RECORD_B}"


def test_a_deletion_has_to_be_stated_and_lands_as_a_tombstone_event(tmp_path: Path) -> None:

    _import(tmp_path, _snapshot(_record(RECORD_A), _record(RECORD_B)))
    remaining = _snapshot(_record(RECORD_A))

    report = _import(tmp_path, remaining, deleted=[RECORD_B], detail="pruned at the source")

    assert report.tombstoned == [RECORD_B]
    assert report.rejected == []
    tombstone = next(event for event in report.events if event.kind == "tombstone")
    assert tombstone.record == RECORD_B
    assert tombstone.payload[migrate.PROVENANCE_KEY] == migrate.EXTRACTED
    assert tombstone.payload[migrate.SOURCE_KEY] == SOURCE
    assert tombstone.payload[migrate.DETAIL_KEY] == "pruned at the source"
    state = _fold(tmp_path).records[RECORD_B]
    assert state.tombstoned is True
    assert state.fields["title"] == f"the record {RECORD_B}"


def test_a_tombstoned_record_is_not_reported_absent_again(tmp_path: Path) -> None:
    _import(tmp_path, _snapshot(_record(RECORD_A), _record(RECORD_B)))
    remaining = _snapshot(_record(RECORD_A))
    _import(tmp_path, remaining, deleted=[RECORD_B])

    later = _import(tmp_path, remaining)

    assert later.absent == []
    assert later.events == []


def test_stating_the_same_deletion_twice_appends_one_tombstone(tmp_path: Path) -> None:
    _import(tmp_path, _snapshot(_record(RECORD_A), _record(RECORD_B)))
    remaining = _snapshot(_record(RECORD_A))
    _import(tmp_path, remaining, deleted=[RECORD_B])

    again = _import(tmp_path, remaining, deleted=[RECORD_B])

    assert again.tombstoned == []
    assert _kinds(_fold_events(tmp_path)) == {"created": 2, "status": 2, "tombstone": 1}


def test_a_deletion_is_refused_for_a_record_the_snapshot_still_asserts(tmp_path: Path) -> None:
    snapshot = _snapshot(_record(RECORD_A), _record(RECORD_B))
    _import(tmp_path, snapshot)

    report = _import(tmp_path, snapshot, deleted=[RECORD_B])

    assert report.tombstoned == []
    assert [rejection.subject for rejection in report.rejected] == [RECORD_B]
    assert "still asserts" in report.rejected[0].reason
    assert _fold(tmp_path).records[RECORD_B].tombstoned is False


def test_a_deletion_is_refused_for_a_record_the_ledger_never_held(tmp_path: Path) -> None:
    report = _import(tmp_path, _snapshot(_record(RECORD_A)), deleted=[RECORD_C, "not an id"])

    assert report.tombstoned == []
    assert [rejection.subject for rejection in report.rejected] == [RECORD_C, repr("not an id")]
    assert _kinds(_fold_events(tmp_path)) == {"created": 1, "status": 1}


def test_a_record_from_another_source_is_never_reported_absent(tmp_path: Path) -> None:

    _import(tmp_path, _snapshot(_record(RECORD_A), name="another/tracker.jsonl"))
    _import(tmp_path, _snapshot(_record(RECORD_B)))

    report = _import(tmp_path, _snapshot(_record(RECORD_B)))

    assert report.absent == []


def test_a_record_the_ledger_already_holds_is_reported_diverged_not_rewritten(
    tmp_path: Path,
) -> None:
    _import(tmp_path, _snapshot(_record(RECORD_A)))

    report = _import(tmp_path, _snapshot(_record(RECORD_A, title="edited at the source")))

    assert report.diverged == [RECORD_A]
    assert report.events == []
    assert _kinds(_fold_events(tmp_path)) == {"created": 1, "status": 1}
    assert _fold(tmp_path).records[RECORD_A].fields["title"] == f"the record {RECORD_A}"


def test_a_new_comment_or_edge_still_lands_after_the_first_import(tmp_path: Path) -> None:
    _import(tmp_path, _snapshot(_record(RECORD_A), _record(RECORD_B)))

    report = _import(
        tmp_path,
        _snapshot(
            _record(RECORD_A, comments=[{"id": 9, "text": "said later"}]),
            _record(
                RECORD_B,
                dependencies=[{"issue_id": RECORD_B, "depends_on_id": RECORD_A, "type": "blocks"}],
            ),
        ),
    )

    assert _kinds(report.events) == {"comment": 1, migrate.KIND_EDGE: 1}
    assert report.imported == []
    assert report.diverged == []
    assert _fold(tmp_path).records[RECORD_A].comments == ["said later"]


def test_a_status_the_record_has_held_before_is_recorded_again_not_swallowed(
    tmp_path: Path,
) -> None:

    _import(tmp_path, _snapshot(_record(RECORD_A, status="closed")))
    _import(tmp_path, _snapshot(_record(RECORD_A, status="open")))

    report = _import(tmp_path, _snapshot(_record(RECORD_A, status="closed")))

    assert _kinds(report.events) == {"status": 1}
    statuses = [
        event.payload["status"] for event in _fold_events(tmp_path) if event.kind == "status"
    ]
    assert statuses == ["closed", "open", "closed"]
    assert _fold(tmp_path).records[RECORD_A].status == "closed"


def test_an_unchanged_status_records_no_second_event(tmp_path: Path) -> None:
    snapshot = _snapshot(_record(RECORD_A, status="in_progress"))
    _import(tmp_path, snapshot)

    assert _import(tmp_path, snapshot).events == []


def test_an_unparseable_line_is_reported_by_number_and_the_rest_still_imports(
    tmp_path: Path,
) -> None:

    text = _export(_record(RECORD_A)) + '{"id": "basicly-broken"\n' + _export(_record(RECORD_B))
    snapshot = migrate.parse_snapshot(text, name=SOURCE)

    report = _import(tmp_path, snapshot)

    assert [rejection.subject for rejection in snapshot.unreadable] == ["line 2"]
    assert report.unreadable == list(snapshot.unreadable)
    assert sorted(report.imported) == [RECORD_A, RECORD_B]


def test_a_slug_id_the_commit_gate_would_refuse_is_rejected_rather_than_written(
    tmp_path: Path,
) -> None:

    snapshot = _snapshot(_record("basicly-my-slug"), _record(RECORD_A))

    report = _import(tmp_path, snapshot)

    assert [rejection.subject for rejection in report.rejected] == [repr("basicly-my-slug")]
    assert report.imported == [RECORD_A]
    assert set(_fold(tmp_path).records) == {RECORD_A}


def test_a_source_field_this_version_never_heard_of_is_imported_verbatim(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(_record(RECORD_A, dolt_commit="abc123", estimate_points=5))

    _import(tmp_path, snapshot)

    fields = _fold(tmp_path).records[RECORD_A].fields
    assert fields["dolt_commit"] == "abc123"
    assert fields["estimate_points"] == 5


def test_a_record_carrying_a_reserved_provenance_field_is_refused(tmp_path: Path) -> None:
    snapshot = _snapshot(_record(RECORD_A, provenance="INFERRED"), _record(RECORD_B))

    report = _import(tmp_path, snapshot)

    assert [rejection.subject for rejection in report.rejected] == [RECORD_A]
    assert "reserved" in report.rejected[0].reason
    assert report.imported == [RECORD_B]


def test_a_field_the_ledger_cannot_hold_rejects_that_record_and_not_the_batch(
    tmp_path: Path,
) -> None:

    snapshot = _snapshot(_record(RECORD_A, detail=["structured", "evidence"]), _record(RECORD_B))

    report = _import(tmp_path, snapshot)

    assert [rejection.subject for rejection in report.rejected] == [RECORD_A]
    assert report.imported == [RECORD_B]


def test_two_records_under_one_id_are_reported_rather_than_folded_together(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(_record(RECORD_A), _record(RECORD_A, title="the same id again"))

    report = _import(tmp_path, snapshot)

    assert [rejection.subject for rejection in report.rejected] == [RECORD_A]
    assert report.imported == [RECORD_A]
    assert _fold(tmp_path).records[RECORD_A].fields["title"] == f"the record {RECORD_A}"


def test_an_edge_pointing_at_something_that_is_not_a_record_is_refused(tmp_path: Path) -> None:
    snapshot = _snapshot(
        _record(
            RECORD_A,
            dependencies=[
                {"issue_id": RECORD_A, "depends_on_id": "not an id", "type": "blocks"},
                {"issue_id": "basicly-zz99", "depends_on_id": RECORD_B, "type": "blocks"},
                {"issue_id": RECORD_A, "depends_on_id": RECORD_B, "type": ""},
                {"issue_id": RECORD_A, "depends_on_id": RECORD_B, "type": "blocks"},
            ],
        )
    )

    report = _import(tmp_path, snapshot)

    assert [rejection.subject for rejection in report.rejected] == [
        f"{RECORD_A} edge 1",
        f"{RECORD_A} edge 2",
        f"{RECORD_A} edge 3",
    ]
    assert _kinds(report.events) == {"created": 1, "status": 1, migrate.KIND_EDGE: 1}


def test_a_record_with_no_usable_status_imports_and_says_so(tmp_path: Path) -> None:
    snapshot = _snapshot(_record(RECORD_A, status=None))

    report = _import(tmp_path, snapshot)

    assert [rejection.subject for rejection in report.rejected] == [f"{RECORD_A} status"]
    assert report.imported == [RECORD_A]
    assert _fold(tmp_path).records[RECORD_A].status is None


def test_a_comment_without_text_is_reported_and_its_siblings_still_land(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(
        _record(
            RECORD_A,
            comments=[{"id": 1, "text": "kept"}, {"id": 2}, "not an object"],
        )
    )

    report = _import(tmp_path, snapshot)

    assert [rejection.subject for rejection in report.rejected] == [
        f"{RECORD_A} comment 2",
        f"{RECORD_A} comment 3",
    ]
    assert _fold(tmp_path).records[RECORD_A].comments == ["kept"]


def test_two_identical_texts_stay_two_comments_because_the_source_id_is_carried(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(
        _record(
            RECORD_A,
            comments=[
                {"id": 1, "author": "niksa", "text": "ready"},
                {"id": 2, "author": "niksa", "text": "ready"},
            ],
        )
    )

    _import(tmp_path, snapshot)

    assert _fold(tmp_path).records[RECORD_A].comments == ["ready", "ready"]


@pytest.mark.parametrize(
    "name",
    [
        "/home/somebody/development/basicly/.beads/issues.jsonl",
        "C:\\Users\\somebody\\basicly\\.beads\\issues.jsonl",
        "C:issues.jsonl",
        "\\\\build-server\\share\\issues.jsonl",
        "~/.beads/issues.jsonl",
        "",
        " beads/issues.jsonl ",
    ],
)
def test_a_source_name_that_is_a_machine_path_is_refused(name: str) -> None:

    with pytest.raises(migrate.SnapshotError):
        migrate.validate_source_name(name)


def test_a_portable_label_is_accepted_and_recorded_on_every_event(tmp_path: Path) -> None:
    assert migrate.validate_source_name(".beads/issues.jsonl") == ".beads/issues.jsonl"

    report = _import(tmp_path, _snapshot(_record(RECORD_A), name=".beads/issues.jsonl"))

    assert {event.payload[migrate.SOURCE_KEY] for event in report.events} == {".beads/issues.jsonl"}


def test_the_default_name_is_the_files_base_name_and_not_its_path(tmp_path: Path) -> None:
    export = tmp_path / "exports" / "issues.jsonl"
    export.parent.mkdir()
    export.write_text(_export(_record(RECORD_A)), encoding="utf-8")

    snapshot = migrate.read_snapshot(export)

    assert snapshot.name == "issues.jsonl"


def test_the_digest_is_the_same_whatever_the_checkout_did_to_the_line_endings(
    tmp_path: Path,
) -> None:

    body = _export(_record(RECORD_A), _record(RECORD_B))
    unix = tmp_path / "unix.jsonl"
    windows = tmp_path / "windows.jsonl"
    unix.write_bytes(body.encode("utf-8"))
    windows.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))

    assert unix.read_bytes() != windows.read_bytes()
    assert migrate.read_snapshot(unix, name=SOURCE).digest == (
        migrate.read_snapshot(windows, name=SOURCE).digest
    )


def test_an_import_reports_contention_rather_than_deciding_on_a_stale_read(
    tmp_path: Path,
) -> None:

    lock = events.LedgerLock(tmp_path, pid=os.getpid(), is_alive=lambda _pid: True)
    lock.acquire()
    try:
        with pytest.raises(events.LockUnavailableError) as caught:
            _import(tmp_path, _snapshot(_record(RECORD_A)), lock_timeout_s=0.0)
    finally:
        lock.release()

    assert caught.value.retryable is True
    assert events.log_paths(tmp_path) == []


def test_a_caller_can_hold_the_lock_across_an_import_and_its_own_work(tmp_path: Path) -> None:
    lock = events.LedgerLock(tmp_path)
    with lock:
        report = _import(tmp_path, _snapshot(_record(RECORD_A)), held_lock=lock)
        assert lock.held is True

    assert report.imported == [RECORD_A]
    assert lock.held is False
    assert not lock.path.exists()


def test_the_actor_and_the_clock_are_the_callers(tmp_path: Path) -> None:
    report = migrate.import_snapshot(
        tmp_path,
        _snapshot(_record(RECORD_A)),
        actor="lane:migration",
        clock=lambda: CLOCK,
    )

    assert {event.actor for event in report.events} == {"lane:migration"}
    assert {event.ts for event in report.events} == {"2001-09-09T01:46:40Z"}


def test_the_redactor_reaches_an_imported_field(tmp_path: Path) -> None:
    snapshot = _snapshot(_record(RECORD_A, description="ran under /home/somebody/dev"))

    _import(tmp_path, snapshot, redact=lambda text: text.replace("/home/somebody", "<home>"))

    fields = _fold(tmp_path).records[RECORD_A].fields
    assert fields["description"] == "ran under <home>/dev"


def test_a_redacted_field_is_not_reported_as_divergence_on_a_replay(tmp_path: Path) -> None:

    snapshot = _snapshot(_record(RECORD_A, description="ran under /home/somebody/dev"))

    def redact(text: str) -> str:
        return text.replace("/home/somebody", "<home>")

    _import(tmp_path, snapshot, redact=redact)
    again = _import(tmp_path, snapshot, redact=redact)

    assert again.diverged == []
    assert again.events == []


_DRIVER = """
import importlib.util
import json
import shutil
import sys
from pathlib import Path

assert importlib.util.find_spec("basicly") is None, "basicly is importable"
assert shutil.which("basicly") is None, "basicly is on PATH"

spec = importlib.util.spec_from_file_location("tracker_migrate", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules["tracker_migrate"] = module
spec.loader.exec_module(module)

export = Path(sys.argv[2])
ledger = Path(sys.argv[3])
snapshot = module.read_snapshot(export, name="their/export.jsonl")
first = module.import_snapshot(ledger, snapshot, clock=lambda: 1_000_000_000.0)
gone = module.parse_snapshot(
    "".join(
        line + "\\n"
        for line in export.read_text(encoding="utf-8").splitlines()
        if "consumer-bb22" not in line
    ),
    name="their/export.jsonl",
)
absent = module.import_snapshot(ledger, gone, clock=lambda: 1_000_000_000.0)
stated = module.import_snapshot(
    ledger, gone, deleted=["consumer-bb22"], clock=lambda: 1_000_000_000.0
)
folded = module.events.fold(module.events.read_events(ledger)[0])
print(json.dumps({
    "imported": sorted(first.imported),
    "absent": absent.absent,
    "absent_events": len(absent.events),
    "tombstoned": stated.tombstoned,
    "still_folded": sorted(folded.records),
    "deleted_is_tombstoned": folded.records["consumer-bb22"].tombstoned,
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


def test_a_consumer_with_no_basicly_can_import_their_tracker(tmp_path: Path) -> None:

    consumer = tmp_path / "consumer" / "kit" / "tracker"
    consumer.mkdir(parents=True)
    for source in (MIGRATE_SOURCE, EVENTS_SOURCE, IDS_SOURCE):
        shutil.copy2(source, consumer / source.name)
    export = tmp_path / "export.jsonl"
    export.write_text(
        _export(
            {"id": "consumer-aa11", "title": "theirs", "status": "open"},
            {
                "id": "consumer-bb22",
                "title": "also theirs",
                "status": "closed",
                "dependencies": [
                    {
                        "issue_id": "consumer-bb22",
                        "depends_on_id": "consumer-aa11",
                        "type": "blocks",
                    }
                ],
            },
        ),
        encoding="utf-8",
    )
    driver = tmp_path / "drive.py"
    driver.write_text(_DRIVER, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-I",
            str(driver),
            str(consumer / "migrate.py"),
            str(export),
            str(tmp_path / "their-ledger"),
        ],
        cwd=tmp_path,
        env=_pruned_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "imported": ["consumer-aa11", "consumer-bb22"],
        "absent": ["consumer-bb22"],
        "absent_events": 0,
        "tombstoned": ["consumer-bb22"],
        "still_folded": ["consumer-aa11", "consumer-bb22"],
        "deleted_is_tombstoned": True,
    }


def test_the_module_imports_nothing_outside_the_standard_library() -> None:
    source = MIGRATE_SOURCE.read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not {name for name in imported if name.split(".")[0] == "basicly"}
    assert imported <= {
        "__future__",
        "collections.abc",
        "dataclasses",
        "hashlib",
        "importlib.util",
        "json",
        "pathlib",
        "sys",
        "types",
        "typing",
    }
    assert "sys.path.insert" not in source
    assert "subprocess" not in imported
