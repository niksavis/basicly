"""Tests for the decomposer & dependency-graph builder (onb.4)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from basicly import decompose, run_record
from basicly.config import SizingConfig
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


def _install(monkeypatch: pytest.MonkeyPatch, fake: Callable[..., _Proc]) -> None:
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


# --- Context-cost sizing (basicly-kjc5.2, factory design D8) -----------------


def _write(repo: Path, rel: str, chars: int) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * chars, encoding="utf-8")


def _sizing(**overrides) -> SizingConfig:
    defaults = {
        "working_set_min": 8_000,
        "working_set_max": 64_000,
        "build_factors": {"task": 3.0, "bug": 2.0, "chore": 1.5},
        "calibration_min_samples": 10,
        "calibration_window": 50,
    }
    defaults.update(overrides)
    return SizingConfig(**defaults)


def test_instruction_overhead_tokenizes_agents_md(tmp_path: Path) -> None:
    """Overhead is the projected AGENTS.md at chars/4; absent contributes zero."""
    assert decompose.instruction_overhead(tmp_path) == 0
    _write(tmp_path, "AGENTS.md", 8_000)
    assert decompose.instruction_overhead(tmp_path) == 2_000


def test_scope_read_cost_sums_matching_files_once(tmp_path: Path) -> None:
    """Matching files sum at chars/4, deduped across overlapping globs."""
    _write(tmp_path, "src/a.py", 400)
    _write(tmp_path, "src/b.py", 200)
    _write(tmp_path, "docs/c.md", 999)
    cost = decompose.scope_read_cost(tmp_path, ("src/*.py", "src/a.py"))
    assert cost == (400 + 200) // 4


def test_scope_read_cost_recursive_glob_and_greenfield(tmp_path: Path) -> None:
    """`**` spans directories; a glob matching nothing contributes zero."""
    _write(tmp_path, "src/pkg/deep/mod.py", 800)
    assert decompose.scope_read_cost(tmp_path, ("src/**/*.py",)) == 200
    assert decompose.scope_read_cost(tmp_path, ("brand/new/file.py",)) == 0


def test_estimate_cost_total_is_overhead_plus_factored_scope(tmp_path: Path) -> None:
    """Total = overhead + scope x class factor; unlisted classes use the task factor."""
    _write(tmp_path, "src/a.py", 4_000)  # 1000 scope tokens
    factors = {"task": 3.0, "bug": 2.0}
    task = decompose.estimate_cost(tmp_path, _child("t", "src/a.py"), factors, overhead=500)
    assert (task.scope_tokens, task.overhead_tokens, task.build_factor) == (1_000, 500, 3.0)
    assert task.total == 500 + 3_000
    bug = ChildSpec(title="b", acceptance=("a",), scope=("src/a.py",), type="bug")
    assert decompose.estimate_cost(tmp_path, bug, factors, overhead=0).total == 2_000
    spike = ChildSpec(title="s", acceptance=("a",), scope=("src/a.py",), type="spike")
    assert decompose.estimate_cost(tmp_path, spike, factors, overhead=0).total == 3_000


def test_parse_scope_section_round_trips_child_body() -> None:
    """The calibration scope parser reads exactly what _child_body records."""
    spec = _child("t", "src/**/*.py", "tests/test_x.py")
    body = decompose._child_body(spec)
    assert decompose._parse_scope_section(body) == ("src/**/*.py", "tests/test_x.py")
    assert decompose._parse_scope_section("no scope section here") == ()


class _FakeBrShow:
    """br stand-in for calibration: serves `show --json` for seeded beads."""

    def __init__(self, beads: dict[str, tuple[str, str]]) -> None:
        self.beads = beads  # id -> (issue_type, description)

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["show"]:
            issue_type, description = self.beads[args[1]]
            payload = [{"id": args[1], "issue_type": issue_type, "description": description}]
            return _Proc(json.dumps(payload))
        raise AssertionError(f"unexpected br call: {args}")


def _record_run_tokens(repo: Path, bead_id: str, tokens: int, *, estimated: bool = False) -> None:
    entry = run_record.build_record(
        agent="claude",
        handoff=False,
        returncode=0,
        duration_s=1.0,
        command=("claude",),
        tokens=tokens,
        estimated=estimated,
    )
    run_record.record(repo, bead_id, entry)


def test_calibration_returns_seeds_without_records(tmp_path: Path) -> None:
    """No run-records (or too few samples) leave the configured seeds untouched."""
    sizing = _sizing()
    assert decompose.calibrated_build_factors(tmp_path, sizing) == sizing.build_factors


def test_calibration_overrides_seed_past_min_samples(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Past calibration_min_samples the measured median replaces the class seed."""
    _write(tmp_path, "src/a.py", 4_000)  # 1000 scope tokens
    body = decompose._child_body(_child("t", "src/a.py"))
    _install(monkeypatch, _FakeBrShow({"b-1": ("task", body)}))
    for tokens in (4_000, 5_000, 6_000):  # factors 4.0, 5.0, 6.0 -> median 5.0
        _record_run_tokens(tmp_path, "b-1", tokens)

    sizing = _sizing(calibration_min_samples=3)
    factors = decompose.calibrated_build_factors(tmp_path, sizing)
    assert factors["task"] == 5.0
    assert factors["bug"] == 2.0  # other classes keep their seeds

    below_min = decompose.calibrated_build_factors(tmp_path, _sizing(calibration_min_samples=4))
    assert below_min["task"] == 3.0  # not enough samples: seed stands


def test_calibration_excludes_estimated_samples(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """chars/4-estimated samples never calibrate (design 7.5 down-weighting)."""
    _write(tmp_path, "src/a.py", 4_000)
    body = decompose._child_body(_child("t", "src/a.py"))
    _install(monkeypatch, _FakeBrShow({"b-1": ("task", body)}))
    for _ in range(5):
        _record_run_tokens(tmp_path, "b-1", 9_000, estimated=True)

    factors = decompose.calibrated_build_factors(tmp_path, _sizing(calibration_min_samples=1))
    assert factors["task"] == 3.0


def test_govern_refuses_oversized_child_before_recording(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A child above working_set_max refuses the whole plan; nothing is created."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _write(tmp_path, "src/big.py", 400_000)  # 100k tokens x 3.0 >> 64k
    with pytest.raises(ValueError, match="split"):
        decompose.decompose(tmp_path, "feat", (_child("huge", "src/big.py"),))
    assert fake.created == []


def test_govern_refuses_underfloor_child_with_merge_guidance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A child below working_set_min (existing scope material) says merge with a sibling."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _write(tmp_path, "src/tiny.py", 100)
    children = (_child("tiny", "src/tiny.py"), _child("other", "src/other-new.py"))
    with pytest.raises(ValueError, match="sibling"):
        decompose.decompose(tmp_path, "feat", children)
    assert fake.created == []


def test_govern_passes_greenfield_plan(tmp_path: Path) -> None:
    """A plan whose scopes match no existing files estimates overhead-only and fits."""
    estimates = decompose.govern_working_set(tmp_path, (_child("a"), _child("b")))
    assert [e.total for e in estimates] == [0, 0]


def test_scope_read_cost_keeps_dot_directory_scopes(tmp_path: Path) -> None:
    """A dot-directory glob keeps its leading dot; only a literal ./ prefix strips."""
    _write(tmp_path, ".claude/rules/python.md", 400)
    assert decompose.scope_read_cost(tmp_path, (".claude/rules/*.md",)) == 100
    assert decompose.scope_read_cost(tmp_path, ("./.claude/rules/*.md",)) == 100
    _write(tmp_path, "src/a.py", 40)
    assert decompose.scope_read_cost(tmp_path, ("./src/a.py",)) == 10


def test_scope_read_cost_skips_unglobbable_patterns(tmp_path: Path) -> None:
    """An anchored or engine-rejected pattern is skipped, never fatal."""
    _write(tmp_path, "etc/conf.py", 40)
    # A leading slash is relativized; a drive-anchored pattern must not raise
    # (on POSIX "c:" is an ordinary segment, on Windows the glob engine rejects
    # it and the guard skips it).
    assert decompose.scope_read_cost(tmp_path, ("/etc/conf.py",)) == 10
    assert decompose.scope_read_cost(tmp_path, ("c:/nowhere/*.py",)) == 0
