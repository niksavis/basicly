"""The recorded form of a plan: what the decomposer writes down, and how it reads back.

Split out of ``test_plan_gate`` along the boundary :mod:`basicly.plan_record` was split
from :mod:`basicly.plan_gate` on — recorded form against judgement. These are the
`edges` group of basicly-u2hl.1: the declared graph reaching the tracker, the five plan
fields surviving on a bead body, and the round trip that is the only thing that fails
when the writer and the reader stop agreeing on the shape.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from basicly import br, decompose, plan_gate, plan_record, policy
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

    recorded = plan_record.parse_plan_section(fake.created[1][2])
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
    body = plan_record.render_plan_section((), 1000, "L1")

    recorded = plan_record.parse_plan_section(f"{plan_record.PLAN_HEADING}\n\n{body}\n")

    assert recorded.depends_on == ()


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
