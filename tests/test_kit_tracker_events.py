"""Tests for the tracker kit's append-only event log (basicly-vkh0.11).

The two acceptance criteria are properties, not outputs, so each is asserted in a way that
can fail rather than restated:

- **The fold is a function of the event set.** A log built with a status *reopen* in it is
  re-folded from a seeded shuffle, from a reversal, and with a duplicate spliced in; every
  one has to reach the same state. The reopen is what makes those runs discriminating —
  without an ordered field in the fixture, a fold that ignored the sort would pass.
- **Nothing branches on a wall clock.** Two ledgers are built from identical drafts under
  two injected clocks a decade apart and every byte except ``ts`` has to match, which
  covers the ids, the sequence numbers and the carried totals in one assertion. A
  source-level guard then pins that the module reads no clock outside the one injected
  seam, because a behavioural test can only catch a clock read on the path it exercises.

Everything the module would otherwise take from its host is injected as test data, per
this repo's platform-hermetic rule: the wall clock, the monotonic clock, the sleep, the
pid, and the platform's answer to *is this pid alive* — including the ``None`` that
Windows is forced to give, which is asserted here on Linux rather than waiting for CI.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
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


events = _load(EVENTS_SOURCE, "tracker_events")

RECORD_A = "basicly-aa11"
RECORD_B = "basicly-bb22"
RECORD_C = "basicly-cc33.4"

# One decade apart, so a value leaking out of `ts` into anything derived shows up as a
# difference rather than as a rounding.
CLOCK_EARLY = 1_000_000_000.0
CLOCK_LATE = 1_800_000_000.0


class _FakeClock:
    """A monotonic clock and a sleep that advance together, with no real waiting.

    Injected rather than patched: the lock's staleness bound is the one duration this
    module measures, and a test that slept for real would be asserting the host's
    scheduler. ``sleep`` advances the clock it shares with ``monotonic``, so a poll loop
    reaches its deadline in bounded iterations.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.slept: list[float] = []

    def monotonic(self) -> float:
        """The current reading."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Advance instead of waiting, recording what was asked for."""
        self.slept.append(seconds)
        self.now += seconds


def _lifecycle() -> list[Any]:
    """Drafts for three records, interleaved, with an ordered reopen on the first.

    ``open → in_progress → done → open`` is the sequence §4.1 names: a fold that ignored
    the canonical order would land on whichever status happened to be last in the file.
    The reopen carries ``generation=2`` because its content repeats the first ``open`` and
    would otherwise be swallowed as an idempotent replay — the module's documented trap.
    """
    return [
        events.Draft(RECORD_A, "created", {"title": "the first"}),
        events.Draft(RECORD_B, "created", {"title": "the second"}),
        events.Draft(RECORD_A, "status", {"status": "open"}),
        events.Draft(RECORD_C, "created", {"title": "a child"}),
        events.Draft(RECORD_A, "status", {"status": "in_progress"}),
        events.Draft(RECORD_B, "comment", {"text": "first note"}),
        events.Draft(RECORD_A, "dispatch", {"spend_micros": 1_250_000}),
        events.Draft(RECORD_A, "status", {"status": "done"}),
        events.Draft(RECORD_B, "comment", {"text": "second note"}),
        events.Draft(RECORD_A, "status", {"status": "open"}, generation=2),
        events.Draft(RECORD_A, "dispatch", {"spend_micros": 400_000}),
        events.Draft(RECORD_C, "field", {"name": "priority", "value": 2}),
    ]


def _build(directory: Path, *, clock: float = CLOCK_EARLY) -> list[Any]:
    """Append :func:`_lifecycle` to a fresh ledger under a fixed clock."""
    return events.append(directory, _lifecycle(), actor="lane:one", clock=lambda: clock)


def _state(result: Any) -> dict[str, tuple[object, ...]]:
    """A fold's per-record state as comparable tuples."""
    return {
        record: (
            state.status,
            dict(state.fields),
            list(state.comments),
            state.tombstoned,
            state.totals,
            state.max_seq,
        )
        for record, state in result.records.items()
    }


# --- AC1: the per-item sequence, and the canonical order ----------------------


def test_a_fresh_ledger_directory_gets_its_first_log_and_one_line_per_event(
    tmp_path: Path,
) -> None:
    """The directory need not exist, and every appended event is one line in one file."""
    ledger = tmp_path / "never-created"
    minted = _build(ledger)

    log = ledger / events.INITIAL_LOG_NAME
    assert events.log_paths(ledger) == [log]
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(minted) == len(_lifecycle())


def test_each_event_carries_its_own_items_max_sequence_plus_one(tmp_path: Path) -> None:
    """Sequence is per item, so three interleaved records each count from one.

    A ledger-wide counter would put every item behind one number and fork on every
    branch (§4.1); this is the assertion that says which of the two we built.
    """
    minted = _build(tmp_path)

    by_record: dict[str, list[int]] = {}
    for event in minted:
        by_record.setdefault(event.record, []).append(event.seq)
    assert by_record == {
        RECORD_A: [1, 2, 3, 4, 5, 6, 7],
        RECORD_B: [1, 2, 3],
        RECORD_C: [1, 2],
    }


def test_a_second_append_continues_each_items_sequence(tmp_path: Path) -> None:
    """The writer reads the item's current max from the log, not from its own memory."""
    _build(tmp_path)

    later = events.append(
        tmp_path,
        [
            events.Draft(RECORD_B, "comment", {"text": "third note"}),
            events.Draft(RECORD_C, "status", {"status": "open"}),
        ],
        actor="lane:two",
        clock=lambda: CLOCK_EARLY,
    )

    assert [(event.record, event.seq) for event in later] == [(RECORD_B, 4), (RECORD_C, 3)]


def test_the_canonical_order_breaks_a_sequence_tie_by_event_id(tmp_path: Path) -> None:
    """Two branches wrote the same sequence on one item; the id decides, not file order.

    The two events are handed to :func:`canonical_order` in *both* orders. A sort that
    stopped at ``(record, seq)`` would be stable — it would return each input in the order
    it arrived — so a missing tie-break shows up as the two calls disagreeing.
    """
    _build(tmp_path)
    existing, _ = events.read_events(tmp_path)
    first = next(event for event in existing if event.record == RECORD_B and event.seq == 2)
    rival = events.Event(
        id=events.event_id_for(RECORD_B, "comment", {"text": "from the other branch"}),
        record=RECORD_B,
        seq=2,
        kind="comment",
        actor="lane:other",
        ts="1999-01-01T00:00:00Z",
        payload={"text": "from the other branch"},
        totals=first.totals,
    )

    forwards = events.canonical_order([first, rival])
    backwards = events.canonical_order([rival, first])

    assert [event.id for event in forwards] == [event.id for event in backwards]
    assert [event.id for event in forwards] == sorted([first.id, rival.id])


def test_a_repeated_sequence_on_one_item_is_reported_as_a_fork(tmp_path: Path) -> None:
    """A union merge of two branches is a visible fork, and the fold restates the totals.

    §4.6's rule is that a forked item's *carried* totals are void until a fold restates
    them, so the assertion is both halves: the fork is named, and the recomputed totals
    count the events the tail's carried totals missed.
    """
    _build(tmp_path)
    existing, _ = events.read_events(tmp_path)
    twin = next(event for event in existing if event.record == RECORD_B and event.seq == 3)
    rival = events.Event(
        id=events.event_id_for(RECORD_B, "comment", {"text": "concurrent"}),
        record=RECORD_B,
        seq=3,
        kind="comment",
        actor="lane:other",
        ts=twin.ts,
        payload={"text": "concurrent"},
        totals=twin.totals,
    )

    result = events.fold([*existing, rival])

    assert result.forked == [RECORD_B]
    assert result.records[RECORD_B].totals.events == 4
    assert twin.totals.events == 3
    # The carried totals that the fold contradicts are named for `fsck`, never repaired.
    assert rival.id in result.mismatched_totals or twin.id in result.mismatched_totals


# --- AC1: the fold is a function of the event set -----------------------------


def test_the_fold_ignores_the_order_the_events_arrive_in(tmp_path: Path) -> None:
    """A seeded shuffle, a reversal and the file order all fold to one state."""
    _build(tmp_path)
    original, quarantined = events.read_events(tmp_path)
    assert quarantined == []

    baseline = _state(events.fold(original))
    reversed_run = _state(events.fold(list(reversed(original))))
    shuffled = list(original)
    random.Random(20260806).shuffle(shuffled)
    shuffled_run = _state(events.fold(shuffled))

    assert baseline == reversed_run == shuffled_run
    # The reopen is what makes the three runs discriminating rather than coincidental: it
    # is the last status by sequence and not by file position, and the folded state and the
    # recomputed totals have to agree on it.
    folded = events.fold(original).records[RECORD_A]
    assert folded.status == "open"
    assert folded.totals.status == "open"


def test_the_fold_reaches_the_reopen_and_not_the_last_line_in_the_file(
    tmp_path: Path,
) -> None:
    """Rewriting the log with the status events last still folds to the reopen.

    This is the positive control for the shuffle test: a fold that took the file's final
    status event would land on ``done`` here, which is a different answer from the sorted
    one rather than the same answer reached by luck.
    """
    _build(tmp_path)
    log = tmp_path / events.INITIAL_LOG_NAME
    lines = log.read_text(encoding="utf-8").splitlines()
    statuses = [line for line in lines if '"kind":"status"' in line]
    others = [line for line in lines if '"kind":"status"' not in line]
    # `done` is seq 5 and the reopen is seq 6, so putting `done` last in the file is the
    # arrangement that catches a reader trusting append order.
    rewritten = [*others, *sorted(statuses, key=lambda line: '"done"' in line)]
    log.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    restored, _ = events.read_events(tmp_path)
    result = events.fold(restored)

    assert '"done"' in rewritten[-1]
    assert result.records[RECORD_A].status == "open"


def test_a_duplicated_event_folds_once_and_is_reported(tmp_path: Path) -> None:
    """Idempotency by id: a union merge that duplicated a line changes no state."""
    _build(tmp_path)
    original, _ = events.read_events(tmp_path)
    baseline = _state(events.fold(original))

    doubled = events.fold([*original, original[3], original[0]])

    assert _state(doubled) == baseline
    assert doubled.duplicate_ids == sorted({original[3].id, original[0].id})


def test_replaying_the_same_drafts_appends_nothing(tmp_path: Path) -> None:
    """A re-run of a write is a no-op, and the return value says so."""
    first = _build(tmp_path)
    before = (tmp_path / events.INITIAL_LOG_NAME).read_bytes()

    again = _build(tmp_path)

    assert first != []
    assert again == []
    assert (tmp_path / events.INITIAL_LOG_NAME).read_bytes() == before


def test_a_repeated_status_needs_a_generation_or_it_is_swallowed(tmp_path: Path) -> None:
    """The documented caller trap, asserted in both directions.

    A ``done → open`` reopen repeats the content of the first ``open``, so without
    ``generation=2`` its id is that event's id and the append skips it as a replay. The
    module says so; this is the test that makes the claim checkable — and the reason the
    fixture carries a generation at all.
    """
    events.append(
        tmp_path,
        [
            events.Draft(RECORD_A, "created", {"title": "x"}),
            events.Draft(RECORD_A, "status", {"status": "open"}),
            events.Draft(RECORD_A, "status", {"status": "done"}),
        ],
        clock=lambda: CLOCK_EARLY,
    )

    swallowed = events.append(
        tmp_path, [events.Draft(RECORD_A, "status", {"status": "open"})], clock=lambda: CLOCK_EARLY
    )
    landed = events.append(
        tmp_path,
        [events.Draft(RECORD_A, "status", {"status": "open"}, generation=2)],
        clock=lambda: CLOCK_EARLY,
    )

    assert swallowed == []
    assert [event.seq for event in landed] == [4]
    folded, _ = events.read_events(tmp_path)
    assert events.fold(folded).records[RECORD_A].status == "open"


# --- AC2: no branch depends on a wall clock ----------------------------------


def test_two_injected_clocks_produce_identical_ledgers_apart_from_the_timestamp(
    tmp_path: Path,
) -> None:
    """The acceptance criterion, as one comparison over every field.

    Ids, sequence numbers and carried totals are all in the compared object, so a clock
    reaching any derived value fails here — and the ``ts`` values are asserted to actually
    differ, or the whole test could pass because the injection did nothing.
    """
    early, late = tmp_path / "early", tmp_path / "late"
    _build(early, clock=CLOCK_EARLY)
    _build(late, clock=CLOCK_LATE)

    early_events, _ = events.read_events(early)
    late_events, _ = events.read_events(late)
    stripped = [
        [json.loads(events.to_json(event)) for event in run] for run in (early_events, late_events)
    ]
    timestamps = [{line.pop("ts") for line in run} for run in stripped]

    assert stripped[0] == stripped[1]
    assert _state(events.fold(early_events)) == _state(events.fold(late_events))
    assert timestamps[0] != timestamps[1]
    assert timestamps == [{"2001-09-09T01:46:40Z"}, {"2027-01-15T08:00:00Z"}]


def test_an_event_id_is_derived_without_the_timestamp(tmp_path: Path) -> None:
    """Stated separately because it is the mechanism the criterion rests on.

    If ``ts`` were in the digest, the same logical event on two branches would carry two
    ids, union-merge dedup would stop working, and the sequence tie-break would order by a
    clock through the back door.
    """
    del tmp_path
    payload = {"status": "open"}
    assert events.event_id_for(RECORD_A, "status", payload) == events.event_id_for(
        RECORD_A, "status", dict(payload)
    )
    assert events.event_id_for(RECORD_A, "status", payload) != events.event_id_for(
        RECORD_A, "status", {"status": "done"}
    )


def test_the_recorded_timestamp_is_exactly_what_the_injected_clock_said(
    tmp_path: Path,
) -> None:
    """The clock seam is the only source of ``ts``, so nothing else can be writing it."""
    readings = iter([CLOCK_EARLY, CLOCK_EARLY + 1.5, CLOCK_EARLY + 90.25])
    minted = events.append(
        tmp_path,
        [
            events.Draft(RECORD_A, "created", {"title": "x"}),
            events.Draft(RECORD_A, "comment", {"text": "y"}),
            events.Draft(RECORD_B, "created", {"title": "z"}),
        ],
        clock=lambda: next(readings),
    )

    assert [event.ts for event in minted] == [
        "2001-09-09T01:46:40Z",
        "2001-09-09T01:46:41.500000Z",
        "2001-09-09T01:48:10.250000Z",
    ]


# The source-level half of AC2. A behavioural test only covers the paths it drives, so
# this pins where the clock comes from across the whole kit tracker tree — the shape
# `tests/test_runner.py` uses for the engine (basicly-jr0l.5). Matched on the parsed tree
# rather than on the text, because a docstring naming `time.time` is documentation and a
# grep cannot tell the two apart — the first draft of this test failed on its own prose.
WALL_CLOCK_ATTRIBUTES = frozenset({"time.time", "time.time_ns", "datetime.now", "datetime.utcnow"})

# `(enclosing function, attribute)` for every permitted read. One entry: `append`'s
# default when no clock is injected. A seam that moves has to be re-stated here.
PERMITTED_CLOCK_READS = [("append", "time.time")]


def _dotted(node: ast.expr) -> str:
    """``a.b.c`` for an attribute chain, or ``""`` for anything else."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return ""
    parts.append(current.id)
    return ".".join(reversed(parts))


def test_no_wall_clock_is_read_outside_the_one_injected_default() -> None:
    """``time.time`` appears once in the kit tracker, as a default a caller can replace.

    Also bans ``.total_seconds()`` and ``datetime.now``: subtracting two wall-clock
    readings is the other way a duration lands on a clock that can step backwards. The
    monotonic clock the lock uses is deliberately *not* banned — §9.5's rule is that
    durations are measured on a monotonic clock, and forbidding it would forbid the remedy.
    """
    found: list[tuple[str, str]] = []
    for path in sorted(KIT_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owners: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                    owners[line] = node.name
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            dotted = _dotted(node)
            if dotted in WALL_CLOCK_ATTRIBUTES or node.attr == "total_seconds":
                found.append((owners.get(node.lineno, f"{path.name}:module"), dotted or node.attr))
    assert sorted(found) == sorted(PERMITTED_CLOCK_READS), (
        f"wall-clock read(s) in the tracker kit: {sorted(found)}"
    )


def test_the_lock_measures_its_staleness_on_a_monotonic_clock(tmp_path: Path) -> None:
    """The one duration this module takes, and where it takes it from.

    Asserted through behaviour rather than by reading the source: the injected monotonic
    clock is the only thing that moves, and the lock has to expire on it.
    """
    clock = _FakeClock()
    holder = events.LedgerLock(tmp_path, monotonic=clock.monotonic, sleep=clock.sleep, pid=4242)
    holder.acquire()

    clock.now += events.LOCK_STALE_AFTER_S + 1.0
    taker = events.LedgerLock(
        tmp_path,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        pid=4243,
        is_alive=lambda _pid: True,
    )
    taker.acquire()

    assert taker.held
    assert taker.steals == 1


# --- the one accumulator ------------------------------------------------------


def test_the_carried_totals_agree_with_the_folds_own_recomputation(tmp_path: Path) -> None:
    """The writer stamped what the fold recomputes, on every event, with no exception.

    This is the check `fsck` will run (§4.6). An empty ``mismatched_totals`` is only
    meaningful if the check can fail, so a tampered line is spliced in as the positive
    control.
    """
    _build(tmp_path)
    original, _ = events.read_events(tmp_path)

    assert events.fold(original).mismatched_totals == []

    tampered = events.Event(
        id=original[2].id,
        record=original[2].record,
        seq=original[2].seq,
        kind=original[2].kind,
        actor=original[2].actor,
        ts=original[2].ts,
        payload=original[2].payload,
        totals=events.Totals(events=99, attempts=0, spend_micros=0, status="open"),
    )
    replaced = [tampered if event.id == tampered.id else event for event in original]
    assert events.fold(replaced).mismatched_totals == [tampered.id]


def test_spend_is_summed_as_integer_micro_units(tmp_path: Path) -> None:
    """Counts and sums only, and the sum is exact — a float would be order-dependent."""
    minted = _build(tmp_path)

    last_a = [event for event in minted if event.record == RECORD_A][-1]
    assert last_a.totals.spend_micros == 1_650_000
    assert last_a.totals.attempts == 2
    assert last_a.totals.events == 7

    with pytest.raises(events.InvalidEventError, match="integer number of micro-units"):
        events.append(
            tmp_path,
            [events.Draft(RECORD_A, "dispatch", {"spend_micros": 1.65})],
            clock=lambda: CLOCK_EARLY,
        )


def test_an_unknown_kind_is_counted_in_the_totals_and_skipped_by_the_fold(
    tmp_path: Path,
) -> None:
    """An old reader must not report every later event as a false disagreement.

    A newer writer counted its own kind toward ``events``, so a fold that skipped it
    entirely would recompute a lower count and flag the whole tail. The unknown kind is
    reported as a warning and changes no state.
    """
    _build(tmp_path)
    events.append(
        tmp_path,
        [events.Draft(RECORD_C, "seismograph_reading", {"magnitude": 4})],
        clock=lambda: CLOCK_EARLY,
    )

    stored, _ = events.read_events(tmp_path)
    result = events.fold(stored)

    assert result.unknown_kinds == {"seismograph_reading": 1}
    assert result.mismatched_totals == []
    assert result.records[RECORD_C].totals.events == 3
    assert result.records[RECORD_C].fields == {"title": "a child", "priority": 2}


def test_a_known_kind_with_an_unusable_payload_is_refused_not_guessed_at(
    tmp_path: Path,
) -> None:
    """Tolerance is for kinds we do not know, not for ones we do."""
    with pytest.raises(events.InvalidEventError, match="string status"):
        events.append(
            tmp_path, [events.Draft(RECORD_A, "status", {"state": "open"})], clock=lambda: 0.0
        )
    with pytest.raises(events.InvalidEventError, match="string name"):
        events.append(tmp_path, [events.Draft(RECORD_A, "field", {"value": 1})], clock=lambda: 0.0)


# --- forward compatibility ----------------------------------------------------


def test_an_unknown_field_survives_a_round_trip_byte_for_byte(tmp_path: Path) -> None:
    """The upgradability property: an old reader rewriting a newer line loses nothing."""
    line = json.dumps(
        {
            "id": f"{RECORD_A}#ev-0123456789",
            "record": RECORD_A,
            "seq": 1,
            "kind": "created",
            "actor": "lane:future",
            "ts": "2030-01-01T00:00:00Z",
            "payload": {"title": "from the future"},
            "totals": {"events": 1, "attempts": 0, "spend_micros": 0, "status": None},
            "provenance": "EXTRACTED",
            "signature": {"alg": "none"},
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    log = tmp_path / events.INITIAL_LOG_NAME
    log.write_text(line + "\n", encoding="utf-8")

    parsed, quarantined = events.read_events(tmp_path)

    assert quarantined == []
    assert parsed[0].extra == {"provenance": "EXTRACTED", "signature": {"alg": "none"}}
    assert events.to_json(parsed[0]) == line


def test_an_unknown_field_can_never_shadow_a_known_one(tmp_path: Path) -> None:
    """Preserved verbatim is not the same as trusted: ``extra`` is written first."""
    del tmp_path
    event = events.Event(
        id=f"{RECORD_A}#ev-0123456789",
        record=RECORD_A,
        seq=7,
        kind="created",
        actor="lane:x",
        ts="2030-01-01T00:00:00Z",
        extra={"seq": 999, "id": "not-an-id"},
    )

    written = json.loads(events.to_json(event))

    assert written["seq"] == 7
    assert written["id"] == f"{RECORD_A}#ev-0123456789"


# --- resilience: torn lines and interior garbage ------------------------------


def test_a_torn_trailing_line_is_tolerated_and_the_fold_before_it_is_intact(
    tmp_path: Path,
) -> None:
    """The crash signature: a partial line with no newline after it."""
    _build(tmp_path)
    log = tmp_path / events.INITIAL_LOG_NAME
    with log.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write('{"id":"basicly-aa11#ev-abcdef0123","record":"basicly-aa11","se')

    parsed, quarantined = events.read_events(tmp_path)

    assert quarantined == []
    assert len(parsed) == len(_lifecycle())
    assert events.fold(parsed).records[RECORD_A].status == "open"


def test_interior_garbage_is_quarantined_by_line_number_and_never_edited(
    tmp_path: Path,
) -> None:
    """`fsck` repairs only by appending; a reader that edited a line would end that."""
    _build(tmp_path)
    log = tmp_path / events.INITIAL_LOG_NAME
    lines = log.read_text(encoding="utf-8").splitlines()
    lines.insert(2, "{ this was never JSON }")
    corrupted = "\n".join(lines) + "\n"
    log.write_text(corrupted, encoding="utf-8")

    parsed, quarantined = events.read_events(tmp_path)

    assert [item.line_number for item in quarantined] == [3]
    assert quarantined[0].line == "{ this was never JSON }"
    assert quarantined[0].path == log
    assert len(parsed) == len(_lifecycle())
    assert log.read_text(encoding="utf-8") == corrupted


def test_a_complete_but_unparseable_last_line_is_quarantined_rather_than_forgiven(
    tmp_path: Path,
) -> None:
    """Only a *torn* tail is silent, and torn means the newline never landed.

    Without this distinction the tolerance would swallow the last line of every corrupt
    log, which is the one place a silent loss is indistinguishable from a crash.
    """
    _build(tmp_path)
    log = tmp_path / events.INITIAL_LOG_NAME
    with log.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write("{ complete, and still not an event }\n")

    _, quarantined = events.read_events(tmp_path)

    assert [item.line for item in quarantined] == ["{ complete, and still not an event }"]


def test_an_append_after_a_torn_line_starts_a_new_line(tmp_path: Path) -> None:
    """The torn-line guard: without it a good event concatenates onto a partial one."""
    _build(tmp_path)
    log = tmp_path / events.INITIAL_LOG_NAME
    with log.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write('{"id":"basicly-aa11#ev-abcdef0123","reco')

    events.append(
        tmp_path,
        [events.Draft(RECORD_B, "comment", {"text": "after the tear"})],
        clock=lambda: CLOCK_EARLY,
    )

    parsed, quarantined = events.read_events(tmp_path)
    assert [item.line for item in quarantined] == ['{"id":"basicly-aa11#ev-abcdef0123","reco']
    assert parsed[-1].payload["text"] == "after the tear"
    assert len(parsed) == len(_lifecycle()) + 1


def test_every_line_is_utf8_with_a_unix_ending_whatever_the_host_prefers(
    tmp_path: Path,
) -> None:
    r"""``encoding="utf-8"`` and ``newline="\n"`` on every open, asserted on the bytes.

    Python 3.14's default encoding is still locale-dependent, so an unmarked open on a
    cp1252 host corrupts on the first non-ASCII comment. The ``\r`` assertion is what
    ``newline="\n"`` buys on Windows, and it is a fact about bytes — so it is checkable
    from any platform rather than only from that one.
    """
    events.append(
        tmp_path,
        [events.Draft(RECORD_A, "comment", {"text": "sequência — ordenação · 順序"})],
        clock=lambda: CLOCK_EARLY,
    )

    raw = (tmp_path / events.INITIAL_LOG_NAME).read_bytes()

    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    assert "順序" in raw.decode("utf-8")


# --- the size cap and the redaction ordering ----------------------------------


def test_the_cap_cuts_free_text_on_a_character_boundary_and_says_how_much(
    tmp_path: Path,
) -> None:
    """A byte-sliced UTF-8 payload would take the whole line down with it."""
    # A 3-byte character straddling the cap, so a byte slice would leave a partial one.
    text = "a" * (events.MAX_TEXT_BYTES - 1) + "順" + "b" * 50
    minted = events.append(
        tmp_path,
        [events.Draft(RECORD_A, "comment", {"text": text})],
        clock=lambda: CLOCK_EARLY,
    )

    payload = minted[0].payload
    assert payload["text"] == "a" * (events.MAX_TEXT_BYTES - 1)
    assert payload["text_truncated"] is True
    assert payload["text_original_length_bytes"] == len(text.encode("utf-8"))
    # It survives the round trip, which is the property a byte slice destroys.
    reread, quarantined = events.read_events(tmp_path)
    assert quarantined == []
    assert reread[0].payload["text"] == payload["text"]


def test_the_cap_truncates_and_never_refuses(tmp_path: Path) -> None:
    """Refusing would lose the fact that a gate ran, along with its output."""
    minted = events.append(
        tmp_path,
        [events.Draft(RECORD_A, "comment", {"text": "z" * (events.MAX_TEXT_BYTES * 4)})],
        clock=lambda: CLOCK_EARLY,
    )

    assert len(minted) == 1
    assert minted[0].payload["text_truncated"] is True


def test_redaction_runs_before_the_cap_and_the_length_is_the_redacted_one(
    tmp_path: Path,
) -> None:
    """Redaction can lengthen text, so the order changes both the cut and the number.

    The redactor here lengthens deliberately: if the cap ran first, the reported original
    length would be the *raw* length and the cut would have happened before the pattern
    could match — which is the failure §4.2 names, a cut through the middle of a secret
    defeating the pattern that would have caught it.
    """
    secret = "TOKEN"
    raw = secret * 200
    minted = events.append(
        tmp_path,
        [events.Draft(RECORD_A, "comment", {"text": raw})],
        clock=lambda: CLOCK_EARLY,
        redact=lambda text: text.replace(secret, "[redacted-credential]"),
        max_text_bytes=64,
    )

    payload = minted[0].payload
    assert secret not in str(payload["text"])
    assert payload["text"].startswith("[redacted-credential]")  # type: ignore[union-attr]
    assert payload["text_original_length_bytes"] == len(
        raw.replace(secret, "[redacted-credential]").encode("utf-8")
    )
    assert payload["text_original_length_bytes"] > len(raw.encode("utf-8"))


def test_redaction_reaches_a_string_nested_under_any_key(tmp_path: Path) -> None:
    """A leak is permanent here, so redaction covers the whole payload, not the cap's keys."""
    minted = events.append(
        tmp_path,
        [
            events.Draft(
                RECORD_A,
                "dispatch",
                {"env": {"paths": ["/home/someone/repo", "relative/ok"]}, "note": "/home/someone"},
            )
        ],
        clock=lambda: CLOCK_EARLY,
        redact=lambda text: text.replace("/home/someone", "<home>"),
    )

    payload = minted[0].payload
    assert payload["env"] == {"paths": ["<home>/repo", "relative/ok"]}
    assert payload["note"] == "<home>"


def test_a_capped_key_holding_a_container_is_refused_by_the_schema(tmp_path: Path) -> None:
    """The markers say how much was cut from one field; a list has nowhere honest to put them."""
    with pytest.raises(events.InvalidEventError, match="capped free text"):
        events.append(
            tmp_path,
            [events.Draft(RECORD_A, "comment", {"text": ["a", "b"]})],
            clock=lambda: CLOCK_EARLY,
        )


def test_a_structural_field_is_never_truncated(tmp_path: Path) -> None:
    """Cutting a field the fold reads would make a derived value depend on the cap."""
    long_status = "waiting_on_" + "x" * (events.MAX_TEXT_BYTES * 2)
    minted = events.append(
        tmp_path,
        [events.Draft(RECORD_A, "status", {"status": long_status})],
        clock=lambda: CLOCK_EARLY,
    )

    assert minted[0].payload["status"] == long_status
    assert minted[0].totals.status == long_status
    assert "status_truncated" not in minted[0].payload


# --- the bound is the kind's, not the key's spelling (basicly-vbl35a) ---------

# The size `basicly-wpc8`'s description was cut from, on a `field` event, because `value` was on
# the allow-list while `_apply_field` folds it into the record's fields. Every case below is sized
# from the record that paid rather than from a round number.
_WPC8_DESCRIPTION_BYTES = 4461
_LONG_BODY = "d" * _WPC8_DESCRIPTION_BYTES


@pytest.mark.parametrize(
    ("kind", "payload", "key", "folded"),
    [
        (
            "field",
            {"name": "description", "value": _LONG_BODY},
            "value",
            lambda state: state.fields["description"],
        ),
        (
            "created",
            {"description": _LONG_BODY},
            "description",
            lambda state: state.fields["description"],
        ),
        (
            "artifact",
            {"artifact": "change-summary", "body": _LONG_BODY},
            "body",
            lambda state: state.artifacts["change-summary"],
        ),
        (
            "checkpoint",
            {"checkpoint": "ship", "approved_by": _LONG_BODY},
            "approved_by",
            lambda state: state.checkpoints["ship"],
        ),
    ],
    ids=("field-value", "created-description", "artifact-body", "checkpoint-approved-by"),
)
def test_the_cap_never_cuts_a_payload_key_the_fold_reads(
    tmp_path: Path, kind: str, payload: dict[str, Any], key: str, folded: Any
) -> None:
    """Cutting one of these would make a derived value depend on the cap (§4.2's first rule).

    Both routes to the fold are here because they are exempt for different reasons: `value`, `body`
    and `approved_by` are named in ``events.FOLD_READ_KEYS``, and `description` is exempt because
    `created` declares no bound at all — `_apply_created` folds **every** key it carries into the
    record's fields. The stored payload and the folded state are both asserted, because an
    exemption that only reached the payload would still leave the fold reading a fragment.
    """
    minted = events.append(
        tmp_path, [events.Draft(RECORD_A, kind, payload)], clock=lambda: CLOCK_EARLY
    )

    stored = minted[0].payload
    assert stored[key] == _LONG_BODY
    assert f"{key}_truncated" not in stored
    assert f"{key}_original_length_bytes" not in stored
    state = events.fold(events.read_events(tmp_path)[0]).records[RECORD_A]
    assert folded(state) == _LONG_BODY


def test_a_payload_key_outside_the_allow_list_takes_the_bound_its_kind_declares(
    tmp_path: Path,
) -> None:
    """``summary`` was stored unbounded before this, and by nobody's decision.

    Membership of ``events.TRUNCATABLE_KEYS`` used to be the whole test of whether a key was cut,
    so a key it did not name was exempt by the spelling its author picked. The kind's declaration
    is what cuts it now, and the injected ceiling still tightens that — the bound applied is the
    tighter of the two, so a caller with a smaller budget is not overridden by the table.
    """
    summary = "s" * (events.MAX_TEXT_BYTES * 2)
    draft = events.Draft(RECORD_A, "note", {"summary": summary})

    minted = events.append(tmp_path, [draft], clock=lambda: CLOCK_EARLY)
    tighter = events.append(
        tmp_path / "tighter", [draft], clock=lambda: CLOCK_EARLY, max_text_bytes=64
    )

    assert "summary" not in events.TRUNCATABLE_KEYS
    assert events.KIND_TEXT_BYTES["note"] == events.MAX_TEXT_BYTES
    stored = minted[0].payload
    assert stored["summary"] == "s" * events.MAX_TEXT_BYTES
    assert stored["summary_truncated"] is True
    assert stored["summary_original_length_bytes"] == len(summary.encode("utf-8"))
    assert tighter[0].payload["summary"] == "s" * 64


def test_a_kind_that_declares_no_bound_is_refused_rather_than_stored_unbounded(
    tmp_path: Path,
) -> None:
    """A kind nobody chose a bound for may not put an unbounded body in every clone forever.

    Three assertions, and the control is the point of the second: the same undeclared kind carrying
    a small payload is still written, because `fold` reads a kind this version does not know (§4.5)
    and a writer that refused every one of them would be narrower than its own reader. The nested
    case is there because a handoff artifact's body is an object rather than a string, so a refusal
    that stopped at the top level would let exactly the payload D-36 describes through.
    """
    oversized = "m" * (events.MAX_TEXT_BYTES + 1)
    assert "seismograph_reading" not in events.KIND_TEXT_BYTES

    for payload in ({"reading": oversized}, {"reading": {"trace": oversized}}):
        with pytest.raises(events.InvalidEventError, match="declares no free-text bound"):
            events.append(
                tmp_path,
                [events.Draft(RECORD_A, "seismograph_reading", payload)],
                clock=lambda: CLOCK_EARLY,
            )
    assert not (tmp_path / events.INITIAL_LOG_NAME).exists()

    minted = events.append(
        tmp_path,
        [events.Draft(RECORD_A, "seismograph_reading", {"magnitude": 4})],
        clock=lambda: CLOCK_EARLY,
    )

    assert [event.kind for event in minted] == ["seismograph_reading"]


def _stored_line(seq: int, kind: str, payload: dict[str, Any]) -> str:
    """One log line as the ledger already holds it — a fixture the writer can no longer produce."""
    return json.dumps(
        {
            "id": f"{RECORD_A}#ev-{seq:010d}",
            "record": RECORD_A,
            "seq": seq,
            "kind": kind,
            "actor": "",
            "ts": "2026-08-17T12:01:54.313239Z",
            "payload": payload,
            "totals": {"events": seq, "attempts": 0, "spend_micros": 0, "status": None},
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def test_an_event_already_carrying_a_truncation_flag_folds_as_it_always_did(tmp_path: Path) -> None:
    """The log is never rewritten, so a cut that already happened is permanent (§4.2).

    The fixture is `basicly-wpc8`'s own stored shape — a `field` event whose `value` was cut to the
    cap from 4461 bytes, with both markers beside it — and one of the 48 `text_truncated` comments.
    The fold has to derive the cut text: not the whole text, which is gone, and not a repair. The
    markers stay out of the record's fields, which is what a fold that tried to interpret them
    rather than carry them would break.
    """
    cut = "d" * events.MAX_TEXT_BYTES
    lines = [
        _stored_line(
            1,
            "field",
            {
                "name": "description",
                "provenance": "dual-write",
                "value": cut,
                "value_original_length_bytes": _WPC8_DESCRIPTION_BYTES,
                "value_truncated": True,
            },
        ),
        _stored_line(
            2,
            "comment",
            {"text": cut, "text_original_length_bytes": 9000, "text_truncated": True},
        ),
    ]
    (tmp_path / events.INITIAL_LOG_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")

    parsed, quarantined = events.read_events(tmp_path)
    state = events.fold(parsed).records[RECORD_A]

    assert quarantined == []
    assert state.fields == {"description": cut}
    assert state.comments == [cut]
    assert parsed[0].payload["value_original_length_bytes"] == _WPC8_DESCRIPTION_BYTES


# --- attribution (basicly-at5tph) ---------------------------------------------


def test_no_appended_event_can_carry_an_empty_actor(tmp_path: Path) -> None:
    """The chain is the draft's actor, then the call's, then the reason — never ``""``.

    All three in one test because the chain is the property: a fallback that shadowed an
    explicit actor would satisfy the last assertion alone, and one that only defaulted the
    call would satisfy the first two.
    """
    minted = events.append(
        tmp_path,
        [
            events.Draft(RECORD_A, "created", {"title": "a"}, actor="lane:one"),
            events.Draft(RECORD_B, "created", {"title": "b"}),
        ],
        actor="lane:two",
        clock=lambda: CLOCK_EARLY,
    )
    assert [event.actor for event in minted] == ["lane:one", "lane:two"]

    bare = events.append(
        tmp_path, [events.Draft(RECORD_C, "created", {"title": "c"})], clock=lambda: CLOCK_EARLY
    )
    assert [event.actor for event in bare] == [events.UNATTRIBUTED_ACTOR]

    # Read back off the file, not off the return value: the field has to survive the JSON.
    stored, _ = events.read_events(tmp_path)
    assert len(stored) == 3
    assert all(event.actor for event in stored)


# --- the writer's lock --------------------------------------------------------


def test_a_live_holder_makes_contention_a_retryable_failure(tmp_path: Path) -> None:
    """R8: contention waits, and a wait that gives up says so.

    ``retryable`` is asserted on the exception because that is what routes the failure to
    a back-off instead of spending a lane's rework budget (basicly-vkh0.10).
    """
    clock = _FakeClock()
    holder = events.LedgerLock(tmp_path, monotonic=clock.monotonic, sleep=clock.sleep, pid=101)
    holder.acquire()

    with pytest.raises(events.LockUnavailableError) as caught:
        events.LedgerLock(
            tmp_path,
            timeout_s=0.2,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            pid=102,
            is_alive=lambda _pid: True,
        ).acquire()

    assert caught.value.retryable is True
    assert events.LockUnavailableError.retryable is True
    assert clock.slept  # it waited rather than failing on the first look
    assert holder.held


def test_an_append_reports_contention_rather_than_writing_unlocked(tmp_path: Path) -> None:
    """The lock is on the write path, not beside it.

    The holder takes the lock on the **real** clocks, because that is what the append's own
    lock will read: a holder stamped from a fake monotonic looks arbitrarily old to a
    default-constructed taker and would be stolen rather than respected. The holder's pid
    is this process, so the liveness rule keeps it too.
    """
    events.LedgerLock(tmp_path).acquire()

    with pytest.raises(events.LockUnavailableError):
        events.append(
            tmp_path,
            [events.Draft(RECORD_A, "created", {"title": "x"})],
            clock=lambda: CLOCK_EARLY,
            lock_timeout_s=0.0,
        )

    assert events.log_paths(tmp_path) == []


def test_a_held_lock_lets_a_caller_wrap_a_wider_critical_section(tmp_path: Path) -> None:
    """§4.5's read-check-write: two CLI calls would let two lanes take the same item."""
    with events.LedgerLock(tmp_path) as lock:
        existing, _ = events.read_events(tmp_path)
        assert events.fold(existing).records.get(RECORD_A) is None
        minted = events.append(
            tmp_path,
            [events.Draft(RECORD_A, "status", {"status": "in_progress"}, actor="lane:one")],
            clock=lambda: CLOCK_EARLY,
            held_lock=lock,
        )
        assert lock.held

    assert [event.actor for event in minted] == ["lane:one"]
    assert not (tmp_path / events.LOCK_NAME).exists()


@pytest.mark.parametrize(
    ("liveness", "stolen"),
    [
        pytest.param(False, True, id="known-dead-is-stolen"),
        pytest.param(True, False, id="known-alive-is-respected"),
        pytest.param(None, False, id="unknown-defers-to-the-age-rule"),
    ],
)
def test_the_steal_rule_follows_the_platforms_liveness_answer(
    tmp_path: Path, liveness: bool | None, stolen: bool
) -> None:
    """All three answers, including the ``None`` only Windows produces, as test data.

    Racing a real process would assert the host's scheduler, and the ``None`` branch could
    not be reached from Linux at all — so the platform's answer is injected and this runs
    everywhere (the repo's platform-hermetic rule).
    """
    clock = _FakeClock()
    events.LedgerLock(tmp_path, monotonic=clock.monotonic, sleep=clock.sleep, pid=999).acquire()
    taker = events.LedgerLock(
        tmp_path,
        timeout_s=0.05,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        pid=1000,
        is_alive=lambda _pid: liveness,
    )

    if stolen:
        assert taker.acquire().held
        assert taker.steals == 1
    else:
        with pytest.raises(events.LockUnavailableError):
            taker.acquire()
        assert taker.steals == 0


def test_a_lock_stamped_before_a_reboot_is_stolen(tmp_path: Path) -> None:
    """A negative age means the stamp came from another monotonic epoch.

    Monotonic clocks share no origin across a reboot, so a surviving lock file can carry a
    reading larger than the current one. Treating that as "not yet stale" would wedge the
    ledger until a human deleted the file.
    """
    clock = _FakeClock(start=50_000.0)
    events.LedgerLock(tmp_path, monotonic=clock.monotonic, sleep=clock.sleep, pid=7).acquire()
    rebooted = _FakeClock(start=12.0)

    taker = events.LedgerLock(
        tmp_path,
        monotonic=rebooted.monotonic,
        sleep=rebooted.sleep,
        pid=8,
        is_alive=lambda _pid: True,
    )

    assert taker.acquire().held
    assert taker.steals == 1


def test_a_lock_nobody_can_parse_is_stolen_rather_than_respected(tmp_path: Path) -> None:
    """An unreadable lock is not a holder; respecting it would wedge writes indefinitely."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / events.LOCK_NAME).write_text("half a json obj", encoding="utf-8")

    minted = events.append(
        tmp_path, [events.Draft(RECORD_A, "created", {"title": "x"})], clock=lambda: CLOCK_EARLY
    )

    assert [event.seq for event in minted] == [1]


def test_releasing_never_removes_a_lock_that_was_stolen_from_us(tmp_path: Path) -> None:
    """Our hold outran the stale bound and somebody took it; unlinking would let a third in."""
    clock = _FakeClock()
    ours = events.LedgerLock(tmp_path, monotonic=clock.monotonic, sleep=clock.sleep, pid=11)
    ours.acquire()
    clock.now += events.LOCK_STALE_AFTER_S + 1.0
    thief = events.LedgerLock(
        tmp_path,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        pid=12,
        is_alive=lambda _pid: True,
    )
    thief.acquire()

    ours.release()

    assert (tmp_path / events.LOCK_NAME).exists()
    assert json.loads((tmp_path / events.LOCK_NAME).read_text(encoding="utf-8"))["pid"] == 12


def test_the_default_liveness_probe_never_signals_a_pid_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Windows ``os.kill(pid, 0)`` calls ``TerminateProcess``, so it must not be reached.

    ``os.name`` is the only way to reach that branch and there is no seam to inject, so it
    is patched for the length of one call — and ``os.kill`` is replaced with a failure, so
    a future edit that dropped the platform check fails here instead of killing a process
    on a contributor's machine.
    """
    monkeypatch.setattr(events.os, "kill", lambda *_: pytest.fail("os.kill reached on Windows"))
    monkeypatch.setattr(events.os, "name", "nt")

    assert events.default_pid_liveness(4242) is None


def test_the_default_liveness_probe_answers_for_this_process_on_posix() -> None:
    """The positive control for the injected answers: a real pid, answered for real."""
    if os.name == "nt":
        pytest.skip("the POSIX probe cannot exist here; the nt branch is covered above")
    assert events.default_pid_liveness(os.getpid()) is True


# --- the ledger's shape -------------------------------------------------------


def test_the_log_glob_is_the_contract_rebuild_and_fsck_will_share() -> None:
    """A narrowed glob would silently drop rotated history from every fold."""
    assert events.LOG_GLOB == "events-*.jsonl"
    assert re.fullmatch(r"events-\d+\.jsonl", events.INITIAL_LOG_NAME)


def test_a_rotated_log_is_read_and_becomes_the_append_target(tmp_path: Path) -> None:
    """Rotation is basicly-vkh0.14's and needs no cooperation from the writer.

    A period file simply sorts last, which is why choosing one here — a wall-clock branch
    on *which year is it* — is not the writer's job.
    """
    _build(tmp_path)
    rotated = tmp_path / "events-2027.jsonl"
    rotated.write_text("", encoding="utf-8")

    assert events.append_target(tmp_path) == rotated
    minted = events.append(
        tmp_path,
        [events.Draft(RECORD_B, "comment", {"text": "next year"})],
        clock=lambda: CLOCK_LATE,
    )

    assert minted[0].seq == 4  # the sequence continues across files
    assert rotated.read_text(encoding="utf-8").count("\n") == 1
    parsed, _ = events.read_events(tmp_path)
    assert len(parsed) == len(_lifecycle()) + 1


def test_a_record_id_and_a_kind_are_validated_before_anything_is_written(
    tmp_path: Path,
) -> None:
    """A slug-shaped id broke our own commit gate; a free-text kind splits one meaning."""
    with pytest.raises(Exception, match="not a record id"):
        events.append(
            tmp_path, [events.Draft("basicly-fix-the-thing", "created", {})], clock=lambda: 0.0
        )
    with pytest.raises(events.InvalidEventError, match="must match"):
        events.append(tmp_path, [events.Draft(RECORD_A, "Status", {})], clock=lambda: 0.0)
    assert events.log_paths(tmp_path) == []


def test_a_tombstoned_record_stays_in_the_fold(tmp_path: Path) -> None:
    """A delete leaves a tombstone, or a later mint hands its id to a new record."""
    _build(tmp_path)
    events.append(tmp_path, [events.Draft(RECORD_C, "tombstone", {})], clock=lambda: CLOCK_EARLY)

    stored, _ = events.read_events(tmp_path)
    state = events.fold(stored).records[RECORD_C]

    assert state.tombstoned is True
    assert state.fields["title"] == "a child"
    assert state.totals.events == 3


def test_appending_nothing_writes_nothing(tmp_path: Path) -> None:
    """An empty batch takes no lock and creates no file — the loop calls this on quiet passes."""
    assert events.append(tmp_path, [], clock=lambda: CLOCK_EARLY) == []
    assert not tmp_path.exists() or events.log_paths(tmp_path) == []


# --- the no-basicly proof, in a subprocess ------------------------------------

# The subprocess asserts the kit constraint itself before appending anything: an
# environment that quietly still had basicly in it would make this whole section vacuous.
_DRIVER = """
import importlib.util
import json
import shutil
import sys
from pathlib import Path

assert importlib.util.find_spec("basicly") is None, "basicly is importable"
assert shutil.which("basicly") is None, "basicly is on PATH"

spec = importlib.util.spec_from_file_location("tracker_events", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules["tracker_events"] = module
spec.loader.exec_module(module)

ledger = Path(sys.argv[2])
module.append(
    ledger,
    [
        module.Draft("consumer-zz99", "created", {"title": "theirs"}),
        module.Draft("consumer-zz99", "status", {"status": "open"}),
        module.Draft("consumer-zz99", "dispatch", {"spend_micros": 7}),
    ],
    actor="their-lane",
    clock=lambda: 1_000_000_000.0,
)
found, quarantined = module.read_events(ledger)
assert quarantined == [], quarantined
state = module.fold(found).records["consumer-zz99"]
print(json.dumps({"status": state.status, "totals": state.totals.as_dict()}))
"""


def _pruned_env(tmp_path: Path) -> dict[str, str]:
    """An environment with no basicly on PATH and nothing pointing at this repo.

    Built from empty rather than filtered, so nothing inherited can smuggle the package
    back in — no ``PYTHONPATH``, no ``VIRTUAL_ENV``. The few names copied back are what an
    interpreter needs on its own platform, which makes the platform difference test data.
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


def test_the_log_is_written_and_folded_with_no_basicly_importable(tmp_path: Path) -> None:
    """The kit's hard constraint, exercised the way a consumer would exercise it.

    ``-S`` drops site-packages, which is where this repo's own ``basicly`` lives, and
    ``-I`` drops ``PYTHONPATH``, the user site directory and the script's own directory.
    ``ids.py`` is copied alongside because the kit's sibling loader is part of what is
    being proved: a consumer copies the directory, not one file.
    """
    consumer = tmp_path / "consumer" / "kit" / "tracker"
    consumer.mkdir(parents=True)
    shutil.copy2(EVENTS_SOURCE, consumer / EVENTS_SOURCE.name)
    shutil.copy2(IDS_SOURCE, consumer / IDS_SOURCE.name)
    driver = tmp_path / "drive.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    ledger = tmp_path / "their-ledger"

    result = subprocess.run(
        [sys.executable, "-S", "-I", str(driver), str(consumer / "events.py"), str(ledger)],
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
    }
    assert (ledger / events.INITIAL_LOG_NAME).exists()


def test_the_module_imports_nothing_outside_the_standard_library() -> None:
    """The kit boundary, read off the source rather than trusted.

    Only ``ids.py`` is loaded from beside it, and that happens by path with no
    ``sys.path`` mutation — a library that reordered a consumer's import path could
    shadow a module they own.
    """
    source = EVENTS_SOURCE.read_text(encoding="utf-8")
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
        "datetime",
        "importlib.util",
        "json",
        "os",
        "pathlib",
        "re",
        "sys",
        "time",
        "types",
    }
    assert "sys.path.insert" not in source


# --- the closed set of kinds --------------------------------------------------

# The kit modules that still spell a kind literal rather than aliasing `events.KIND_*`, and why
# that is not drift: `baseline.py` loads no sibling at all, so it has nothing to alias from. An
# entry is only ever removed, with the literal.
SPELLS_ITS_OWN_KIND = frozenset({"baseline.py"})
_KIND_CONSTANT = re.compile(r"^KIND_[A-Z0-9_]+$")


def test_the_live_ledger_holds_no_kind_outside_the_closed_set() -> None:
    """Read off this repo's own log, because a list restated here would agree by construction.

    The event floor is the positive control: the log held 5,485 events on 2026-08-17 and is
    append-only, so a lower count means this read missed the ledger and its empty kind set
    would pass. The delegated floor is the same argument for the same log: 1,015 of its 5,611
    events were `edge` or `gate` that day, every one of which the fold called unknown until
    basicly-vkh0.38, and the count can only rise.
    """
    parsed, quarantined = events.read_events(REPO_ROOT / ".basicly" / "ledger")
    kinds = {event.kind for event in parsed}
    folded = events.fold(parsed)

    assert len(parsed) >= 5000, f"parsed {len(parsed)} events, so the read is the finding"
    assert not quarantined, quarantined[:3]
    assert kinds <= events.KNOWN_KINDS, sorted(kinds - events.KNOWN_KINDS)
    assert folded.unknown_kinds == {}, folded.unknown_kinds
    assert sum(folded.delegated_kinds.values()) >= 1015, folded.delegated_kinds


def test_no_kit_module_spells_a_kind_the_closed_set_does_not_hold() -> None:
    """One module answers *which kinds exist*, read off every sibling's source.

    Static, because loading a sibling here would mint a second `events` module and every
    ``except events.LedgerError`` in the suite would stop matching one of them.
    """
    spelled: list[tuple[str, str, str]] = []  # module, constant, its literal
    aliased: list[tuple[str, str, str]] = []  # module, constant, the name it is taken from
    for source in sorted(KIT_DIR.glob("*.py")):
        for node in ast.parse(source.read_text(encoding="utf-8")).body:
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            for target in node.targets:
                if not (isinstance(target, ast.Name) and _KIND_CONSTANT.match(target.id)):
                    continue
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    spelled.append((source.name, target.id, value.value))
                elif isinstance(value, ast.Attribute):
                    aliased.append((source.name, target.id, ast.unparse(value)))

    declared = {name: kind for module, name, kind in spelled if module == "events.py"}
    assert declared and len(spelled) + len(aliased) >= 3, (spelled, aliased)
    assert set(declared.values()) == events.KNOWN_KINDS
    assert {module for module, _, _ in spelled} - {"events.py"} == SPELLS_ITS_OWN_KIND
    for module, name, kind in spelled:
        assert kind in events.KNOWN_KINDS, f"{module} spells {name} as {kind!r}, no closed member"
    for module, name, taken_from in aliased:
        assert taken_from == f"events.{name}", f"{module} takes {name} from {taken_from}"
        assert name in declared, f"{module} aliases {name}, which events.py does not declare"


# --- delegated, applied, unknown ----------------------------------------------


def test_a_kind_a_sibling_folds_is_reported_as_delegated_and_not_as_unknown(
    tmp_path: Path,
) -> None:
    """Asserted through a real append, because the defect was in what the fold *reported*.

    The unknown kind in the same ledger is the positive control: a fold that had simply
    stopped counting the second class would satisfy the first assertion on its own.
    """
    _build(tmp_path)
    events.append(
        tmp_path,
        [
            events.Draft(RECORD_A, "edge", {"target": RECORD_B, "edge_type": "blocks"}),
            events.Draft(RECORD_A, "gate", {"gate": "verify", "passed": True}),
            events.Draft(RECORD_A, "seismograph_reading", {"magnitude": 4}),
        ],
        clock=lambda: CLOCK_EARLY,
    )

    result = events.fold(events.read_events(tmp_path)[0])

    assert result.delegated_kinds == {"edge": 1, "gate": 1}
    assert result.unknown_kinds == {"seismograph_reading": 1}
    assert result.mismatched_totals == []
    assert result.records[RECORD_A].totals.events == 10
    assert result.records[RECORD_A].fields == {"title": "the first"}


def test_the_closed_set_is_partitioned_by_who_folds_each_kind() -> None:
    """A kind cannot join the vocabulary with nobody named to fold it.

    This is what the `comment` split (basicly-vkh0.30) rests on: it adds five kinds, and one
    added to `KNOWN_KINDS` with no handler and no delegate has to fail here rather than fold
    to nothing quietly. Both directions of the partition, because the union alone would hold
    while a kind was in both halves.
    """
    applied = events.APPLIED_KINDS
    delegated = frozenset(events.DELEGATED_KINDS)

    assert applied | delegated == events.KNOWN_KINDS, sorted(
        (applied | delegated) ^ events.KNOWN_KINDS
    )
    assert not applied & delegated, sorted(applied & delegated)
    assert {events.classify_kind(kind) for kind in events.KNOWN_KINDS} == {
        events.APPLIED,
        events.DELEGATED,
    }
    assert events.classify_kind("seismograph_reading") == events.UNKNOWN


def test_every_delegated_kind_names_a_sibling_that_reads_that_kind() -> None:
    """The delegation is a claim about another module, so it is checked against that module.

    Statically, for the reason the closed-set test above gives. The bind is one hop deep on
    purpose: `provenance.fold_edges` reaches its kind through `is_edge_event` and
    `gates.fold_gates` through `is_gate_event`, so requiring the constant in the fold's own
    body would fail on code that does delegate.
    """
    assert events.DELEGATED_KINDS, "nothing is declared delegated, so this test proves nothing"
    for kind, owner in events.DELEGATED_KINDS.items():
        module, _, fold_name = owner.partition(".")
        constant = next(
            name
            for name in vars(events)
            if _KIND_CONSTANT.match(name) and getattr(events, name) == kind
        )
        functions = {
            node.name: ast.dump(node)
            for node in ast.parse((KIT_DIR / f"{module}.py").read_text(encoding="utf-8")).body
            if isinstance(node, ast.FunctionDef)
        }
        readers = {name for name, dumped in functions.items() if f"'{constant}'" in dumped}

        assert fold_name in functions, f"{owner} is not a module-level function"
        assert fold_name in readers or any(
            f"'{name}'" in functions[fold_name] for name in readers
        ), f"{owner} never reaches {constant}, so it does not fold {kind!r}"


# --- prose, and the typed machine kinds ---------------------------------------


def test_prose_folds_to_one_work_log_whichever_of_the_two_spellings_carried_it(
    tmp_path: Path,
) -> None:
    """The alias, asserted as state equality rather than as a membership check.

    A log written before basicly-vkh0.30 holds only `comment`, and this repository's holds
    2,667 of them, so the property that matters is that folding them derives *the same
    state* as folding the same prose written as `note` — the skip path would derive an empty
    work log and a warning instead. The unknown kind in the same ledger is the positive
    control: a fold that had stopped classifying anything would satisfy the first half alone.
    """
    old_spelling = tmp_path / "before"
    new_spelling = tmp_path / "after"
    for directory, kind in ((old_spelling, "comment"), (new_spelling, "note")):
        events.append(
            directory,
            [
                events.Draft(RECORD_A, "created", {"title": "the first"}),
                events.Draft(RECORD_A, kind, {"text": "what happened"}),
                events.Draft(RECORD_A, kind, {"text": "and then this"}),
                events.Draft(RECORD_A, "seismograph_reading", {"magnitude": 4}),
            ],
            actor="lane:one",
            clock=lambda: CLOCK_EARLY,
        )

    before = events.fold(events.read_events(old_spelling)[0])
    after = events.fold(events.read_events(new_spelling)[0])

    assert _state(before) == _state(after)
    assert before.records[RECORD_A].comments == ["what happened", "and then this"]
    assert before.unknown_kinds == {"seismograph_reading": 1}
    assert after.unknown_kinds == {"seismograph_reading": 1}
    assert {"note", "comment"} == events.PROSE_KINDS
    assert {events.classify_kind(kind) for kind in events.PROSE_KINDS} == {events.APPLIED}


def test_a_checkpoint_is_folded_from_its_kind_and_never_from_a_marker_in_prose(
    tmp_path: Path,
) -> None:
    """The prose carrying the same words is the discriminator.

    A fold that still recognised `[harness-policy] checkpoint=ship approved` in a body would
    report three approvals here rather than two, which is what a reader keying on the kind
    buys: an approval is a claim the fold can refuse, not a substring.
    """
    _build(tmp_path)
    events.append(
        tmp_path,
        [
            events.Draft(
                RECORD_A, "checkpoint", {"checkpoint": "classify", "approved_by": "owner"}
            ),
            events.Draft(RECORD_A, "checkpoint", {"checkpoint": "decompose"}),
            events.Draft(RECORD_A, "note", {"text": "[harness-policy] checkpoint=ship approved"}),
            events.Draft(
                RECORD_A, "checkpoint", {"checkpoint": "classify", "approved_by": "grant:L3"}
            ),
        ],
        clock=lambda: CLOCK_EARLY,
    )

    state = events.fold(events.read_events(tmp_path)[0]).records[RECORD_A]

    assert state.checkpoints == {"classify": "grant:L3", "decompose": ""}
    assert state.comments == ["[harness-policy] checkpoint=ship approved"]


def test_an_artifact_is_keyed_by_its_kind_and_its_body_is_not_capped(tmp_path: Path) -> None:
    """Last body wins per kind, and a body larger than the free-text cap arrives whole.

    ``body`` is outside :data:`events.TRUNCATABLE_KEYS`, which is what `_prepare_entry` tells
    a caller with structured evidence to do: a handoff artifact a consumer refuses on must not
    reach it as a fragment (basicly-pp7q4i).
    """
    long_reason = "x" * (events.MAX_TEXT_BYTES + 1)
    events.append(
        tmp_path,
        [
            events.Draft(RECORD_A, "created", {"title": "the first"}),
            events.Draft(RECORD_A, "artifact", {"artifact": "plan", "body": {"units": 1}}),
            events.Draft(RECORD_A, "artifact", {"artifact": "review", "body": long_reason}),
            events.Draft(RECORD_A, "artifact", {"artifact": "plan", "body": {"units": 2}}),
        ],
        clock=lambda: CLOCK_EARLY,
    )

    state = events.fold(events.read_events(tmp_path)[0]).records[RECORD_A]

    assert state.artifacts == {"plan": {"units": 2}, "review": long_reason}


def test_a_dispatchs_telemetry_reading_rides_on_the_kind_it_belongs_to(tmp_path: Path) -> None:
    """A reading is not a kind of its own: `accumulate` reads it off the `dispatch` payload.

    The fields beside ``spend_micros`` are asserted because the fold neither sums nor parses
    them — they are read back by name from the event, which is the whole difference from a
    reader grepping `[harness-cost]` out of a body.
    """
    reading = {"spend_micros": 2_500_000, "input_tokens": 41_000, "model": "claude-sonnet-4-5"}
    events.append(
        tmp_path,
        [
            events.Draft(RECORD_A, "created", {"title": "the first"}),
            events.Draft(RECORD_A, "dispatch", reading),
        ],
        clock=lambda: CLOCK_EARLY,
    )

    stored, _ = events.read_events(tmp_path)
    totals = events.fold(stored).records[RECORD_A].totals

    assert (totals.attempts, totals.spend_micros) == (1, 2_500_000)
    assert stored[-1].payload == reading
    assert "telemetry" not in events.KNOWN_KINDS


def test_a_typed_machine_event_missing_its_own_key_is_refused(tmp_path: Path) -> None:
    """Tolerance is for kinds we do not know: a `checkpoint` with no name names no approval."""
    with pytest.raises(events.InvalidEventError, match="checkpoint name"):
        events.append(
            tmp_path,
            [events.Draft(RECORD_A, "checkpoint", {"approved_by": "owner"})],
            clock=lambda: CLOCK_EARLY,
        )
    with pytest.raises(events.InvalidEventError, match="string approved_by"):
        events.append(
            tmp_path,
            [events.Draft(RECORD_A, "checkpoint", {"checkpoint": "classify", "approved_by": 7})],
            clock=lambda: CLOCK_EARLY,
        )
    with pytest.raises(events.InvalidEventError, match="artifact kind"):
        events.append(
            tmp_path,
            [events.Draft(RECORD_A, "artifact", {"body": {"units": 1}})],
            clock=lambda: CLOCK_EARLY,
        )

    assert not list(tmp_path.glob("events-*.jsonl"))
