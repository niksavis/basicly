"""Tests for the tracker kit's `fsck` and `rebuild` (basicly-vkh0.15).

Both acceptance criteria are claims about the *failing* case, so each test seeds the defect
and asserts the report names it. A checker that only passes on a healthy ledger is the fail-open
shape this repo keeps paying for — indistinguishable from one that checks nothing — so the
healthy ledger appears once, as the control the seeded cases are measured against, and every
defect class the module declares has a test that turns it red.

- **AC1, the log.** One test per finding class, each naming the event id the report has to
  carry. Two of them are the criteria's own examples: two events claiming one sequence number,
  and an edge into a record no ``created`` event ever minted. The malformed case is the one that
  would break a naive checker rather than the ledger — `events.fold` *raises* on a known kind
  carrying a payload it cannot mean, so a checker that folded and hoped would die on exactly the
  corruption it exists to find.
- **AC2, the derivatives.** A stale snapshot is deliberately **not** a finding: every reader
  regenerates it by scanning. The case that is one is the case no scan can reach — a header that
  agrees with the log over a body that does not — and that test asserts `snapshot.staleness`
  calls the same file fresh, which is what makes the fold worth spending. Rebuild is then proved
  to fold the log *alone* by making every derivative on disk unreadable garbage first, and to
  reproduce what rotation published byte for byte.

Everything a host would otherwise decide is injected as test data, per this repo's
platform-hermetic rule: the wall clock on every append and the rotation period. Files are
compared by name and by text read through one encoding, never by an absolute path literal.
"""

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
# Derived, never enumerated: a hand list went stale when `labels.py` split out.
KIT_SOURCES = tuple(sorted(KIT_DIR.glob("*.py")))


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way a consumer without basicly would."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fsck = _load(FSCK_SOURCE, "tracker_fsck")
# The module objects `fsck.py` itself loaded, not second copies: two loads mint two
# `RecordState` classes and a dataclass compares unequal across them, so a snapshot read
# through one would never equal a fold taken through the other.
snapshot = fsck.snapshot
events = fsck.events

RECORD_A = "basicly-aa11"
RECORD_B = "basicly-bb22"
MISSING = "basicly-zz99"

CLOCK = 1_000_000_000.0
NEXT_PERIOD = "2027"


def _append(directory: Path, drafts: list[Any]) -> list[Any]:
    """Append *drafts* under a fixed injected clock."""
    return events.append(directory, drafts, actor="a-lane", clock=lambda: CLOCK)


def _seed(directory: Path) -> Path:
    """A healthy two-record ledger touching every folded field."""
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
    """The ledger's current log file."""
    return events.log_paths(directory)[-1]


def _lines(path: Path) -> list[str]:
    """The non-blank lines of a ledger or derived file."""
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write(path: Path, lines: list[str]) -> None:
    """Replace *path* with *lines*, newline-terminated, so nothing reads as a torn write."""
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8", newline="\n")


def _dumps(obj: dict[str, Any]) -> str:
    """One line, rendered the way the ledger renders one."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _of_kind(report: Any, kind: str) -> list[Any]:
    """Every finding of one class."""
    return [found for found in report.findings if found.kind == kind]


def _kinds(report: Any) -> set[str]:
    """The classes the report found."""
    return {found.kind for found in report.findings}


def _derived_text(directory: Path) -> dict[str, str]:
    """Every derived file by name and by text, read through one encoding on every platform."""
    return {
        path.name: path.read_text(encoding="utf-8") for path in snapshot.derived_paths(directory)
    }


# --- the control ---------------------------------------------------------------


def test_a_healthy_ledger_is_clean_and_every_seeded_case_below_is_measured_against_it(
    tmp_path: Path,
) -> None:
    """The control. Without it every red below could be a checker that fails on anything."""
    ledger = _seed(tmp_path / "ledger")
    snapshot.rebuild(ledger)

    report = fsck.check(ledger)

    assert report.findings == ()
    assert report.clean is True
    assert report.exit_code == fsck.EXIT_CLEAN
    assert report.events == 5
    assert report.records == 2


def test_a_ledger_that_does_not_exist_is_inert_rather_than_an_error(tmp_path: Path) -> None:
    """A checker wired into a repository with no tracker must report, not raise."""
    report = fsck.check(tmp_path / "no-ledger-here")

    assert report.clean is True
    assert report.events == 0


# --- AC1: each defect class in the log turns the check red ---------------------


def test_two_events_claiming_one_sequence_number_name_both_ids_and_fail(
    tmp_path: Path,
) -> None:
    """§4.1's visible fork: two branches incremented one item's sequence concurrently.

    Seeded the way a union merge produces it — a second event carrying a sequence number the
    record already spent — because the id is content-derived and does *not* cover ``seq``, so
    the two lines are two genuine events rather than one duplicated.
    """
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
    """§13's referentially broken: an edge into nothing gates a landing on no record."""
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
    """The kit writes edges two ways and a checker that knew one would pass the other.

    The keys are read from the modules that declare them — `migrate.py`'s ``to`` and
    `provenance.py`'s ``target`` — so this asserts the reconciliation is real rather than that
    a literal was copied.
    """
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
    """A record exists once a ``created`` event mints it; the fold would invent this one."""
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
    """A delete leaves a tombstone rather than removing anything, which is not nothing."""
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
    """§4.4: interior garbage is a finding, quarantined by line number and never edited.

    It carries no event id, which is the reason it is a finding — the line the report names is
    all there is to name.
    """
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
    """The case that breaks a naive checker rather than the ledger.

    `events.fold` raises on a ``field`` event with no name — correct for a reader that must be
    right, fatal for one whose whole job is the corrupt case. The check has to name it and
    still fold everything else.
    """
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
    # Everything else still folded: the malformed event costs its own line, not the report.
    assert report.records == 2
    assert report.events == 5


def test_carried_totals_the_fold_disagrees_with_name_the_event_and_fail(
    tmp_path: Path,
) -> None:
    """§4.6: the fold is the authority and a carried total is a cache that lives in the log."""
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
    """§4.6 voids a forked item's carried totals until a fold restates them.

    So the totals disagreement on a forked record is the fork's consequence, and reporting it
    beside the cause is how one root defect prints as a page of findings.
    """
    ledger = _seed(tmp_path / "ledger")
    _append(ledger, [events.Draft(RECORD_A, "comment", {"text": "the other branch"})])
    lines = _lines(_log(ledger))
    collided = json.loads(lines[-1])
    collided["seq"] = 2
    _write(_log(ledger), [*lines[:-1], _dumps(collided)])

    report = fsck.check(ledger)

    assert _kinds(report) == {fsck.FORKED_SEQUENCE}


def _drop_seq(ledger: Path, record: str, seq: int) -> dict[str, Any]:
    """Delete one event's line, leaving a hole in *record*'s sequence chain.

    This is how the real defect looks and the only way to seed it: `events.append` assigns
    max+1 and `_append_lines` has one caller, so no sequence of writes produces a gap. The
    line removed is returned so a test can name what is no longer there.
    """
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
    """work-tracker.md §4.1's other broken chain: not two events on one number, but none on it.

    The writer reads the item's max and writes max+1, so a hole means a line that was written
    is gone — and nothing can restore it, which is why this is reported to be known rather
    than to be repaired. Found on this repo's own ledger, where the single symptom was a
    ``carried-totals`` finding on the *next* event and the missing number went unreported
    (basicly-t10ipy).

    ``event_ids`` is asserted empty on purpose: the defect is that the event is not here, and
    naming the sound survivors either side would send a reader to inspect two good lines.
    """
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
    """The hole voids the same cache a fork does, and for the same reason.

    Every event after the hole carries totals that counted the event that is gone, so each of
    them disagrees with the fold — one root defect printing as a finding per later event is
    the page-of-findings shape work-tracker.md §4.6 already avoids for a fork. Two events are
    appended after the hole rather than one, so a fix that suppressed only the first still fails.
    """
    ledger = _seed(tmp_path / "ledger")
    _append(ledger, [events.Draft(RECORD_A, "comment", {"text": "before the hole"})])
    _append(ledger, [events.Draft(RECORD_A, "comment", {"text": "after the hole"})])
    _append(ledger, [events.Draft(RECORD_A, "comment", {"text": "later still"})])
    _drop_seq(ledger, RECORD_A, 4)
    snapshot.rebuild(ledger)

    report = fsck.check(ledger)

    assert _kinds(report) == {fsck.SEQUENCE_GAP}
    assert _of_kind(report, fsck.CARRIED_TOTALS) == []
    # The positive control that the cache really is broken underneath the suppression: the
    # fold names both later events, so this is a report that declines to repeat a cause and
    # not a checker that stopped measuring.
    folded = events.fold(events.read_events(ledger)[0])
    assert len(folded.mismatched_totals) == 2


def test_one_id_on_lines_that_disagree_about_their_content_fails(tmp_path: Path) -> None:
    """The id covers the record, kind and payload — not the sequence the dedup then picks."""
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
    """A repeated id is what content-derived ids are *for*: idempotent replay, not corruption."""
    ledger = _seed(tmp_path / "ledger")
    lines = _lines(_log(ledger))
    _write(_log(ledger), [*lines, lines[4]])

    report = fsck.check(ledger)

    assert _of_kind(report, fsck.DUPLICATE_ID) == []
    assert report.clean is True


def test_a_kind_the_fold_applies_no_state_for_warns_and_does_not_fail(tmp_path: Path) -> None:
    """§4.5's tolerant direction: a newer ledger is not corruption to an older reader."""
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
    """The warning has to fire on an unreadable event and stay silent on a delegated one.

    Both classes in one ledger, because a checker that had simply stopped warning about
    unfolded kinds would satisfy the first half on its own — and that is the failure mode
    here, a signal whose 1,015 false entries made it unusable as D-34's safety net.
    """
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


# --- AC2: the derivatives ------------------------------------------------------


def test_a_stale_derivative_is_not_a_finding_because_every_reader_regenerates_it(
    tmp_path: Path,
) -> None:
    """The snapshot is derived, disposable, and refolded on a stale read.

    Both directions asserted, because the interesting half is the second: a ledger that never
    had a snapshot is not broken either, or `fsck` would fail on every fresh install.
    """
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
    """The one case worth a fold: a header that agrees with the log over a body that does not.

    `snapshot.load` serves this file verbatim, so the assertion that `staleness` calls it fresh
    is not colour — it is what makes the finding this module's to make and nobody else's.
    """
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
    """A wrong checkpoint outlives itself.

    `fold_resumed` seeds the next snapshot from it, so the error propagates into a file that
    then looks freshly built.
    """
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
    """Refused rather than half-read, and reported rather than replaced on the next read."""
    ledger = _seed(tmp_path / "ledger")
    snapshot.rebuild(ledger)
    lines = _lines(snapshot.snapshot_path(ledger))
    _write(snapshot.snapshot_path(ledger), [lines[0], "not a record line"])

    report = fsck.check(ledger)

    found = _of_kind(report, fsck.DERIVED_UNREADABLE)
    assert [one.subject for one in found] == [snapshot.SNAPSHOT_NAME]
    assert report.exit_code == fsck.EXIT_DERIVED


def test_a_log_the_fold_refuses_has_no_derivative_to_be_judged_against(tmp_path: Path) -> None:
    """A log that cannot be folded has no correct derivative to compare one with.

    Reporting the cache for a defect in its source is noise beside the line that caused it.
    """
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
    """AC2 end to end: every derived file reconstructed by folding the log alone.

    Both derivatives are replaced with garbage a reader cannot use, which is stronger than
    editing them — a rebuild that read either one to seed itself would fail here rather than
    quietly carrying the corruption forward.
    """
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
    """§13's recovery is *delete and rebuild*, so deletion is a state rebuild recovers from.

    One checkpoint per closed period, which is the invariant rotation maintains.
    """
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
    """Rotation's checkpoint and a rebuild's are one file, or the derived set is not derived."""
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
    """An orphan checkpoint is `latest_checkpoint`'s answer and so seeds the steady-state fold.

    The derived *set* is derived too, which is the whole reason `rebuild` deletes before it
    writes instead of overwriting what it happens to find.
    """
    ledger = _seed(tmp_path / "ledger")
    snapshot.rebuild(ledger)
    orphan = snapshot.checkpoint_path(ledger, "9999")
    shutil.copy2(snapshot.snapshot_path(ledger), orphan)
    assert snapshot.latest_checkpoint(ledger) == orphan

    fsck.rebuild(ledger)

    assert snapshot.latest_checkpoint(ledger) is None
    assert [path.name for path in snapshot.derived_paths(ledger)] == [snapshot.SNAPSHOT_NAME]


def test_a_rebuild_deletes_no_log_and_loses_no_event(tmp_path: Path) -> None:
    """The one thing `rebuild` must never do, asserted against the deleter rather than the list.

    `rebuild` unlinks every path `snapshot.derived_paths` hands it, so the truth surviving
    rests on that function's contract that a log can never appear in it — asserted where the
    contract is *defined* (basicly-vkh0.14). This asserts it against the **deleter**.

    It is not covering an uncovered mutation, and the difference is worth stating rather than
    implying: widening `DERIVED_PATTERNS` to swallow the log glob was measured to fail ten
    tests in this file, because a deleted log corrupts derived output that several of them
    compare byte for byte. What this one adds is a *diagnosis*. Those ten failures name the
    symptom — two rebuilds disagreeing, a fold refusing — and this one names the cause, which
    is the distinction `fsck.py`'s own docstring draws when it suppresses a consequence
    reported beside its root.

    Run on a rotated ledger on purpose, so an archive, the current file, a checkpoint and a
    snapshot all sit in one directory. That is the arrangement in which a too-wide pattern
    does damage; on an unrotated ledger the two sets barely overlap.
    """
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
    """Fold determinism (§14): what lets a rebuild be compared rather than argued about."""
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
    """Skipping an event nobody understood would publish a wrong answer under a fresh header.

    Worse than no derivative at all, which is a state every reader already recovers from.
    """
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


# --- R9's reach through the rebuild (basicly-dx2ngn) ---------------------------

# Every `write_snapshot` call site in the kit, and what answers *a publish here that would
# drop records*. `guard` is the writer's own refusal; `call site` is a caller that unlinks its
# target first, so the writer always lands on its nothing-to-compare branch and the comparison
# has to be taken before the delete.
GUARD_REACH = {
    ("snapshot.py", "rebuild"): "guard",
    ("snapshot.py", "refresh"): "guard",
    ("snapshot.py", "rotate"): "guard",
    ("fsck.py", "rebuild"): "call site",
}


def _calls(function: Any, name: str) -> bool:
    """Whether *function* calls *name*, bare or through a module attribute."""
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        called = node.func
        if getattr(called, "attr", None) == name or getattr(called, "id", None) == name:
            return True
    return False


def _functions() -> dict[tuple[str, str], Any]:
    """Every kit function by module name and function name, a method included.

    Walked rather than read off `tree.body`: a call site added inside a class would be
    invisible to a top-level scan, which is the same fail-open shape as the defect below.
    """
    found: dict[tuple[str, str], Any] = {}
    for source in KIT_SOURCES:
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found[(source.name, node.name)] = node
    return found


def test_a_rebuild_whose_publish_would_shrink_refuses_and_deletes_nothing(tmp_path: Path) -> None:
    """The incident's shape through the publish R9's guard did not reach (basicly-dx2ngn).

    Nothing removed is the second half: a refusal taken after the unlink would itself
    destroy the records it exists to keep.
    """
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
    """A fifth call site fails here until somebody records which answer covers it.

    The guard is one `if` inside `write_snapshot`, so its *reach* is a property of the
    callers and a caller that deletes its target first silently opts out. Each answer is
    checked against the code rather than believed: `call site` means the caller takes the
    comparison itself, `guard` means it leaves it to the writer.
    """
    functions = _functions()
    sites = {key for key, node in functions.items() if _calls(node, "write_snapshot")}

    assert sites == set(GUARD_REACH)
    for site, answer in GUARD_REACH.items():
        assert _calls(functions[site], "shrinkage") is (answer == "call site")


# --- the unattributed census (basicly-at5tph) ----------------------------------


def test_events_naming_no_actor_are_counted_in_both_spellings(tmp_path: Path) -> None:
    """The empty field and `events.UNATTRIBUTED_ACTOR` are one population, counted together.

    Two lines are rewritten to each spelling over a five-event control, which is what makes
    the census discriminating: counting either spelling alone answers 2, and counting every
    event answers 5. Rewriting `actor` cannot disturb anything else — it is excluded from the
    event id digest, so no line stops re-minting from its own content.
    """
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


# --- the entry point -----------------------------------------------------------


def test_the_entry_point_exits_by_which_remedy_applies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Broken beats derived beats clean, because the exit code is what a gate reads."""
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
    """A repair nobody verified is a claim, so the flag reports the check's answer."""
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
    """An entry point that traced back would hide the one sentence a reader needs."""
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


# --- the kit boundary ----------------------------------------------------------


def test_the_module_imports_nothing_outside_the_standard_library() -> None:
    """The kit's one structural rule, read off the source rather than trusted.

    ``time`` and ``datetime`` are absent from the permitted set on purpose: nothing in the kit
    may ask what year it is (§9.5), and a checker that dated a derivative by its mtime instead
    of by the log would be the first place that rule breaks.
    """
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


def test_the_ledger_is_checked_and_repaired_in_a_process_with_no_basicly(
    tmp_path: Path,
) -> None:
    """The kit's hard constraint, exercised the way a consumer would exercise it.

    ``-S`` drops site-packages, which is where this repo's own ``basicly`` lives, and ``-I``
    drops ``PYTHONPATH``, the user site directory and the script's own directory. The ledger is
    built and corrupted here, and checked and rebuilt *there*, as one script with one argument.
    """
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
    """Found by running it: one warning on this repo's own ledger printed 656 ids.

    The bound is on the *printed* form only — the finding still carries every id for a caller
    that wants them — and it declares the cut, because a silent truncation reads as "that was
    all of them" to exactly the reader who would have acted on the rest.
    """
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
