from __future__ import annotations

import json
import statistics
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from basicly import decompose, merge, policy, read_cost, run_record, tracker
from basicly.config import (
    DEFAULT_BUILD_FACTOR,
    DEFAULT_BUILD_FACTOR_SEEDS,
    DEFAULT_WORKING_SET_MAX,
    DEFAULT_WORKING_SET_MIN,
    SizingConfig,
    load_sizing_config,
)
from basicly.decompose import ChildSpec
from tests import fake_tracker, flipped_tracker

REPO_ROOT = Path(__file__).resolve().parents[1]


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeBr:
    def __init__(
        self, *, cycles: list[list[str]] | None = None, labels: list[str] | None = None
    ) -> None:
        self.cycles = cycles or []
        self.labels = labels
        self.created: list[tuple[str, str, str]] = []
        self.create_args: list[list[str]] = []
        self.shown: list[str] = []
        self.edges: list[tuple[str, str]] = []
        self.comments: dict[str, list[str]] = {}
        self._counter = 0

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["create"]:
            return self._create(args)
        if args[:1] == ["show"]:
            self.shown.append(args[1])
            return _Proc(json.dumps([{"id": args[1], "labels": self.labels}]))
        if args[:2] == ["dep", "add"]:
            self.edges.append((args[2], args[3]))
            return _Proc("")
        if args[:2] == ["dep", "cycles"]:
            return _Proc(json.dumps({"cycles": self.cycles, "count": len(self.cycles)}))
        if args[:2] == ["comments", "add"]:
            self.comments.setdefault(args[2], []).append(args[3])
            return _Proc("")
        if args[:2] == ["comments", "list"]:
            texts = self.comments.get(args[2], [])
            return _Proc(json.dumps([{"text": text} for text in texts]))
        raise AssertionError(f"unexpected br call: {args}")

    def _create(self, args: list[str]) -> _Proc:
        self._counter += 1
        issue_id = f"feat.{self._counter}"
        title = args[1]
        body = args[args.index("-d") + 1]
        self.created.append((issue_id, title, body))
        self.create_args.append(list(args))
        return _Proc(json.dumps({"id": issue_id}))


def _install(monkeypatch: pytest.MonkeyPatch, fake: Callable[..., _Proc]) -> None:
    fake_tracker.install(monkeypatch, fake)


_GATED = {
    "depends_on": (),
    "budget_tokens": 40_000,
    "integrity": "L2",
    "demonstration": "run `basicly decompose feat --dry-run`",
}


def _child(title: str, *scope: str) -> ChildSpec:
    return ChildSpec(title=title, acceptance=("does the thing",), scope=scope or (title,), **_GATED)


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
    assert decompose.globs_overlap(a, b) is expected
    assert decompose.globs_overlap(b, a) is expected


def test_disjoint_scopes_are_separate_groups() -> None:
    children = (_child("a", "src/a.py"), _child("b", "src/b.py"), _child("c", "src/c.py"))
    assert decompose.group_children(children) == (0, 1, 2)


def test_overlapping_scopes_share_a_group() -> None:
    children = (
        _child("a", "src/shared.py"),
        _child("b", "src/shared.py", "src/b.py"),
        _child("c", "src/c.py"),
    )
    assert decompose.group_children(children) == (0, 0, 1)


def test_overlap_is_transitive_via_a_bridge() -> None:
    children = (
        _child("a", "src/a.py", "src/x.py"),
        _child("b", "src/x.py", "src/y.py"),
        _child("c", "src/y.py"),
    )
    assert decompose.group_children(children) == (0, 0, 0)


def test_chain_predecessors_are_within_group_only() -> None:
    groups = (0, 0, 1, 0)
    assert decompose.chain_predecessors(groups) == (None, 0, None, 1)


def _manifest_plan(*, shared: bool) -> tuple[ChildSpec, ...]:
    return tuple(
        ChildSpec(
            title=name,
            acceptance=("does the thing",),
            scope=(f"src/{name}.py", "pyproject.toml"),
            shared=("pyproject.toml",) if shared else (),
            **_GATED,
        )
        for name in ("a", "b", "c", "d")
    )


def test_one_owned_path_collapses_every_child_into_one_group() -> None:
    assert decompose.group_children(_manifest_plan(shared=False)) == (0, 0, 0, 0)


def test_a_shared_manifest_keeps_the_distinct_modules_parallel() -> None:
    assert decompose.group_children(_manifest_plan(shared=True)) == (0, 1, 2, 3)


def test_a_single_owner_still_serializes_everyone_who_touches_the_path() -> None:

    plan = list(_manifest_plan(shared=True))
    plan[2] = ChildSpec(plan[2].title, plan[2].acceptance, plan[2].scope, plan[2].type)
    assert decompose.group_children(tuple(plan)) == (0, 0, 0, 0)


def test_shared_does_not_excuse_an_overlap_on_an_owned_path() -> None:
    children = (
        ChildSpec("a", ("ac",), ("src/x.py", "pyproject.toml"), shared=("pyproject.toml",)),
        ChildSpec("b", ("ac",), ("src/x.py", "pyproject.toml"), shared=("pyproject.toml",)),
    )
    assert decompose.group_children(children) == (0, 0)


def test_collapsing_paths_names_the_path_that_collapses_the_plan() -> None:
    (item,) = decompose.collapsing_paths(_manifest_plan(shared=False))
    assert item.glob == "pyproject.toml"
    assert item.declarers == (0, 1, 2, 3)
    assert (item.groups, item.groups_without) == (1, 4)
    assert item.neutralized is False
    assert "`pyproject.toml`" in decompose.describe_collapsing_path(item)


def test_collapsing_paths_still_names_a_path_a_declaration_defused() -> None:

    (item,) = decompose.collapsing_paths(_manifest_plan(shared=True))
    assert item.glob == "pyproject.toml"
    assert (item.groups, item.groups_without) == (1, 4)
    assert item.neutralized is True
    assert "no longer collapses" in decompose.describe_collapsing_path(item)


def test_collapsing_paths_is_silent_when_no_single_path_decides() -> None:
    children = (_child("a", "src/a.py"), _child("b", "src/b.py"), _child("c", "src/c.py"))
    assert decompose.collapsing_paths(children) == ()


def test_collapsing_paths_names_a_wildcard_that_swallows_its_siblings() -> None:

    children = (
        _child("wide", "src/**"),
        _child("a", "src/a.py"),
        _child("b", "src/b.py"),
    )
    globs = {item.glob for item in decompose.collapsing_paths(children)}
    assert "src/**" in globs
    assert all(item.neutralized is False for item in decompose.collapsing_paths(children))


def test_collapsing_paths_handles_a_child_whose_whole_scope_is_the_shared_path() -> None:

    children = (
        ChildSpec("manifest-only", ("ac",), ("pyproject.toml",)),
        _child("b", "src/b.py", "pyproject.toml"),
    )
    (item,) = decompose.collapsing_paths(children)
    assert (item.glob, item.groups, item.groups_without) == ("pyproject.toml", 1, 2)


def test_collapse_note_speaks_only_for_a_live_collapse() -> None:
    live = decompose.collapse_note(decompose.collapsing_paths(_manifest_plan(shared=False)))
    assert "`pyproject.toml`" in live
    assert decompose.collapse_note(decompose.collapsing_paths(_manifest_plan(shared=True))) == ""
    assert decompose.collapse_note(()) == ""


_APPEND_ONLY = ("CHANGELOG.md",)


def _disjoint_plan() -> tuple[ChildSpec, ...]:
    return tuple(_child(name, f"src/basicly/{name}.py") for name in ("schema", "config", "usage"))


def test_a_configured_append_only_path_serializes_lanes_that_share_no_scope() -> None:
    plan = _disjoint_plan()
    assert decompose.group_children(plan, _APPEND_ONLY) == (0, 0, 0)


def test_a_pass_that_shares_no_append_only_path_stays_parallel() -> None:
    assert decompose.group_children(_disjoint_plan()) == (0, 1, 2)
    assert decompose.group_children(_disjoint_plan(), ()) == (0, 1, 2)


def test_a_child_may_declare_the_append_only_path_shared_and_stay_parallel() -> None:

    plan = tuple(
        ChildSpec(c.title, c.acceptance, (*c.scope, "CHANGELOG.md"), shared=("CHANGELOG.md",))
        for c in _disjoint_plan()
    )
    assert decompose.group_children(plan, _APPEND_ONLY) == (0, 1, 2)


def test_one_child_owning_the_append_only_path_still_serializes_the_pass() -> None:
    plan = list(_disjoint_plan())
    plan[0] = ChildSpec(
        plan[0].title, plan[0].acceptance, (*plan[0].scope, "CHANGELOG.md"), shared=()
    )
    assert decompose.group_children(tuple(plan), _APPEND_ONLY) == (0, 0, 0)


def test_the_collapsing_path_report_names_the_configured_path_and_its_origin() -> None:

    plan = _disjoint_plan()
    (item,) = decompose.collapsing_paths(plan, _APPEND_ONLY)
    assert item.glob == "CHANGELOG.md"
    assert item.declarers == ()
    assert (item.groups, item.groups_without) == (1, 3)
    assert item.neutralized is False

    line = decompose.describe_collapsing_path(item, _APPEND_ONLY)
    assert "`CHANGELOG.md`" in line
    assert "[worktree] append_only_paths" in line
    assert "no child declares it" in line


def test_the_report_marks_an_append_only_path_every_child_declared_shared() -> None:
    plan = tuple(
        ChildSpec(c.title, c.acceptance, (*c.scope, "CHANGELOG.md"), shared=("CHANGELOG.md",))
        for c in _disjoint_plan()
    )
    (item,) = decompose.collapsing_paths(plan, _APPEND_ONLY)
    assert item.neutralized is True
    assert "no longer serializes" in decompose.describe_collapsing_path(item, _APPEND_ONLY)


def test_two_configured_paths_are_both_named_rather_than_neither() -> None:

    contended = ("CHANGELOG.md", "docs/release-notes.md")
    named = {item.glob for item in decompose.collapsing_paths(_disjoint_plan(), contended)}
    assert named == set(contended)


def test_the_preview_groups_a_plan_against_the_same_configured_paths() -> None:
    planned = decompose.preview(_disjoint_plan(), _APPEND_ONLY)
    assert [child.group for child in planned] == [0, 0, 0]
    assert [child.predecessor for child in planned] == [None, 0, 1]


def test_load_plan_text_json_and_toml_agree() -> None:
    child = {
        "title": "t",
        "acceptance": ["ac"],
        "scope": ["src/x.py"],
        "type": "bug",
        **_GATED_JSON,
    }
    json_children = decompose.load_plan_text(json.dumps({"children": [child]}), "json")
    toml_children = decompose.load_plan_text(
        '[[children]]\ntitle = "t"\nacceptance = ["ac"]\nscope = ["src/x.py"]\ntype = "bug"\n'
        + _GATED_TOML,
        "toml",
    )
    assert json_children == toml_children
    assert json_children[0] == ChildSpec("t", ("ac",), ("src/x.py",), "bug", **_GATED)


def test_load_plan_file_detects_format_by_suffix(tmp_path: Path) -> None:
    plan = tmp_path / "plan.toml"
    plan.write_text(
        '[[children]]\ntitle = "t"\nacceptance = ["ac"]\nscope = ["s"]\n' + _GATED_TOML, "utf-8"
    )
    assert decompose.load_plan_file(plan) == (ChildSpec("t", ("ac",), ("s",), **_GATED),)


def test_parse_children_rejects_empty() -> None:
    with pytest.raises(ValueError, match="non-empty 'children'"):
        decompose.parse_children({"children": []})


def test_parse_children_requires_scope() -> None:
    with pytest.raises(ValueError, match="'scope'"):
        decompose.parse_children({"children": [{"title": "t", "acceptance": ["ac"]}]})


def test_parse_children_requires_acceptance() -> None:
    with pytest.raises(ValueError, match="'acceptance'"):
        decompose.parse_children({"children": [{"title": "t", "scope": ["s"]}]})


def _one_child(**extra: object) -> dict[str, object]:
    return {
        "children": [
            {
                "title": "t",
                "acceptance": ["ac"],
                "scope": ["src/x.py"],
                "depends_on": [],
                "budget_tokens": 40_000,
                "integrity": "L2",
                "demonstration": "run `basicly decompose feat --dry-run`",
                **extra,
            }
        ]
    }


_GATED_JSON = {
    "depends_on": [],
    "budget_tokens": 40000,
    "integrity": "L2",
    "demonstration": "run `basicly decompose feat --dry-run`",
}
_GATED_TOML = (
    'depends_on = []\nbudget_tokens = 40000\nintegrity = "L2"\n'
    'demonstration = "run `basicly decompose feat --dry-run`"\n'
)


@pytest.mark.parametrize("shared", [None, []], ids=["absent", "empty"])
def test_parse_children_defaults_shared_to_owning_everything(shared: object) -> None:
    entry = {} if shared is None else {"shared": shared}
    assert decompose.parse_children(_one_child(**entry))[0].shared == ()


def test_parse_children_rejects_a_shared_path_outside_the_scope() -> None:

    with pytest.raises(ValueError, match="not in that child's 'scope'"):
        decompose.parse_children(_one_child(shared=["pyproject.toml"]))


def test_parse_children_rejects_a_glob_as_a_shared_path() -> None:
    with pytest.raises(ValueError, match="is a glob"):
        decompose.parse_children(_one_child(scope=["src/**"], shared=["src/**"]))


def test_parse_children_rejects_a_malformed_shared_list() -> None:
    with pytest.raises(ValueError, match="'shared' must be a list"):
        decompose.parse_children(_one_child(shared="src/x.py"))
    with pytest.raises(ValueError, match="'shared' entries must be non-empty"):
        decompose.parse_children(_one_child(shared=[" "]))


def test_load_plan_text_reads_shared_in_json_and_toml() -> None:
    child = {
        "title": "t",
        "acceptance": ["ac"],
        "scope": ["src/x.py", "pyproject.toml"],
        **_GATED_JSON,
    }
    child["shared"] = ["pyproject.toml"]
    from_json = decompose.load_plan_text(json.dumps({"children": [child]}), "json")
    from_toml = decompose.load_plan_text(
        '[[children]]\ntitle = "t"\nacceptance = ["ac"]\n'
        'scope = ["src/x.py", "pyproject.toml"]\nshared = ["pyproject.toml"]\n' + _GATED_TOML,
        "toml",
    )
    assert from_json == from_toml
    assert from_json[0].shared == ("pyproject.toml",)


def test_decompose_parallel_children_get_no_sibling_deps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    children = (_child("a", "src/a.py"), _child("b", "src/b.py"))

    result = decompose.decompose(tmp_path, "feat", children)

    assert fake.edges == []
    assert result.parallel_groups == 2
    assert result.groups == (("feat.1",), ("feat.2",))
    assert result.serial_order == ("feat.1", "feat.2")
    assert all("## Acceptance Criteria" in body for _id, _title, body in fake.created)
    assert "src/a.py" in fake.created[0][2]


def test_decompose_children_inherit_the_features_labels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr(labels=["phase-2", "determinism"])
    _install(monkeypatch, fake)
    children = (_child("a", "src/a.py"), _child("b", "src/b.py"))

    decompose.decompose(tmp_path, "feat", children)

    assert len(fake.create_args) == 2
    for args in fake.create_args:
        assert args[args.index("-l") + 1] == "phase-2,determinism"


def test_decompose_reads_the_feature_labels_once_not_once_per_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr(labels=["phase-2"])
    _install(monkeypatch, fake)
    children = (_child("a", "src/a.py"), _child("b", "src/b.py"), _child("c", "src/c.py"))

    decompose.decompose(tmp_path, "feat", children)

    assert fake.shown == ["feat"]


def test_decompose_unlabelled_feature_sends_no_empty_label_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    for labels in (None, []):
        fake = _FakeBr(labels=labels)
        _install(monkeypatch, fake)

        decompose.decompose(tmp_path, "feat", (_child("a", "src/a.py"),))

        assert "-l" not in fake.create_args[0], f"labels={labels!r} emitted an -l flag"


def test_decompose_overlapping_children_are_chained_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    children = (_child("a", "src/shared.py"), _child("b", "src/shared.py"))

    result = decompose.decompose(tmp_path, "feat", children)

    assert fake.edges == [("feat.2", "feat.1")]
    assert result.parallel_groups == 1
    assert result.groups == (("feat.1", "feat.2"),)
    assert result.children[1].depends_on == ("feat.1",)


def test_decompose_raises_on_introduced_cycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr(cycles=[["feat.1", "feat.2"]])
    _install(monkeypatch, fake)
    with pytest.raises(RuntimeError, match="cycle"):
        decompose.decompose(tmp_path, "feat", (_child("a", "src/s.py"), _child("b", "src/s.py")))


def test_decompose_wires_no_chain_through_a_shared_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr()
    _install(monkeypatch, fake)

    result = decompose.decompose(tmp_path, "feat", _manifest_plan(shared=True))

    assert fake.edges == []
    assert result.parallel_groups == 4
    assert [item.glob for item in result.collapsing] == ["pyproject.toml"]
    assert result.collapsing[0].neutralized is True


def test_decompose_result_names_a_live_collapsing_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)

    result = decompose.decompose(tmp_path, "feat", _manifest_plan(shared=False))

    assert result.parallel_groups == 1
    assert len(fake.edges) == 3
    assert [(item.glob, item.neutralized) for item in result.collapsing] == [
        ("pyproject.toml", False)
    ]


def test_decompose_reads_the_append_only_paths_from_config_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    (tmp_path / "basicly.toml").write_text(
        '[worktree]\nappend_only_paths = ["CHANGELOG.md"]\n', encoding="utf-8"
    )
    fake = _FakeBr()
    _install(monkeypatch, fake)

    result = decompose.decompose(tmp_path, "feat", _disjoint_plan())

    assert result.parallel_groups == 1
    assert len(fake.edges) == 2
    assert [(item.glob, item.neutralized) for item in result.collapsing] == [
        ("CHANGELOG.md", False)
    ]


def test_decompose_stays_parallel_when_no_append_only_path_is_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)

    result = decompose.decompose(tmp_path, "feat", _disjoint_plan())

    assert result.parallel_groups == 3
    assert fake.edges == []
    assert result.collapsing == ()


def test_preview_matches_recorded_grouping(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    children = (_child("a", "src/s.py"), _child("b", "src/s.py"), _child("c", "src/c.py"))
    planned = decompose.preview(children)
    assert [p.group for p in planned] == [0, 0, 1]
    assert [p.predecessor for p in planned] == [None, 0, None]

    _install(monkeypatch, _FakeBr())
    result = decompose.decompose(tmp_path, "feat", children)
    assert result.parallel_groups == 2


def _write(repo: Path, rel: str, chars: int) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * chars, encoding="utf-8")


_OVERSIZED_SCOPE = "src/big/*.py"


def _write_oversized(repo: Path) -> None:
    files = -(-DEFAULT_WORKING_SET_MAX // (read_cost.SCOPE_FILE_READ_CAP * 3)) + 2
    for index in range(files):
        _write(repo, f"src/big/m{index}.py", read_cost.SCOPE_FILE_READ_CAP * 4 * 2)


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


def test_the_read_cap_lets_the_band_admit_a_change_to_a_large_module(
    tmp_path: Path,
) -> None:

    _write(tmp_path, "src/cli.py", 182_396)
    sizing = _sizing(
        working_set_min=DEFAULT_WORKING_SET_MIN, working_set_max=DEFAULT_WORKING_SET_MAX
    )
    estimate = decompose.estimate_cost(
        tmp_path,
        _child("t", "src/cli.py"),
        _sizing(build_factors=DEFAULT_BUILD_FACTOR_SEEDS),
        overhead=2_000,
    )

    assert estimate.total == 2_000 + 12_000
    assert policy.check_working_set("t", estimate.total, estimate.scope_tokens, sizing) is None


def test_estimate_cost_total_is_overhead_plus_factored_scope(tmp_path: Path) -> None:
    _write(tmp_path, "src/a.py", 4_000)
    sizing = _sizing(build_factors={"task": 3.0, "bug": 2.0})
    task = decompose.estimate_cost(tmp_path, _child("t", "src/a.py"), sizing, overhead=500)
    assert (task.scope_tokens, task.overhead_tokens, task.build_factor) == (1_000, 500, 3.0)
    assert task.total == 500 + 3_000
    bug = ChildSpec(title="b", acceptance=("a",), scope=("src/a.py",), type="bug")
    assert decompose.estimate_cost(tmp_path, bug, sizing, overhead=0).total == 2_000
    spike = ChildSpec(title="s", acceptance=("a",), scope=("src/a.py",), type="spike")
    assert decompose.estimate_cost(tmp_path, spike, sizing, overhead=0).total == 3_000


def test_grouping_is_unchanged_by_the_size_of_the_file_two_children_share(
    tmp_path: Path,
) -> None:

    pair = (_child("a", "src/big.py", "src/a.py"), _child("b", "src/big.py", "src/b.py"))
    apart = (_child("a", "src/a.py"), _child("b", "src/b.py"))

    for chars in (40, read_cost.SCOPE_FILE_READ_CAP * 4 * 10):
        _write(tmp_path, "src/big.py", chars)
        _write(tmp_path, "src/a.py", chars)
        _write(tmp_path, "src/b.py", chars)
        assert decompose.group_children(pair) == (0, 0)
        assert decompose.group_children(apart) == (0, 1)
        assert decompose.serializes(*pair) is True


def test_scope_overlap_is_unchanged_by_the_size_of_the_file(tmp_path: Path) -> None:

    _write(tmp_path, "src/big.py", read_cost.SCOPE_FILE_READ_CAP * 4 * 10)
    _write(tmp_path, "src/small.py", 40)

    for name in ("big", "small"):
        assert decompose.globs_overlap(f"src/{name}.py", "src/**/*.py") is True
        assert decompose.globs_overlap(f"src/{name}.py", "tests/*.py") is False
        assert decompose.scopes_overlap((f"src/{name}.py",), ("src/*.py", "docs/*")) is True

    assert merge.out_of_scope_paths(["src/big.py"], ("src/big.py",)) == ()
    assert merge.out_of_scope_paths(["src/big.py"], ("src/small.py",)) == ("src/big.py",)


def test_merge_coupling_attribution_is_unchanged_by_the_size_of_the_file(
    tmp_path: Path,
) -> None:

    _write(tmp_path, "src/big.py", read_cost.SCOPE_FILE_READ_CAP * 4 * 10)
    scopes = {"lane-a": ("src/big.py",), "lane-b": ("src/small.py",)}

    assert merge.coupled_lanes(("src/big.py",), scopes, bounced="lane-b") == ("lane-a",)
    assert merge.coupled_lanes(("src/big.py",), scopes, bounced="lane-a") == ()
    assert merge.coupled_lanes(("src/other.py",), scopes, bounced="lane-b") == ()


def test_parse_scope_section_round_trips_child_body() -> None:
    spec = _child("t", "src/**/*.py", "tests/test_x.py")
    body = decompose._child_body(spec)
    assert decompose.parse_scope_section(body) == ("src/**/*.py", "tests/test_x.py")
    assert decompose.parse_scope_section("no scope section here") == ()


def test_scope_line_example_is_a_line_the_parser_actually_accepts() -> None:

    assert decompose.parse_scope_section(f"## Scope\n\n{policy.SCOPE_LINE_EXAMPLE}\n") != ()


def test_unparsed_scope_warning_fires_when_the_heading_yielded_no_glob() -> None:

    bare = "## Scope\n\n- src/basicly/loop.py\n"
    annotated = "## Scope\n\n- `src/basicly/supervise.py`  (admit_working_set only)\n"
    for description in (bare, annotated):
        assert decompose.parse_scope_section(description) == ()
        warning = decompose.unparsed_scope_warning(description)
        assert warning is not None
        assert policy.SCOPE_LINE_EXAMPLE in warning


def test_unparsed_scope_warning_is_silent_when_there_is_nothing_to_say() -> None:

    assert decompose.unparsed_scope_warning("no scope section here") is None
    assert decompose.unparsed_scope_warning(decompose._child_body(_child("t", "src/a.py"))) is None


def test_scaffolded_scope_hint_does_not_itself_read_as_a_declared_scope() -> None:

    body = policy.scaffold_body("task")
    assert decompose.parse_scope_section(body) == ()
    assert decompose.unparsed_scope_warning(body) is not None


def test_child_body_carries_the_sections_the_childs_own_type_requires() -> None:

    bug = ChildSpec(
        title="b", acceptance=("given x then y",), scope=("src/a.py",), type="bug", **_GATED
    )
    body = decompose._child_body(bug)
    headings = [line for line in body.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Trigger",
        "## Steps to Reproduce",
        "## Acceptance Criteria",
        "## Scope",
        "## Plan",
    ]
    assert "- given x then y" in body
    assert decompose.parse_scope_section(body) == ("src/a.py",)


class _FakeBrShow:
    def __init__(self, beads: dict[str, tuple[str, str]]) -> None:
        self.beads = beads

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["show"]:
            issue_type, description = self.beads[args[1]]
            payload = [{"id": args[1], "issue_type": issue_type, "description": description}]
            return _Proc(json.dumps(payload))
        raise AssertionError(f"unexpected br call: {args}")


def _record_run_tokens(  # noqa: PLR0913 — one parameter per seeded record field
    repo: Path,
    bead_id: str,
    tokens: int,
    *,
    estimated: bool = False,
    scope_tokens: int | None = None,
    returncode: int = 0,
    phase: str | None = "lane",
) -> None:
    entry = run_record.build_record(
        agent="claude",
        handoff=False,
        returncode=returncode,
        duration_s=1.0,
        command=("claude",),
        tokens=tokens,
        estimated=estimated,
        scope_tokens=scope_tokens,
        phase=phase,
    )
    run_record.record(repo, bead_id, entry)


def _export(repo: Path, *records: dict) -> None:
    flipped_tracker.seed_records(repo, records)


def _lane_estimate(scope_tokens: int, task_class: str) -> int:

    return round(scope_tokens * DEFAULT_BUILD_FACTOR_SEEDS.get(task_class, DEFAULT_BUILD_FACTOR))


def _exported_class_and_scope(repo_root: Path) -> dict[str, tuple[str, tuple[str, ...]]]:

    beads: dict[str, tuple[str, tuple[str, ...]]] = {}
    for record in tracker.all_records(repo_root):
        description = record.get("description")
        text = description if isinstance(description, str) else ""
        scope = decompose.parse_scope_section(text)
        beads[str(record["id"])] = (
            str(record.get("issue_type") or "task"),
            decompose.working_set_for(text, scope)[0],
        )
    return beads


def _lane_estimates(repo_root: Path, outcome: str) -> dict[str, int]:

    beads = _exported_class_and_scope(repo_root)
    estimates: dict[str, int] = {}
    for bead_id, history in run_record.dispatch_history(repo_root).items():
        task_class, scope = beads.get(bead_id, ("task", ()))
        scope_tokens = read_cost.scope_read_cost(repo_root, scope)
        if scope_tokens <= 0:
            continue
        estimate = _lane_estimate(scope_tokens, task_class)
        for entry in history:
            if entry.get("phase") in ("build", "lane") and entry.get("outcome") == outcome:
                estimates[bead_id] = estimate
    return estimates


def completed_lane_estimates(repo_root: Path) -> dict[str, int]:

    return _lane_estimates(repo_root, run_record.EXECUTED)


def failed_lane_estimates(repo_root: Path) -> dict[str, int]:

    return _lane_estimates(repo_root, run_record.FAILED)


def _ceiling_violations(repo_root: Path, ceiling: int) -> list[str]:

    completed = completed_lane_estimates(repo_root)
    proven = max(completed.values(), default=0)
    violations: list[str] = []
    over = {bead: estimate for bead, estimate in completed.items() if estimate > ceiling}
    if over:
        required = -(-max(over.values()) // DEFAULT_WORKING_SET_MIN) * DEFAULT_WORKING_SET_MIN
        violations += [
            f"{bead} completed at an estimate of {estimate:,}, above working_set_max "
            f"{ceiling:,}; raise it to at least {required:,}"
            for bead, estimate in sorted(over.items(), key=lambda item: -item[1])
        ]
    admitted = {
        bead: estimate
        for bead, estimate in failed_lane_estimates(repo_root).items()
        if proven < estimate <= ceiling
    }
    if admitted:
        allowed = (min(admitted.values()) - 1) // DEFAULT_WORKING_SET_MIN * DEFAULT_WORKING_SET_MIN
        violations += [
            f"{bead} failed at an estimate of {estimate:,} and nothing above "
            f"{proven:,} has completed, yet working_set_max {ceiling:,} admits it; "
            f"lower it to at most {allowed:,}"
            for bead, estimate in sorted(admitted.items())
        ]
    return violations


def test_the_ceiling_separates_the_sizes_that_completed_from_the_sizes_that_failed() -> None:

    assert _ceiling_violations(REPO_ROOT, DEFAULT_WORKING_SET_MAX) == []


def test_the_recorded_failures_are_visible_to_the_ceiling_gate() -> None:

    assert failed_lane_estimates(REPO_ROOT)


def test_no_lane_this_engine_completed_is_refused_by_the_band() -> None:

    sizing = _sizing(
        working_set_min=DEFAULT_WORKING_SET_MIN, working_set_max=DEFAULT_WORKING_SET_MAX
    )
    completed = completed_lane_estimates(REPO_ROOT)
    assert "basicly-kjc5.42" in completed

    refused = {
        bead: policy.check_working_set(bead, estimate, estimate, sizing)
        for bead, estimate in completed.items()
        if estimate > sizing.working_set_max
    }
    assert refused == {}, "the band refuses work this engine has already completed"


def test_the_ceiling_gate_names_the_lane_and_the_value_it_requires(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _write(tmp_path, "src/a.py", 16_000)
    body = decompose._child_body(_child("t", "src/a.py"))
    _install(monkeypatch, _FakeBrShow({"b-1": ("task", body)}))
    _export(tmp_path, {"id": "b-1", "issue_type": "task", "description": body})
    _record_run_tokens(tmp_path, "b-1", 1_000, scope_tokens=4_000)

    assert _ceiling_violations(tmp_path, 112_000) == []

    violations = _ceiling_violations(tmp_path, 8_000)
    assert len(violations) == 1
    assert "b-1 completed at an estimate of 12,000" in violations[0]
    required = -(-12_000 // DEFAULT_WORKING_SET_MIN) * DEFAULT_WORKING_SET_MIN
    assert f"raise it to at least {required:,}" in violations[0]


def test_completing_a_scope_for_the_merge_gate_does_not_move_the_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    for name in "abcde":
        _write(tmp_path, f"src/{name}.py", 16_000)
    owned = "".join(f"- `src/{name}.py`\n" for name in "abcde")
    narrow = "## Scope\n\n- `src/a.py`\n\n## Working Set\n\n- `src/a.py`\n"
    complete = f"## Scope\n\n{owned}\n## Working Set\n\n- `src/a.py`\n"
    _install(monkeypatch, _FakeBrShow({"b-1": ("task", narrow)}))
    _record_run_tokens(tmp_path, "b-1", 1_000, scope_tokens=4_000)

    sized = []
    for body in (narrow, complete):
        _export(tmp_path, {"id": "b-1", "issue_type": "task", "description": body})
        sized.append(completed_lane_estimates(tmp_path)["b-1"])
        assert _ceiling_violations(tmp_path, 16_000) == []
    assert sized == [12_000, 12_000]

    _export(tmp_path, {"id": "b-1", "issue_type": "task", "description": f"## Scope\n\n{owned}"})
    assert completed_lane_estimates(tmp_path)["b-1"] == 60_000
    assert _ceiling_violations(tmp_path, 16_000) != []


def test_a_violation_of_this_gate_is_attributed_to_the_lane_it_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _write(tmp_path, "src/a.py", 16_000)
    body = decompose._child_body(_child("t", "src/a.py"))
    _install(monkeypatch, _FakeBrShow({"b-1": ("task", body)}))
    _export(tmp_path, {"id": "b-1", "issue_type": "task", "description": body})
    _record_run_tokens(tmp_path, "b-1", 1_000, scope_tokens=4_000)

    violation = _ceiling_violations(tmp_path, 8_000)[0]

    attributed = policy.shared_tracker_gate_failure(violation, "b-2")
    assert attributed is not None and attributed.culprits == ("b-1",)
    assert policy.shared_tracker_gate_failure(violation, "b-1") is None


def test_a_decider_dispatch_is_not_evidence_about_how_big_a_lane_can_be(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _write(tmp_path, "src/a.py", 16_000)
    body = decompose._child_body(_child("t", "src/a.py"))
    _install(monkeypatch, _FakeBrShow({"b-1": ("task", body)}))
    _export(tmp_path, {"id": "b-1", "issue_type": "task", "description": body})

    _record_run_tokens(tmp_path, "b-1", 1_000, scope_tokens=4_000, phase="decide")
    assert _ceiling_violations(tmp_path, 8_000) == []
    assert completed_lane_estimates(tmp_path) == {}

    _record_run_tokens(tmp_path, "b-1", 1_000, scope_tokens=4_000, phase="lane")
    assert "b-1 completed at an estimate of 12,000" in _ceiling_violations(tmp_path, 8_000)[0]


def test_the_ceiling_gate_refuses_to_admit_a_size_a_lane_died_at(tmp_path: Path) -> None:

    _write(tmp_path, "src/small.py", 16_000)
    for index in range(10):
        _write(tmp_path, f"src/big/m{index}.py", 160_000)
    _export(
        tmp_path,
        {
            "id": "b-ran",
            "issue_type": "task",
            "description": decompose._child_body(_child("s", "src/small.py")),
        },
        {
            "id": "b-died",
            "issue_type": "task",
            "description": decompose._child_body(_child("b", "src/big/*.py")),
        },
    )
    _record_run_tokens(tmp_path, "b-ran", 1_000, scope_tokens=4_000)
    _record_run_tokens(tmp_path, "b-died", 1_000, returncode=143)

    assert _ceiling_violations(tmp_path, 16_000) == []

    violations = _ceiling_violations(tmp_path, 120_000)
    assert len(violations) == 1
    assert "b-died failed at an estimate of 120,000" in violations[0]
    assert "nothing above 12,000 has completed" in violations[0]
    allowed = (120_000 - 1) // DEFAULT_WORKING_SET_MIN * DEFAULT_WORKING_SET_MIN
    assert f"lower it to at most {allowed:,}" in violations[0]


def test_a_failure_below_a_proven_size_is_not_evidence_about_the_ceiling(tmp_path: Path) -> None:

    _write(tmp_path, "src/small.py", 16_000)
    for index in range(10):
        _write(tmp_path, f"src/big/m{index}.py", 160_000)
    _export(
        tmp_path,
        {
            "id": "b-ran",
            "issue_type": "task",
            "description": decompose._child_body(_child("b", "src/big/*.py")),
        },
        {
            "id": "b-died",
            "issue_type": "task",
            "description": decompose._child_body(_child("s", "src/small.py")),
        },
    )
    _record_run_tokens(tmp_path, "b-ran", 1_000)
    _record_run_tokens(tmp_path, "b-died", 1_000, returncode=143)

    assert failed_lane_estimates(tmp_path) == {"b-died": 12_000}
    assert _ceiling_violations(tmp_path, 120_000) == []


def test_a_recorded_scope_size_never_overrides_the_current_measure(tmp_path: Path) -> None:

    _write(tmp_path, "src/big.py", 160_000)
    _export(
        tmp_path,
        {
            "id": "b-died",
            "issue_type": "task",
            "description": decompose._child_body(_child("b", "src/big.py")),
        },
    )
    _record_run_tokens(tmp_path, "b-died", 1_000, returncode=143, scope_tokens=9_000)

    assert failed_lane_estimates(tmp_path) == {"b-died": 12_000}


def test_a_handoff_lane_is_not_evidence_for_the_dispatch_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _write(tmp_path, "src/a.py", 16_000)
    body = decompose._child_body(_child("t", "src/a.py"))
    _install(monkeypatch, _FakeBrShow({"b-1": ("task", body)}))
    _export(tmp_path, {"id": "b-1", "issue_type": "task", "description": body})
    run_record.record(
        tmp_path,
        "b-1",
        run_record.build_record(
            agent="claude",
            handoff=True,
            returncode=None,
            duration_s=1.0,
            command=(),
            tokens=None,
            estimated=True,
            scope_tokens=4_000,
        ),
    )

    assert completed_lane_estimates(tmp_path) == {}


def test_no_working_set_factor_is_derived_from_spend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _write(tmp_path, "src/a.py", 16_000)
    body = decompose._child_body(_child("t", "src/a.py"))
    _install(monkeypatch, _FakeBrShow({"b-1": ("task", body)}))
    for _ in range(20):
        _record_run_tokens(tmp_path, "b-1", 2_000_000, scope_tokens=4_000)

    estimates = decompose.govern_working_set(tmp_path, (_child("t", "src/a.py"),))

    assert estimates[0].build_factor == 3.0
    assert estimates[0].total == 12_000


def test_using_the_engine_cannot_make_a_dispatchable_child_undispatchable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _write(tmp_path, "src/a.py", 40_000)
    spec = _child("t", "src/a.py")
    body = decompose._child_body(spec)
    _install(monkeypatch, _FakeBrShow({"b-1": ("task", body)}))

    before = decompose.govern_working_set(tmp_path, (spec,))

    for _ in range(20):
        _record_run_tokens(tmp_path, "b-1", 2_000_000, scope_tokens=10_000)

    after = decompose.govern_working_set(tmp_path, (spec,))
    assert after == before


def test_govern_freezes_the_accepted_estimate_on_the_feature(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _write(tmp_path, "src/a.py", 16_000)
    spec = _child("a", "src/a.py")

    estimates = decompose.govern_working_set(tmp_path, (spec,), feature_id="feat")

    frozen = decompose.frozen_estimates(tmp_path, "feat")
    assert frozen == {decompose.sizing_key(spec): estimates[0]}


def test_govern_reuses_a_frozen_estimate_when_the_tree_has_grown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _install(monkeypatch, _FakeBr())
    _write(tmp_path, "src/a.py", 16_000)
    spec = _child("a", "src/*.py")

    first = decompose.govern_working_set(tmp_path, (spec,), feature_id="feat")
    assert first[0].build_factor == 3.0
    assert first[0].total == 12_000

    _write(tmp_path, "src/b.py", 16_000)
    _write(tmp_path, "src/c.py", 16_000)
    assert read_cost.scope_read_cost(tmp_path, ("src/*.py",)) == 12_000

    second = decompose.govern_working_set(tmp_path, (spec,), feature_id="feat")
    assert second == first
    assert second[0].total == 12_000


def test_govern_without_a_feature_id_neither_reads_nor_writes_a_freeze(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _write(tmp_path, "src/a.py", 16_000)

    decompose.govern_working_set(tmp_path, (_child("a", "src/a.py"),))

    assert fake.comments == {}


def test_govern_does_not_freeze_a_refused_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _write_oversized(tmp_path)

    with pytest.raises(ValueError, match="split"):
        decompose.govern_working_set(
            tmp_path, (_child("huge", _OVERSIZED_SCOPE),), feature_id="feat"
        )

    assert fake.comments == {}


def test_forecast_for_finds_the_freeze_by_content_in_the_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr()
    _install(monkeypatch, fake)
    _write(tmp_path, "src/a.py", 16_000)
    spec = _child("a", "src/a.py")
    estimates = decompose.govern_working_set(tmp_path, (spec,), feature_id="feat")
    _export(tmp_path, {"id": "b-feat", "comments": [{"text": fake.comments["feat"][0]}]})

    _install(monkeypatch, lambda *_a, **_k: pytest.fail("the forecast lookup must not need br"))
    assert decompose.forecast_for(tmp_path, "task", ("src/a.py",)) == estimates[0]
    assert decompose.forecast_for(tmp_path, "bug", ("src/a.py",)) is None
    assert decompose.forecast_for(tmp_path, "task", ("src/b.py",)) is None


def test_forecast_for_without_an_export(tmp_path: Path) -> None:
    assert decompose.forecast_for(tmp_path, "task", ("src/a.py",)) is None


def test_govern_refuses_oversized_child_before_recording(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _write_oversized(tmp_path)
    with pytest.raises(ValueError, match="split"):
        decompose.decompose(tmp_path, "feat", (_child("huge", _OVERSIZED_SCOPE),))
    assert fake.created == []


def test_govern_refuses_underfloor_child_with_merge_guidance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _write(tmp_path, "src/tiny.py", 100)
    children = (_child("tiny", "src/tiny.py"), _child("other", "src/other-new.py"))
    with pytest.raises(ValueError, match="sibling"):
        decompose.decompose(tmp_path, "feat", children)
    assert fake.created == []


def test_dry_run_estimate_refuses_exactly_what_the_real_run_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr()
    _install(monkeypatch, fake)
    _write_oversized(tmp_path)
    children = (_child("huge", _OVERSIZED_SCOPE),)

    verdict = decompose.estimate_plan(tmp_path, children)

    assert verdict.refused
    assert verdict.estimates[0].total > 0
    assert fake.created == []
    assert fake.comments == {}

    with pytest.raises(ValueError) as excinfo:
        decompose.decompose(tmp_path, "feat", children)
    for message in verdict.violations:
        assert message in str(excinfo.value)


def test_dry_run_estimate_accepts_what_the_real_run_accepts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    children = (_child("greenfield", "src/new-a.py"), _child("other", "src/new-b.py"))

    verdict = decompose.estimate_plan(tmp_path, children)

    assert not verdict.refused
    assert verdict.violations == ()
    assert len(verdict.estimates) == 2
    assert fake.created == []


def test_govern_passes_greenfield_plan(tmp_path: Path) -> None:
    estimates = decompose.govern_working_set(tmp_path, (_child("a"), _child("b")))
    assert [e.total for e in estimates] == [0, 0]


def _scoped_bead(*globs: str, issue_type: str = "task") -> _FakeBrShow:
    body = "## Scope\n\n" + "\n".join(f"- `{glob}`" for glob in globs)
    return _FakeBrShow({"b-1": (issue_type, body)})


def test_dispatch_sizing_prefers_the_forecast_that_was_frozen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr()
    _install(monkeypatch, fake)
    _write(tmp_path, "src/a.py", 16_000)
    frozen = decompose.govern_working_set(tmp_path, (_child("a", "src/a.py"),), feature_id="feat")
    _export(tmp_path, {"id": "b-feat", "comments": [{"text": fake.comments["feat"][0]}]})

    _install(monkeypatch, _scoped_bead("src/a.py"))
    sizing = decompose.dispatch_sizing(tmp_path, "b-1")
    assert sizing is not None
    assert sizing.estimate == frozen[0]
    assert sizing.source == decompose.FROZEN_FORECAST
    assert sizing.task_class == "task"


def test_dispatch_sizing_computes_a_forecast_when_none_was_frozen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, _scoped_bead("src/a.py", issue_type="bug"))
    _write(tmp_path, "src/a.py", 16_000)
    sizing = decompose.dispatch_sizing(tmp_path, "b-1")
    assert sizing is not None
    assert sizing.source == decompose.DISPATCH_FORECAST
    assert sizing.task_class == "bug"
    assert sizing.estimate.scope_tokens == 4_000
    assert sizing.estimate.build_factor == 2.0
    assert sizing.estimate.total == sizing.estimate.overhead_tokens + 8_000


def test_dispatch_sizing_declines_a_bead_with_no_readable_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _install(monkeypatch, _FakeBrShow({"b-1": ("task", "## Context\n\nno scope here")}))
    assert decompose.dispatch_sizing(tmp_path, "b-1") is None


def test_resolve_dispatch_sizing_separates_an_undeclared_scope_from_an_unreadable_bead(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _install(monkeypatch, _scoped_bead("src/a.py"))
    _write(tmp_path, "src/a.py", 16_000)
    sized = decompose.resolve_dispatch_sizing(tmp_path, "b-1")
    assert sized.sizing is not None
    assert sized.absence == ""

    _install(monkeypatch, _FakeBrShow({"b-1": ("task", "## Context\n\nno scope here")}))
    undeclared = decompose.resolve_dispatch_sizing(tmp_path, "b-1")
    assert undeclared.sizing is None
    assert undeclared.absence == decompose.SCOPE_UNDECLARED

    def broken(*_a: object, **_k: object) -> _Proc:
        raise RuntimeError("the tracker could not answer")

    _install(monkeypatch, broken)
    unreadable = decompose.resolve_dispatch_sizing(tmp_path, "b-1")
    assert unreadable.sizing is None
    assert unreadable.absence == decompose.SCOPE_UNREADABLE


def test_resolve_dispatch_sizing_calls_a_scope_matching_no_file_greenfield(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _install(monkeypatch, _scoped_bead("src/created-later.py"))
    greenfield = decompose.resolve_dispatch_sizing(tmp_path, "b-1")

    assert greenfield.sizing is None, "an overhead-only forecast is not a forecast"
    assert greenfield.absence == decompose.SCOPE_GREENFIELD

    _write(tmp_path, "src/created-later.py", 16_000)
    assert decompose.resolve_dispatch_sizing(tmp_path, "b-1").sizing is not None


def test_greenfield_is_checked_before_a_frozen_estimate_is_honoured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _install(monkeypatch, _scoped_bead("src/created-later.py"))
    frozen_calls: list[object] = []

    def _never(*args: object) -> None:
        frozen_calls.append(args)
        raise AssertionError("a frozen estimate must not be consulted for a greenfield scope")

    monkeypatch.setattr(decompose, "forecast_for", _never)
    assert decompose.resolve_dispatch_sizing(tmp_path, "b-1").absence == decompose.SCOPE_GREENFIELD
    assert frozen_calls == []


def test_resolve_dispatch_sizing_calls_a_record_without_the_fields_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _install(monkeypatch, lambda _r, args, **_k: _Proc(json.dumps([{"id": args[1]}])))
    lookup = decompose.resolve_dispatch_sizing(tmp_path, "b-1")
    assert lookup.sizing is None
    assert lookup.absence == decompose.SCOPE_UNREADABLE


def _manual_runner(repo: Path) -> None:

    (repo / "basicly.toml").write_text('[runner]\ndefault = "manual"\n', encoding="utf-8")


def _estimate(total: int) -> decompose.CostEstimate:
    return decompose.CostEstimate(scope_tokens=total, overhead_tokens=0, build_factor=1.0)


def _calibration(
    *, tokens: float | None = 100.0, usd: float | None = 2.0, seconds: float | None = 60.0
) -> run_record.SpendCalibration:
    seeded = run_record.PRIOR_RATIO
    return run_record.SpendCalibration(
        tokens_per_working_set_token=run_record.CalibratedRatio(tokens, seeded),
        usd_per_million_tokens=run_record.CalibratedRatio(usd, seeded),
        seconds_per_million_tokens=run_record.CalibratedRatio(seconds, seeded),
        prior=run_record.DECLARED_SPEND_PRIOR,
        model="claude-opus-5",
        task_class="task",
    )


def _declared(ratio: str) -> float:
    value = getattr(run_record.DECLARED_SPEND_PRIOR, ratio)
    assert value is not None
    return value


def test_forecast_spend_multiplies_the_working_set_and_prices_the_tokens() -> None:
    spend = decompose.forecast_spend(_estimate(10_000), _calibration())
    assert spend.tokens == 1_000_000
    assert spend.cost == pytest.approx(2.0)
    assert spend.wall_clock_s == pytest.approx(60.0)
    assert spend.indeterminate is False


def test_forecast_spend_refuses_a_zero_working_set() -> None:
    spend = decompose.forecast_spend(_estimate(0), _calibration())
    assert (spend.tokens, spend.cost, spend.wall_clock_s) == (None, None, None)
    assert spend.indeterminate is True


def test_forecast_spend_predicts_tokens_while_leaving_money_unknown() -> None:
    spend = decompose.forecast_spend(_estimate(10_000), _calibration(usd=None))
    assert spend.tokens == 1_000_000
    assert spend.cost is None
    assert spend.wall_clock_s == pytest.approx(60.0)
    assert spend.indeterminate is False


def test_forecast_model_is_none_when_the_runner_pins_no_model(tmp_path: Path) -> None:
    _manual_runner(tmp_path)
    assert decompose.forecast_model(tmp_path) is None


def test_estimate_plan_carries_predicted_spend_beside_the_working_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, _FakeBr())
    _manual_runner(tmp_path)
    _write(tmp_path, "src/a.py", 16_000)

    verdict = decompose.estimate_plan(tmp_path, (_child("a", "src/a.py"),))

    assert verdict.estimates[0].total == 12_000
    spend = verdict.spend[0]
    tokens = round(12_000 * _declared("tokens_per_working_set_token"))
    assert spend.tokens == tokens
    assert spend.cost == pytest.approx(tokens / 1_000_000 * _declared("usd_per_million_tokens"))
    assert spend.wall_clock_s == pytest.approx(
        tokens / 1_000_000 * _declared("seconds_per_million_tokens")
    )
    assert spend.calibration.tokens_per_working_set_token.source == run_record.PRIOR_RATIO
    assert spend.calibration.model is None


def test_govern_freezes_the_spend_forecast_and_the_prior_behind_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr()
    _install(monkeypatch, fake)
    _manual_runner(tmp_path)
    _write(tmp_path, "src/a.py", 16_000)

    decompose.govern_working_set(tmp_path, (_child("a", "src/a.py"),), feature_id="feat")

    payload = json.loads(fake.comments["feat"][0].partition("\n")[2])
    spend = payload["spend"]
    assert spend["tokens"] == round(12_000 * _declared("tokens_per_working_set_token"))
    assert spend["cost"] is not None and spend["wall_clock_s"] is not None
    assert spend["calibration"]["tokens_per_working_set_token"]["source"] == run_record.PRIOR_RATIO
    assert spend["calibration"]["prior"]["basis"] == run_record.DECLARED_SPEND_PRIOR.basis


_PAIR_TOKENS = 5_000_000
_PAIR_FORECAST = 100_000
_PAIR_RATIO = _PAIR_TOKENS / _PAIR_FORECAST


def _record_paired_run(repo: Path, bead_id: str, model: str, *, cost: float | None = None) -> None:
    run_record.record(
        repo,
        bead_id,
        run_record.build_record(
            agent="claude",
            handoff=False,
            returncode=0,
            duration_s=100.0,
            command=("claude",),
            tokens=_PAIR_TOKENS,
            estimated=False,
            model=model,
            task_class="task",
            forecast_tokens=_PAIR_FORECAST,
            cost=cost,
            phase=run_record.LANE_PHASE,
        ),
    )


def test_measured_history_replaces_the_prior_once_the_minimum_is_paired(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _install(monkeypatch, _FakeBr())
    (tmp_path / "basicly.toml").write_text(
        '[runner]\ndefault = "manual"\n\n[policy.sizing]\ncalibration_min_samples = 3\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(decompose, "forecast_model", lambda _repo: "claude-opus-5")
    _write(tmp_path, "src/a.py", 16_000)
    for index in range(3):
        _record_paired_run(tmp_path, f"b-{index}", "claude-opus-5", cost=10.0)

    verdict = decompose.estimate_plan(tmp_path, (_child("a", "src/a.py"),))

    spend = verdict.spend[0]
    calibration = spend.calibration
    assert calibration.pairs == 3
    assert calibration.tokens_per_working_set_token.source == run_record.MEASURED_RATIO
    assert calibration.tokens_per_working_set_token.value == pytest.approx(_PAIR_RATIO)
    tokens = round(12_000 * _PAIR_RATIO)
    assert spend.tokens == tokens
    assert calibration.usd_per_million_tokens.value == pytest.approx(2.0)
    assert spend.cost == pytest.approx(tokens / 1_000_000 * 2.0)
    assert spend.wall_clock_s == pytest.approx(tokens / 1_000_000 * 20.0)


def test_a_foreign_models_history_leaves_the_forecast_seeded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, _FakeBr())
    (tmp_path / "basicly.toml").write_text(
        '[runner]\ndefault = "manual"\n\n[policy.sizing]\ncalibration_min_samples = 3\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(decompose, "forecast_model", lambda _repo: "claude-opus-5")
    _write(tmp_path, "src/a.py", 16_000)
    for index in range(3):
        _record_paired_run(tmp_path, f"b-{index}", "some-other-model")

    spend = decompose.estimate_plan(tmp_path, (_child("a", "src/a.py"),)).spend[0]
    assert spend.calibration.pairs == 0
    assert spend.calibration.tokens_per_working_set_token.source == run_record.PRIOR_RATIO


def test_the_unsized_bound_falls_back_to_the_declared_seed(tmp_path: Path) -> None:
    tokens, source = decompose.unsized_lane_tokens(tmp_path, _sizing())

    assert (tokens, source) == (decompose.UNSIZED_LANE_TOKENS_SEED, "seed")


def test_the_unsized_bound_measures_the_committed_markers_alone(tmp_path: Path) -> None:
    body = json.dumps({"phase": run_record.LANE_PHASE, "tokens": 9_000_000, "estimated": False})
    text = f"{run_record.MARKER} id=b-1 phase=lane\n{body}"
    _export(tmp_path, {"id": "b-1", "comments": [{"text": text}]})

    assert run_record.load_run_records(tmp_path) is None
    assert decompose.unsized_lane_tokens(tmp_path, _sizing()) == (9_000_000, "measured")


def test_the_unsized_bound_is_a_sample_some_lane_really_incurred(tmp_path: Path) -> None:

    _record_run_tokens(tmp_path, "b-1", 1_000)
    _record_run_tokens(tmp_path, "b-2", 9_000)
    _record_run_tokens(tmp_path, "b-3", 2_000)

    tokens, source = decompose.unsized_lane_tokens(tmp_path, _sizing())

    assert (tokens, source) == (9_000, "measured")
    assert tokens in {1_000, 9_000, 2_000}
    assert tokens != statistics.mean((1_000, 9_000, 2_000))


def test_one_pathological_lane_does_not_own_the_unsized_bound(tmp_path: Path) -> None:

    for i in range(9):
        _record_run_tokens(tmp_path, f"b-{i}", 1_000)
    _record_run_tokens(tmp_path, "b-outlier", 20_000_000)

    tokens, _source = decompose.unsized_lane_tokens(tmp_path, _sizing())

    assert tokens == 1_000, "a single outlier must not become the bound for every lane"


def test_the_unsized_bound_still_refuses_the_overrun_that_motivated_it(tmp_path: Path) -> None:

    _record_run_tokens(tmp_path, "b-1", 4_079_243)

    tokens, _source = decompose.unsized_lane_tokens(tmp_path, _sizing())

    assert tokens > 3_000_000


def test_the_unsized_bound_is_exceeded_by_at_most_the_quantiles_tail(tmp_path: Path) -> None:
    actuals = (
        856_182, 1_022_380, 1_482_961, 1_652_344, 1_736_146, 2_066_758, 4_079_243,
        7_674_671, 7_695_800, 9_418_977, 9_430_203, 9_880_120, 10_834_801,
        11_478_450, 11_867_602, 16_002_352, 20_594_047,
    )  # fmt: skip
    for index, tokens in enumerate(actuals):
        _record_run_tokens(tmp_path, f"b-{index}", tokens)

    bound, _source = decompose.unsized_lane_tokens(tmp_path, _sizing())

    exceeded = [tokens for tokens in actuals if tokens > bound]
    assert len(exceeded) / len(actuals) <= 0.1, f"{len(exceeded)} of {len(actuals)} exceed {bound}"
    assert bound < max(actuals)
    assert sum(t > decompose.UNSIZED_LANE_TOKENS_SEED for t in actuals) / len(actuals) <= 0.1


def test_the_unsized_bound_follows_the_configured_quantile(tmp_path: Path) -> None:
    for index, tokens in enumerate((1_000, 2_000, 3_000, 4_000, 100_000)):
        _record_run_tokens(tmp_path, f"b-{index}", tokens)

    low, _ = decompose.unsized_lane_tokens(tmp_path, _sizing(unsized_lane_quantile=0.2))
    high, _ = decompose.unsized_lane_tokens(tmp_path, _sizing(unsized_lane_quantile=1.0))

    assert low == 1_000
    assert high == 100_000


def test_the_unsized_bound_ignores_an_estimated_sample(tmp_path: Path) -> None:
    _record_run_tokens(tmp_path, "b-1", 5_000)
    _record_run_tokens(tmp_path, "b-2", 90_000, estimated=True)

    assert decompose.unsized_lane_tokens(tmp_path, _sizing()) == (5_000, "measured")


def test_the_unsized_bound_needs_no_declared_scope(tmp_path: Path) -> None:

    _record_run_tokens(tmp_path, "b-1", 7_000)

    assert decompose.unsized_lane_tokens(tmp_path, _sizing()) == (7_000, "measured")


def test_the_unsized_bound_counts_a_write_dispatch_from_either_path(tmp_path: Path) -> None:

    _record_run_tokens(tmp_path, "b-lane", 5_000, phase=run_record.LANE_PHASE)
    _record_run_tokens(tmp_path, "b-build", 40_000, phase=run_record.BUILD_PHASE)

    bound, source = decompose.unsized_lane_tokens(tmp_path, _sizing(unsized_lane_quantile=1.0))

    assert (bound, source) == (40_000, "measured")


def test_the_unsized_bound_ignores_a_helper_unphased_or_dying_dispatch(tmp_path: Path) -> None:

    _record_run_tokens(tmp_path, "b-lane", 5_000)
    _record_run_tokens(tmp_path, "b-judge", 90_000, phase=run_record.VALIDATE_PHASE)
    _record_run_tokens(tmp_path, "b-decide", 90_000, phase=run_record.DECIDE_PHASE)
    _record_run_tokens(tmp_path, "b-legacy", 90_000, phase=None)
    _record_run_tokens(tmp_path, "b-died", 90_000, returncode=1)

    bound = decompose.unsized_lane_tokens(tmp_path, _sizing(unsized_lane_quantile=1.0))

    assert bound == (5_000, "measured")


def test_a_seeded_build_factor_is_recorded_as_seeded(tmp_path: Path) -> None:

    _write(tmp_path, "src/a.py", 4_000)

    estimate = decompose.estimate_cost(tmp_path, _child("t", "src/a.py"), _sizing(), overhead=0)

    assert estimate.build_factor_source == decompose.BUILD_FACTOR_SEED


def test_a_configured_build_factor_is_recorded_as_configured(tmp_path: Path) -> None:

    (tmp_path / "basicly.toml").write_text(
        "[policy.sizing.build_factor]\ntask = 9.0\n", encoding="utf-8"
    )
    _write(tmp_path, "src/a.py", 4_000)
    sizing = load_sizing_config(tmp_path)

    estimate = decompose.estimate_cost(tmp_path, _child("t", "src/a.py"), sizing, overhead=0)

    assert (estimate.build_factor, estimate.build_factor_source) == (
        9.0,
        decompose.BUILD_FACTOR_CONFIGURED,
    )
    spike = ChildSpec(title="s", acceptance=("a",), scope=("src/a.py",), type="spike")
    assert (
        decompose.estimate_cost(tmp_path, spike, sizing, overhead=0).build_factor_source
        == decompose.BUILD_FACTOR_CONFIGURED
    )


def test_the_recorded_dispatch_inputs_carry_the_factors_provenance(tmp_path: Path) -> None:
    sizing = decompose.DispatchSizing(
        task_class="task",
        estimate=decompose.CostEstimate(
            scope_tokens=1_000,
            overhead_tokens=0,
            build_factor=3.0,
            build_factor_source=decompose.BUILD_FACTOR_SEED,
        ),
        source=decompose.DISPATCH_FORECAST,
    )

    assert sizing.record_inputs(tmp_path)["build_factor_source"] == decompose.BUILD_FACTOR_SEED


def test_a_dispatch_records_its_forecast_in_both_units(tmp_path: Path) -> None:

    sizing = decompose.DispatchSizing(
        task_class="task",
        estimate=decompose.CostEstimate(scope_tokens=1_000, overhead_tokens=0, build_factor=3.0),
        source=decompose.DISPATCH_FORECAST,
    )

    inputs = sizing.record_inputs(tmp_path)

    assert inputs["forecast_tokens"] == 3_000
    prior = run_record.DECLARED_SPEND_PRIOR.tokens_per_working_set_token
    assert prior is not None
    assert inputs["forecast_spend_tokens"] == round(3_000 * prior)


def test_a_frozen_estimate_round_trips_its_factor_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    estimate = decompose.CostEstimate(
        scope_tokens=1_000,
        overhead_tokens=0,
        build_factor=9.0,
        build_factor_source=decompose.BUILD_FACTOR_CONFIGURED,
    )

    decompose.freeze_estimate(tmp_path, "b-1", "key-1", estimate)

    frozen = decompose.frozen_estimates(tmp_path, "b-1")
    assert frozen["key-1"] == estimate


def test_a_marker_frozen_before_the_field_existed_reads_as_seeded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr()
    _install(monkeypatch, fake)
    payload = '{"scope_tokens": 1000, "overhead_tokens": 0, "build_factor": 3.0, "total": 3000}'
    fake.comments["b-1"] = [f"[harness-sizing] key=key-1\n{payload}"]

    frozen = decompose.frozen_estimates(tmp_path, "b-1")

    assert frozen["key-1"].build_factor_source == decompose.BUILD_FACTOR_SEED


def test_the_calibration_status_reports_a_class_still_on_seeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, _FakeBr())
    monkeypatch.setattr(decompose, "forecast_model", lambda _repo: "claude-opus-5")
    for index in range(2):
        _record_paired_run(tmp_path, f"b-{index}", "claude-opus-5", cost=10.0)

    status = decompose.calibration_status(tmp_path, _sizing(calibration_min_samples=3))

    assert status.samples["task"] == 2
    assert status.samples["bug"] == 0
    assert status.on_seeds
    assert status.measured_classes == ()
    assert status.build_factor_sources["task"] == decompose.BUILD_FACTOR_SEED


def test_the_calibration_status_names_the_class_that_measured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _install(monkeypatch, _FakeBr())
    monkeypatch.setattr(decompose, "forecast_model", lambda _repo: "claude-opus-5")
    for index in range(3):
        _record_paired_run(tmp_path, f"b-{index}", "claude-opus-5", cost=10.0)

    status = decompose.calibration_status(tmp_path, _sizing(calibration_min_samples=3))

    assert status.measured_classes == ("task",)
    assert not status.on_seeds


def _record_spend_pair(  # noqa: PLR0913 — one parameter per seeded record field
    repo: Path,
    bead_id: str,
    *,
    tokens: int,
    forecast_tokens: int | None = None,
    forecast_spend_tokens: int | None = None,
    task_class: str = "bug",
    phase: str | None = run_record.LANE_PHASE,
    estimated: bool = False,
    returncode: int = 0,
    forecast_source: str | None = None,
    timestamp: str | None = None,
) -> None:

    record = run_record.build_record(
        agent="claude",
        handoff=False,
        returncode=returncode,
        forecast_source=forecast_source,
        duration_s=100.0,
        command=("claude",),
        tokens=tokens,
        estimated=estimated,
        task_class=task_class,
        forecast_tokens=forecast_tokens,
        forecast_spend_tokens=forecast_spend_tokens,
        phase=phase,
    )
    if timestamp is not None:
        record = replace(record, timestamp=timestamp)
    run_record.record(repo, bead_id, record)


def test_the_spend_gate_measures_a_populated_ledger() -> None:

    accuracy = decompose.spend_accuracy(REPO_ROOT, load_sizing_config(REPO_ROOT))

    assert len(accuracy.pairs) >= 20
    assert "basicly-gczc" in {pair.bead for pair in accuracy.pairs}
    median = accuracy.median_ratio
    assert median is not None
    assert 0.1 <= median <= 10


def test_a_lane_that_overran_its_forecast_by_two_orders_is_reported(tmp_path: Path) -> None:

    _record_spend_pair(tmp_path, "b-1", tokens=16_963_245, forecast_spend_tokens=66_780)

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    assert len(accuracy.pairs) == 1
    assert not accuracy.pairs[0].in_band
    assert len(accuracy.violations) == 1
    assert "b-1 spent 16,963,245 tokens" in accuracy.violations[0]
    assert "forecast of 66,780" in accuracy.violations[0]
    assert "254.017x" in accuracy.violations[0]


def test_an_over_forecast_is_reported_too(tmp_path: Path) -> None:

    _record_spend_pair(tmp_path, "b-1", tokens=100_000, forecast_spend_tokens=10_000_000)

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    assert len(accuracy.violations) == 1
    assert "0.010x" in accuracy.violations[0]


def test_a_bead_the_ledger_still_holds_open_is_held_back_rather_than_scored(
    tmp_path: Path,
) -> None:
    _record_spend_pair(tmp_path, "b-1", tokens=100_000, forecast_spend_tokens=10_000_000)
    _export(tmp_path, {"id": "b-1", "status": "open"})

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    assert accuracy.pairs == ()
    assert accuracy.unfinished == ("b-1",)
    assert accuracy.violations == ()


def test_a_closed_bead_with_the_same_miss_is_still_scored(tmp_path: Path) -> None:
    _record_spend_pair(tmp_path, "b-1", tokens=100_000, forecast_spend_tokens=10_000_000)
    _export(tmp_path, {"id": "b-1", "status": "closed"})

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    assert accuracy.unfinished == ()
    assert len(accuracy.violations) == 1
    assert "0.010x" in accuracy.violations[0]


def test_a_forecast_within_the_band_is_no_violation(tmp_path: Path) -> None:
    _record_spend_pair(tmp_path, "b-1", tokens=20_000_000, forecast_spend_tokens=10_000_000)

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    assert accuracy.pairs[0].basis == decompose.RECORDED_SPEND_FORECAST
    assert accuracy.violations == ()


def test_an_older_record_is_compared_through_todays_calibration(tmp_path: Path) -> None:

    _record_spend_pair(tmp_path, "b-1", tokens=3_000_000, forecast_tokens=10_000)

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    prior = run_record.DECLARED_SPEND_PRIOR.tokens_per_working_set_token
    assert prior is not None
    assert accuracy.pairs[0].basis == decompose.DERIVED_SPEND_FORECAST
    assert accuracy.pairs[0].forecast_tokens == round(10_000 * prior)
    assert accuracy.violations == ()


def test_a_recorded_forecast_the_band_would_refuse_is_named_not_dropped(tmp_path: Path) -> None:

    _record_spend_pair(tmp_path, "b-1", tokens=8_574_169, forecast_tokens=6_762_766)

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    assert accuracy.pairs == ()
    assert accuracy.incomparable == ("b-1",)


def test_the_spend_gate_samples_only_measured_write_dispatches(tmp_path: Path) -> None:

    _record_spend_pair(
        tmp_path, "b-1", tokens=16_963_245, forecast_spend_tokens=66_780, phase="rubric"
    )
    _record_spend_pair(
        tmp_path, "b-2", tokens=16_963_245, forecast_spend_tokens=66_780, estimated=True
    )
    _record_spend_pair(tmp_path, "b-3", tokens=16_963_245, forecast_spend_tokens=66_780, phase=None)

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    assert accuracy.pairs == ()
    assert accuracy.violations == ()
    assert accuracy.unmetered == 1


def test_a_failed_dispatch_is_not_held_to_a_whole_lane_forecast(tmp_path: Path) -> None:

    _record_spend_pair(
        tmp_path, "b-1", tokens=33_880, forecast_spend_tokens=4_805_997, returncode=1
    )

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    assert accuracy.pairs == ()
    assert accuracy.violations == ()
    assert accuracy.aborted == 1


def test_an_assumed_fallback_forecast_is_named_not_compared(tmp_path: Path) -> None:

    _record_spend_pair(
        tmp_path,
        "b-1",
        tokens=1_218_172,
        forecast_spend_tokens=16_576_875,
        forecast_source="assumed:measured",
    )

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    assert accuracy.pairs == ()
    assert accuracy.violations == ()
    assert accuracy.unscoped == ("b-1",)


def test_a_beads_attempts_are_one_lane_rather_than_three_forecast_misses(tmp_path: Path) -> None:

    for index, tokens in enumerate((30_139_416, 2_785_270, 1_512_403)):
        _record_spend_pair(
            tmp_path,
            "b-1",
            tokens=tokens,
            forecast_spend_tokens=26_320_290,
            timestamp=f"2026-08-08T1{index}:00:00+00:00",
        )

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    assert len(accuracy.pairs) == 1
    assert accuracy.pairs[0].attempts == 3
    assert accuracy.pairs[0].actual_tokens == 34_437_089
    assert accuracy.violations == ()


def test_an_overrun_spread_across_dispatches_is_still_reported(tmp_path: Path) -> None:

    for index in range(4):
        _record_spend_pair(
            tmp_path,
            "b-1",
            tokens=4_250_000,
            forecast_spend_tokens=100_000,
            timestamp=f"2026-08-08T1{index}:00:00+00:00",
        )

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    assert len(accuracy.violations) == 1
    assert "b-1 spent 17,000,000 tokens over 4 dispatches" in accuracy.violations[0]
    assert "170.000x" in accuracy.violations[0]


def test_a_re_dispatched_lane_is_held_to_its_latest_forecast(tmp_path: Path) -> None:

    _record_spend_pair(
        tmp_path,
        "b-1",
        tokens=1_000_000,
        forecast_spend_tokens=12_936_362,
        timestamp="2026-08-08T10:00:00+00:00",
    )
    _record_spend_pair(
        tmp_path,
        "b-1",
        tokens=1_000_000,
        forecast_spend_tokens=13_749_377,
        timestamp="2026-08-08T11:00:00+00:00",
    )

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    assert accuracy.pairs[0].forecast_tokens == 13_749_377
    assert accuracy.pairs[0].actual_tokens == 2_000_000
