"""Tests for the shadow differential against the live tracker (basicly-vkh0.18).

The bead's two acceptance criteria pull in opposite directions, and the second is the one
that decides whether the first means anything:

- **The two agree on every query.** Asserted on an authored population that spans the whole
  verdict space — every phase rung the ladder can reach, both answers to the ready question,
  and a gate that passes, one that fails and one that is missing — because a comparison whose
  answers never vary cannot tell agreement from silence. `test_each_query_reports_its_own_
  disagreement` is the control pair: one mutation per query, each moving *only* that query,
  so a clean report is a fact about the stores rather than about the comparison never firing.
- **A re-import of the tracker's own export is refused.** Asserted on this repo's real
  `.beads/issues.jsonl` — the live tracker's whole history — and the assertion is the
  sharpest form of §5.1's argument: the re-import agrees with the owned ledger on *every*
  query it can express, zero disagreements, and the run is refused anyway with the reason
  recorded. A synthetic fixture could not show that, because the whole point is that the
  agreement is real and worthless.

`test_a_live_reference_is_not_refused` is the discrimination control for the audit: without
it, an audit that refused everything would pass both criteria above.

Everything the module would take from its host is test data — the wall clock, the ledger
lock's timeout, the derivation's vocabulary, and the reference side itself, which is an
authored mapping standing in for what the engine reads out of ``br show --json`` and
``br gate list``. Nothing here sleeps, spawns a process, or reads the host's platform.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
LIVE_EXPORT = REPO_ROOT / ".beads" / "issues.jsonl"


def _load(path: Path, name: str) -> Any:
    """Load a standalone kit module by path, the way a consumer without basicly would."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


differential = _load(KIT_DIR / "differential.py", "tracker_differential")
# The importer and event log the differential itself loaded, not second copies: two loads of
# one file give two `Event` classes, and a comparison across the two is unequal for the wrong
# reason.
migrate = differential.migrate
events = differential.events

SOURCE = "beads/issues.jsonl"
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
    """A comment approving the *name* checkpoint, spelled as the engine records it."""
    return differential.checkpoint_marker(name, VOCAB)


def _export_record(record: str, **overrides: Any) -> dict[str, Any]:
    """One record in the export's shape, carrying the fields a beads record carries."""
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
    """Comment rows in the export's shape. The source id keeps two identical texts two."""
    return [
        {"id": index, "text": text, "author": "niksa", "created_at": "2026-08-01T11:00:00Z"}
        for index, text in enumerate(texts, start=1)
    ]


def _dependency(target: str, edge_type: str) -> dict[str, Any]:
    """One outgoing edge in the export's spelling (``depends_on_id``/``type``)."""
    return {"depends_on_id": target, "type": edge_type}


# The authored population. Every phase rung the ladder can reach, both ready answers, and a
# gate that passes, one that fails and one that is missing — see the module docstring.
POPULATION: tuple[dict[str, Any], ...] = (
    # A decomposed parent: it has a child, so it derives `decompose` and is not itself work.
    _export_record(PARENT, issue_type="epic"),
    # A lane mid-build whose verify gate passed: bound worktree + green gate derives `verify`.
    _export_record(
        CHILD,
        status="in_progress",
        external_ref="worktree:lane-a:harness/lane-a",
        dependencies=[_dependency(PARENT, "parent-child")],
    ),
    # Classified and nothing else.
    _export_record(CLASSIFIED, comments=_comments(_checkpoint("classify"))),
    _export_record(CLOSED, status="closed"),
    # Ship approved with no binding and a green gate: landed, so the ship rung is reached.
    # Its blocker is closed, so it is also ready.
    _export_record(
        SHIPPED,
        comments=_comments(_checkpoint("ship")),
        dependencies=[_dependency(CLOSED, "blocks")],
    ),
    # Held behind an open blocker, with a failing gate: not ready, and back at intake.
    _export_record(BLOCKED, dependencies=[_dependency(SHIPPED, "blocks")]),
)

# The gate rows the live tracker holds for this population. Absent from any export by
# construction — `br gate report` writes them and `.beads/issues.jsonl` has no gate field.
LIVE_GATES: dict[str, tuple[tuple[str, str, bool], ...]] = {
    CHILD: (("verify", VERIFY_PROVIDER, True),),
    SHIPPED: (("verify", VERIFY_PROVIDER, True),),
    BLOCKED: (("verify", VERIFY_PROVIDER, False),),
}


def _gate_rows(record: str) -> tuple[Any, ...]:
    """The live tracker's gate rows for *record*, as the reference reports them."""
    return tuple(
        differential.GateRow(gate, provider, passed)
        for gate, provider, passed in LIVE_GATES.get(record, ())
    )


def _gate_drafts(record: str) -> list[Any]:
    """The same rows as ledger events, which is what the dual write will append."""
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
    """*records* serialised the way br writes the export: one JSON object per line."""
    return "".join(
        json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n" for record in records
    )


def _ledger(
    directory: Path,
    records: tuple[dict[str, Any], ...] = POPULATION,
    *,
    with_gates: bool = True,
) -> str:
    """Import *records* into a ledger at *directory*; return the snapshot text imported.

    The clock is injected, so nothing here depends on the host's wall clock or on whether it
    stepped backwards mid-test (§9.5).
    """
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
    """The reference side: what the engine reads out of ``br show`` plus ``br gate list``.

    Built from the same facts as the ledger but in the live tracker's own shape, so this
    stands in for a source the audit cannot distinguish from `br` — it declares no snapshot
    and it ignores the event set it is handed.
    """
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
    """A reference that answers from *views* and ignores the ledger — a live source's shape."""
    return differential.ReferenceSource(views=lambda _ledger_events: views)


def _without(record: str) -> tuple[dict[str, Any], ...]:
    """The authored population with *record* removed."""
    return tuple(item for item in POPULATION if item["id"] != record)


def _tombstone(directory: Path, record: str) -> None:
    """Delete *record* through `migrate`, which is the only route to a tombstone event.

    A tombstone is refused for a record the snapshot still asserts, so a deletion arrives as
    a **later import** whose text no longer carries the record and whose caller states it was
    deleted. That is the shape basicly-vkh0.17 produces on a real cutover, so testing against
    a hand-appended event would be testing a state the migration cannot reach.
    """
    snapshot = migrate.parse_snapshot(_snapshot_text(_without(record)), name=SOURCE)
    report = migrate.import_snapshot(directory, snapshot, deleted=[record], clock=lambda: CLOCK)
    assert report.tombstoned == [record], report.rejected


# --- the verdict space this suite rests on ------------------------------------


def test_the_authored_population_spans_every_query(tmp_path: Path) -> None:
    """The positive control: a constant answer set would make every test below vacuous."""
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


# --- AC 1: the two stores agree on every query --------------------------------


def test_every_query_agrees_when_the_two_stores_hold_the_same_facts(tmp_path: Path) -> None:
    """AC: phase derivation, the ready set and gate status agree, record by record."""
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
        # A second approved checkpoint moves the phase ladder and nothing else.
        (
            differential.QUERY_PHASE,
            CLASSIFIED,
            {"comments": (_checkpoint("classify"), _checkpoint("decompose"))},
        ),
        # Dropping the blocking edge releases the record; its phase rests on the failed gate
        # and its gate rows are untouched.
        (differential.QUERY_READY, BLOCKED, {"dependencies": ()}),
        # An advisory row is not a required gate, so it cannot reach `can_advance` and so
        # cannot move the phase.
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
    """The comparison discriminates per query: each mutation moves exactly one answer."""
    ledger = tmp_path / "ledger"
    _ledger(ledger)
    views = _live_views()
    views[record] = dataclasses.replace(views[record], **mutate)

    report = differential.run_differential(ledger, _live_source(views), VOCAB)

    assert [(item.record, item.query) for item in report.disagreements] == [(record, query)]
    assert not report.clean
    assert report.refusals == []


# --- AC 2: a re-import of the tracker's own export is refused -----------------


def test_a_reimport_of_the_tracker_own_export_is_refused_across_the_whole_history() -> None:
    """AC: pointed at a re-import of its own export, the run fails with the reason recorded.

    The subject is this repo's real tracker export, so "the whole history" is the whole
    history rather than a fixture's worth of it, and the demonstration is exact: the
    re-import agrees on every query, and the run is refused anyway.
    """
    text = LIVE_EXPORT.read_text(encoding="utf-8")
    snapshot = migrate.parse_snapshot(text, name=SOURCE)
    assert len(snapshot.records) > 100, "the live export is the subject; it must not be empty"

    with tempfile.TemporaryDirectory() as tmp:
        ledger = Path(tmp) / "ledger"
        migrate.import_snapshot(ledger, snapshot, clock=lambda: CLOCK)
        # The bad construction, spelled out: re-import the same export and answer from it.
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
    # The refusal is the whole finding: the two agreed on every query, for every record.
    assert report.disagreements == [], report.summary()
    assert report.records == len(snapshot.records)
    assert report.compared == report.records


def test_a_live_reference_is_not_refused(tmp_path: Path) -> None:
    """The audit's discrimination control: an audit that refused everything proves nothing."""
    ledger = tmp_path / "ledger"
    _ledger(ledger)

    baseline = _live_views()
    refusals, unproven = differential.audit_reference(
        _live_source(baseline), differential.read_ledger(ledger), baseline, VOCAB
    )

    assert refusals == []
    assert unproven == []


def test_a_reference_derived_from_the_owned_ledger_is_refused(tmp_path: Path) -> None:
    """A derivative that declares no snapshot is caught by the perturbation probe.

    This is the route the digest check cannot see: the reference reads the owned event log
    rather than an export, so it has no snapshot to declare and it agrees perfectly. The
    probe is what makes a perfect agreement a refusal.
    """
    ledger = tmp_path / "ledger"
    _ledger(ledger)
    source = differential.ReferenceSource(views=differential.views_from_events)

    report = differential.run_differential(ledger, source, VOCAB)

    assert [refusal.rule for refusal in report.refusals] == [differential.RULE_DERIVED_FROM_LEDGER]
    assert differential.LOSSY_SNAPSHOT_REASON in report.refusals[0].detail
    assert report.disagreements == [], "the derivative agrees with itself; that is the point"
    assert not report.clean


def test_an_export_the_ledger_was_not_imported_from_is_still_refused(tmp_path: Path) -> None:
    """Any snapshot-backed reference is refused, on the gate row's measured absence."""
    ledger = tmp_path / "ledger"
    _ledger(ledger)
    views = _live_views()
    source = differential.ReferenceSource(
        views=lambda _ledger_events: views,
        snapshot="{}\n",  # a different export entirely
    )

    report = differential.run_differential(ledger, source, VOCAB)

    assert [refusal.rule for refusal in report.refusals] == [differential.RULE_EXPORT_BACKED]
    assert differential.EXPORT_CANNOT_EXPRESS in report.refusals[0].detail
    assert not report.clean


# --- the vacuity guards -------------------------------------------------------


def test_a_query_whose_answers_never_vary_is_reported_inconclusive(tmp_path: Path) -> None:
    """A clean report over a constant answer set is the absence of evidence, and says so.

    The live case, not a hypothetical: on this repo's tracker every bead reports zero gate
    rows today, so the gate query is constant on both sides and agreement on it discriminates
    nothing. ``clean`` and ``conclusive`` are separate properties for exactly this run.
    """
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
    """The residual hole in the audit is closed by ``conclusive``, not by ``clean``.

    A reference that reads an export **from disk** while declaring no snapshot escapes both
    the digest check and the perturbation probe — the module docstring says so rather than
    claiming a completeness it does not have. What it cannot escape is the measured
    asymmetry: the export has no gate field, so such a reference can never supply a gate row,
    the gate query is constant, and the run is inconclusive however clean it looks. A caller
    deciding whether the flip is licensed has to ask both questions, and this is why.
    """
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
    """A reference that answers for a subset must not report clean by saying less."""
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
    """The other direction: a record only the reference knows is a finding, not a filter."""
    ledger = tmp_path / "ledger"
    _ledger(ledger)
    views = _live_views()
    views["basicly-ff66"] = differential.RecordView(record="basicly-ff66", status="open")

    report = differential.run_differential(ledger, _live_source(views), VOCAB)

    assert report.unknown == ["basicly-ff66"]
    assert not report.clean


def test_a_deleted_record_is_not_in_the_owned_ready_set(tmp_path: Path) -> None:
    """A tombstone leaves the status alone, so the flag is the only thing that can refuse it.

    `events._apply_tombstone` writes ``tombstoned`` and nothing else, by design — a delete
    leaves a tombstone rather than removing anything. So a record deleted while its status
    was dispatchable keeps that status, and a ready set derived from status alone still
    offers it. basicly-vkh0.19 puts the scheduler on this set, which turns that into work
    handed out on a record somebody deleted.
    """
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
    """Both stores say deleted, in their own spelling, so the report stays clean.

    The live tracker expresses a deletion by not returning the record; the owned ledger
    expresses it as an event and keeps the record in the fold. Counting that as *unanswered*
    would make every ledger carrying a tombstone permanently unclean — and a clean report is
    what licenses the flip, so no cutover could ever be licensed after the first deletion.
    """
    ledger = tmp_path / "ledger"
    _ledger(ledger)
    _tombstone(ledger, SHIPPED)

    source = _live_source(_live_views(_without(SHIPPED)))
    report = differential.run_differential(ledger, source, VOCAB)

    assert report.unanswered == []
    assert report.disagreements == []
    assert report.clean


def test_a_tombstone_the_reference_still_answers_for_is_a_disagreement(tmp_path: Path) -> None:
    """The exclusion is not a blanket filter on deleted records.

    A reference that still holds the record is the two stores disagreeing about whether it
    exists, which is exactly what shadow mode is for. It stays in the comparison, and
    :func:`is_ready` is what makes the disagreement visible.
    """
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
    """With nothing to perturb, the probe reports that it could not run."""
    ledger = tmp_path / "ledger"
    ledger.mkdir()

    report = differential.run_differential(ledger, _live_source({}), VOCAB)

    assert next(item.subject for item in report.inconclusive) == (
        differential.RULE_DERIVED_FROM_LEDGER
    )
    assert not report.conclusive


# --- the probe itself ---------------------------------------------------------


def test_the_probe_is_deterministic_and_writes_nothing(tmp_path: Path) -> None:
    """Two probes of one ledger are the same events, and the ledger is untouched.

    Determinism matters because the probe's verdict is a comparison of two answers: a probe
    that varied between calls would make an independent source look like a derivative. Taken
    from the file's bytes rather than from its mtime, which has a coarser resolution on some
    filesystems than the test takes to run.
    """
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
    """The probe has to move a derivative's answers, so it must move the owned ones."""
    ledger = tmp_path / "ledger"
    _ledger(ledger)
    ledger_events = differential.read_ledger(ledger)
    perturbed = differential.probe_events(ledger_events, VOCAB)

    before = differential.verdicts(differential.views_from_events(ledger_events), VOCAB)
    after = differential.verdicts(differential.views_from_events(perturbed), VOCAB)

    moved = [record for record in before if before[record] != after[record]]
    assert moved, "a probe that changes no verdict cannot detect a derivative"


# --- the derivation's own edges ----------------------------------------------


def test_a_ship_approval_on_an_unstarted_record_does_not_derive_ship() -> None:
    """The landed rule: an out-of-order ship approval must not read as shipped.

    `basicly-jr0l.49` is the defect — a ship approval recorded on a leaf that never built
    derived ``ship`` and closed the bead with zero work done. The mirrored derivation carries
    the same rule, so the same input must give the same answer here.
    """
    view = differential.RecordView(
        record=CLASSIFIED, status="open", comments=(_checkpoint("ship"),)
    )

    verdict = differential.verdicts({CLASSIFIED: view}, VOCAB)[CLASSIFIED]

    assert verdict.gates.missing == ("verify",)
    assert verdict.phase == "intake"


def test_a_required_gate_from_a_foreign_provider_is_disregarded() -> None:
    """`br gate report` authenticates nothing, so a foreign result cannot satisfy a gate."""
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
    """`br gate list` guarantees no row order, so the verdict must not depend on one."""
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
    """An unknown blocker is unknown, not closed — the fail-open reading would free the work."""
    view = differential.RecordView(
        record=BLOCKED,
        status="open",
        dependencies=(differential.Edge("basicly-zz99", "blocks"),),
    )

    assert differential.verdicts({BLOCKED: view}, VOCAB)[BLOCKED].ready is False


def test_an_unknown_query_is_refused_rather_than_answered_none() -> None:
    """A None answer would compare equal on both sides and read as agreement."""
    verdict = differential.Verdict(phase="intake", ready=True, gates=differential.GateVerdict())

    with pytest.raises(differential.DifferentialError, match="unknown query"):
        verdict.answer("throughput")


# --- the vocabulary is the host's, not the kit's -----------------------------


def test_the_derivation_takes_its_vocabulary_as_an_argument() -> None:
    """The kit reads no config, so a host with other names still gets right answers."""
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
    # Triage is this host's first rung, and the default vocabulary cannot see it at all.
    assert differential.approved_checkpoints(view, other) == ("triage",)
    assert differential.approved_checkpoints(view, VOCAB) == ()
