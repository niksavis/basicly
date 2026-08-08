"""The recorded form of a plan: what the decomposer writes down, and how it reads back.

Split out of ``test_plan_gate`` along the boundary :mod:`basicly.plan_record` was split
from :mod:`basicly.plan_gate` on — recorded form against judgement. These are the
`edges` group of basicly-u2hl.1: the declared graph reaching the tracker, the five plan
fields surviving on a bead body, and the round trip that is the only thing that fails
when the writer and the reader stop agreeing on the shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from basicly import decompose, plan_entry, plan_record, policy
from tests.plan_fixtures import DEMONSTRATION, FakeBr
from tests.plan_fixtures import install as _install
from tests.plan_fixtures import planned as _planned

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

# --- Declared edges reach the tracker ---------------------------------------


def test_decompose_records_declared_edges_on_the_tracker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ordering the scopes cannot express must still reach `br dep tree`.

    `a` and `b` own different files, so scope overlap puts them in separate parallel
    groups and derives no edge at all. The declared dependency is the only thing that
    can say `b` needs `a` first.
    """
    fake = FakeBr()
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
    fake = FakeBr()
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
    fake = FakeBr()
    _install(monkeypatch, fake)
    children = (_planned("a", "src/s.py"), _planned("b", "src/s.py", depends_on=("a",)))

    result = decompose.decompose(tmp_path, "feat", children)

    assert fake.edges == [("feat.2", "feat.1", "blocks")]
    assert result.children[1].depends_on == ("feat.1",)


def test_the_computed_chain_still_records_edges_a_plan_declared_nothing_about(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The negative control for the union: scope overlap keeps serializing on its own."""
    fake = FakeBr()
    _install(monkeypatch, fake)
    children = (_planned("a", "src/s.py"), _planned("b", "src/s.py"))

    decompose.decompose(tmp_path, "feat", children)

    assert fake.edges == [("feat.2", "feat.1", "blocks")]


def test_a_created_child_records_its_plan_fields_in_its_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The fields must outlive the plan document, which nothing keeps."""
    fake = FakeBr()
    _install(monkeypatch, fake)
    children = (_planned("a", "src/a.py"), _planned("b", "src/b.py", depends_on=("a",)))

    decompose.decompose(tmp_path, "feat", children)

    recorded = plan_record.parse_plan_section(fake.created[1][2])
    assert recorded.integrity == "L2"
    assert recorded.budget_tokens == 40_000
    assert recorded.depends_on == ("a",)
    assert recorded.scope == ("src/b.py",)
    # Free text with backticks inside it, so this is also the case that fails if the
    # recorded form ever starts unwrapping the value the way it unwraps a scalar.
    assert recorded.demonstration == DEMONSTRATION


def test_a_recorded_body_still_satisfies_the_definition_of_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Adding a section must not displace one the DoR requires (basicly-kjc5.44)."""
    fake = FakeBr()
    _install(monkeypatch, fake)

    decompose.decompose(tmp_path, "feat", (_planned("a", "src/a.py"),))

    body = fake.created[0][2]
    for heading in policy.required_sections("task"):
        assert heading in body


def test_a_recorded_empty_dependency_list_reads_back_as_declared_empty() -> None:
    """`none` must round-trip as `()`, not as the absence the entry predicate refuses."""
    body = plan_record.render_plan_section((), 1000, "L1", DEMONSTRATION)

    recorded = plan_record.parse_plan_section(f"{plan_record.PLAN_HEADING}\n\n{body}\n")

    assert recorded.depends_on == ()


def test_an_unfilled_demonstration_reads_back_as_absent_not_as_blank() -> None:
    """The fall-back records an empty value rather than a plausible one.

    `_child_body` gates before it records, so this only arises for a spec that reached
    the writer ungated — and then the recorded line must read back as *nothing
    declared*, so the bead is refused again rather than looking as if it answered.
    """
    body = plan_record.render_plan_section((), 1000, "L1", "")

    recorded = plan_record.parse_plan_section(f"{plan_record.PLAN_HEADING}\n\n{body}\n")

    assert recorded.demonstration is None
    assert recorded.integrity == "L1"


def test_a_decomposed_child_passes_the_predicate_that_gates_its_own_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The round trip: what decompose records is what build entry accepts.

    Two halves written apart drift — this is the only test that fails when the writer
    and the reader stop agreeing on the recorded form.
    """
    fake = FakeBr()
    _install(monkeypatch, fake)
    children = (_planned("a", "src/a.py"), _planned("b", "src/b.py", depends_on=("a",)))

    decompose.decompose(tmp_path, "feat", children)

    for issue_id, _title, body in fake.created:
        verdict = plan_entry.entry_verdict_for(issue_id, body)
        assert verdict.admitted, verdict.reason
