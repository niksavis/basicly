"""Tests for the decomposer & dependency-graph builder (onb.4)."""

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
    """Stateful stand-in for the br CLI, routed by subcommand.

    Hands out sequential child ids on create, records dep-add edges, keeps comments
    per issue (the carrier for frozen sizing markers), and reports no cycles unless
    seeded with one — exactly enough to exercise the decomposer.
    """

    def __init__(
        self, *, cycles: list[list[str]] | None = None, labels: list[str] | None = None
    ) -> None:
        self.cycles = cycles or []
        # The labels the *parent* feature carries, which children inherit.
        self.labels = labels
        self.created: list[tuple[str, str, str]] = []  # (id, title, body)
        self.create_args: list[list[str]] = []  # the full argv, for flag assertions
        self.shown: list[str] = []  # ids read back, to assert the read is not per-child
        self.edges: list[tuple[str, str]] = []  # (issue, depends_on)
        self.comments: dict[str, list[str]] = {}
        self._counter = 0

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["create"]:
            return self._create(args)
        if args[:1] == ["show"]:
            self.shown.append(args[1])
            # A *list* of records, which is what the installed br actually
            # returns — the fake said dict until the real binary was exercised.
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
    # Nothing on `decompose`: every read and write it makes is behind a seam (basicly-wpc8).
    fake_tracker.install(monkeypatch, fake)


# The three fields the plan gate requires of every child (basicly-u2hl.1). Spread into
# every spec these tests build, so a fixture stays a fixture for what it is *about*
# rather than re-stating the gate's minimum in twenty places. What the gate does with
# them is tested in `test_plan_gate.py`, not here.
_GATED = {
    "depends_on": (),
    "budget_tokens": 40_000,
    "integrity": "L2",
    "demonstration": "run `basicly decompose feat --dry-run`",
}


def _child(title: str, *scope: str) -> ChildSpec:
    return ChildSpec(title=title, acceptance=("does the thing",), scope=scope or (title,), **_GATED)


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


# --- One shared path must not collapse the plan (basicly-jr0l.45) -------------
#
# Four children that each declare their own module plus the manifest they will add
# a line to. Every pair overlaps through that one file, so the transitive closure
# used to merge all four into one serial chain — the more honestly the scopes were
# declared, the worse the grouping got.


def _manifest_plan(*, shared: bool) -> tuple[ChildSpec, ...]:
    """Four children, each owning one module and touching one shared manifest."""
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
    """Undeclared, a shared manifest still serializes everyone — the safe default."""
    assert decompose.group_children(_manifest_plan(shared=False)) == (0, 0, 0, 0)


def test_a_shared_manifest_keeps_the_distinct_modules_parallel() -> None:
    """Declaring the manifest shared leaves the owned modules to decide the grouping."""
    assert decompose.group_children(_manifest_plan(shared=True)) == (0, 1, 2, 3)


def test_a_single_owner_still_serializes_everyone_who_touches_the_path() -> None:
    """A shared declaration is only as strong as the weakest claim on the path.

    Three children append to the manifest and the fourth owns it. Owning it means
    doing something the appenders cannot be reordered against, so the group stays
    serial — the declaration cannot be used to route around a real owner.
    """
    plan = list(_manifest_plan(shared=True))
    plan[2] = ChildSpec(plan[2].title, plan[2].acceptance, plan[2].scope, plan[2].type)
    assert decompose.group_children(tuple(plan)) == (0, 0, 0, 0)


def test_shared_does_not_excuse_an_overlap_on_an_owned_path() -> None:
    """Two children sharing a manifest still serialize on the module they both own."""
    children = (
        ChildSpec("a", ("ac",), ("src/x.py", "pyproject.toml"), shared=("pyproject.toml",)),
        ChildSpec("b", ("ac",), ("src/x.py", "pyproject.toml"), shared=("pyproject.toml",)),
    )
    assert decompose.group_children(children) == (0, 0)


def test_collapsing_paths_names_the_path_that_collapses_the_plan() -> None:
    """The report names the single path the serial chain is owed to, and the cost."""
    (item,) = decompose.collapsing_paths(_manifest_plan(shared=False))
    assert item.glob == "pyproject.toml"
    assert item.declarers == (0, 1, 2, 3)
    assert (item.groups, item.groups_without) == (1, 4)
    assert item.neutralized is False
    assert "`pyproject.toml`" in decompose.describe_collapsing_path(item)


def test_collapsing_paths_still_names_a_path_a_declaration_defused() -> None:
    """A declared-shared manifest is reported as neutralized, never silently dropped.

    Naming it is how a reviewer checks the declaration was honest: the path that
    *would* have collapsed the plan is exactly the one worth a second look.
    """
    (item,) = decompose.collapsing_paths(_manifest_plan(shared=True))
    assert item.glob == "pyproject.toml"
    assert (item.groups, item.groups_without) == (1, 4)
    assert item.neutralized is True
    assert "no longer collapses" in decompose.describe_collapsing_path(item)


def test_collapsing_paths_is_silent_when_no_single_path_decides() -> None:
    """A plan whose groups survive dropping any one glob has nothing to report."""
    children = (_child("a", "src/a.py"), _child("b", "src/b.py"), _child("c", "src/c.py"))
    assert decompose.collapsing_paths(children) == ()


def test_collapsing_paths_names_a_wildcard_that_swallows_its_siblings() -> None:
    """The diagnostic is as specific as the plan: a subtree glob is named too.

    The pathology is not only manifests — one child declaring ``src/**`` serializes
    every sibling under it, and that is the same silent collapse with a different
    shape. A wildcard cannot be declared shared, so it is reported live.
    """
    children = (
        _child("wide", "src/**"),
        _child("a", "src/a.py"),
        _child("b", "src/b.py"),
    )
    globs = {item.glob for item in decompose.collapsing_paths(children)}
    assert "src/**" in globs
    assert all(item.neutralized is False for item in decompose.collapsing_paths(children))


def test_collapsing_paths_handles_a_child_whose_whole_scope_is_the_shared_path() -> None:
    """Dropping a glob must not leave an empty scope reading as "matches everything".

    An emptied scope compares as overlapping another emptied scope, so measuring the
    counterfactual by editing the scopes would have suppressed the very split it was
    measuring — hence :func:`decompose.serializes` takes *ignoring* instead.
    """
    children = (
        ChildSpec("manifest-only", ("ac",), ("pyproject.toml",)),
        _child("b", "src/b.py", "pyproject.toml"),
    )
    (item,) = decompose.collapsing_paths(children)
    assert (item.glob, item.groups, item.groups_without) == ("pyproject.toml", 1, 2)


def test_collapse_note_speaks_only_for_a_live_collapse() -> None:
    """The one-line suffix names live collapses and stays empty once one is defused."""
    live = decompose.collapse_note(decompose.collapsing_paths(_manifest_plan(shared=False)))
    assert "`pyproject.toml`" in live
    assert decompose.collapse_note(decompose.collapsing_paths(_manifest_plan(shared=True))) == ""
    assert decompose.collapse_note(()) == ""


# --- A path every lane writes and no child declares (basicly-o8p0) -----------
#
# The reported failure: three lanes over `schema.py`, `config.py` and `usage.py` —
# disjoint, `VERDICT: ready`, all three appending a `CHANGELOG.md` entry nobody
# declared. Two landed and the third rebased onto an anchor that had moved twice,
# conflicted, and spent both rework retries there. So the control in each test below
# is the same plan with nothing configured: it must stay fully parallel, or the fix
# has simply serialized every plan in every repo.

_APPEND_ONLY = ("CHANGELOG.md",)


def _disjoint_plan() -> tuple[ChildSpec, ...]:
    """The reported pass: three children whose declared scopes cannot overlap."""
    return tuple(_child(name, f"src/basicly/{name}.py") for name in ("schema", "config", "usage"))


def test_a_configured_append_only_path_serializes_lanes_that_share_no_scope() -> None:
    """The bead's case: disjoint scopes, one undeclared file, one serial chain."""
    plan = _disjoint_plan()
    assert decompose.group_children(plan, _APPEND_ONLY) == (0, 0, 0)


def test_a_pass_that_shares_no_append_only_path_stays_parallel() -> None:
    """The control: with nothing configured the same plan is three parallel groups."""
    assert decompose.group_children(_disjoint_plan()) == (0, 1, 2)
    assert decompose.group_children(_disjoint_plan(), ()) == (0, 1, 2)


def test_a_child_may_declare_the_append_only_path_shared_and_stay_parallel() -> None:
    """The escape hatch is the declaration that already existed, read from both sides.

    A child that has thought about the path puts it in its own scope *and* under
    ``shared``; two children that both did are appending distinct entries by their own
    account, and the grouping leaves them parallel. Without that, a repo declaring one
    append-only path could never fan out again.
    """
    plan = tuple(
        ChildSpec(c.title, c.acceptance, (*c.scope, "CHANGELOG.md"), shared=("CHANGELOG.md",))
        for c in _disjoint_plan()
    )
    assert decompose.group_children(plan, _APPEND_ONLY) == (0, 1, 2)


def test_one_child_owning_the_append_only_path_still_serializes_the_pass() -> None:
    """Weakest claim wins here too: a declared owner blocks the undeclared appenders."""
    plan = list(_disjoint_plan())
    plan[0] = ChildSpec(
        plan[0].title, plan[0].acceptance, (*plan[0].scope, "CHANGELOG.md"), shared=()
    )
    assert decompose.group_children(tuple(plan), _APPEND_ONLY) == (0, 0, 0)


def test_the_collapsing_path_report_names_the_configured_path_and_its_origin() -> None:
    """The collapse must not read as a grouping bug on scopes a reader can see are disjoint.

    The path is in no child's scope, so ``declarers`` is empty and the line has to say
    where it came from — otherwise the report names a path nothing in the plan mentions
    and the author cannot act on it.
    """
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
    """Neutralized, and said so: the plan is informed rather than broken."""
    plan = tuple(
        ChildSpec(c.title, c.acceptance, (*c.scope, "CHANGELOG.md"), shared=("CHANGELOG.md",))
        for c in _disjoint_plan()
    )
    (item,) = decompose.collapsing_paths(plan, _APPEND_ONLY)
    assert item.neutralized is True
    assert "no longer serializes" in decompose.describe_collapsing_path(item, _APPEND_ONLY)


def test_two_configured_paths_are_both_named_rather_than_neither() -> None:
    """Each one is independently sufficient, so a one-at-a-time counterfactual is silent.

    Dropping `CHANGELOG.md` alone leaves `docs/release-notes.md` merging the same
    children, so measuring per path against the plan-as-configured would report
    nothing at all — the silence the diagnostic exists to remove.
    """
    contended = ("CHANGELOG.md", "docs/release-notes.md")
    named = {item.glob for item in decompose.collapsing_paths(_disjoint_plan(), contended)}
    assert named == set(contended)


def test_the_preview_groups_a_plan_against_the_same_configured_paths() -> None:
    """A dry run that ignored the convention would promise groups the run serializes."""
    planned = decompose.preview(_disjoint_plan(), _APPEND_ONLY)
    assert [child.group for child in planned] == [0, 0, 0]
    assert [child.predecessor for child in planned] == [None, 0, 1]


# --- Plan parsing -----------------------------------------------------------


def test_load_plan_text_json_and_toml_agree() -> None:
    """The same plan in JSON and TOML parses to identical child specs."""
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
    """A .toml plan file is parsed as TOML."""
    plan = tmp_path / "plan.toml"
    plan.write_text(
        '[[children]]\ntitle = "t"\nacceptance = ["ac"]\nscope = ["s"]\n' + _GATED_TOML, "utf-8"
    )
    assert decompose.load_plan_file(plan) == (ChildSpec("t", ("ac",), ("s",), **_GATED),)


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


# The three gate-required fields as a plan document spells them, for the format tests
# that compare JSON against TOML — each needs the pair to be the same plan, and the
# gate refuses the plan without them.
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
    """No shared declaration means the child owns its whole scope, as plans always did."""
    entry = {} if shared is None else {"shared": shared}
    assert decompose.parse_children(_one_child(**entry))[0].shared == ()


def test_parse_children_rejects_a_shared_path_outside_the_scope() -> None:
    """A shared declaration may only reclassify a path the plan already declared.

    Otherwise it would weaken serialization on a path the recorded ``## Scope`` never
    mentions, and nothing downstream — sizing, merge-time attribution — could see it.
    """
    with pytest.raises(ValueError, match="not in that child's 'scope'"):
        decompose.parse_children(_one_child(shared=["pyproject.toml"]))


def test_parse_children_rejects_a_glob_as_a_shared_path() -> None:
    """One literal path at a time: no plan may exempt a whole subtree from serializing."""
    with pytest.raises(ValueError, match="is a glob"):
        decompose.parse_children(_one_child(scope=["src/**"], shared=["src/**"]))


def test_parse_children_rejects_a_malformed_shared_list() -> None:
    """A shared declaration that is not a list of non-empty strings is a loud error."""
    with pytest.raises(ValueError, match="'shared' must be a list"):
        decompose.parse_children(_one_child(shared="src/x.py"))
    with pytest.raises(ValueError, match="'shared' entries must be non-empty"):
        decompose.parse_children(_one_child(shared=[" "]))


def test_load_plan_text_reads_shared_in_json_and_toml() -> None:
    """Both plan formats carry the shared declaration onto the spec identically."""
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


def test_decompose_children_inherit_the_features_labels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A child of a phase-labelled feature must stay in that phase.

    Phase membership is a label rather than a re-parenting, so a child created
    without the parent's labels is well-formed, passes its own DoR, and is absent
    from every ``br list --label phase-N`` — the feature stays in the phase while
    none of the work under it does (basicly-jr0l.26).
    """
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
    """An external br invocation is ~175x an in-process read, and the answer cannot change."""
    fake = _FakeBr(labels=["phase-2"])
    _install(monkeypatch, fake)
    children = (_child("a", "src/a.py"), _child("b", "src/b.py"), _child("c", "src/c.py"))

    decompose.decompose(tmp_path, "feat", children)

    assert fake.shown == ["feat"]


def test_decompose_unlabelled_feature_sends_no_empty_label_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``-l ''`` is not the same request as omitting ``-l``.

    A feature that has never been labelled reads back as null rather than an
    empty list, and both must produce an argv with no ``-l`` at all.
    """
    for labels in (None, []):
        fake = _FakeBr(labels=labels)
        _install(monkeypatch, fake)

        decompose.decompose(tmp_path, "feat", (_child("a", "src/a.py"),))

        assert "-l" not in fake.create_args[0], f"labels={labels!r} emitted an -l flag"


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


def test_decompose_wires_no_chain_through_a_shared_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The recorded graph, not just the grouping: no blocks edge for a shared path.

    The grouping is only a computation until it reaches ``br`` — what the merge queue
    and ``ready_lanes`` read is the absence of a sibling ``blocks`` edge.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)

    result = decompose.decompose(tmp_path, "feat", _manifest_plan(shared=True))

    assert fake.edges == []
    assert result.parallel_groups == 4
    # And the collapsing path travels with the result, so the loop can name it.
    assert [item.glob for item in result.collapsing] == ["pyproject.toml"]
    assert result.collapsing[0].neutralized is True


def test_decompose_result_names_a_live_collapsing_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An undeclared shared path serializes the plan *and* is named on the result."""
    fake = _FakeBr()
    _install(monkeypatch, fake)

    result = decompose.decompose(tmp_path, "feat", _manifest_plan(shared=False))

    assert result.parallel_groups == 1
    assert len(fake.edges) == 3  # one chain through all four children
    assert [(item.glob, item.neutralized) for item in result.collapsing] == [
        ("pyproject.toml", False)
    ]


def test_decompose_reads_the_append_only_paths_from_config_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The recorded graph, from a repo config alone — no caller passes the list in.

    The loop calls ``decompose.decompose`` with nothing but the plan, so a list only
    the CLI knew about would serialize `basicly decompose` and leave the factory path
    — the one that actually fans out — grouping as if the convention did not exist.
    """
    (tmp_path / "basicly.toml").write_text(
        '[worktree]\nappend_only_paths = ["CHANGELOG.md"]\n', encoding="utf-8"
    )
    fake = _FakeBr()
    _install(monkeypatch, fake)

    result = decompose.decompose(tmp_path, "feat", _disjoint_plan())

    assert result.parallel_groups == 1
    assert len(fake.edges) == 2  # one chain through all three children
    assert [(item.glob, item.neutralized) for item in result.collapsing] == [
        ("CHANGELOG.md", False)
    ]


def test_decompose_stays_parallel_when_no_append_only_path_is_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The control at the recording layer: no config, no chain, three lanes."""
    fake = _FakeBr()
    _install(monkeypatch, fake)

    result = decompose.decompose(tmp_path, "feat", _disjoint_plan())

    assert result.parallel_groups == 3
    assert fake.edges == []
    assert result.collapsing == ()


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


# Scope glob and matching tree for a plan the band must refuse. Spread over files
# rather than concentrated in one, because `SCOPE_FILE_READ_CAP` means no single
# module can reach a refusable size any more (basicly-fcls) — a one-huge-file
# fixture would leave every refusal test asserting nothing.
_OVERSIZED_SCOPE = "src/big/*.py"


def _write_oversized(repo: Path) -> None:
    """A tree whose `_OVERSIZED_SCOPE` sizes well above `DEFAULT_WORKING_SET_MAX`."""
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
    """AC: decompose no longer refuses a lane on the size of the file it names.

    Asserted through `check_working_set`, the governor that actually refuses, and at
    the real ceiling — the arithmetic being right is not the claim, the lane being
    dispatchable is. `src/basicly/cli.py`'s size is used verbatim because it is the
    module every chunking candidate in this repo names.
    """
    _write(tmp_path, "src/cli.py", 182_396)  # cli.py's real size: 45,599 tokens
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
    """Total = overhead + scope x class factor; unlisted classes use the task factor."""
    _write(tmp_path, "src/a.py", 4_000)  # 1000 scope tokens
    sizing = _sizing(build_factors={"task": 3.0, "bug": 2.0})
    task = decompose.estimate_cost(tmp_path, _child("t", "src/a.py"), sizing, overhead=500)
    assert (task.scope_tokens, task.overhead_tokens, task.build_factor) == (1_000, 500, 3.0)
    assert task.total == 500 + 3_000
    bug = ChildSpec(title="b", acceptance=("a",), scope=("src/a.py",), type="bug")
    assert decompose.estimate_cost(tmp_path, bug, sizing, overhead=0).total == 2_000
    spike = ChildSpec(title="s", acceptance=("a",), scope=("src/a.py",), type="spike")
    assert decompose.estimate_cost(tmp_path, spike, sizing, overhead=0).total == 3_000


# --- The measure moved; the glob consumers did not (basicly-fcls) ------------
#
# Eleven call sites read a scope glob as a *set of paths* — grouping, the loop's
# scope-collision gate, merge coupling attribution — and exactly one reads it as a
# quantity. Changing the grammar (a `cli.py:100-200` range) would have broken all
# eleven silently: `globs_overlap('cli.py', 'cli.py:100-200')` is False, so two lanes
# on one file would have been parallelized and the wrong permanent coupling edge
# written. So only the *function applied to* the globs changed, and these three pin
# that — one per consumer, each asserting the answer is invariant to the file size
# that `scope_read_cost` now caps.


def test_grouping_is_unchanged_by_the_size_of_the_file_two_children_share(
    tmp_path: Path,
) -> None:
    """Consumer 1: two children naming one large module still serialize.

    Grouping never touches the filesystem, and this is what proves the read cap did
    not leak into it: the same pair groups identically whether the shared module is
    a stub or ten times the cap.
    """
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
    """Consumer 2: the loop's scope-collision gate sees the same overlaps.

    `loop._scope_collision_block` decides on `merge.out_of_scope_paths`, which is
    `globs_overlap` underneath — a pure predicate over path segments. A capped
    read-cost must not make a large file look less collided than a small one, or the
    gate would quietly stop refusing exactly the modules chunking is aimed at.
    """
    _write(tmp_path, "src/big.py", read_cost.SCOPE_FILE_READ_CAP * 4 * 10)
    _write(tmp_path, "src/small.py", 40)

    for name in ("big", "small"):
        assert decompose.globs_overlap(f"src/{name}.py", "src/**/*.py") is True
        assert decompose.globs_overlap(f"src/{name}.py", "tests/*.py") is False
        assert decompose.scopes_overlap((f"src/{name}.py",), ("src/*.py", "docs/*")) is True

    # And the gate's own question: a path inside the declared scope is never "outside".
    assert merge.out_of_scope_paths(["src/big.py"], ("src/big.py",)) == ()
    assert merge.out_of_scope_paths(["src/big.py"], ("src/small.py",)) == ("src/big.py",)


def test_merge_coupling_attribution_is_unchanged_by_the_size_of_the_file(
    tmp_path: Path,
) -> None:
    """Consumer 3: a conflict on a large module still names the lane that declared it.

    The coupling edge outlives the pass, so a wrong one teaches the graph a
    relationship that does not exist. It is attributed from the declared globs and
    from nothing else — never from read-cost — and that must hold for the largest
    file in the repo as much as for a fragment.
    """
    _write(tmp_path, "src/big.py", read_cost.SCOPE_FILE_READ_CAP * 4 * 10)
    scopes = {"lane-a": ("src/big.py",), "lane-b": ("src/small.py",)}

    assert merge.coupled_lanes(("src/big.py",), scopes, bounced="lane-b") == ("lane-a",)
    assert merge.coupled_lanes(("src/big.py",), scopes, bounced="lane-a") == ()
    assert merge.coupled_lanes(("src/other.py",), scopes, bounced="lane-b") == ()


def test_parse_scope_section_round_trips_child_body() -> None:
    """The calibration scope parser reads exactly what _child_body records."""
    spec = _child("t", "src/**/*.py", "tests/test_x.py")
    body = decompose._child_body(spec)
    assert decompose.parse_scope_section(body) == ("src/**/*.py", "tests/test_x.py")
    assert decompose.parse_scope_section("no scope section here") == ()


def test_scope_line_example_is_a_line_the_parser_actually_accepts() -> None:
    """The example an author is shown must parse, or it teaches the mistake it prevents.

    ``policy`` owns the example because it scaffolds the section; ``decompose`` owns
    the pattern. Nothing but this test holds the two together (basicly-tuy6).
    """
    assert decompose.parse_scope_section(f"## Scope\n\n{policy.SCOPE_LINE_EXAMPLE}\n") != ()


def test_unparsed_scope_warning_fires_when_the_heading_yielded_no_glob() -> None:
    """Heading present, entries prose: the silent case that cost a whole tracker its sizing.

    Both spellings measured on the real tracker are covered — a bare path, and a
    backticked path with a trailing parenthetical note (basicly-jr0l.60's own body).
    """
    bare = "## Scope\n\n- src/basicly/loop.py\n"
    annotated = "## Scope\n\n- `src/basicly/supervise.py`  (admit_working_set only)\n"
    for description in (bare, annotated):
        assert decompose.parse_scope_section(description) == ()
        warning = decompose.unparsed_scope_warning(description)
        assert warning is not None
        assert policy.SCOPE_LINE_EXAMPLE in warning


def test_unparsed_scope_warning_is_silent_when_there_is_nothing_to_say() -> None:
    """No heading is the ordinary state of an undecomposed bead, not an authoring error.

    Warning on it would fire on most of the tracker and train the reader to ignore
    the one case that matters.
    """
    assert decompose.unparsed_scope_warning("no scope section here") is None
    assert decompose.unparsed_scope_warning(decompose._child_body(_child("t", "src/a.py"))) is None


def test_scaffolded_scope_hint_does_not_itself_read_as_a_declared_scope() -> None:
    """A scaffold nobody filled in must warn, not pass as a bead that declared a scope.

    The hint names the format by example, so it necessarily contains a backticked
    path; if it sat on its own line the parser would read it as a real entry and the
    unfilled scaffold would size a lane against ``src/basicly/cli.py``.
    """
    body = policy.scaffold_body("task")
    assert decompose.parse_scope_section(body) == ()
    assert decompose.unparsed_scope_warning(body) is not None


def test_child_body_carries_the_sections_the_childs_own_type_requires() -> None:
    """A bug child owes Steps to Reproduce too, or it blocks at its own classify gate.

    The body used to hard-code the ``task`` section set, so a plan that typed a
    child ``bug`` produced a child the DoR gate then refused (basicly-kjc5.44).
    """
    bug = ChildSpec(
        title="b", acceptance=("given x then y",), scope=("src/a.py",), type="bug", **_GATED
    )
    body = decompose._child_body(bug)
    headings = [line for line in body.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Steps to Reproduce",
        "## Acceptance Criteria",
        "## Scope",
        "## Plan",
    ]
    # The supplied content survives; only the unfilled section carries a placeholder.
    assert "- given x then y" in body
    assert decompose.parse_scope_section(body) == ("src/a.py",)


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


def _record_run_tokens(  # noqa: PLR0913 — one parameter per seeded record field
    repo: Path,
    bead_id: str,
    tokens: int,
    *,
    estimated: bool = False,
    scope_tokens: int | None = None,
    returncode: int = 0,
    # A seeded record stands for a *lane* unless a test says otherwise: that is what
    # every caller here means, and leaving it unset made them all indistinguishable
    # from a decider or judge dispatch once the population learned to tell the
    # difference. Override it to seed a non-lane dispatch deliberately.
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
    """Seed the committed ledger — what a fresh clone has and nothing more."""
    flipped_tracker.seed_records(repo, records)


# --- The ceiling is answerable to the lanes that ran (basicly-3w44) ----------


def _lane_estimate(scope_tokens: int, task_class: str) -> int:
    """One lane's working-set estimate: scope read-cost x its class's seed factor.

    The single formula both directions of the gate size a lane with. Two would be the
    very defect the gate exists to catch — a number compared against a number
    denominated in a different quantity (basicly-z2wi, basicly-ipx2).
    """
    return round(scope_tokens * DEFAULT_BUILD_FACTOR_SEEDS.get(task_class, DEFAULT_BUILD_FACTOR))


def _exported_class_and_scope(repo_root: Path) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Task class and priced globs per bead, from the committed tracker export.

    The export rather than ``br show``, for the same reason the rest of this gate uses
    it: it is what a fresh clone has, it carries closed records, and it costs no
    subprocess. A bead nobody decomposed reads as an empty scope.

    Priced globs, not the ownership scope: the ceiling is derived from what a lane reads,
    and pricing `## Scope` here is what let completing a declaration for the merge gate
    drag the constant upward (basicly-efw2).
    """
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
    """Estimate per bead for every headless dispatch that ended in *outcome*.

    One function for both populations, sizing every lane the same way: the globs it
    prices, read from the tree now, at the seed factor for its class. Two functions is
    how the ceiling has been wrong twice.

    **Never the record's own ``scope_tokens``**, even where one is present: those were
    written by whatever measure was current when the dispatch ran (the ones on this tree
    are whole-file sums predating basicly-fcls), so preferring them mixes two quantities
    into one comparison — the defect this gate exists to catch (basicly-z2wi,
    basicly-ipx2). Re-deriving drifts as the scoped files grow, which
    ``RunRecord.context_tokens`` is here to replace.

    Handoffs are excluded: a driving agent or human carried that work in their own
    session, so it bounds what a *session* holds, not what a dispatched lane does. Forty
    of the forty-seven recorded lanes are handoffs, so counting them would inflate the
    evidence roughly fivefold.
    """
    beads = _exported_class_and_scope(repo_root)
    estimates: dict[str, int] = {}
    for bead_id, history in run_record.dispatch_history(repo_root).items():
        task_class, scope = beads.get(bead_id, ("task", ()))
        scope_tokens = read_cost.scope_read_cost(repo_root, scope)
        if scope_tokens <= 0:
            continue
        estimate = _lane_estimate(scope_tokens, task_class)
        for entry in history:
            # Write dispatches only. A decider or rubric-judge dispatch is recorded
            # against whatever bead raised the question — including an *epic* — and
            # sizing that bead's scope produces a number about the epic's whole
            # surface rather than about any lane: basicly-tcmy's escalation on
            # 2026-08-03 sized 1_344_546 and demanded a ceiling of 1_352_000. It
            # became visible only once basicly-gczc made those dispatches
            # adapter-measured, so this filter is the third consumer of the same
            # defect basicly-tcmy.5 fixes in `unsized_lane_tokens` and
            # `calibrated_build_factors`; unify on its `run_record.is_write_phase`
            # when that lands. A record whose phase was never written is not
            # evidence that a lane ran, so it is excluded too.
            if entry.get("phase") in ("build", "lane") and entry.get("outcome") == outcome:
                estimates[bead_id] = estimate
    return estimates


def completed_lane_estimates(repo_root: Path) -> dict[str, int]:
    """Estimate per bead for every lane a headless dispatch actually completed.

    Sized identically to :func:`failed_lane_estimates` — see :func:`_lane_estimates`
    for why that symmetry is load-bearing. This side used to filter on the record's
    own ``scope_tokens`` and so could not see basicly-kjc5.42's success, which is the
    only reason the ceiling appeared to be refusing basicly-kjc5.44 (basicly-fcls).
    """
    return _lane_estimates(repo_root, run_record.EXECUTED)


def failed_lane_estimates(repo_root: Path) -> dict[str, int]:
    """Estimate per bead for every lane a headless dispatch failed (basicly-ipx2).

    The half that was missing, and why: the completed side filtered on the record's own
    ``scope_tokens``, and **every** failed record on this tree carries
    ``scope_tokens: None`` — the sizing fields landed after those dispatches died. A
    failure-side query written the same way returns ``{}``, which is how "zero lanes have
    failed at any size" came to be committed beside the ceiling.
    """
    return _lane_estimates(repo_root, run_record.FAILED)


def _ceiling_violations(repo_root: Path, ceiling: int) -> list[str]:
    """Every way *ceiling* contradicts the lanes this engine has actually dispatched.

    Two directions, because a bound has two. Too low is a lane that completed above
    it — work the engine refuses despite having proven it can do it (basicly-3w44).
    Too high is a lane that died at a size nothing has ever completed, which the
    ceiling then admits on no evidence at all (basicly-ipx2). Worst first within each.
    """
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
    # A failure is evidence about size only when nothing larger has completed: a lane
    # that died below a size already proven runnable died of something else, and its
    # exit code cannot settle that — the record keeps a returncode, never a cause.
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
    """The live gate: the band must admit every proven size and no unproven fatal one.

    The lower half is basicly-3w44: `working_set_max` was 64,000 and eighteen recorded
    lanes exceeded it, every one of which completed — 0-for-18 against the only evidence
    available. The upper half is basicly-ipx2, and it exists because the one-directional
    version of this test could not contradict the false claim shipped beside it: a
    ceiling admitting a size only failures have reached licenses it on nothing.

    Neither half asserts equality with the observed maximum: a larger lane succeeding is
    good news and must not turn main red. It fails only when the constant and the record
    disagree, and it names the value that reconciles them.
    """
    assert _ceiling_violations(REPO_ROOT, DEFAULT_WORKING_SET_MAX) == []


def test_the_recorded_failures_are_visible_to_the_ceiling_gate() -> None:
    """The positive control on the population the old query deleted (basicly-ipx2).

    Without this, the upper half of the gate above is indistinguishable from one
    measuring an empty set — and an empty set is what a failure-side query inherits
    the moment it filters on `isinstance(scope_tokens, int)`, because no failed record
    on this tree has ever carried one. That silence read as "zero lanes have failed"
    once already.
    """
    assert failed_lane_estimates(REPO_ROOT)


def test_no_lane_this_engine_completed_is_refused_by_the_band() -> None:
    """AC: the band admits every size a headless dispatch has actually completed.

    The live half of basicly-fcls' fourth criterion, asserted through the governor the
    engine really runs rather than through `_ceiling_violations`' arithmetic: a ceiling
    that reconciles with the record but whose `check_working_set` still refuses would
    pass that test and fail every lane.

    basicly-kjc5.42 motivates it — it completed, and until that bead was sized at 136,668
    against a ceiling of 112,000. The contradiction was invisible because the
    completed-side query dropped it on the `scope_tokens` filter, hence the membership
    assertion, this test's positive control.
    """
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
    """The known-bad control: a lane above the ceiling is reported, not shrugged off.

    Without this the live test above is indistinguishable from one that measures an
    empty set — which is exactly how the 64,000 constant survived eighteen
    contradictions.
    """
    _write(tmp_path, "src/a.py", 16_000)  # 4_000 scope tokens, exactly at the read cap
    body = decompose._child_body(_child("t", "src/a.py"))
    _install(monkeypatch, _FakeBrShow({"b-1": ("task", body)}))
    _export(tmp_path, {"id": "b-1", "issue_type": "task", "description": body})
    _record_run_tokens(tmp_path, "b-1", 1_000, scope_tokens=4_000)  # 4_000 x 3.0 = 12_000

    assert _ceiling_violations(tmp_path, 112_000) == []

    violations = _ceiling_violations(tmp_path, 8_000)
    assert len(violations) == 1
    assert "b-1 completed at an estimate of 12,000" in violations[0]
    assert "raise it to at least 16,000" in violations[0]  # rounded up to a floor-unit


def test_completing_a_scope_for_the_merge_gate_does_not_move_the_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC: a complete declaration above the old ceiling records no ceiling violation.

    basicly-efw2: the collision gate names paths the diff touched, the author declares
    them, and the estimate grows although the diff did not — 78,709 to 197,646 to
    245,466 in one landing, two raises. This lane reads one module and owns five.
    """
    for name in "abcde":
        _write(tmp_path, f"src/{name}.py", 16_000)  # 4_000 tokens each, at the read cap
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

    # The control, or an inert gate would pass the assertion above too: the identical
    # completion does move the ceiling when nothing declares a working set.
    _export(tmp_path, {"id": "b-1", "issue_type": "task", "description": f"## Scope\n\n{owned}"})
    assert completed_lane_estimates(tmp_path)["b-1"] == 60_000
    assert _ceiling_violations(tmp_path, 16_000) != []


def test_a_violation_of_this_gate_is_attributed_to_the_lane_it_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The gate's wording is a *contract* with the shared-tracker register (basicly-qorx).

    This gate reads the whole tracker, and every lane in a supervised pass shares one
    `.beads` through the redirect, so one lane's failing finishing record fails inside
    every sibling's landing. ``policy.shared_tracker_gate_failure`` keeps that from
    charging the siblings rework, and it recognises the failure by matching this text and
    reading the bead id out of it. Reword the violation and that forgiveness goes
    silently inert — a defect no other test here can see.
    """
    _write(tmp_path, "src/a.py", 16_000)
    body = decompose._child_body(_child("t", "src/a.py"))
    _install(monkeypatch, _FakeBrShow({"b-1": ("task", body)}))
    _export(tmp_path, {"id": "b-1", "issue_type": "task", "description": body})
    _record_run_tokens(tmp_path, "b-1", 1_000, scope_tokens=4_000)

    violation = _ceiling_violations(tmp_path, 8_000)[0]

    attributed = policy.shared_tracker_gate_failure(violation, "b-2")
    assert attributed is not None and attributed.culprits == ("b-1",)
    # And the lane the violation names still owns it — the control that keeps this
    # from being a way to launder any tracker-wide failure.
    assert policy.shared_tracker_gate_failure(violation, "b-1") is None


def test_a_decider_dispatch_is_not_evidence_about_how_big_a_lane_can_be(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A decision recorded against a bead must not size that bead as a completed lane.

    The control for the phase filter, whose failure mode is silent in the direction that
    matters: a decider dispatch lands on whichever bead raised the question, an **epic**
    included, and sizing an epic answers a question about its whole surface. On
    2026-08-03 the escalation on basicly-tcmy sized 1,344,546 and demanded a ceiling of
    1,352,000 — from a dispatch that wrote no code. Same record twice, one field apart:
    as a lane it is evidence and must be reported, as a decision it must vanish.
    """
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
    """The known-bad control for the upper direction (basicly-ipx2).

    Its counterpart above exists because a one-directional gate is indistinguishable from
    one measuring an empty set; this one exists because the *new* direction was the empty
    one. The failed lane declares a scope and records no size — the shape of every real
    failure on this tree — so it is sized from the tree. The big lane is ten capped files
    rather than one huge one (basicly-fcls): under the read cap no single module reaches
    a fatal size.
    """
    _write(tmp_path, "src/small.py", 16_000)  # 4_000 scope tokens
    for index in range(10):
        _write(tmp_path, f"src/big/m{index}.py", 160_000)  # 4_000 each once capped
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
    _record_run_tokens(tmp_path, "b-ran", 1_000, scope_tokens=4_000)  # 4_000 x 3.0 = 12_000
    _record_run_tokens(tmp_path, "b-died", 1_000, returncode=143)  # 40_000 x 3.0 = 120_000

    # A ceiling below the failure refuses it, which is the whole point of one.
    assert _ceiling_violations(tmp_path, 16_000) == []

    violations = _ceiling_violations(tmp_path, 120_000)
    assert len(violations) == 1
    assert "b-died failed at an estimate of 120,000" in violations[0]
    assert "nothing above 12,000 has completed" in violations[0]
    assert "lower it to at most 112,000" in violations[0]  # rounded down to a floor-unit


def test_a_failure_below_a_proven_size_is_not_evidence_about_the_ceiling(tmp_path: Path) -> None:
    """basicly-kjc5.43's case: a 5,734-token lane died while a 105,318 one completed.

    Size cannot be the explanation, so the ceiling owes it nothing and must not be
    dragged below a size the engine has demonstrably run. The discrimination is on
    the lane's estimate and never on its exit code — all four real failures share
    returncode 143, and a record keeps a returncode, not a cause.
    """
    _write(tmp_path, "src/small.py", 16_000)  # 4_000 scope tokens
    for index in range(10):
        _write(tmp_path, f"src/big/m{index}.py", 160_000)  # 4_000 each once capped
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
    _record_run_tokens(tmp_path, "b-ran", 1_000)  # 40_000 x 3.0 = 120_000 completed
    _record_run_tokens(tmp_path, "b-died", 1_000, returncode=143)  # 12_000, far below it

    assert failed_lane_estimates(tmp_path) == {"b-died": 12_000}
    assert _ceiling_violations(tmp_path, 120_000) == []


def test_a_recorded_scope_size_never_overrides_the_current_measure(tmp_path: Path) -> None:
    """Both outcome populations are sized by today's estimator, never by a stored one.

    A recorded `scope_tokens` is denominated in whatever measure was current when that
    dispatch ran, and every one on this tree is a whole-file sum from before
    basicly-fcls. Preferring it — which the failure side used to, as the better evidence
    against a drifting tree — mixes two quantities into the one comparison this gate
    exists to make, the shape of both basicly-z2wi and basicly-ipx2. The re-derivation
    drifts, the honest cost until `RunRecord.context_tokens` has a sample to replace it.
    """
    _write(tmp_path, "src/big.py", 160_000)  # 40_000 raw, 4_000 once capped
    _export(
        tmp_path,
        {
            "id": "b-died",
            "issue_type": "task",
            "description": decompose._child_body(_child("b", "src/big.py")),
        },
    )
    _record_run_tokens(tmp_path, "b-died", 1_000, returncode=143, scope_tokens=9_000)

    # 4_000 x 3.0 from the tree, not 9_000 x 3.0 from the record.
    assert failed_lane_estimates(tmp_path) == {"b-died": 12_000}


def test_a_handoff_lane_is_not_evidence_for_the_dispatch_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A human-carried lane must not raise the ceiling a dispatched lane is held to.

    The largest recorded lane on this tree is a 152,377-token handoff. Counting it
    would licence dispatching a headless agent into work no headless agent has ever
    been shown to finish — the reverse of the defect, and worse.
    """
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

    # The same bead sized 12,000 in the control above; only the outcome differs.
    assert completed_lane_estimates(tmp_path) == {}


# --- The build factor is a working set, never a spend (basicly-z2wi) ---------


def test_no_working_set_factor_is_derived_from_spend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Whole-lane spend must never move the build factor, however many lanes land.

    The factor answers "how much working set per token read", and it is compared against
    ``working_set_max``, a context-window ceiling. A calibration used to overwrite it with
    ``whole-lane spend / scope``: a different quantity, three orders of magnitude larger,
    because total spend already contains the turn multiplier
    :func:`run_record.spend_calibration` owns.

    On this repo's history that reached **216.65** against a seed of 3.0, capping the
    largest dispatchable scope at ~295 tokens and refusing every task-typed child. Ten
    real dispatches crossed the sample threshold, so the engine broke itself by being
    used — hence a behavioural guard rather than a check that one function exists.
    """
    _write(tmp_path, "src/a.py", 16_000)  # 4_000 scope tokens x 3.0 = 12_000, in band
    body = decompose._child_body(_child("t", "src/a.py"))
    _install(monkeypatch, _FakeBrShow({"b-1": ("task", body)}))
    for _ in range(20):
        # Each lane spends far more than any working set could hold. Under the old
        # calibration this alone drove the factor to 500.0 and the estimate to
        # 2,000,000 — thirty times the band ceiling, for an unchanged plan.
        _record_run_tokens(tmp_path, "b-1", 2_000_000, scope_tokens=4_000)

    estimates = decompose.govern_working_set(tmp_path, (_child("t", "src/a.py"),))

    assert estimates[0].build_factor == 3.0
    assert estimates[0].total == 12_000


def test_using_the_engine_cannot_make_a_dispatchable_child_undispatchable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A plan the band accepted must not be refused merely because lanes have run.

    The failure this pins is not a wrong number, it is a gate that degrades with use:
    twenty successful dispatches were enough to refuse work that passed before them.
    """
    _write(tmp_path, "src/a.py", 40_000)  # 10_000 scope tokens x 3.0 = 30_000, in band
    spec = _child("t", "src/a.py")
    body = decompose._child_body(spec)
    _install(monkeypatch, _FakeBrShow({"b-1": ("task", body)}))

    before = decompose.govern_working_set(tmp_path, (spec,))

    for _ in range(20):
        _record_run_tokens(tmp_path, "b-1", 2_000_000, scope_tokens=10_000)

    after = decompose.govern_working_set(tmp_path, (spec,))
    assert after == before


# --- Frozen estimates (basicly-kjc5.30) -------------------------------------


def test_govern_freezes_the_accepted_estimate_on_the_feature(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An accepted plan records each child's estimate against the feature."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _write(tmp_path, "src/a.py", 16_000)  # 4_000 tokens x seed 3.0 = 12_000, inside the band
    spec = _child("a", "src/a.py")

    estimates = decompose.govern_working_set(tmp_path, (spec,), feature_id="feat")

    frozen = decompose.frozen_estimates(tmp_path, "feat")
    assert frozen == {decompose.sizing_key(spec): estimates[0]}


def test_govern_reuses_a_frozen_estimate_when_the_tree_has_grown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The same plan governs to the same numbers after the tree moved (D9).

    Scope read-cost is measured against the tree as it stands, so without the freeze
    a plan accepted last week is refused today with neither the plan nor the code
    touched. That is now the *only* drift source: basicly-z2wi removed the build
    factor's calibration, and the seeds are constants.

    The drift has to be real, and the config read has to be the repo's:
    ``govern_working_set`` calls ``load_sizing_config`` itself, so a ``SizingConfig``
    handed to a helper here would never reach it — the first version of this test passed
    with the reuse path deleted for exactly that reason.
    """
    _install(monkeypatch, _FakeBr())
    _write(tmp_path, "src/a.py", 16_000)  # 4_000 scope tokens x 3.0 = 12_000
    spec = _child("a", "src/*.py")

    first = decompose.govern_working_set(tmp_path, (spec,), feature_id="feat")
    assert first[0].build_factor == 3.0
    assert first[0].total == 12_000

    # The scope triples. Recomputing would take the estimate to 36_000 and, on a
    # larger plan, across the band ceiling. It has to grow in *files*: growing one
    # file no longer moves the estimate past SCOPE_FILE_READ_CAP (basicly-fcls),
    # so a single-file drift would leave this test asserting nothing.
    _write(tmp_path, "src/b.py", 16_000)
    _write(tmp_path, "src/c.py", 16_000)
    assert read_cost.scope_read_cost(tmp_path, ("src/*.py",)) == 12_000

    second = decompose.govern_working_set(tmp_path, (spec,), feature_id="feat")
    assert second == first
    assert second[0].total == 12_000


def test_govern_without_a_feature_id_neither_reads_nor_writes_a_freeze(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Estimating a plan that has no bead yet stays a pure snapshot of this moment."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _write(tmp_path, "src/a.py", 16_000)

    decompose.govern_working_set(tmp_path, (_child("a", "src/a.py"),))

    assert fake.comments == {}


def test_govern_does_not_freeze_a_refused_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A refusal is the agent's cue to re-propose, so its numbers are not pinned."""
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
    """A shipped child can look up the forecast that was made for it (basicly-kjc5.50).

    The governor freezes estimates on the *feature*, because when it runs no child
    exists — so a shipped bead has no id to look itself up by. The key is derived
    from the content the estimate is a function of, and the export carries every
    marker, so the lookup needs neither the parent link nor br.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _write(tmp_path, "src/a.py", 16_000)  # 4_000 tokens x seed 3.0, inside the band
    spec = _child("a", "src/a.py")
    estimates = decompose.govern_working_set(tmp_path, (spec,), feature_id="feat")
    # The freeze is on the feature; the export is how it reaches everyone else.
    _export(tmp_path, {"id": "b-feat", "comments": [{"text": fake.comments["feat"][0]}]})

    _install(monkeypatch, lambda *_a, **_k: pytest.fail("the forecast lookup must not need br"))
    assert decompose.forecast_for(tmp_path, "task", ("src/a.py",)) == estimates[0]
    # A different class or scope is a different estimate, never a near-enough one.
    assert decompose.forecast_for(tmp_path, "bug", ("src/a.py",)) is None
    assert decompose.forecast_for(tmp_path, "task", ("src/b.py",)) is None


def test_forecast_for_without_an_export(tmp_path: Path) -> None:
    """No freeze to find means no forecast, and the caller records the actual alone."""
    assert decompose.forecast_for(tmp_path, "task", ("src/a.py",)) is None


def test_govern_refuses_oversized_child_before_recording(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A child above working_set_max refuses the whole plan; nothing is created."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _write_oversized(tmp_path)
    with pytest.raises(ValueError, match="split"):
        decompose.decompose(tmp_path, "feat", (_child("huge", _OVERSIZED_SCOPE),))
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


def test_dry_run_estimate_refuses_exactly_what_the_real_run_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The preview has to predict the run: same plan, same refusal, same guidance.

    ``--dry-run`` used to call :func:`decompose.preview` alone, which knows nothing about
    the sizing band, so an oversized plan previewed clean and was then refused on the real
    run (basicly-u6tw).

    Pinned as an equivalence rather than against a message, so the two paths cannot
    drift: whatever the governor refuses, the estimate must refuse, with the identical
    guidance strings.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _write_oversized(tmp_path)
    children = (_child("huge", _OVERSIZED_SCOPE),)

    verdict = decompose.estimate_plan(tmp_path, children)

    assert verdict.refused
    assert verdict.estimates[0].total > 0
    # Estimating is read-only: it creates no children and freezes no verdict.
    assert fake.created == []
    assert fake.comments == {}

    with pytest.raises(ValueError) as excinfo:
        decompose.decompose(tmp_path, "feat", children)
    for message in verdict.violations:
        assert message in str(excinfo.value)


def test_dry_run_estimate_accepts_what_the_real_run_accepts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """And the in-band case agrees too, so the check is not vacuously strict."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    children = (_child("greenfield", "src/new-a.py"), _child("other", "src/new-b.py"))

    verdict = decompose.estimate_plan(tmp_path, children)

    assert not verdict.refused
    assert verdict.violations == ()
    assert len(verdict.estimates) == 2
    assert fake.created == []


def test_govern_passes_greenfield_plan(tmp_path: Path) -> None:
    """A plan whose scopes match no existing files estimates overhead-only and fits."""
    estimates = decompose.govern_working_set(tmp_path, (_child("a"), _child("b")))
    assert [e.total for e in estimates] == [0, 0]


# --- sizing carried into the dispatch record (basicly-jr0l.34) ---------------


def _scoped_bead(*globs: str, issue_type: str = "task") -> _FakeBrShow:
    body = "## Scope\n\n" + "\n".join(f"- `{glob}`" for glob in globs)
    return _FakeBrShow({"b-1": (issue_type, body)})


def test_dispatch_sizing_prefers_the_forecast_that_was_frozen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A registered forecast is the one of record, and is marked as such.

    A frozen estimate was committed to before the work started, so it is evidence of
    prediction skill; one computed at dispatch is the same formula applied at the last
    honest moment. A calibration that averaged the two would read as skill the
    estimator has not shown, so the source travels with the number.
    """
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
    """A hand-filed bead still yields a pairable forecast rather than a null."""
    _install(monkeypatch, _scoped_bead("src/a.py", issue_type="bug"))
    _write(tmp_path, "src/a.py", 16_000)
    sizing = decompose.dispatch_sizing(tmp_path, "b-1")
    assert sizing is not None
    assert sizing.source == decompose.DISPATCH_FORECAST
    assert sizing.task_class == "bug"
    # 4_000 scope tokens at the seeded `bug` factor of 2.0, plus the overhead.
    assert sizing.estimate.scope_tokens == 4_000
    assert sizing.estimate.build_factor == 2.0
    assert sizing.estimate.total == sizing.estimate.overhead_tokens + 8_000


def test_dispatch_sizing_declines_a_bead_with_no_readable_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No declared scope means no forecast — an invented one would poison calibration.

    The forecast is a function of the scope read-cost, so a bead that declares no
    scope has nothing to compute from. Recording overhead alone would look like a
    forecast and be a guaranteed under-count of every such package.
    """
    _install(monkeypatch, _FakeBrShow({"b-1": ("task", "## Context\n\nno scope here")}))
    assert decompose.dispatch_sizing(tmp_path, "b-1") is None


def test_resolve_dispatch_sizing_separates_an_undeclared_scope_from_an_unreadable_bead(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The two absences one None used to answer for (basicly-jr0l.60).

    Both leave a lane unsized, but only one is a fact about the package: a bead that
    declares no scope will still declare none on the next read, while a failed read
    says nothing about its size. The band gate acts on the first and waives the
    second, so a caller has to be able to tell them apart.
    """
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
    """A third absence, and the one that cost a wave (basicly-jr0l.69).

    Globs that match nothing read to zero, so the only forecast left is instruction
    overhead — the same invented number an undeclared scope is refused for, invented in
    the dangerous direction: a lane creating a module and its tests from nothing is the
    expensive case. Measured, two such lanes forecast at 657033 spent 13367072 and
    7730640 - 20.3x and 11.8x outside the accuracy band.

    Distinct from `undeclared`, because the bead did its part: the scope is well formed
    and the files simply do not exist yet.
    """
    _install(monkeypatch, _scoped_bead("src/created-later.py"))
    greenfield = decompose.resolve_dispatch_sizing(tmp_path, "b-1")

    assert greenfield.sizing is None, "an overhead-only forecast is not a forecast"
    assert greenfield.absence == decompose.SCOPE_GREENFIELD

    # The same bead sizes normally the moment its scope exists, so this is about the
    # files rather than about the declaration.
    _write(tmp_path, "src/created-later.py", 16_000)
    assert decompose.resolve_dispatch_sizing(tmp_path, "b-1").sizing is not None


def test_greenfield_is_checked_before_a_frozen_estimate_is_honoured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Order matters, and getting it wrong is what let the bad number reach the ledger.

    `decompose` freezes an estimate for each child, and at that moment a greenfield
    child's files do not exist - so the frozen figure is overhead-only. Honoured, it
    comes back with `forecast_source` reading `frozen`, indistinguishable from a real
    prediction while resting on nothing. Rejecting the scope first is what stops that.
    """
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
    """A record that answered but carries no class or description is a failed read.

    The boundary between the two absences: "undeclared" is a bead whose description
    was read and holds no ``## Scope``. A payload with no description at all was not
    read, whatever the exit code said, and calling that undeclared would file a
    tracker defect against the package.
    """
    _install(monkeypatch, lambda _r, args, **_k: _Proc(json.dumps([{"id": args[1]}])))
    lookup = decompose.resolve_dispatch_sizing(tmp_path, "b-1")
    assert lookup.sizing is None
    assert lookup.absence == decompose.SCOPE_UNREADABLE


# --- predicted spend beside the working set (basicly-jr0l.21) ----------------


def _manual_runner(repo: Path) -> None:
    """Pin the repo to the handoff runner so the forecast model is resolvable and stable.

    Without this the runner is ``auto``, which detects whichever agent binary the
    machine happens to have on PATH — a test that reads the forecast model must not
    depend on that.
    """
    (repo / "basicly.toml").write_text('[runner]\ndefault = "manual"\n', encoding="utf-8")


def _estimate(total: int) -> decompose.CostEstimate:
    """A working-set estimate whose total is exactly *total* tokens."""
    return decompose.CostEstimate(scope_tokens=total, overhead_tokens=0, build_factor=1.0)


def _calibration(
    *, tokens: float | None = 100.0, usd: float | None = 2.0, seconds: float | None = 60.0
) -> run_record.SpendCalibration:
    """A seeded calibration: 100x the working set, at 2.00 USD and 60 s per million."""
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
    """One declared prior ratio, refusing a None so a test can compute against it."""
    value = getattr(run_record.DECLARED_SPEND_PRIOR, ratio)
    assert value is not None
    return value


def test_forecast_spend_multiplies_the_working_set_and_prices_the_tokens() -> None:
    """Tokens come from the working set; money and time come from the tokens."""
    spend = decompose.forecast_spend(_estimate(10_000), _calibration())
    assert spend.tokens == 1_000_000  # 10_000 working set x 100
    assert spend.cost == pytest.approx(2.0)  # 1M tokens at 2.00 USD/Mtok
    assert spend.wall_clock_s == pytest.approx(60.0)  # 1M tokens at 60 s/Mtok
    assert spend.indeterminate is False


def test_forecast_spend_refuses_a_zero_working_set() -> None:
    """A package with no readable scope material forecasts nothing, not a free package."""
    spend = decompose.forecast_spend(_estimate(0), _calibration())
    assert (spend.tokens, spend.cost, spend.wall_clock_s) == (None, None, None)
    assert spend.indeterminate is True


def test_forecast_spend_predicts_tokens_while_leaving_money_unknown() -> None:
    """An undeclared, unmeasured ratio yields None for its metric and nothing else."""
    spend = decompose.forecast_spend(_estimate(10_000), _calibration(usd=None))
    assert spend.tokens == 1_000_000
    assert spend.cost is None
    assert spend.wall_clock_s == pytest.approx(60.0)
    assert spend.indeterminate is False


def test_forecast_model_is_none_when_the_runner_pins_no_model(tmp_path: Path) -> None:
    """A handoff runner has no model flag, so there is no key — and none is invented."""
    _manual_runner(tmp_path)
    assert decompose.forecast_model(tmp_path) is None


def test_estimate_plan_carries_predicted_spend_beside_the_working_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A sized package forecasts tokens, cost and wall clock, not the working set alone."""
    _install(monkeypatch, _FakeBr())
    _manual_runner(tmp_path)
    _write(tmp_path, "src/a.py", 16_000)  # 4_000 scope tokens x seed 3.0 = 12_000

    verdict = decompose.estimate_plan(tmp_path, (_child("a", "src/a.py"),))

    assert verdict.estimates[0].total == 12_000
    spend = verdict.spend[0]
    tokens = round(12_000 * _declared("tokens_per_working_set_token"))
    assert spend.tokens == tokens
    assert spend.cost == pytest.approx(tokens / 1_000_000 * _declared("usd_per_million_tokens"))
    assert spend.wall_clock_s == pytest.approx(
        tokens / 1_000_000 * _declared("seconds_per_million_tokens")
    )
    # Seeded, and saying so: the model could not be resolved, so nothing measured it.
    assert spend.calibration.tokens_per_working_set_token.source == run_record.PRIOR_RATIO
    assert spend.calibration.model is None


def test_govern_freezes_the_spend_forecast_and_the_prior_behind_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The recorded forecast carries its own provenance, so a seed cannot read as measured.

    The sizing marker is the only carrier that survives a clone, so a spend number
    recorded without the prior it came from could never be audited once the prior
    was replaced.
    """
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


# One paired sample, as a `task` on the named model: 5M tokens against a 100k
# forecast (50x), 10 USD and 100 seconds (2.00 USD and 20 s per million tokens).
_PAIR_TOKENS = 5_000_000
_PAIR_FORECAST = 100_000
_PAIR_RATIO = _PAIR_TOKENS / _PAIR_FORECAST


def _record_paired_run(repo: Path, bead_id: str, model: str, *, cost: float | None = None) -> None:
    """Record a dispatch carrying both halves of the pair, keyed to *model*."""
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
            # Only a write dispatch calibrates (basicly-tcmy.5).
            phase=run_record.LANE_PHASE,
        ),
    )


def test_measured_history_replaces_the_prior_once_the_minimum_is_paired(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Past calibration_min_samples the forecast is measured for that model, not seeded.

    The prior would predict ~334x the working set; three paired records on this model
    say 50x, and it is the measured number the forecast must carry.
    """
    _install(monkeypatch, _FakeBr())
    (tmp_path / "basicly.toml").write_text(
        '[runner]\ndefault = "manual"\n\n[policy.sizing]\ncalibration_min_samples = 3\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(decompose, "forecast_model", lambda _repo: "claude-opus-5")
    _write(tmp_path, "src/a.py", 16_000)  # 12_000 working set at the task seed
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
    # 10 USD and 100 seconds per 5M tokens -> 2.00 USD and 20 s per million.
    assert calibration.usd_per_million_tokens.value == pytest.approx(2.0)
    assert spend.cost == pytest.approx(tokens / 1_000_000 * 2.0)
    assert spend.wall_clock_s == pytest.approx(tokens / 1_000_000 * 20.0)


def test_a_foreign_models_history_leaves_the_forecast_seeded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Calibration is per model: another model's landings cannot move this forecast."""
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


# --- The unsizeable-lane bound (basicly-vz78) ----------------------------------


def test_the_unsized_bound_falls_back_to_the_declared_seed(tmp_path: Path) -> None:
    """With neither store holding a dispatch there is nothing to measure, and it says so."""
    tokens, source = decompose.unsized_lane_tokens(tmp_path, _sizing())

    assert (tokens, source) == (decompose.UNSIZED_LANE_TOKENS_SEED, "seed")


def test_the_unsized_bound_measures_the_committed_markers_alone(tmp_path: Path) -> None:
    """AC: a checkout with no local record of its own measures the bound (basicly-izpi)."""
    body = json.dumps({"phase": run_record.LANE_PHASE, "tokens": 9_000_000, "estimated": False})
    text = f"{run_record.MARKER} id=b-1 phase=lane\n{body}"
    _export(tmp_path, {"id": "b-1", "comments": [{"text": text}]})

    assert run_record.load_run_records(tmp_path) is None
    assert decompose.unsized_lane_tokens(tmp_path, _sizing()) == (9_000_000, "measured")


def test_the_unsized_bound_is_a_sample_some_lane_really_incurred(tmp_path: Path) -> None:
    """A quantile *of the samples*, so the figure is a real observation, never a mean.

    What no change of statistic may alter: the bound is a spend some lane actually
    incurred, so it needs no rounding rule and points back at a run record.
    """
    _record_run_tokens(tmp_path, "b-1", 1_000)
    _record_run_tokens(tmp_path, "b-2", 9_000)
    _record_run_tokens(tmp_path, "b-3", 2_000)

    tokens, source = decompose.unsized_lane_tokens(tmp_path, _sizing())

    assert (tokens, source) == (9_000, "measured")
    assert tokens in {1_000, 9_000, 2_000}
    assert tokens != statistics.mean((1_000, 9_000, 2_000))


def test_one_pathological_lane_does_not_own_the_unsized_bound(tmp_path: Path) -> None:
    """A quantile, not a max: the most expensive run ever seen must not price them all.

    This once read "a central estimate, not a max, because the population is bimodal" -
    leaves supposedly 856182-4079243 tokens and packages 7674671-20594047. **More data
    refuted that split** (basicly-jr0l.58): four leaf lanes measured 9418977, 10834801,
    11478450 and 11867602, inside the supposed package band, so the population is one wide
    spread. What survives is the narrower property pinned here: a single outlier sits
    above the quantile and does not become every lane's bound.
    """
    for i in range(9):
        _record_run_tokens(tmp_path, f"b-{i}", 1_000)
    _record_run_tokens(tmp_path, "b-outlier", 20_000_000)

    tokens, _source = decompose.unsized_lane_tokens(tmp_path, _sizing())

    assert tokens == 1_000, "a single outlier must not become the bound for every lane"


def test_the_unsized_bound_still_refuses_the_overrun_that_motivated_it(tmp_path: Path) -> None:
    """The one case that must not regress, in its measured numbers (basicly-vz78).

    One lane spent 4079243 tokens against a 3000000 remainder and the gate admitted it,
    because an unsizeable lane contributed nothing to the pass total. Whatever statistic
    the bound uses, that pass has to refuse.
    """
    _record_run_tokens(tmp_path, "b-1", 4_079_243)

    tokens, _source = decompose.unsized_lane_tokens(tmp_path, _sizing())

    assert tokens > 3_000_000


def test_the_unsized_bound_is_exceeded_by_at_most_the_quantiles_tail(tmp_path: Path) -> None:
    """The acceptance criterion, stated as the overrun rate it targets.

    Replayed against this repo's own 17 measured lane actuals - the population that
    produced the failure. At the default 0.9 no more than one lane in ten may exceed
    its bound; the median it replaced was exceeded by 8 of 17 (47%), which is how a
    pass forecast at 16316972 tokens came to spend 43599830 (basicly-jr0l.58).
    """
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
    # And the statistic is not merely the max, which would price every lane off the
    # single worst run and refuse passes that genuinely fit.
    assert bound < max(actuals)
    # The seed answers to it too: at 4000000, 11 of these 17 exceeded it (basicly-izpi).
    assert sum(t > decompose.UNSIZED_LANE_TOKENS_SEED for t in actuals) / len(actuals) <= 0.1


def test_the_unsized_bound_follows_the_configured_quantile(tmp_path: Path) -> None:
    """The quantile is config, so a consumer can trade throughput against overrun."""
    for index, tokens in enumerate((1_000, 2_000, 3_000, 4_000, 100_000)):
        _record_run_tokens(tmp_path, f"b-{index}", tokens)

    low, _ = decompose.unsized_lane_tokens(tmp_path, _sizing(unsized_lane_quantile=0.2))
    high, _ = decompose.unsized_lane_tokens(tmp_path, _sizing(unsized_lane_quantile=1.0))

    assert low == 1_000
    assert high == 100_000


def test_the_unsized_bound_ignores_an_estimated_sample(tmp_path: Path) -> None:
    """A chars/4 figure is not an observation, so it must not set the ceiling."""
    _record_run_tokens(tmp_path, "b-1", 5_000)
    _record_run_tokens(tmp_path, "b-2", 90_000, estimated=True)

    assert decompose.unsized_lane_tokens(tmp_path, _sizing()) == (5_000, "measured")


def test_the_unsized_bound_needs_no_declared_scope(tmp_path: Path) -> None:
    """The point of the bound: an actual is an observation whatever the scope says.

    A raw ceiling needs no readable ``## Scope`` the way calibration does, which is why
    it can bound the very beads ``dispatch_sizing`` refuses to size (basicly-vz78).
    """
    _record_run_tokens(tmp_path, "b-1", 7_000)

    # No tracker at all under tmp_path, so no bead here has a readable scope.
    assert decompose.unsized_lane_tokens(tmp_path, _sizing()) == (7_000, "measured")


# --- One phase set for write dispatches (basicly-tcmy.5) -----------------------


def test_the_unsized_bound_counts_a_write_dispatch_from_either_path(tmp_path: Path) -> None:
    """AC: the interactive build and the supervised lane are both a lane's cost.

    The bound required ``phase == "lane"``, so the interactive path — 128 of this repo's
    214 records — was invisible to the ceiling. Asserted at the quantile so the build
    sample has to be *in the population*, not merely not crash it: at 1.0 the bound is
    the largest sample of whichever set was harvested.
    """
    _record_run_tokens(tmp_path, "b-lane", 5_000, phase=run_record.LANE_PHASE)
    _record_run_tokens(tmp_path, "b-build", 40_000, phase=run_record.BUILD_PHASE)

    bound, source = decompose.unsized_lane_tokens(tmp_path, _sizing(unsized_lane_quantile=1.0))

    assert (bound, source) == (40_000, "measured")


def test_the_unsized_bound_ignores_a_helper_unphased_or_dying_dispatch(tmp_path: Path) -> None:
    """A judge, the decider, a record with no phase and a dying attempt are not a lane.

    The other half of one named phase set: widening it to both write phases must not
    widen it to *every* dispatch. A rubric judge is cheap and read-only, so admitting
    one would drag the ceiling down; a record whose phase was never written cannot be
    shown to be a lane at all and fails closed; and what a dying attempt spent is not
    what the work costs — `spend_accuracy`'s own exclusion (basicly-5xcj).
    """
    _record_run_tokens(tmp_path, "b-lane", 5_000)
    _record_run_tokens(tmp_path, "b-judge", 90_000, phase=run_record.VALIDATE_PHASE)
    _record_run_tokens(tmp_path, "b-decide", 90_000, phase=run_record.DECIDE_PHASE)
    _record_run_tokens(tmp_path, "b-legacy", 90_000, phase=None)
    _record_run_tokens(tmp_path, "b-died", 90_000, returncode=1)

    bound = decompose.unsized_lane_tokens(tmp_path, _sizing(unsized_lane_quantile=1.0))

    assert bound == (5_000, "measured")


# --- A declared build factor is recorded as declared (basicly-tcmy.5) ---------


def test_a_seeded_build_factor_is_recorded_as_seeded(tmp_path: Path) -> None:
    """AC: the estimate states that its factor came from the seeds, not a measurement.

    Nothing measures a working-set factor (basicly-z2wi removed the calibration that
    appeared to), so every dispatch this repo records is sized by a declared constant.
    The record has to say so: a forecast carrying a bare multiplier is how the previous
    two derivations of ``working_set_max`` came to validate an estimator against its
    own output.
    """
    _write(tmp_path, "src/a.py", 4_000)

    estimate = decompose.estimate_cost(tmp_path, _child("t", "src/a.py"), _sizing(), overhead=0)

    assert estimate.build_factor_source == decompose.BUILD_FACTOR_SEED


def test_a_configured_build_factor_is_recorded_as_configured(tmp_path: Path) -> None:
    """A repo that declares its own factor is not reported as running on the seeds.

    Provenance is read from the config that produced the number rather than inferred by
    comparing it against the seed — a repo declaring 3.0 for ``task`` would otherwise
    read back as never having declared anything.
    """
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
    # An unlisted class falls back to the ``task`` entry, so it inherits that entry's
    # provenance rather than reporting the seed that did not size it.
    spike = ChildSpec(title="s", acceptance=("a",), scope=("src/a.py",), type="spike")
    assert (
        decompose.estimate_cost(tmp_path, spike, sizing, overhead=0).build_factor_source
        == decompose.BUILD_FACTOR_CONFIGURED
    )


def test_the_recorded_dispatch_inputs_carry_the_factors_provenance(tmp_path: Path) -> None:
    """The source reaches the run record, where a later calibration reads the pair."""
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
    """AC: the record carries the forecast the *spend* gate trusts, not only the working set.

    The two numbers are 334x apart and only one of them is denominated in what a run
    record's ``tokens`` measures, so recording the working set alone left every completed
    lane comparable against nothing (basicly-tcmy.34).
    """
    sizing = decompose.DispatchSizing(
        task_class="task",
        estimate=decompose.CostEstimate(scope_tokens=1_000, overhead_tokens=0, build_factor=3.0),
        source=decompose.DISPATCH_FORECAST,
    )

    inputs = sizing.record_inputs(tmp_path)

    assert inputs["forecast_tokens"] == 3_000
    # No history on a bare tree, so the declared prior is the multiplier in force.
    prior = run_record.DECLARED_SPEND_PRIOR.tokens_per_working_set_token
    assert prior is not None
    assert inputs["forecast_spend_tokens"] == round(3_000 * prior)


def test_a_frozen_estimate_round_trips_its_factor_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The freeze is the forecast of record (D9), so the provenance must survive it."""
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
    """An older marker keeps the provenance it really had rather than being discarded.

    The seeds were the only source that existed when it was written, and requiring the
    key would drop the frozen verdict and recompute — the drift the freeze prevents.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    payload = '{"scope_tokens": 1000, "overhead_tokens": 0, "build_factor": 3.0, "total": 3000}'
    fake.comments["b-1"] = [f"[harness-sizing] key=key-1\n{payload}"]

    frozen = decompose.frozen_estimates(tmp_path, "b-1")

    assert frozen["key-1"].build_factor_source == decompose.BUILD_FACTOR_SEED


# --- Reporting whether the sizing is measured yet (basicly-tcmy.5) ------------


def test_the_calibration_status_reports_a_class_still_on_seeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC: the per-class sample counts, and the verdict that nothing is measured yet."""
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
    """Past the minimum the report says which class stopped being a seed, not just that.

    Per class because that is how the ratios are keyed: one class crossing the minimum
    says nothing about the others, and a single "calibrated" flag would.
    """
    _install(monkeypatch, _FakeBr())
    monkeypatch.setattr(decompose, "forecast_model", lambda _repo: "claude-opus-5")
    for index in range(3):
        _record_paired_run(tmp_path, f"b-{index}", "claude-opus-5", cost=10.0)

    status = decompose.calibration_status(tmp_path, _sizing(calibration_min_samples=3))

    assert status.measured_classes == ("task",)
    assert not status.on_seeds


# --- The spend forecast is within an order of magnitude (basicly-tcmy.34) ----


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
    """Seed one write dispatch with an actual and whichever forecast half the test needs.

    *timestamp* overrides the stamp `build_record` takes from the clock. Records of one
    bead are deduplicated on it and folded in its order, so a test about several attempts
    at one bead has to make the ordering an input rather than race two `now()` calls.
    """
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


def test_the_spend_forecast_lands_within_an_order_of_magnitude_of_recorded_spend() -> None:
    """The live gate: AC of basicly-tcmy.34, measured on this repo's own ledger.

    `basicly-gczc` spent 16,963,245 tokens against a recorded forecast of 66,780 — 254x,
    and the paired median was 307x, reading as a forecast wrong by two orders of
    magnitude. It was wrong by *unit*: the recorded number was a working set and the
    actual is whole-lane spend. Held in one unit the same records come in at 0.19x-2.37x.

    Not an equality: a lane spending less than forecast is not a defect and must not turn
    main red. It fails only past `SPEND_RATIO_BAND`, naming the lane and the factor.
    """
    accuracy = decompose.spend_accuracy(REPO_ROOT, load_sizing_config(REPO_ROOT))

    assert accuracy.violations == ()


def test_the_spend_gate_measures_a_populated_ledger() -> None:
    """The positive control: the gate above must not be measuring an empty set.

    A check whose population is empty passes for the same reason a correct one does, and
    this repo has committed that mistake twice (basicly-ipx2, basicly-fcls). The named
    bead is the one basicly-tcmy.34 was filed on, and it reaches the gate from the
    committed tracker markers alone, so a fresh clone measures it too.
    """
    accuracy = decompose.spend_accuracy(REPO_ROOT, load_sizing_config(REPO_ROOT))

    assert len(accuracy.pairs) >= 20
    assert "basicly-gczc" in {pair.bead for pair in accuracy.pairs}
    # The median is the whole point of the fix: a same-unit comparison sits near 1x,
    # while the working-set-against-spend comparison this replaced sat at 307x.
    median = accuracy.median_ratio
    assert median is not None
    assert 0.1 <= median <= 10


def test_a_lane_that_overran_its_forecast_by_two_orders_is_reported(tmp_path: Path) -> None:
    """The known-bad control: the exact shape of basicly-tcmy.34, on a seeded ledger.

    Without it the live gate is indistinguishable from one that cannot fail. The figures
    are the real ones: the 66,780-token forecast recorded for `basicly-gczc` against the
    16,963,245 it spent, in the same unit so the 254x is a forecast error rather than a
    turn multiplier.
    """
    _record_spend_pair(tmp_path, "b-1", tokens=16_963_245, forecast_spend_tokens=66_780)

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    assert len(accuracy.pairs) == 1
    assert not accuracy.pairs[0].in_band
    assert len(accuracy.violations) == 1
    assert "b-1 spent 16,963,245 tokens" in accuracy.violations[0]
    assert "forecast of 66,780" in accuracy.violations[0]
    assert "254.017x" in accuracy.violations[0]


def test_an_over_forecast_is_reported_too(tmp_path: Path) -> None:
    """Both directions: a forecast 100x too big refuses passes that would have fitted.

    The band exists because a spend forecast feeds a *grant*, and the two ways of being
    wrong cost different things — money one way, throughput the other. A ceiling would
    only have caught one of them.
    """
    _record_spend_pair(tmp_path, "b-1", tokens=100_000, forecast_spend_tokens=10_000_000)

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    assert len(accuracy.violations) == 1
    assert "0.010x" in accuracy.violations[0]


def test_a_forecast_within_the_band_is_no_violation(tmp_path: Path) -> None:
    """The band admits an honest miss: 2x is a forecast, not a defect."""
    _record_spend_pair(tmp_path, "b-1", tokens=20_000_000, forecast_spend_tokens=10_000_000)

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    assert accuracy.pairs[0].basis == decompose.RECORDED_SPEND_FORECAST
    assert accuracy.violations == ()


def test_an_older_record_is_compared_through_todays_calibration(tmp_path: Path) -> None:
    """A record predating the spend field is still held to a spend forecast.

    Deriving it is what makes the gate bind on the 26 dispatches already recorded rather
    than only on ones written from now on. The working set is converted by the one
    multiplier `forecast_spend` uses, so the derived number is what the engine would have
    recorded had the field existed.
    """
    _record_spend_pair(tmp_path, "b-1", tokens=3_000_000, forecast_tokens=10_000)

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    prior = run_record.DECLARED_SPEND_PRIOR.tokens_per_working_set_token
    assert prior is not None
    assert accuracy.pairs[0].basis == decompose.DERIVED_SPEND_FORECAST
    assert accuracy.pairs[0].forecast_tokens == round(10_000 * prior)
    assert accuracy.violations == ()


def test_a_recorded_forecast_the_band_would_refuse_is_named_not_dropped(tmp_path: Path) -> None:
    """A working set above the ceiling is evidence about a removed estimator, not a lane.

    `basicly-tcmy.31` recorded a forecast of 6,762,766 tokens against a scope read-cost of
    35,106 — a factor of ~193 from the spend-derived calibration basicly-z2wi deleted. No
    spend forecast can be derived from it, and silently skipping it is how a filter came
    to delete a whole population once already (basicly-ipx2), so it is reported by name.
    """
    _record_spend_pair(tmp_path, "b-1", tokens=8_574_169, forecast_tokens=6_762_766)

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    assert accuracy.pairs == ()
    assert accuracy.incomparable == ("b-1",)


def test_the_spend_gate_samples_only_measured_write_dispatches(tmp_path: Path) -> None:
    """A judge, a handoff and a chars/4 estimate are not evidence of what a lane spends.

    The same two rules `unsized_lane_tokens` samples on, so the bound and this check
    cannot come to disagree about what a lane dispatch is (basicly-tcmy.5).
    """
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
    """What a dying attempt spent is not what the work costs (basicly-5xcj).

    The lane exited 1 after 33,880 tokens of startup against a 4,805,997 forecast — 0.007x
    — and the gate read that as a forecast wrong by two orders of magnitude. Its sibling
    record for the same bead, the attempt that actually ran, came in at 1.60x. Counted
    rather than dropped: the failures are exactly the population a filter on an optional
    field loses silently (basicly-ipx2).
    """
    _record_spend_pair(
        tmp_path, "b-1", tokens=33_880, forecast_spend_tokens=4_805_997, returncode=1
    )

    accuracy = decompose.spend_accuracy(tmp_path, _sizing())

    assert accuracy.pairs == ()
    assert accuracy.violations == ()
    assert accuracy.aborted == 1


def test_an_assumed_fallback_forecast_is_named_not_compared(tmp_path: Path) -> None:
    """A stand-in for an unsizeable bead is a placeholder, not a prediction.

    `basicly-sco6` declares no scope the estimator can read, so the forecast fell back to
    the measured whole-lane quantile — 16,576,875 tokens for a docs-only change that spent
    1,218,172 (0.073x). Holding a placeholder to an actual is the basicly-z2wi shape one
    level on: a number compared against a quantity it does not denominate. Named, so the
    remedy (declare a scope) stays visible instead of the record vanishing.
    """
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
    """The known-bad control for basicly-u2hl.15, on `basicly-u2hl.14`'s real numbers.

    `forecast_spend_tokens` is derived from the bead's scope, so all three of that lane's
    dispatches recorded the same 26,320,290 — the cost of getting *the bead* done. Scored
    per attempt, the two re-dispatches are compared against a forecast covering work the
    first attempt already did, so the third read as 0.057x and failed the live gate while
    the lane itself came in at 1.31x. Every attempt here is individually under the band.
    """
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
    """Summing the attempts must not become a way to spend past the band quietly.

    The half of the fix that can silently stop working: a bead is what the forecast
    denominates, so re-dispatching it is exactly how a lane reaches 170x, and the count
    is named in the violation because "spent 17,000,000" without it invites the reader to
    hold one dispatch responsible for four.
    """
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
    """A re-dispatch re-reads the bead, so the two forecasts differ and one must win.

    Four beads in this repo's ledger carry forecasts 6-10% apart across their attempts.
    The newest is taken: it is the number a re-grant would be sized from. Asserted so the
    tie-break is a decision on the record rather than whichever order the ledger enumerates.
    """
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
