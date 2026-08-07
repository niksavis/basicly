"""The plan gate: the five fields a unit must declare before BUILD spends on it.

Every test here is a control pair wherever a control pair is possible — the same plan
with the field present and with it absent — because a gate that only ever sees good
input cannot be shown to bind. The four groups match the four acceptance criteria of
basicly-u2hl.1, and the group keywords (`cycle`, `edges`, `entry`) are the ones those
criteria name as their checks.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from basicly import br, decompose, plan_gate, policy
from basicly.decompose import ChildSpec


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeBr:
    """Stateful stand-in for the br CLI, routed by subcommand.

    Hands out sequential child ids on create and records every dep-add edge, which is
    what the declared-graph assertions read. Deliberately raises on any call it was not
    taught, so a test cannot pass because the decomposer quietly stopped calling br.
    """

    def __init__(self, *, records: dict[str, dict] | None = None) -> None:
        self.records = records or {}
        self.created: list[tuple[str, str, str]] = []  # (id, title, body)
        self.edges: list[tuple[str, str, str]] = []  # (issue, depends_on, type)
        self._counter = 0

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["create"]:
            self._counter += 1
            issue_id = f"feat.{self._counter}"
            self.created.append((issue_id, args[1], args[args.index("-d") + 1]))
            return _Proc(json.dumps({"id": issue_id}))
        if args[:1] == ["show"]:
            record = self.records.get(args[1], {"id": args[1], "labels": []})
            return _Proc(json.dumps([record]))
        if args[:2] == ["dep", "add"]:
            self.edges.append((args[2], args[3], args[args.index("-t") + 1]))
            return _Proc("")
        if args[:2] == ["dep", "cycles"]:
            return _Proc(json.dumps({"cycles": [], "count": 0}))
        if args[:2] == ["comments", "list"]:
            return _Proc(json.dumps([]))
        if args[:2] == ["comments", "add"]:
            return _Proc("")
        raise AssertionError(f"unexpected br call: {args}")


def _install(monkeypatch: pytest.MonkeyPatch, fake: Callable[..., _Proc]) -> None:
    monkeypatch.setattr(decompose, "_run_br", fake)
    monkeypatch.setattr(br, "try_run_br", fake)


def _planned(title: str, *scope: str, **overrides: object) -> ChildSpec:
    """A child that passes the gate, so a test can remove exactly one thing."""
    fields: dict[str, object] = {
        "title": title,
        "acceptance": ("given a plan when it is gated then it passes",),
        "scope": scope or (f"src/{title}.py",),
        "depends_on": (),
        "budget_tokens": 40_000,
        "integrity": "L2",
    }
    fields.update(overrides)
    return ChildSpec(**fields)  # type: ignore[arg-type]


def _plan_payload(*children: dict) -> dict:
    return {"children": list(children)}


def _child_payload(title: str, **overrides: object) -> dict:
    payload: dict[str, object] = {
        "title": title,
        "acceptance": ["given a plan when it is gated then it passes"],
        "scope": [f"src/{title}.py"],
        "depends_on": [],
        "budget_tokens": 40_000,
        "integrity": "L2",
    }
    payload.update(overrides)
    return payload


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
    fake = _FakeBr()
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
    fake = _FakeBr()
    _install(monkeypatch, fake)
    bare = ChildSpec(title="a", acceptance=("does the thing",), scope=("src/a.py",))

    with pytest.raises(plan_gate.PlanGateError, match="integrity"):
        decompose.decompose(tmp_path, "feat", (bare,))

    assert fake.created == []


# --- Declared edges reach the tracker ---------------------------------------


def test_decompose_records_declared_edges_on_the_tracker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ordering the scopes cannot express must still reach `br dep tree`.

    `a` and `b` own different files, so scope overlap puts them in separate parallel
    groups and derives no edge at all. The declared dependency is the only thing that
    can say `b` needs `a` first.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    children = (_planned("a", "src/a.py"), _planned("b", "src/b.py", depends_on=("a",)))

    result = decompose.decompose(tmp_path, "feat", children)

    assert fake.edges == [("feat.2", "feat.1", "blocks")]
    assert result.children[1].depends_on == ("feat.1",)
    # The grouping still reports them as scope-disjoint; the edge is what orders them.
    assert result.parallel_groups == 2


def test_declared_edges_resolve_sibling_titles_to_the_ids_just_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A plan is written before anything is recorded, so it can only name titles."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    children = (
        _planned("a", "src/a.py"),
        _planned("b", "src/b.py"),
        _planned("c", "src/c.py", depends_on=("a", "b")),
    )

    decompose.decompose(tmp_path, "feat", children)

    assert fake.edges == [("feat.3", "feat.1", "blocks"), ("feat.3", "feat.2", "blocks")]


def test_a_declared_edges_duplicate_of_the_computed_chain_is_recorded_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two children sharing a scope already chain; declaring it too must not double it."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    children = (_planned("a", "src/s.py"), _planned("b", "src/s.py", depends_on=("a",)))

    result = decompose.decompose(tmp_path, "feat", children)

    assert fake.edges == [("feat.2", "feat.1", "blocks")]
    assert result.children[1].depends_on == ("feat.1",)


def test_the_computed_chain_still_records_edges_a_plan_declared_nothing_about(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The negative control for the union: scope overlap keeps serializing on its own."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    children = (_planned("a", "src/s.py"), _planned("b", "src/s.py"))

    decompose.decompose(tmp_path, "feat", children)

    assert fake.edges == [("feat.2", "feat.1", "blocks")]


def test_a_created_child_records_its_plan_fields_in_its_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The fields must outlive the plan document, which nothing keeps."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    children = (_planned("a", "src/a.py"), _planned("b", "src/b.py", depends_on=("a",)))

    decompose.decompose(tmp_path, "feat", children)

    recorded = plan_gate.parse_plan_section(fake.created[1][2])
    assert recorded.integrity == "L2"
    assert recorded.budget_tokens == 40_000
    assert recorded.depends_on == ("a",)
    assert recorded.scope == ("src/b.py",)


def test_a_recorded_body_still_satisfies_the_definition_of_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Adding a section must not displace one the DoR requires (basicly-kjc5.44)."""
    fake = _FakeBr()
    _install(monkeypatch, fake)

    decompose.decompose(tmp_path, "feat", (_planned("a", "src/a.py"),))

    body = fake.created[0][2]
    for heading in policy.required_sections("task"):
        assert heading in body


def test_a_recorded_empty_dependency_list_reads_back_as_declared_empty() -> None:
    """`none` must round-trip as `()`, not as the absence the entry predicate refuses."""
    body = plan_gate.render_plan_section((), 1000, "L1")

    recorded = plan_gate.parse_plan_section(f"{plan_gate.PLAN_HEADING}\n\n{body}\n")

    assert recorded.depends_on == ()


# --- The build entry predicate ----------------------------------------------


def _recorded_body(**overrides: object) -> str:
    """A bead body carrying every plan field, so a test can drop exactly one."""
    fields: dict[str, object] = {
        "acceptance": ("given the lane when it is dispatched then it is held to this",),
        "scope": ("src/a.py",),
        "depends_on": (),
        "budget_tokens": 40_000,
        "integrity": "L2",
    }
    fields.update(overrides)
    sections = []
    if fields["acceptance"]:
        entries = "\n".join(f"- {item}" for item in fields["acceptance"])  # type: ignore[union-attr]
        sections.append(f"{plan_gate.ACCEPTANCE_HEADING}\n\n{entries}")
    if fields["scope"]:
        entries = "\n".join(f"- `{glob}`" for glob in fields["scope"])  # type: ignore[union-attr]
        sections.append(f"{plan_gate.SCOPE_HEADING}\n\n{entries}")
    plan_lines = []
    if fields["integrity"] is not None:
        plan_lines.append(f"- integrity: `{fields['integrity']}`")
    if fields["budget_tokens"] is not None:
        plan_lines.append(f"- budget: `{fields['budget_tokens']}`")
    if fields["depends_on"] is not None:
        declared = (
            ", ".join(f"`{dep}`" for dep in fields["depends_on"])  # type: ignore[union-attr]
            or plan_gate.NOTHING_DECLARED
        )
        plan_lines.append(f"- depends on: {declared}")
    if plan_lines:
        sections.append(plan_gate.PLAN_HEADING + "\n\n" + "\n".join(plan_lines))
    return "\n\n".join(sections) + "\n"


def test_a_fully_planned_unit_is_admitted_to_build_entry() -> None:
    """The positive control: a lane decomposed under the gate still dispatches."""
    verdict = plan_gate.entry_verdict_for("feat.1", _recorded_body())

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
    verdict = plan_gate.entry_verdict_for("feat.1", _recorded_body(**{field: absent}))

    assert not verdict.admitted
    assert verdict.missing == (field,)
    assert field in verdict.reason
    assert "feat.1" in verdict.reason


def test_build_entry_refuses_a_hand_filed_bead_that_carries_no_plan_at_all() -> None:
    """The population the decomposer never saw is the one this predicate exists for."""
    verdict = plan_gate.entry_verdict_for("feat.1", "Some prose and no headings.\n")

    assert verdict.missing == plan_gate.PLAN_FIELDS


def test_build_entry_reads_the_bead_from_the_tracker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The predicate's input is the recorded bead, not a spec held in memory."""
    fake = _FakeBr(records={"feat.1": {"id": "feat.1", "description": _recorded_body()}})
    _install(monkeypatch, fake)

    assert plan_gate.build_entry_verdict(tmp_path, "feat.1").admitted


def test_build_entry_refuses_a_unit_whose_record_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail closed: a tracker that did not answer is not a bead that declared a plan."""

    def unreadable(*_args: object, **_kwargs: object) -> _Proc:
        return _Proc("", returncode=1)

    monkeypatch.setattr(br, "try_run_br", unreadable)

    verdict = plan_gate.build_entry_verdict(tmp_path, "feat.1")

    assert not verdict.admitted
    assert verdict.unreadable
    assert "could not be read" in verdict.reason


def test_a_decomposed_child_passes_the_predicate_that_gates_its_own_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The round trip: what decompose records is what build entry accepts.

    Two halves written apart drift — this is the only test that fails when the writer
    and the reader stop agreeing on the recorded form.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    children = (_planned("a", "src/a.py"), _planned("b", "src/b.py", depends_on=("a",)))

    decompose.decompose(tmp_path, "feat", children)

    for issue_id, _title, body in fake.created:
        verdict = plan_gate.entry_verdict_for(issue_id, body)
        assert verdict.admitted, verdict.reason
