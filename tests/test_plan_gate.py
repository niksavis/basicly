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


def test_a_complete_plan_loads() -> None:
    children = decompose.parse_children(_plan_payload(_child_payload("a"), _child_payload("b")))

    assert [child.title for child in children] == ["a", "b"]
    assert children[0].budget_tokens == 40_000
    assert children[0].integrity == "L2"
    assert children[0].depends_on == ()


@pytest.mark.parametrize("field", plan_gate.PLAN_FIELDS)
def test_loading_a_plan_missing_one_field_is_refused_naming_it(field: str) -> None:

    payload = _child_payload("a")
    del payload[field]

    with pytest.raises(ValueError) as caught:
        decompose.parse_children(_plan_payload(payload))

    assert field in str(caught.value)
    assert "children[0]" in str(caught.value)


@pytest.mark.parametrize("field", ["depends_on", "budget_tokens", "integrity"])
def test_the_gate_owns_the_refusal_for_the_fields_it_added(field: str) -> None:
    payload = _child_payload("a")
    del payload[field]

    with pytest.raises(plan_gate.PlanGateError) as caught:
        decompose.parse_children(_plan_payload(payload))

    assert caught.value.verdict.refused
    assert plan_gate.missing_fields(_planned("a", **{field: None})) == (field,)


def test_a_plan_gate_refusal_is_a_value_error() -> None:

    with pytest.raises(ValueError):
        decompose.parse_children(_plan_payload(_child_payload("a", integrity=None)))


def test_a_declared_empty_dependency_list_is_not_a_missing_one() -> None:
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
    bare = ChildSpec(title="a", acceptance=(), scope=())

    verdict = plan_gate.gate_plan((bare,))

    assert verdict.refused
    for field in plan_gate.PLAN_FIELDS:
        assert field in verdict.reason


def test_an_unknown_integrity_level_is_refused() -> None:
    verdict = plan_gate.gate_plan((_planned("a", integrity="high"),))

    assert verdict.refused
    assert "'high'" in verdict.reason
    assert "L1" in verdict.reason


def test_a_budget_that_cannot_be_spent_is_refused() -> None:
    verdict = plan_gate.gate_plan((_planned("a", budget_tokens=0),))

    assert verdict.refused
    assert "budget" in verdict.reason


def test_a_dependency_on_a_title_the_plan_does_not_contain_is_refused() -> None:
    verdict = plan_gate.gate_plan((_planned("a", depends_on=("ghost",)),))

    assert verdict.refused
    assert "'ghost'" in verdict.reason


def test_duplicate_titles_are_refused() -> None:
    verdict = plan_gate.gate_plan((_planned("a", "src/a.py"), _planned("a", "src/b.py")))

    assert verdict.refused
    assert "more than one child" in verdict.reason


def test_a_malformed_field_still_raises_where_it_is_read() -> None:
    with pytest.raises(ValueError, match="budget_tokens"):
        decompose.parse_children(_plan_payload(_child_payload("a", budget_tokens="lots")))


def test_a_boolean_budget_is_not_a_number_of_tokens() -> None:
    with pytest.raises(ValueError, match="budget_tokens"):
        decompose.parse_children(_plan_payload(_child_payload("a", budget_tokens=True)))


def test_a_two_child_cycle_is_refused_naming_both_members() -> None:
    verdict = plan_gate.gate_plan((
        _planned("a", depends_on=("b",)),
        _planned("b", depends_on=("a",)),
    ))

    assert verdict.cycles == (("a", "b"),)
    assert "a -> b -> a" in verdict.reason


def test_a_three_child_cycle_is_refused_naming_every_member() -> None:
    verdict = plan_gate.gate_plan((
        _planned("a", depends_on=("b",)),
        _planned("b", depends_on=("c",)),
        _planned("c", depends_on=("a",)),
    ))

    assert verdict.cycles == (("a", "b", "c"),)


def test_a_self_dependency_is_a_cycle_of_one() -> None:
    verdict = plan_gate.gate_plan((_planned("a", depends_on=("a",)),))

    assert verdict.cycles == (("a",),)


def test_a_cycle_is_named_identically_whatever_order_it_is_declared_in() -> None:
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
    fake = FakeBr()
    _install(monkeypatch, fake)
    bare = ChildSpec(title="a", acceptance=("does the thing",), scope=("src/a.py",))

    with pytest.raises(plan_gate.PlanGateError, match="integrity"):
        decompose.decompose(tmp_path, "feat", (bare,))

    assert fake.created == []


def test_a_fully_planned_unit_is_admitted_to_build_entry() -> None:
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
    verdict = plan_entry.entry_verdict_for("feat.1", _recorded_body(**{field: absent}))

    assert not verdict.admitted
    assert verdict.missing == (field,)
    assert field in verdict.reason
    assert "feat.1" in verdict.reason


def test_build_entry_admits_a_hand_filed_bead_that_carries_no_plan_section() -> None:

    verdict = plan_entry.entry_verdict_for("feat.1", "Some prose and no headings.\n")

    assert verdict.admitted
    assert verdict.missing == ()


def test_build_entry_refuses_a_bead_whose_plan_section_is_present_but_empty() -> None:
    verdict = plan_entry.entry_verdict_for("feat.1", f"{plan_record.PLAN_HEADING}\n\nprose\n")

    assert not verdict.admitted
    assert verdict.missing == plan_gate.PLAN_FIELDS


def test_build_entry_reads_the_bead_from_the_tracker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBr(records={"feat.1": {"id": "feat.1", "description": _recorded_body()}})
    _install(monkeypatch, fake)

    assert plan_entry.build_entry_verdict(tmp_path, "feat.1").admitted


def test_build_entry_refuses_a_unit_whose_record_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    def unreadable(*_args: object, **_kwargs: object) -> Proc:
        return Proc("", returncode=1)

    fake_tracker.install(monkeypatch, unreadable)

    verdict = plan_entry.build_entry_verdict(tmp_path, "feat.1")

    assert not verdict.admitted
    assert verdict.unreadable
    assert "could not be read" in verdict.reason
