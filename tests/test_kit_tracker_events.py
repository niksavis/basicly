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

CLOCK_EARLY = 1_000_000_000.0
CLOCK_LATE = 1_800_000_000.0


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _lifecycle() -> list[Any]:

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
    return events.append(directory, _lifecycle(), actor="lane:one", clock=lambda: clock)


def _state(result: Any) -> dict[str, tuple[object, ...]]:
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


def test_a_fresh_ledger_directory_gets_its_first_log_and_one_line_per_event(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "never-created"
    minted = _build(ledger)

    log = ledger / events.INITIAL_LOG_NAME
    assert events.log_paths(ledger) == [log]
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(minted) == len(_lifecycle())


def test_each_event_carries_its_own_items_max_sequence_plus_one(tmp_path: Path) -> None:

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
    assert rival.id in result.mismatched_totals or twin.id in result.mismatched_totals


def test_the_fold_ignores_the_order_the_events_arrive_in(tmp_path: Path) -> None:
    _build(tmp_path)
    original, quarantined = events.read_events(tmp_path)
    assert quarantined == []

    baseline = _state(events.fold(original))
    reversed_run = _state(events.fold(list(reversed(original))))
    shuffled = list(original)
    random.Random(20260806).shuffle(shuffled)
    shuffled_run = _state(events.fold(shuffled))

    assert baseline == reversed_run == shuffled_run
    folded = events.fold(original).records[RECORD_A]
    assert folded.status == "open"
    assert folded.totals.status == "open"


def test_the_fold_reaches_the_reopen_and_not_the_last_line_in_the_file(
    tmp_path: Path,
) -> None:

    _build(tmp_path)
    log = tmp_path / events.INITIAL_LOG_NAME
    lines = log.read_text(encoding="utf-8").splitlines()
    statuses = [line for line in lines if '"kind":"status"' in line]
    others = [line for line in lines if '"kind":"status"' not in line]
    rewritten = [*others, *sorted(statuses, key=lambda line: '"done"' in line)]
    log.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    restored, _ = events.read_events(tmp_path)
    result = events.fold(restored)

    assert '"done"' in rewritten[-1]
    assert result.records[RECORD_A].status == "open"


def test_a_duplicated_event_folds_once_and_is_reported(tmp_path: Path) -> None:
    _build(tmp_path)
    original, _ = events.read_events(tmp_path)
    baseline = _state(events.fold(original))

    doubled = events.fold([*original, original[3], original[0]])

    assert _state(doubled) == baseline
    assert doubled.duplicate_ids == sorted({original[3].id, original[0].id})


def test_replaying_the_same_drafts_appends_nothing(tmp_path: Path) -> None:
    first = _build(tmp_path)
    before = (tmp_path / events.INITIAL_LOG_NAME).read_bytes()

    again = _build(tmp_path)

    assert first != []
    assert again == []
    assert (tmp_path / events.INITIAL_LOG_NAME).read_bytes() == before


def test_a_repeated_status_needs_a_generation_or_it_is_swallowed(tmp_path: Path) -> None:

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


def test_two_injected_clocks_produce_identical_ledgers_apart_from_the_timestamp(
    tmp_path: Path,
) -> None:

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


WALL_CLOCK_ATTRIBUTES = frozenset({"time.time", "time.time_ns", "datetime.now", "datetime.utcnow"})

PERMITTED_CLOCK_READS = [("append", "time.time")]


def _dotted(node: ast.expr) -> str:
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


def test_the_carried_totals_agree_with_the_folds_own_recomputation(tmp_path: Path) -> None:

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
    with pytest.raises(events.InvalidEventError, match="string status"):
        events.append(
            tmp_path, [events.Draft(RECORD_A, "status", {"state": "open"})], clock=lambda: 0.0
        )
    with pytest.raises(events.InvalidEventError, match="string name"):
        events.append(tmp_path, [events.Draft(RECORD_A, "field", {"value": 1})], clock=lambda: 0.0)


def test_an_unknown_field_survives_a_round_trip_byte_for_byte(tmp_path: Path) -> None:
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


def test_a_torn_trailing_line_is_tolerated_and_the_fold_before_it_is_intact(
    tmp_path: Path,
) -> None:
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

    _build(tmp_path)
    log = tmp_path / events.INITIAL_LOG_NAME
    with log.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write("{ complete, and still not an event }\n")

    _, quarantined = events.read_events(tmp_path)

    assert [item.line for item in quarantined] == ["{ complete, and still not an event }"]


def test_an_append_after_a_torn_line_starts_a_new_line(tmp_path: Path) -> None:
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

    events.append(
        tmp_path,
        [events.Draft(RECORD_A, "comment", {"text": "sequência — ordenação · 順序"})],
        clock=lambda: CLOCK_EARLY,
    )

    raw = (tmp_path / events.INITIAL_LOG_NAME).read_bytes()

    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    assert "順序" in raw.decode("utf-8")


def test_the_cap_cuts_free_text_on_a_character_boundary_and_says_how_much(
    tmp_path: Path,
) -> None:
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
    reread, quarantined = events.read_events(tmp_path)
    assert quarantined == []
    assert reread[0].payload["text"] == payload["text"]


def test_the_cap_truncates_and_never_refuses(tmp_path: Path) -> None:
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
    with pytest.raises(events.InvalidEventError, match="capped free text"):
        events.append(
            tmp_path,
            [events.Draft(RECORD_A, "comment", {"text": ["a", "b"]})],
            clock=lambda: CLOCK_EARLY,
        )


def test_a_structural_field_is_never_truncated(tmp_path: Path) -> None:
    long_status = "waiting_on_" + "x" * (events.MAX_TEXT_BYTES * 2)
    minted = events.append(
        tmp_path,
        [events.Draft(RECORD_A, "status", {"status": long_status})],
        clock=lambda: CLOCK_EARLY,
    )

    assert minted[0].payload["status"] == long_status
    assert minted[0].totals.status == long_status
    assert "status_truncated" not in minted[0].payload


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


def test_no_appended_event_can_carry_an_empty_actor(tmp_path: Path) -> None:

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

    stored, _ = events.read_events(tmp_path)
    assert len(stored) == 3
    assert all(event.actor for event in stored)


def test_a_live_holder_makes_contention_a_retryable_failure(tmp_path: Path) -> None:

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
    assert clock.slept
    assert holder.held


def test_an_append_reports_contention_rather_than_writing_unlocked(tmp_path: Path) -> None:

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
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / events.LOCK_NAME).write_text("half a json obj", encoding="utf-8")

    minted = events.append(
        tmp_path, [events.Draft(RECORD_A, "created", {"title": "x"})], clock=lambda: CLOCK_EARLY
    )

    assert [event.seq for event in minted] == [1]


def test_releasing_never_removes_a_lock_that_was_stolen_from_us(tmp_path: Path) -> None:
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

    monkeypatch.setattr(events.os, "kill", lambda *_: pytest.fail("os.kill reached on Windows"))
    monkeypatch.setattr(events.os, "name", "nt")

    assert events.default_pid_liveness(4242) is None


def test_the_default_liveness_probe_answers_for_this_process_on_posix() -> None:
    if os.name == "nt":
        pytest.skip("the POSIX probe cannot exist here; the nt branch is covered above")
    assert events.default_pid_liveness(os.getpid()) is True


def test_the_log_glob_is_the_contract_rebuild_and_fsck_will_share() -> None:
    assert events.LOG_GLOB == "events-*.jsonl"
    assert re.fullmatch(r"events-\d+\.jsonl", events.INITIAL_LOG_NAME)


def test_a_rotated_log_is_read_and_becomes_the_append_target(tmp_path: Path) -> None:

    _build(tmp_path)
    rotated = tmp_path / "events-2027.jsonl"
    rotated.write_text("", encoding="utf-8")

    assert events.append_target(tmp_path) == rotated
    minted = events.append(
        tmp_path,
        [events.Draft(RECORD_B, "comment", {"text": "next year"})],
        clock=lambda: CLOCK_LATE,
    )

    assert minted[0].seq == 4
    assert rotated.read_text(encoding="utf-8").count("\n") == 1
    parsed, _ = events.read_events(tmp_path)
    assert len(parsed) == len(_lifecycle()) + 1


def test_a_record_id_and_a_kind_are_validated_before_anything_is_written(
    tmp_path: Path,
) -> None:
    with pytest.raises(Exception, match="not a record id"):
        events.append(
            tmp_path, [events.Draft("basicly-fix-the-thing", "created", {})], clock=lambda: 0.0
        )
    with pytest.raises(events.InvalidEventError, match="must match"):
        events.append(tmp_path, [events.Draft(RECORD_A, "Status", {})], clock=lambda: 0.0)
    assert events.log_paths(tmp_path) == []


def test_a_tombstoned_record_stays_in_the_fold(tmp_path: Path) -> None:
    _build(tmp_path)
    events.append(tmp_path, [events.Draft(RECORD_C, "tombstone", {})], clock=lambda: CLOCK_EARLY)

    stored, _ = events.read_events(tmp_path)
    state = events.fold(stored).records[RECORD_C]

    assert state.tombstoned is True
    assert state.fields["title"] == "a child"
    assert state.totals.events == 3


def test_appending_nothing_writes_nothing(tmp_path: Path) -> None:
    assert events.append(tmp_path, [], clock=lambda: CLOCK_EARLY) == []
    assert not tmp_path.exists() or events.log_paths(tmp_path) == []


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


SPELLS_ITS_OWN_KIND = frozenset({"baseline.py"})
_KIND_CONSTANT = re.compile(r"^KIND_[A-Z0-9_]+$")


def test_the_live_ledger_holds_no_kind_outside_the_closed_set() -> None:

    parsed, quarantined = events.read_events(REPO_ROOT / ".basicly" / "ledger")
    kinds = {event.kind for event in parsed}
    folded = events.fold(parsed)

    assert len(parsed) >= 5000, f"parsed {len(parsed)} events, so the read is the finding"
    assert not quarantined, quarantined[:3]
    assert kinds <= events.KNOWN_KINDS, sorted(kinds - events.KNOWN_KINDS)
    assert folded.unknown_kinds == {}, folded.unknown_kinds
    assert sum(folded.delegated_kinds.values()) >= 1015, folded.delegated_kinds


def test_no_kit_module_spells_a_kind_the_closed_set_does_not_hold() -> None:

    spelled: list[tuple[str, str, str]] = []
    aliased: list[tuple[str, str, str]] = []
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


def test_a_kind_a_sibling_folds_is_reported_as_delegated_and_not_as_unknown(
    tmp_path: Path,
) -> None:

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


def test_prose_folds_to_one_work_log_whichever_of_the_two_spellings_carried_it(
    tmp_path: Path,
) -> None:

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


LEAKED = "AWS_SECRET_ACCESS_KEY=" + "wJalrXUtnFEMIK7MDENGbPxRfiCY"

FSCK_SOURCE = KIT_DIR / "fsck.py"


def _leaky(directory: Path) -> list[Any]:

    return events.append(
        directory,
        [
            events.Draft(RECORD_A, "created", {"title": "the first"}),
            events.Draft(RECORD_A, "comment", {"text": "env dump: " + LEAKED}),
            events.Draft(
                RECORD_A, "dispatch", {"spend_micros": 1_250_000, "output": "a run log, 40 lines"}
            ),
        ],
        actor="lane:one",
        clock=lambda: CLOCK_EARLY,
    )


def _fsck(ledger: Path) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(
        [sys.executable, str(FSCK_SOURCE), str(ledger)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.stdout, completed.stderr
    return completed.returncode, json.loads(completed.stdout)


def _broken_kinds(report: dict[str, Any]) -> list[str]:
    return sorted({found["kind"] for found in report["findings"] if found["severity"] == "broken"})


def test_a_withdrawal_takes_the_content_out_of_the_store_and_the_fold_says_why(
    tmp_path: Path,
) -> None:

    minted = _leaky(tmp_path)
    log = tmp_path / events.INITIAL_LOG_NAME
    assert LEAKED in log.read_text(encoding="utf-8")

    trail = events.withdraw(
        tmp_path,
        minted[1].id,
        reason="leaked environment dump",
        actor="operator:owner",
        clock=lambda: CLOCK_LATE,
    )

    assert LEAKED not in log.read_text(encoding="utf-8")
    folded = events.fold(events.read_events(tmp_path)[0])
    assert folded.withdrawals == [
        events.Withdrawal(
            record=RECORD_A,
            target=trail.payload["target"],
            reason="leaked environment dump",
            at="2027-01-15T08:00:00Z",
        )
    ]
    assert trail.ts == "2027-01-15T08:00:00Z" != minted[1].ts
    assert folded.records[RECORD_A].comments == [""]


def test_a_withdrawal_keeps_every_value_the_fold_reads_by_name(tmp_path: Path) -> None:

    minted = _leaky(tmp_path)
    before = events.fold(events.read_events(tmp_path)[0]).records[RECORD_A].totals

    events.withdraw(tmp_path, minted[2].id, reason="leaked environment dump")

    parsed = events.read_events(tmp_path)[0]
    folded = events.fold(parsed)
    state = folded.records[RECORD_A]
    withdrawn = next(event for event in parsed if events.WITHDRAWN_FROM in event.payload)
    assert withdrawn.payload["spend_micros"] == 1_250_000
    assert withdrawn.payload["output"] == events.WITHDRAWN_PLACEHOLDER
    assert withdrawn.payload["output" + events.WITHDRAWN_SUFFIX] is True
    assert withdrawn.seq == minted[2].seq
    assert state.totals.spend_micros == before.spend_micros
    assert state.totals.attempts == before.attempts
    assert state.totals.events == before.events + 1
    assert folded.mismatched_totals == [] and folded.forked == []


def test_fsck_reports_a_withdrawn_log_sound_and_a_hand_edited_one_broken(
    tmp_path: Path,
) -> None:

    minted = _leaky(tmp_path)
    events.withdraw(tmp_path, minted[1].id, reason="leaked environment dump")

    code, report = _fsck(tmp_path)

    assert (code, report["clean"], _broken_kinds(report)) == (0, True, [])

    log = tmp_path / events.INITIAL_LOG_NAME
    lines = log.read_text(encoding="utf-8").splitlines()
    edited = json.loads(lines[2])
    edited["payload"] = {"spend_micros": 1_250_000, "output": "[redacted by hand]"}
    lines[2] = json.dumps(edited, sort_keys=True, separators=(",", ":"))
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    code, report = _fsck(tmp_path)

    assert _broken_kinds(report) == ["unminted-id"]
    assert code == 2 and report["findings"][0]["subject"] == edited["id"]


def test_fsck_reports_a_withdrawal_a_union_merge_put_back(tmp_path: Path) -> None:

    minted = _leaky(tmp_path)
    log = tmp_path / events.INITIAL_LOG_NAME
    retired_line = log.read_text(encoding="utf-8").splitlines()[1]
    events.withdraw(tmp_path, minted[1].id, reason="leaked environment dump")

    with log.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(retired_line + "\n")

    code, report = _fsck(tmp_path)
    resurrected = [
        found for found in report["findings"] if found["kind"] == "withdrawal-resurrected"
    ]

    assert LEAKED in log.read_text(encoding="utf-8"), "the fixture must restore the content"
    assert code == 2
    assert [found["subject"] for found in resurrected] == [minted[1].id]


@pytest.mark.parametrize(
    ("target", "match"),
    [
        ("trail", "withdrawing that would leave the first one unaccounted for"),
        ("twice", "has already been withdrawn"),
        ("nothing to withdraw", "no free text to withdraw"),
        ("absent", "no event in the log carries the id"),
    ],
)
def test_a_withdrawal_refuses_what_it_could_not_audit(
    tmp_path: Path, target: str, match: str
) -> None:

    minted = _leaky(tmp_path)
    trail = events.withdraw(tmp_path, minted[1].id, reason="leaked environment dump")
    events.append(
        tmp_path, [events.Draft(RECORD_A, "status", {"status": "open"})], clock=lambda: CLOCK_EARLY
    )
    withdrawn = next(
        event for event in events.read_events(tmp_path)[0] if events.WITHDRAWN_FROM in event.payload
    )
    only_folded = next(event for event in events.read_events(tmp_path)[0] if event.kind == "status")
    event_id = {
        "trail": trail.id,
        "twice": withdrawn.id,
        "nothing to withdraw": only_folded.id,
        "absent": RECORD_B + "#ev-0123456789",
    }[target]
    before = (tmp_path / events.INITIAL_LOG_NAME).read_text(encoding="utf-8")

    with pytest.raises(events.InvalidEventError, match=match):
        events.withdraw(tmp_path, event_id, reason="a second thought")

    assert (tmp_path / events.INITIAL_LOG_NAME).read_text(encoding="utf-8") == before


def test_a_withdrawal_takes_the_writer_lock_a_plain_append_takes(tmp_path: Path) -> None:

    minted = _leaky(tmp_path)
    log = tmp_path / events.INITIAL_LOG_NAME
    before = log.read_text(encoding="utf-8")
    lock = events.LedgerLock(tmp_path, pid=os.getpid())
    lock.acquire()

    with pytest.raises(events.LockUnavailableError):
        events.withdraw(
            tmp_path, minted[1].id, reason="leaked environment dump", lock_timeout_s=0.0
        )

    assert log.read_text(encoding="utf-8") == before
    assert LEAKED in before


@pytest.mark.parametrize(
    ("dropped", "expected"),
    [
        ("the trail", "withdrawal-unrecorded"),
        ("the rewrite", "withdrawal-unapplied"),
    ],
)
def test_fsck_reports_a_withdrawal_only_half_of_which_landed(
    tmp_path: Path, dropped: str, expected: str
) -> None:

    minted = _leaky(tmp_path)
    events.withdraw(tmp_path, minted[1].id, reason="leaked environment dump")
    log = tmp_path / events.INITIAL_LOG_NAME
    survives = (
        (lambda line: '"kind":"withdrawn"' not in line)
        if dropped == "the trail"
        else (lambda line: events.WITHDRAWN_FROM not in line)
    )
    kept = [line for line in log.read_text(encoding="utf-8").splitlines() if survives(line)]
    log.write_text("\n".join(kept) + "\n", encoding="utf-8")

    code, report = _fsck(tmp_path)

    assert code == 2
    assert expected in _broken_kinds(report)
