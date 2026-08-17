"""The plan gate: what a unit must declare before BUILD spends tokens on it.

Every test here is a control pair wherever a control pair is possible — the same plan
with the field present and with it absent — because a gate that only ever sees good
input cannot be shown to bind. The groups match the acceptance criteria of
basicly-u2hl.1, and the group keywords (`cycle`, `entry`) are the ones those criteria
name as their checks. The `edges` group and the decompose round trip are in
``test_plan_record.py``, on the recorded-form-against-judgement boundary
:mod:`basicly.plan_record` was split from :mod:`basicly.plan_gate` on; the sixth field
D18 added is in ``test_plan_demonstration.py``, because only one of the two halves here
binds on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from basicly import decompose, plan_entry, plan_gate, plan_record
from basicly.decompose import ChildSpec
from tests import fake_tracker
from tests.plan_fixtures import FakeBr, Proc
from tests.plan_fixtures import child_payload as _child_payload
from tests.plan_fixtures import install as _install
from tests.plan_fixtures import plan_payload as _plan_payload
from tests.plan_fixtures import planned as _planned
from tests.plan_fixtures import recorded_body as _recorded_body

if TYPE_CHECKING:
    from pathlib import Path

# --- The five required fields, at plan load ---------------------------------


def test_a_complete_plan_loads() -> None:
    """The positive control: nothing is refused when every field is declared."""
    children = decompose.parse_children(_plan_payload(_child_payload("a"), _child_payload("b")))

    assert [child.title for child in children] == ["a", "b"]
    assert children[0].budget_tokens == 40_000
    assert children[0].integrity == "L2"
    assert children[0].depends_on == ()


@pytest.mark.parametrize("field", plan_gate.PLAN_FIELDS)
def test_loading_a_plan_missing_one_field_is_refused_naming_it(field: str) -> None:
    """Each of the five is load-bearing on its own, and the refusal names which.

    Two refusal paths, both ``ValueError`` and both naming the field: ``acceptance``
    and ``scope`` are refused by the entry parser that predates the gate, the three
    new fields by the gate itself. Parametrising over the whole set is what stops one
    of the five from quietly becoming optional.
    """
    payload = _child_payload("a")
    del payload[field]

    with pytest.raises(ValueError) as caught:
        decompose.parse_children(_plan_payload(payload))

    assert field in str(caught.value)
    assert "children[0]" in str(caught.value)


@pytest.mark.parametrize("field", ["depends_on", "budget_tokens", "integrity"])
def test_the_gate_owns_the_refusal_for_the_fields_it_added(field: str) -> None:
    """The three new fields refuse as a gate verdict a caller can read, not a bare raise."""
    payload = _child_payload("a")
    del payload[field]

    with pytest.raises(plan_gate.PlanGateError) as caught:
        decompose.parse_children(_plan_payload(payload))

    assert caught.value.verdict.refused
    assert plan_gate.missing_fields(_planned("a", **{field: None})) == (field,)


def test_a_plan_gate_refusal_is_a_value_error() -> None:
    """Callers that already handled a schema refusal handle this one unchanged.

    ``loop._proposed_children`` and ``cli`` both catch ``ValueError`` around the plan
    load; a refusal that escaped as a new exception type would crash the loop instead
    of falling back to a human, which is the opposite of blocking.
    """
    with pytest.raises(ValueError):
        decompose.parse_children(_plan_payload(_child_payload("a", integrity=None)))


def test_a_declared_empty_dependency_list_is_not_a_missing_one() -> None:
    """`[]` says "nothing blocks this"; an absent key says nothing at all."""
    declared = _planned("a")
    silent = ChildSpec(
        title="a",
        acceptance=("does the thing",),
        scope=("src/a.py",),
        budget_tokens=40_000,
        integrity="L2",
    )

    assert plan_gate.missing_fields(declared) == ()
    assert plan_gate.missing_fields(silent) == ("depends_on",)


def test_every_missing_field_is_reported_in_one_pass() -> None:
    """An author who fixes one field per round trip pays a dispatch for each."""
    bare = ChildSpec(title="a", acceptance=(), scope=())

    verdict = plan_gate.gate_plan((bare,))

    assert verdict.refused
    for field in plan_gate.PLAN_FIELDS:
        assert field in verdict.reason


def test_an_unknown_integrity_level_is_refused() -> None:
    """A level outside the three selects no gate set, tier or rework allowance."""
    verdict = plan_gate.gate_plan((_planned("a", integrity="high"),))

    assert verdict.refused
    assert "'high'" in verdict.reason
    assert "L1" in verdict.reason


def test_a_budget_that_cannot_be_spent_is_refused() -> None:
    """Zero tokens is a declared field carrying no decision."""
    verdict = plan_gate.gate_plan((_planned("a", budget_tokens=0),))

    assert verdict.refused
    assert "budget" in verdict.reason


def test_a_dependency_on_a_title_the_plan_does_not_contain_is_refused() -> None:
    """An edge that resolves to nothing would be silently dropped at record time."""
    verdict = plan_gate.gate_plan((_planned("a", depends_on=("ghost",)),))

    assert verdict.refused
    assert "'ghost'" in verdict.reason


def test_duplicate_titles_are_refused() -> None:
    """A title-keyed graph with a duplicate key names an edge nobody can resolve."""
    verdict = plan_gate.gate_plan((_planned("a", "src/a.py"), _planned("a", "src/b.py")))

    assert verdict.refused
    assert "more than one child" in verdict.reason


def test_a_malformed_field_still_raises_where_it_is_read() -> None:
    """Shape errors are the entry's own problem, and stay a parse-time ValueError."""
    with pytest.raises(ValueError, match="budget_tokens"):
        decompose.parse_children(_plan_payload(_child_payload("a", budget_tokens="lots")))


def test_a_boolean_budget_is_not_a_number_of_tokens() -> None:
    """`True` is an int in Python and would otherwise record a one-token budget."""
    with pytest.raises(ValueError, match="budget_tokens"):
        decompose.parse_children(_plan_payload(_child_payload("a", budget_tokens=True)))


# --- Cycles in the declared graph -------------------------------------------


def test_a_two_child_cycle_is_refused_naming_both_members() -> None:
    """A bare `the plan has a cycle` is not something an author can act on."""
    verdict = plan_gate.gate_plan((
        _planned("a", depends_on=("b",)),
        _planned("b", depends_on=("a",)),
    ))

    assert verdict.cycles == (("a", "b"),)
    assert "a -> b -> a" in verdict.reason


def test_a_three_child_cycle_is_refused_naming_every_member() -> None:
    """A cycle found through a bridge names the whole ring, not the closing edge."""
    verdict = plan_gate.gate_plan((
        _planned("a", depends_on=("b",)),
        _planned("b", depends_on=("c",)),
        _planned("c", depends_on=("a",)),
    ))

    assert verdict.cycles == (("a", "b", "c"),)


def test_a_self_dependency_is_a_cycle_of_one() -> None:
    """A child that blocks itself is never ready, which is the same defect."""
    verdict = plan_gate.gate_plan((_planned("a", depends_on=("a",)),))

    assert verdict.cycles == (("a",),)


def test_a_cycle_is_named_identically_whatever_order_it_is_declared_in() -> None:
    """The same graph must produce the same message; a rotation is not a new finding."""
    forward = plan_gate.gate_plan((
        _planned("a", depends_on=("b",)),
        _planned("b", depends_on=("c",)),
        _planned("c", depends_on=("a",)),
    ))
    rotated = plan_gate.gate_plan((
        _planned("c", depends_on=("a",)),
        _planned("b", depends_on=("c",)),
        _planned("a", depends_on=("b",)),
    ))

    assert forward.cycles == rotated.cycles == (("a", "b", "c"),)


def test_a_diamond_is_not_a_cycle() -> None:
    """The negative control: a node reachable by two paths is ordinary, not a loop."""
    verdict = plan_gate.gate_plan((
        _planned("a", depends_on=()),
        _planned("b", depends_on=("a",)),
        _planned("c", depends_on=("a",)),
        _planned("d", depends_on=("b", "c")),
    ))

    assert verdict.cycles == ()
    assert not verdict.refused


def test_decompose_refuses_a_cycle_and_creates_no_issue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A half-recorded decomposition is worse than none: nothing owns un-creating it."""
    fake = FakeBr()
    _install(monkeypatch, fake)
    children = (_planned("a", depends_on=("b",)), _planned("b", depends_on=("a",)))

    with pytest.raises(plan_gate.PlanGateError, match="cycle"):
        decompose.decompose(tmp_path, "feat", children)

    assert fake.created == []
    assert fake.edges == []


def test_decompose_refuses_a_plan_missing_a_field_and_creates_no_issue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The gate binds at decompose too, for a caller that built specs directly."""
    fake = FakeBr()
    _install(monkeypatch, fake)
    bare = ChildSpec(title="a", acceptance=("does the thing",), scope=("src/a.py",))

    with pytest.raises(plan_gate.PlanGateError, match="integrity"):
        decompose.decompose(tmp_path, "feat", (bare,))

    assert fake.created == []


# --- The build entry predicate ----------------------------------------------


def test_a_fully_planned_unit_is_admitted_to_build_entry() -> None:
    """The positive control: a lane decomposed under the gate still dispatches."""
    verdict = plan_entry.entry_verdict_for("feat.1", _recorded_body())

    assert verdict.admitted
    assert verdict.reason == ""


@pytest.mark.parametrize(
    ("field", "absent"),
    [
        ("acceptance", ()),
        ("scope", ()),
        ("depends_on", None),
        ("budget_tokens", None),
        ("integrity", None),
    ],
)
def test_build_entry_refuses_a_unit_missing_a_plan_field_naming_it(
    field: str, absent: object
) -> None:
    """The refusal has to say which field, or nobody can fix the lane."""
    verdict = plan_entry.entry_verdict_for("feat.1", _recorded_body(**{field: absent}))

    assert not verdict.admitted
    assert verdict.missing == (field,)
    assert field in verdict.reason
    assert "feat.1" in verdict.reason


def test_build_entry_admits_a_hand_filed_bead_that_carries_no_plan_section() -> None:
    """The ratchet: a bead the decomposer never wrote predates the gate (D8).

    This assertion was inverted once. Refusing the no-heading population made every
    granted dispatch of a pre-existing bead fail, which is a stopped harness rather
    than a bound one.
    """
    verdict = plan_entry.entry_verdict_for("feat.1", "Some prose and no headings.\n")

    assert verdict.admitted
    assert verdict.missing == ()


def test_build_entry_refuses_a_bead_whose_plan_section_is_present_but_empty() -> None:
    """Present-but-incomplete is the defect the ratchet still has to catch."""
    verdict = plan_entry.entry_verdict_for("feat.1", f"{plan_record.PLAN_HEADING}\n\nprose\n")

    assert not verdict.admitted
    assert verdict.missing == plan_gate.PLAN_FIELDS


def test_build_entry_reads_the_bead_from_the_tracker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The predicate's input is the recorded bead, not a spec held in memory."""
    fake = FakeBr(records={"feat.1": {"id": "feat.1", "description": _recorded_body()}})
    _install(monkeypatch, fake)

    assert plan_entry.build_entry_verdict(tmp_path, "feat.1").admitted


def test_build_entry_refuses_a_unit_whose_record_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail closed: a tracker that did not answer is not a bead that declared a plan."""

    def unreadable(*_args: object, **_kwargs: object) -> Proc:
        return Proc("", returncode=1)

    fake_tracker.install(monkeypatch, unreadable)

    verdict = plan_entry.build_entry_verdict(tmp_path, "feat.1")

    assert not verdict.admitted
    assert verdict.unreadable
    assert "could not be read" in verdict.reason
