from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from basicly import (
    classify,
    decompose,
    dependency_graph,
    gate_source,
    handoff,
    label_source,
    loop_state,
    owned_store,
    policy,
    supervise,
    tracker,
    validate_gate,
)
from basicly.config import VERIFY_GATE_PROVIDER, PolicyConfig
from tests.plan_fixtures import planned
from tests.test_owned_write import no_br

__all__ = ["no_br"]

ROOT = "wpc-1"
CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)


@pytest.fixture
def flipped(work_repo: Path) -> Path:

    assert owned_store.tracker_mode(work_repo) == owned_store.MODE_OWNED
    for log in owned_store.ledger_dir(work_repo).glob("events-*.jsonl"):
        log.unlink()
    kit = owned_store.kit(work_repo)
    kit.events.append(
        owned_store.ledger_dir(work_repo),
        [
            kit.events.Draft(ROOT, kit.events.KIND_CREATED, {"labels": ["phase-6"]}),
            kit.events.Draft(ROOT, kit.events.KIND_STATUS, {"status": "open"}),
        ],
    )
    return work_repo


def _children() -> tuple[decompose.ChildSpec, ...]:

    return (
        planned(
            "port the blocked set",
            "src/basicly/dependency_graph.py",
            "src/basicly/gate_source.py",
        ),
        planned(
            "port the label query", "src/basicly/label_source.py", "src/basicly/owned_write.py"
        ),
    )


@pytest.mark.usefixtures("no_br")
def test_a_decomposition_creates_types_and_wires_its_children_from_the_ledger_alone(
    flipped: Path,
) -> None:

    result = decompose.decompose(flipped, ROOT, _children())

    assert list(result.serial_order) == [f"{ROOT}.1", f"{ROOT}.2"]
    kit = owned_store.kit(flipped)
    kinds = [
        event.kind
        for event in kit.read_ledger(owned_store.ledger_dir(flipped))
        if event.record == f"{ROOT}.1"
    ]
    assert kinds[:3] == [
        kit.events.KIND_CREATED,
        kit.events.KIND_STATUS,
        kit.migrate.KIND_EDGE,
    ]
    assert handoff.entry_verdict(flipped, ROOT, handoff.IMPLEMENTATION_PLAN).admitted


@pytest.mark.usefixtures("no_br")
def test_a_child_inherits_the_parents_label_so_the_pass_can_still_select_it(
    flipped: Path,
) -> None:

    decompose.decompose(flipped, ROOT, _children())

    assert label_source.labelled(flipped, "phase-6") == {
        ROOT: "open",
        f"{ROOT}.1": "open",
        f"{ROOT}.2": "open",
    }
    assert supervise.lane_selection(flipped, "phase-6", exclude=(ROOT,)) == (
        (f"{ROOT}.1", "open"),
        (f"{ROOT}.2", "open"),
    )


@pytest.mark.usefixtures("no_br")
def test_the_blocked_set_the_ready_set_and_the_cycles_all_answer(flipped: Path) -> None:

    first, second = _children()
    decompose.decompose(flipped, ROOT, (first, replace(second, depends_on=(first.title,))))

    assert loop_state.blocked_ids(flipped) == (f"{ROOT}.2",)
    assert dependency_graph.blocking_cycles(flipped) == ()
    assert [node.issue_id for node in loop_state.ready_ranked(flipped)] == [f"{ROOT}.1"]


@pytest.mark.usefixtures("no_br")
def test_a_cycle_the_engine_would_have_created_is_refused_from_the_ledger_alone(
    flipped: Path,
) -> None:

    result = decompose.decompose(flipped, ROOT, _children())
    tracker.write(flipped, ["dep", "add", f"{ROOT}.1", f"{ROOT}.2", "-t", "blocks"])
    tracker.write(flipped, ["dep", "add", f"{ROOT}.2", f"{ROOT}.1", "-t", "blocks"])

    assert dependency_graph.blocking_cycles(flipped) == ((f"{ROOT}.1", f"{ROOT}.2"),)
    with pytest.raises(RuntimeError, match="introduced a dependency cycle"):
        decompose._assert_no_new_cycles(flipped, set(result.serial_order))


@pytest.mark.usefixtures("no_br")
def test_typing_gating_and_closing_a_bead_all_land_in_the_ledger(flipped: Path) -> None:

    classify.classify(flipped, ROOT, "feature", scope=("src/basicly/**",))
    validate_gate.record_verdict(flipped, ROOT, passed=True)
    tracker.write(flipped, ["close", ROOT, "--reason", "shipped by the harness loop"])

    record = tracker.read_record(flipped, ROOT)
    assert record is not None
    assert record["issue_type"] == "feature"
    assert record["status"] == "closed"
    assert gate_source.read_gates(flipped, ROOT) == [
        {"gate": validate_gate.VALIDATE_GATE, "provider": VERIFY_GATE_PROVIDER, "passed": True}
    ]
    assert policy.gate_status(flipped, ROOT, CONFIG).required_missing == ("verify",)
