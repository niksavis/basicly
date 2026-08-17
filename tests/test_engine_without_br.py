"""The acceptance criterion of basicly-wpc8, driven rather than described.

*Given the engine at tracker mode owned with br absent from PATH, when a bead is created
or typed or closed or given an edge, and when the ready set and the blocked set and
dependency cycles are computed, then every one succeeds from the ledger alone.*

Driven through the engine's own entry points — ``decompose.decompose``,
``classify.classify``, ``loop_state.blocked_ids``, ``loop_state.ready_ranked``,
``supervise.lane_selection``, ``validate_gate.record_verdict`` — rather than through the
seams underneath them, because what has to survive the flip is the engine. A seam-level
round trip would pass while a caller still spawned br at its own call site.

The fixture does not merely un-install br: **a spawn fails the test**. "br was absent and
the engine silently degraded to doing nothing" satisfies a weaker assertion and is exactly
the failure mode this bead could have.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from basicly import (
    br,
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
    validate_gate,
)
from basicly.config import VERIFY_GATE_PROVIDER, PolicyConfig
from tests.plan_fixtures import planned
from tests.test_owned_write import no_br

__all__ = ["no_br"]  # re-exported so the fixture resolves in this module

ROOT = "wpc-1"
CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)


@pytest.fixture
def flipped(work_repo: Path) -> Path:
    """This repo's tracked files, declared ``owned``, with the root bead in the ledger.

    ``work_repo`` rather than a bare ``tmp_path``: the decomposition reads the sizing
    config, the instruction overhead and the scope material off the checkout, so a
    synthetic directory would exercise the estimator's absence paths instead of the walk.
    The kit ships in the same tracked set, so the ledger it writes is the real one.
    """
    (work_repo / "basicly.toml").write_text(
        (work_repo / "basicly.toml")
        .read_text(encoding="utf-8")
        .replace(f'mode = "{owned_store.MODE_DUAL}"', f'mode = "{owned_store.MODE_OWNED}"'),
        encoding="utf-8",
    )
    assert owned_store.tracker_mode(work_repo) == owned_store.MODE_OWNED
    # The committed event log is tracked, so it arrives with the copy. Cleared, because
    # every query below is over the whole population: this repository's own 800-odd
    # records would answer the blocked set and the label query instead of the walk's.
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
    """Two children whose scopes do not overlap, so the graph carries no computed chain.

    Real modules, and two apiece: the sizing governor refuses a plan below
    ``working_set_min``, so a one-small-file scope would fail the decomposition before it
    reached the store this test is about (measured — one file gives 7209 tokens of 8000).
    """
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
    """Create, the declared type, the parent-child edge and the cycle check, in one walk.

    The ids are the assertion that the *mint* is the ledger's: br hands out
    ``<prefix>-<root>`` tokens of its own, so ``wpc-1.1`` and ``wpc-1.2`` can only have
    come from ``ids.next_child_id`` reading this ledger back.
    """
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
    # The plan handoff is a marker, and it has to be readable back for BUILD to enter.
    assert handoff.entry_verdict(flipped, ROOT, handoff.IMPLEMENTATION_PLAN).admitted


@pytest.mark.usefixtures("no_br")
def test_a_child_inherits_the_parents_label_so_the_pass_can_still_select_it(
    flipped: Path,
) -> None:
    """The label read and the label *inheritance*, which is the only owned label writer.

    Phase membership is a label rather than a re-parenting, so an uninherited label leaves
    the parent in the phase while none of the work under it is — and the engine's only way
    to put one there is the ``create`` this decomposition makes.
    """
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
    """The three queries the AC names, over a graph the decomposition itself recorded.

    A declared edge rather than a hand-appended one, so the blocked set is derived from
    what the engine wrote. The ready set excludes both the decomposed parent and the
    blocked child, which is what makes the blocked answer discriminating rather than a
    restatement of the population.
    """
    first, second = _children()
    decompose.decompose(flipped, ROOT, (first, replace(second, depends_on=(first.title,))))

    assert loop_state.blocked_ids(flipped) == (f"{ROOT}.2",)
    assert dependency_graph.blocking_cycles(flipped) == ()
    assert [node.issue_id for node in loop_state.ready_ranked(flipped)] == [f"{ROOT}.1"]


@pytest.mark.usefixtures("no_br")
def test_a_cycle_the_engine_would_have_created_is_refused_from_the_ledger_alone(
    flipped: Path,
) -> None:
    """The refusal, and its control: the same plan without the cycle decomposes.

    The cycle is closed behind the plan gate's back — by recording the reverse edge after
    the fact — because the gate refuses a declared cycle before anything is created, and
    what this asserts is the *post-record* check reading the owned graph.
    """
    result = decompose.decompose(flipped, ROOT, _children())
    br.write(flipped, ["dep", "add", f"{ROOT}.1", f"{ROOT}.2", "-t", "blocks"])
    br.write(flipped, ["dep", "add", f"{ROOT}.2", f"{ROOT}.1", "-t", "blocks"])

    assert dependency_graph.blocking_cycles(flipped) == ((f"{ROOT}.1", f"{ROOT}.2"),)
    with pytest.raises(RuntimeError, match="introduced a dependency cycle"):
        decompose._assert_no_new_cycles(flipped, set(result.serial_order))


@pytest.mark.usefixtures("no_br")
def test_typing_gating_and_closing_a_bead_all_land_in_the_ledger(flipped: Path) -> None:
    """The write half of the AC, read back through the surface that gates on it.

    The gate is asserted through ``policy.gate_status`` rather than through the row list:
    the engine's question is *may this advance*, and the classification behind it — whose
    provider counts, which gate is required — is what has to survive the flip.
    """
    classify.classify(flipped, ROOT, "feature", scope=("src/basicly/**",))
    validate_gate.record_verdict(flipped, ROOT, passed=True)
    br.write(flipped, ["close", ROOT, "--reason", "shipped by the harness loop"])

    record = br.read_record(flipped, ROOT)
    assert record is not None
    assert record["issue_type"] == "feature"
    assert record["status"] == "closed"
    assert gate_source.read_gates(flipped, ROOT) == [
        {"gate": validate_gate.VALIDATE_GATE, "provider": VERIFY_GATE_PROVIDER, "passed": True}
    ]
    assert policy.gate_status(flipped, ROOT, CONFIG).required_missing == ("verify",)
