"""Tests for the decomposer & dependency-graph builder (onb.4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import decompose
from basicly.decompose import ChildSpec


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeBr:
    """Stateful stand-in for the br CLI, routed by subcommand.

    Hands out sequential child ids on create, records dep-add edges, and reports
    no cycles unless seeded with one, exactly enough to exercise the decomposer.
    """

    def __init__(self, *, cycles: list[list[str]] | None = None) -> None:
        self.cycles = cycles or []
        self.created: list[tuple[str, str, str]] = []  # (id, title, body)
        self.edges: list[tuple[str, str]] = []  # (issue, depends_on)
        self._counter = 0

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["create"]:
            return self._create(args)
        if args[:2] == ["dep", "add"]:
            self.edges.append((args[2], args[3]))
            return _Proc("")
        if args[:2] == ["dep", "cycles"]:
            return _Proc(json.dumps({"cycles": self.cycles, "count": len(self.cycles)}))
        raise AssertionError(f"unexpected br call: {args}")

    def _create(self, args: list[str]) -> _Proc:
        self._counter += 1
        issue_id = f"feat.{self._counter}"
        title = args[1]
        body = args[args.index("-d") + 1]
        self.created.append((issue_id, title, body))
        return _Proc(json.dumps({"id": issue_id}))


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr) -> None:
    monkeypatch.setattr(decompose, "_run_br", fake)


def _child(title: str, *scope: str) -> ChildSpec:
    return ChildSpec(title=title, acceptance=("does the thing",), scope=scope or (title,))


# --- Deterministic glob overlap ---------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("src/basicly/decompose.py", "src/basicly/cli.py", False),
        ("src/basicly/decompose.py", "src/basicly/decompose.py", True),
        ("src/**/*.py", "src/basicly/verify.py", True),
        ("tests/**", "tests/test_decompose.py", True),
        ("src/*.py", "docs/*.md", False),
        ("src/a/*.py", "src/b/*.py", False),
        ("./src/x.py", "src/x.py", True),
    ],
)
def test_globs_overlap(a: str, b: str, expected: bool) -> None:
    """Glob intersection is symmetric and matches hand-computed expectations."""
    assert decompose.globs_overlap(a, b) is expected
    assert decompose.globs_overlap(b, a) is expected


def test_disjoint_scopes_are_separate_groups() -> None:
    """Pairwise-disjoint scopes each land in their own parallel group."""
    children = (_child("a", "src/a.py"), _child("b", "src/b.py"), _child("c", "src/c.py"))
    assert decompose.group_children(children) == (0, 1, 2)


def test_overlapping_scopes_share_a_group() -> None:
    """Any scope overlap unions children into one serialized group."""
    children = (
        _child("a", "src/shared.py"),
        _child("b", "src/shared.py", "src/b.py"),
        _child("c", "src/c.py"),
    )
    assert decompose.group_children(children) == (0, 0, 1)


def test_overlap_is_transitive_via_a_bridge() -> None:
    """A overlaps B and B overlaps C bridges A and C into one serial group."""
    children = (
        _child("a", "src/a.py", "src/x.py"),
        _child("b", "src/x.py", "src/y.py"),
        _child("c", "src/y.py"),
    )
    assert decompose.group_children(children) == (0, 0, 0)


def test_chain_predecessors_are_within_group_only() -> None:
    """Only consecutive same-group members chain; group starts have no predecessor."""
    groups = (0, 0, 1, 0)
    assert decompose.chain_predecessors(groups) == (None, 0, None, 1)


# --- Plan parsing -----------------------------------------------------------


def test_load_plan_text_json_and_toml_agree() -> None:
    """The same plan in JSON and TOML parses to identical child specs."""
    child = {"title": "t", "acceptance": ["ac"], "scope": ["src/x.py"], "type": "bug"}
    json_children = decompose.load_plan_text(json.dumps({"children": [child]}), "json")
    toml_children = decompose.load_plan_text(
        '[[children]]\ntitle = "t"\nacceptance = ["ac"]\nscope = ["src/x.py"]\ntype = "bug"\n',
        "toml",
    )
    assert json_children == toml_children
    assert json_children[0] == ChildSpec("t", ("ac",), ("src/x.py",), "bug")


def test_load_plan_file_detects_format_by_suffix(tmp_path: Path) -> None:
    """A .toml plan file is parsed as TOML."""
    plan = tmp_path / "plan.toml"
    plan.write_text('[[children]]\ntitle = "t"\nacceptance = ["ac"]\nscope = ["s"]\n', "utf-8")
    assert decompose.load_plan_file(plan) == (ChildSpec("t", ("ac",), ("s",)),)


def test_parse_children_rejects_empty() -> None:
    """A plan with no children is a loud error, not a silent no-op."""
    with pytest.raises(ValueError, match="non-empty 'children'"):
        decompose.parse_children({"children": []})


def test_parse_children_requires_scope() -> None:
    """A child without a scope can't have its parallel-safety computed — reject it."""
    with pytest.raises(ValueError, match="'scope'"):
        decompose.parse_children({"children": [{"title": "t", "acceptance": ["ac"]}]})


def test_parse_children_requires_acceptance() -> None:
    """A child without acceptance criteria would fail DoR — reject it up front."""
    with pytest.raises(ValueError, match="'acceptance'"):
        decompose.parse_children({"children": [{"title": "t", "scope": ["s"]}]})


# --- Recording in br --------------------------------------------------------


def test_decompose_parallel_children_get_no_sibling_deps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Disjoint scopes create children with acceptance bodies and no blocks chain."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    children = (_child("a", "src/a.py"), _child("b", "src/b.py"))

    result = decompose.decompose(tmp_path, "feat", children)

    assert fake.edges == []  # parallel-safe: no serial chain
    assert result.parallel_groups == 2
    assert result.groups == (("feat.1",), ("feat.2",))
    assert result.serial_order == ("feat.1", "feat.2")
    # Every child body carries the DoR section and its scope.
    assert all("## Acceptance Criteria" in body for _id, _title, body in fake.created)
    assert "src/a.py" in fake.created[0][2]


def test_decompose_overlapping_children_are_chained_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Overlapping scopes emit a fixed serial blocks chain in declared order."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    children = (_child("a", "src/shared.py"), _child("b", "src/shared.py"))

    result = decompose.decompose(tmp_path, "feat", children)

    assert fake.edges == [("feat.2", "feat.1")]  # b depends on a
    assert result.parallel_groups == 1
    assert result.groups == (("feat.1", "feat.2"),)
    assert result.children[1].depends_on == ("feat.1",)


def test_decompose_raises_on_introduced_cycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A cycle involving a freshly-created child aborts loudly."""
    fake = _FakeBr(cycles=[["feat.1", "feat.2"]])
    _install(monkeypatch, fake)
    with pytest.raises(RuntimeError, match="cycle"):
        decompose.decompose(tmp_path, "feat", (_child("a", "src/s.py"), _child("b", "src/s.py")))


def test_preview_matches_recorded_grouping(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--dry-run preview computes the same groups/chains the recording would."""
    children = (_child("a", "src/s.py"), _child("b", "src/s.py"), _child("c", "src/c.py"))
    planned = decompose.preview(children)
    assert [p.group for p in planned] == [0, 0, 1]
    assert [p.predecessor for p in planned] == [None, 0, None]

    # And recording produces a matching graph.
    _install(monkeypatch, _FakeBr())
    result = decompose.decompose(tmp_path, "feat", children)
    assert result.parallel_groups == 2
