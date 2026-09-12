from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from tests import tracker_corpus

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


differential = _load(KIT_DIR / "differential.py", "tracker_differential")
migrate = differential.migrate
events = differential.events

SOURCE = "the committed ledger"

CLOCK = 1_000_000_000.0
VOCAB = differential.DEFAULT_VOCABULARY

PARENT = "basicly-aa11"
CHILD = "basicly-aa11.1"
CLASSIFIED = "basicly-bb22"
CLOSED = "basicly-cc33"
SHIPPED = "basicly-dd44"
BLOCKED = "basicly-ee55"

VERIFY_PROVIDER = "basicly-verify"
RUBRIC_PROVIDER = "basicly-rubric"


def _checkpoint(name: str) -> str:
    return differential.checkpoint_marker(name, VOCAB)


def _export_record(record: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": record,
        "title": f"the record {record}",
        "status": "open",
        "priority": 2,
        "issue_type": "task",
        "created_at": "2026-08-01T10:00:00Z",
        "created_by": "niksa",
        "updated_at": "2026-08-01T10:00:00Z",
    }
    body.update(overrides)
    return body


def _comments(*texts: str) -> list[dict[str, Any]]:
    return [
        {"id": index, "text": text, "author": "niksa", "created_at": "2026-08-01T11:00:00Z"}
        for index, text in enumerate(texts, start=1)
    ]


def _dependency(target: str, edge_type: str) -> dict[str, Any]:
    return {"depends_on_id": target, "type": edge_type}


POPULATION: tuple[dict[str, Any], ...] = (
    _export_record(PARENT, issue_type="epic"),
    _export_record(
        CHILD,
        status="in_progress",
        external_ref="worktree:lane-a:harness/lane-a",
        dependencies=[_dependency(PARENT, "parent-child")],
    ),
    _export_record(CLASSIFIED, comments=_comments(_checkpoint("classify"))),
    _export_record(CLOSED, status="closed"),
    _export_record(
        SHIPPED,
        comments=_comments(_checkpoint("ship")),
        dependencies=[_dependency(CLOSED, "blocks")],
    ),
    _export_record(BLOCKED, dependencies=[_dependency(SHIPPED, "blocks")]),
)

LIVE_GATES: dict[str, tuple[tuple[str, str, bool], ...]] = {
    CHILD: (("verify", VERIFY_PROVIDER, True),),
    SHIPPED: (("verify", VERIFY_PROVIDER, True),),
    BLOCKED: (("verify", VERIFY_PROVIDER, False),),
}


def _gate_rows(record: str) -> tuple[Any, ...]:
    return tuple(
        differential.GateRow(gate, provider, passed)
        for gate, provider, passed in LIVE_GATES.get(record, ())
    )


def _gate_drafts(record: str) -> list[Any]:
    return [
        events.Draft(
            record,
            differential.KIND_GATE,
            {
                differential.GATE_NAME_KEY: gate,
                differential.GATE_PROVIDER_KEY: provider,
                differential.GATE_PASSED_KEY: passed,
            },
        )
        for gate, provider, passed in LIVE_GATES.get(record, ())
    ]


def _snapshot_text(records: tuple[dict[str, Any], ...]) -> str:
    return "".join(
        json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n" for record in records
    )


def _ledger(
    directory: Path,
    records: tuple[dict[str, Any], ...] = POPULATION,
    *,
    with_gates: bool = True,
) -> str:

    text = _snapshot_text(records)
    snapshot = migrate.parse_snapshot(text, name=SOURCE)
    report = migrate.import_snapshot(directory, snapshot, clock=lambda: CLOCK)
    assert report.rejected == [], report.rejected
    assert report.unreadable == [], report.unreadable
    if with_gates:
        drafts = [draft for record in records for draft in _gate_drafts(str(record["id"]))]
        events.append(directory, drafts, clock=lambda: CLOCK)
    return text


def _live_views(
    records: tuple[dict[str, Any], ...] = POPULATION, *, with_gates: bool = True
) -> dict[str, Any]:

    views: dict[str, Any] = {}
    for record in records:
        record_id = str(record["id"])
        views[record_id] = differential.RecordView(
            record=record_id,
            status=str(record["status"]),
            external_ref=str(record.get("external_ref", "")),
            comments=tuple(comment["text"] for comment in record.get("comments", [])),
            dependencies=tuple(
                differential.Edge(edge["depends_on_id"], edge["type"])
                for edge in record.get("dependencies", [])
            ),
            gates=_gate_rows(record_id) if with_gates else (),
        )
    return views


def _live_source(views: dict[str, Any]) -> Any:
    return differential.ReferenceSource(views=lambda _ledger_events: views)


def _without(record: str) -> tuple[dict[str, Any], ...]:
    return tuple(item for item in POPULATION if item["id"] != record)


def _tombstone(directory: Path, record: str) -> None:

    snapshot = migrate.parse_snapshot(_snapshot_text(_without(record)), name=SOURCE)
    report = migrate.import_snapshot(directory, snapshot, deleted=[record], clock=lambda: CLOCK)
    assert report.tombstoned == [record], report.rejected


def test_the_authored_population_spans_every_query(tmp_path: Path) -> None:
    _ledger(tmp_path / "ledger")
    answers = differential.verdicts(_live_views(), VOCAB)

    assert {verdict.phase for verdict in answers.values()} == {
        "decompose",
        "verify",
        "classify",
        "done",
        "ship",
        "intake",
    }
    assert {verdict.ready for verdict in answers.values()} == {True, False}
    assert answers[CHILD].gates.passed == ("verify",)
    assert answers[BLOCKED].gates.failed == ("verify",)
    assert answers[CLASSIFIED].gates.missing == ("verify",)


def test_every_query_agrees_when_the_two_stores_hold_the_same_facts(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    _ledger(ledger)

    report = differential.run_differential(ledger, _live_source(_live_views()), VOCAB)

    assert report.disagreements == [], report.summary()
    assert report.unanswered == []
    assert report.unknown == []
    assert report.refusals == []
    assert report.records == len(POPULATION)
    assert report.compared == len(POPULATION)
    assert report.clean
    assert report.conclusive, report.summary()


@pytest.mark.parametrize(
    ("query", "record", "mutate"),
    [
        (
            differential.QUERY_PHASE,
            CLASSIFIED,
            {"comments": (_checkpoint("classify"), _checkpoint("decompose"))},
        ),
        (differential.QUERY_READY, BLOCKED, {"dependencies": ()}),
        (
            differential.QUERY_GATES,
            CLASSIFIED,
            {"gates": (differential.GateRow("rubric", RUBRIC_PROVIDER, True),)},
        ),
    ],
)
def test_each_query_reports_its_own_disagreement(
    tmp_path: Path, query: str, record: str, mutate: dict[str, Any]
) -> None:
    ledger = tmp_path / "ledger"
    _ledger(ledger)
    views = _live_views()
    views[record] = dataclasses.replace(views[record], **mutate)

    report = differential.run_differential(ledger, _live_source(views), VOCAB)

    assert [(item.record, item.query) for item in report.disagreements] == [(record, query)]
    assert not report.clean
    assert report.refusals == []


def test_a_reimport_of_the_tracker_own_export_is_refused_across_the_whole_history() -> None:

    text = tracker_corpus.snapshot_text()
    snapshot = migrate.parse_snapshot(text, name=SOURCE)
    assert len(snapshot.records) > 100, "the real history is the subject; it must not be empty"

    with tempfile.TemporaryDirectory() as tmp:
        ledger = Path(tmp) / "ledger"
        migrate.import_snapshot(ledger, snapshot, clock=lambda: CLOCK)
        reimported = Path(tmp) / "reimported"
        migrate.import_snapshot(
            reimported, migrate.parse_snapshot(text, name=SOURCE), clock=lambda: CLOCK
        )
        source = differential.ReferenceSource(
            views=lambda _ledger_events: differential.views_from_events(
                differential.read_ledger(reimported)
            ),
            snapshot=text,
        )

        report = differential.run_differential(ledger, source, VOCAB)

    assert [refusal.rule for refusal in report.refusals] == [differential.RULE_REIMPORTED_EXPORT]
    assert differential.LOSSY_SNAPSHOT_REASON in report.refusals[0].detail
    assert not report.clean
    assert report.disagreements == [], report.summary()
    assert report.records == len(snapshot.records)
    assert report.compared == report.records


def test_a_live_reference_is_not_refused(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    _ledger(ledger)

    baseline = _live_views()
    refusals, unproven = differential.audit_reference(
        _live_source(baseline), differential.read_ledger(ledger), baseline, VOCAB
    )

    assert refusals == []
    assert unproven == []


def test_a_reference_derived_from_the_owned_ledger_is_refused(tmp_path: Path) -> None:

    ledger = tmp_path / "ledger"
    _ledger(ledger)
    source = differential.ReferenceSource(views=differential.views_from_events)

    report = differential.run_differential(ledger, source, VOCAB)

    assert [refusal.rule for refusal in report.refusals] == [differential.RULE_DERIVED_FROM_LEDGER]
    assert differential.LOSSY_SNAPSHOT_REASON in report.refusals[0].detail
    assert report.disagreements == [], "the derivative agrees with itself; that is the point"
    assert not report.clean


def test_an_export_the_ledger_was_not_imported_from_is_still_refused(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    _ledger(ledger)
    views = _live_views()
    source = differential.ReferenceSource(
        views=lambda _ledger_events: views,
        snapshot="{}\n",
    )

    report = differential.run_differential(ledger, source, VOCAB)

    assert [refusal.rule for refusal in report.refusals] == [differential.RULE_EXPORT_BACKED]
    assert differential.EXPORT_CANNOT_EXPRESS in report.refusals[0].detail
    assert not report.clean


def test_a_query_whose_answers_never_vary_is_reported_inconclusive(tmp_path: Path) -> None:

    ledger = tmp_path / "ledger"
    _ledger(ledger, with_gates=False)

    report = differential.run_differential(
        ledger, _live_source(_live_views(with_gates=False)), VOCAB
    )

    assert report.clean, report.summary()
    assert not report.conclusive
    assert [item.subject for item in report.inconclusive] == [differential.QUERY_GATES]
    assert "discriminated nothing" in report.inconclusive[0].reason


def test_an_undeclared_export_derivative_cannot_reach_conclusive(tmp_path: Path) -> None:

    ledger = tmp_path / "ledger"
    _ledger(ledger, with_gates=False)
    elsewhere = tmp_path / "reimported"
    _ledger(elsewhere, with_gates=False)
    source = differential.ReferenceSource(
        views=lambda _ledger_events: differential.views_from_events(
            differential.read_ledger(elsewhere)
        )
    )

    report = differential.run_differential(ledger, source, VOCAB)

    assert report.refusals == [], "the audit cannot see this one; that is the documented limit"
    assert report.clean
    assert not report.conclusive
    assert [item.subject for item in report.inconclusive] == [differential.QUERY_GATES]


def test_a_reference_that_omits_a_record_is_not_clean(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    _ledger(ledger)
    views = _live_views()
    del views[BLOCKED]

    report = differential.run_differential(ledger, _live_source(views), VOCAB)

    assert report.unanswered == [BLOCKED]
    assert report.compared == len(POPULATION) - 1
    assert report.records == len(POPULATION)
    assert not report.clean


def test_a_record_the_ledger_does_not_hold_is_reported_unknown(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    _ledger(ledger)
    views = _live_views()
    views["basicly-ff66"] = differential.RecordView(record="basicly-ff66", status="open")

    report = differential.run_differential(ledger, _live_source(views), VOCAB)

    assert report.unknown == ["basicly-ff66"]
    assert not report.clean


def test_a_deleted_record_is_not_in_the_owned_ready_set(tmp_path: Path) -> None:

    ledger = tmp_path / "ledger"
    _ledger(ledger)
    before = differential.views_from_events(differential.read_ledger(ledger))
    assert differential.verdicts(before, VOCAB)[SHIPPED].ready, "the control: ready before"

    _tombstone(ledger, SHIPPED)

    after = differential.views_from_events(differential.read_ledger(ledger))
    assert after[SHIPPED].tombstoned
    assert after[SHIPPED].status == before[SHIPPED].status, "the tombstone moved the status"
    assert not differential.verdicts(after, VOCAB)[SHIPPED].ready


def test_a_tombstone_the_reference_is_silent_about_is_not_unanswered(tmp_path: Path) -> None:

    ledger = tmp_path / "ledger"
    _ledger(ledger)
    _tombstone(ledger, SHIPPED)

    source = _live_source(_live_views(_without(SHIPPED)))
    report = differential.run_differential(ledger, source, VOCAB)

    assert report.unanswered == []
    assert report.disagreements == []
    assert report.clean


def test_a_tombstone_the_reference_still_answers_for_is_a_disagreement(tmp_path: Path) -> None:

    ledger = tmp_path / "ledger"
    _ledger(ledger)
    _tombstone(ledger, SHIPPED)

    report = differential.run_differential(ledger, _live_source(_live_views()), VOCAB)

    assert report.unanswered == []
    assert [(item.record, item.query) for item in report.disagreements] == [
        (SHIPPED, differential.QUERY_READY)
    ]
    assert not report.clean


def test_an_empty_ledger_cannot_establish_independence(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()

    report = differential.run_differential(ledger, _live_source({}), VOCAB)

    assert next(item.subject for item in report.inconclusive) == (
        differential.RULE_DERIVED_FROM_LEDGER
    )
    assert not report.conclusive


def test_the_probe_is_deterministic_and_writes_nothing(tmp_path: Path) -> None:

    ledger = tmp_path / "ledger"
    _ledger(ledger)
    before = {path.name: path.read_bytes() for path in sorted(ledger.iterdir())}
    ledger_events = differential.read_ledger(ledger)

    first = differential.probe_events(ledger_events, VOCAB)
    second = differential.probe_events(ledger_events, VOCAB)

    assert first == second
    assert len(first) == len(ledger_events) + 1
    assert {path.name: path.read_bytes() for path in sorted(ledger.iterdir())} == before


def test_the_probe_changes_the_owned_verdict_it_perturbs(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    _ledger(ledger)
    ledger_events = differential.read_ledger(ledger)
    perturbed = differential.probe_events(ledger_events, VOCAB)

    before = differential.verdicts(differential.views_from_events(ledger_events), VOCAB)
    after = differential.verdicts(differential.views_from_events(perturbed), VOCAB)

    moved = [record for record in before if before[record] != after[record]]
    assert moved, "a probe that changes no verdict cannot detect a derivative"


def test_a_ship_approval_on_an_unstarted_record_does_not_derive_ship() -> None:

    view = differential.RecordView(
        record=CLASSIFIED, status="open", comments=(_checkpoint("ship"),)
    )

    verdict = differential.verdicts({CLASSIFIED: view}, VOCAB)[CLASSIFIED]

    assert verdict.gates.missing == ("verify",)
    assert verdict.phase == "intake"


def test_a_required_gate_from_a_foreign_provider_is_disregarded() -> None:
    view = differential.RecordView(
        record=CLASSIFIED,
        status="open",
        gates=(differential.GateRow("verify", "some-lane-agent", True),),
    )

    verdict = differential.verdicts({CLASSIFIED: view}, VOCAB)[CLASSIFIED]

    assert verdict.gates.missing == ("verify",)
    assert verdict.gates.passed == ()
    assert [row.provider for row in verdict.gates.disregarded] == ["some-lane-agent"]


def test_gate_row_order_is_not_a_disagreement() -> None:
    rows = (
        differential.GateRow("rubric", RUBRIC_PROVIDER, True),
        differential.GateRow("lint", "some-provider", False),
    )
    forward = differential.RecordView(record=CLASSIFIED, status="open", gates=rows)
    reversed_rows = differential.RecordView(
        record=CLASSIFIED, status="open", gates=tuple(reversed(rows))
    )

    assert differential.gate_verdict(forward, VOCAB) == differential.gate_verdict(
        reversed_rows, VOCAB
    )


def test_a_blocker_the_population_does_not_hold_is_not_treated_as_satisfied() -> None:
    view = differential.RecordView(
        record=BLOCKED,
        status="open",
        dependencies=(differential.Edge("basicly-zz99", "blocks"),),
    )

    assert differential.verdicts({BLOCKED: view}, VOCAB)[BLOCKED].ready is False


def test_an_unknown_query_is_refused_rather_than_answered_none() -> None:
    verdict = differential.Verdict(phase="intake", ready=True, gates=differential.GateVerdict())

    with pytest.raises(differential.DifferentialError, match="unknown query"):
        verdict.answer("throughput")


def test_the_derivation_takes_its_vocabulary_as_an_argument() -> None:
    other = differential.Vocabulary(
        marker="[other-harness]",
        checkpoints=("triage",),
        required_gates=("build",),
        engine_gate_providers=frozenset({"other-verify"}),
        worktree_ref_prefix="tree=",
    )
    view = differential.RecordView(
        record=CLASSIFIED,
        status="open",
        comments=("[other-harness] checkpoint=triage approved",),
        gates=(differential.GateRow("build", "other-verify", True),),
    )

    verdict = differential.verdicts({CLASSIFIED: view}, other)[CLASSIFIED]

    assert verdict.gates.passed == ("build",)
    assert differential.approved_checkpoints(view, other) == ("triage",)
    assert differential.approved_checkpoints(view, VOCAB) == ()
