"""Tests for the decomposer & dependency-graph builder (onb.4)."""

from __future__ import annotations

import json
import statistics
from collections.abc import Callable
from pathlib import Path

import pytest

from basicly import decompose, policy, run_record
from basicly.config import SizingConfig, load_sizing_config
from basicly.decompose import ChildSpec


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


def _one_child(**extra: object) -> dict[str, object]:
    return {"children": [{"title": "t", "acceptance": ["ac"], "scope": ["src/x.py"], **extra}]}


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
    child = {"title": "t", "acceptance": ["ac"], "scope": ["src/x.py", "pyproject.toml"]}
    child["shared"] = ["pyproject.toml"]
    from_json = decompose.load_plan_text(json.dumps({"children": [child]}), "json")
    from_toml = decompose.load_plan_text(
        '[[children]]\ntitle = "t"\nacceptance = ["ac"]\n'
        'scope = ["src/x.py", "pyproject.toml"]\nshared = ["pyproject.toml"]\n',
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
    bug = ChildSpec(title="b", acceptance=("given x then y",), scope=("src/a.py",), type="bug")
    body = decompose._child_body(bug)
    headings = [line for line in body.splitlines() if line.startswith("## ")]
    assert headings == ["## Steps to Reproduce", "## Acceptance Criteria", "## Scope"]
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


def _record_run_tokens(
    repo: Path,
    bead_id: str,
    tokens: int,
    *,
    estimated: bool = False,
    scope_tokens: int | None = None,
) -> None:
    entry = run_record.build_record(
        agent="claude",
        handoff=False,
        returncode=0,
        duration_s=1.0,
        command=("claude",),
        tokens=tokens,
        estimated=estimated,
        scope_tokens=scope_tokens,
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


def test_calibration_uses_the_scope_cost_recorded_at_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A sample's denominator is the scope cost persisted with it, not today's tree.

    Regression for basicly-kjc5.30: measuring scope cost at read time makes a
    sample mean something different once the files it named have grown, so the
    build factor drifts with the tree and the governor's verdict drifts with it.
    """
    _write(tmp_path, "src/a.py", 40_000)  # 10_000 tokens *now* — grown since dispatch
    body = decompose._child_body(_child("t", "src/a.py"))
    _install(monkeypatch, _FakeBrShow({"b-1": ("task", body)}))
    # Each dispatch cost 4_000 tokens against a 1_000-token scope: factor 4.0.
    for _ in range(3):
        _record_run_tokens(tmp_path, "b-1", 4_000, scope_tokens=1_000)

    factors = decompose.calibrated_build_factors(tmp_path, _sizing(calibration_min_samples=3))
    # Recomputing against the grown tree would give 4_000 / 10_000 = 0.4.
    assert factors["task"] == 4.0


def test_calibration_falls_back_to_the_tree_for_a_record_without_scope_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A record written before scope cost was persisted still calibrates."""
    _write(tmp_path, "src/a.py", 4_000)  # 1_000 tokens
    body = decompose._child_body(_child("t", "src/a.py"))
    _install(monkeypatch, _FakeBrShow({"b-1": ("task", body)}))
    for _ in range(3):
        _record_run_tokens(tmp_path, "b-1", 4_000)  # no scope_tokens

    factors = decompose.calibrated_build_factors(tmp_path, _sizing(calibration_min_samples=3))
    assert factors["task"] == 4.0


def _export(repo: Path, *records: dict) -> None:
    """Write the committed tracker export — what a fresh clone has and nothing more."""
    beads = repo / ".beads"
    beads.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record) for record in records]
    (beads / "issues.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _exported_dispatch(tokens: int, scope_tokens: int, stamp: str) -> dict:
    payload = {
        "tokens": tokens,
        "estimated": False,
        "scope_tokens": scope_tokens,
        "timestamp": stamp,
    }
    return {"text": f"{run_record.MARKER} id=b-1#run-{stamp} phase=build\n{json.dumps(payload)}"}


def test_calibration_reads_samples_from_the_tracker_in_a_fresh_clone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A clone with no local usage files still calibrates (basicly-kjc5.50).

    ``.basicly/usage/`` is self-ignored, so reading it alone meant a fresh clone —
    or a new teammate — forecast every class from the seed factors while the machine
    that did the work knew better. Every dispatch also writes a ``[harness-run]``
    marker and the export carries comments, so the shared ledger answers. It has to
    answer without br as well: the export already carries the class and the scope.
    """
    _install(monkeypatch, lambda *_a, **_k: pytest.fail("calibration must not need br"))
    body = decompose._child_body(_child("t", "src/a.py"))
    _export(
        tmp_path,
        {
            "id": "b-1",
            "issue_type": "task",
            "description": body,
            "comments": [
                _exported_dispatch(4_000, 1_000, "2026-07-26T10:00:00+00:00"),
                _exported_dispatch(5_000, 1_000, "2026-07-26T11:00:00+00:00"),
                _exported_dispatch(6_000, 1_000, "2026-07-26T12:00:00+00:00"),
            ],
        },
    )
    assert run_record.load_run_records(tmp_path) is None  # no local telemetry at all

    factors = decompose.calibrated_build_factors(tmp_path, _sizing(calibration_min_samples=3))
    assert factors["task"] == 5.0  # factors 4.0, 5.0, 6.0 -> median 5.0
    assert factors["bug"] == 2.0  # other classes keep their seeds


def test_calibration_counts_a_dispatch_in_both_places_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A locally-recorded dispatch and its marker are one sample, not two.

    The union has to deduplicate or every local dispatch weighs double in the
    median once it reaches the tracker — a silently different answer on the machine
    that ran the work.
    """
    _write(tmp_path, "src/a.py", 4_000)  # 1_000 scope tokens
    body = decompose._child_body(_child("t", "src/a.py"))
    _install(monkeypatch, _FakeBrShow({"b-1": ("task", body)}))
    _record_run_tokens(tmp_path, "b-1", 4_000, scope_tokens=1_000)
    local = run_record.load_run_records(tmp_path)
    assert local is not None
    stamp = local["b-1"][0]["timestamp"]
    _export(
        tmp_path,
        {
            "id": "b-1",
            "issue_type": "task",
            "description": body,
            # The same dispatch as above, plus two the tracker alone carries.
            "comments": [
                _exported_dispatch(4_000, 1_000, stamp),
                _exported_dispatch(9_000, 1_000, "2026-07-26T11:00:00+00:00"),
                _exported_dispatch(9_000, 1_000, "2026-07-26T12:00:00+00:00"),
            ],
        },
    )

    # Three distinct samples (4.0, 9.0, 9.0) -> median 9.0. Double-counting the
    # shared one would make four (4.0, 4.0, 9.0, 9.0) and a median of 6.5.
    factors = decompose.calibrated_build_factors(tmp_path, _sizing(calibration_min_samples=3))
    assert factors["task"] == 9.0


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


def test_govern_reuses_a_frozen_estimate_when_calibration_has_moved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The same plan governs to the same numbers after the window moved (D9).

    The drift has to be real for this to prove anything, and the config it reads
    has to be the repo's: ``govern_working_set`` calls ``load_sizing_config``
    itself, so a ``SizingConfig`` handed to a helper here would never reach it —
    the first version of this test passed with the reuse path deleted for exactly
    that reason.
    """
    # calibration_min_samples = 3 so three landings really do move the factor;
    # the default is 10, under which nothing would drift and nothing be proven.
    (tmp_path / "basicly.toml").write_text(
        "[policy.sizing]\ncalibration_min_samples = 3\n", encoding="utf-8"
    )
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _write(tmp_path, "src/a.py", 16_000)  # 4_000 scope tokens
    spec = _child("a", "src/a.py")

    first = decompose.govern_working_set(tmp_path, (spec,), feature_id="feat")
    assert first[0].build_factor == 3.0  # the seed: no samples yet

    # Unrelated landings fill the calibration window for the task class.
    _write(tmp_path, "src/other.py", 4_000)
    body = decompose._child_body(_child("other", "src/other.py"))
    show = _FakeBrShow({"b-1": ("task", body)})
    original = fake.__call__

    def routed(repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        return show(repo_root, args) if args[:1] == ["show"] else original(repo_root, args)

    _install(monkeypatch, routed)
    for _ in range(3):
        _record_run_tokens(tmp_path, "b-1", 9_000, scope_tokens=1_000)  # factor 9.0

    # The drift is now live in the very function under test, not merely available
    # to a helper: recomputing would triple the estimate from 12_000 to 36_000.
    drifted = decompose.calibrated_build_factors(tmp_path, load_sizing_config(tmp_path))
    assert drifted["task"] == 9.0

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
    _write(tmp_path, "src/big.py", 400_000)

    with pytest.raises(ValueError, match="split"):
        decompose.govern_working_set(tmp_path, (_child("huge", "src/big.py"),), feature_id="feat")

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
    _export(tmp_path, {"id": "feat", "comments": [{"text": fake.comments["feat"][0]}]})

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


def test_dry_run_estimate_refuses_exactly_what_the_real_run_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The preview has to predict the run: same plan, same refusal, same guidance.

    ``--dry-run`` used to call :func:`decompose.preview` alone, which knows
    nothing about the sizing band — so an oversized plan previewed clean and was
    then refused on the real run, and the preview predicted nothing about the
    thing it previewed (basicly-u6tw).

    Pinned as an equivalence rather than against a message, so the two paths
    cannot drift: whatever the governor refuses, the estimate must refuse, with
    the identical guidance strings.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _write(tmp_path, "src/big.py", 400_000)  # 100k tokens x 3.0 >> 64k
    children = (_child("huge", "src/big.py"),)

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


def test_scope_read_cost_keeps_dot_directory_scopes(tmp_path: Path) -> None:
    """A dot-directory glob keeps its leading dot; only a literal ./ prefix strips."""
    _write(tmp_path, ".claude/rules/python.md", 400)
    assert decompose.scope_read_cost(tmp_path, (".claude/rules/*.md",)) == 100
    assert decompose.scope_read_cost(tmp_path, ("./.claude/rules/*.md",)) == 100
    _write(tmp_path, "src/a.py", 40)
    assert decompose.scope_read_cost(tmp_path, ("./src/a.py",)) == 10


def test_scope_read_cost_excludes_dependency_and_cache_trees(tmp_path: Path) -> None:
    """A virtualenv, dependency tree or cache under a glob is not the lane's work."""
    _write(tmp_path, "src/a.py", 40)
    _write(tmp_path, ".venv/lib/dep.py", 4000)
    _write(tmp_path, "node_modules/pkg/index.py", 4000)
    _write(tmp_path, "src/__pycache__/a.cpython-314.pyc", 4000)
    _write(tmp_path, ".git/hooks/thing.py", 4000)
    # Only src/a.py is the project's own file, so the recursive glob costs 10 —
    # not the 4010 the same glob measured before the exclusion.
    assert decompose.scope_read_cost(tmp_path, ("**/*.py",)) == 10


def test_scope_read_cost_keeps_project_authored_dot_directories(tmp_path: Path) -> None:
    """Exclusion is by directory name, so .basicly and .claude are still counted."""
    _write(tmp_path, ".basicly/core/skills/s/skill.yaml", 400)
    _write(tmp_path, ".claude/rules/python.md", 400)
    assert decompose.scope_read_cost(tmp_path, (".basicly/**",)) == 100
    assert decompose.scope_read_cost(tmp_path, (".claude/**",)) == 100


def test_scope_read_cost_reads_a_file_named_like_an_excluded_dir(tmp_path: Path) -> None:
    """The name check covers directories only, so a file called venv is still read."""
    _write(tmp_path, "src/venv", 40)
    assert decompose.scope_read_cost(tmp_path, ("src/**",)) == 10


def test_scope_read_cost_skips_unglobbable_patterns(tmp_path: Path) -> None:
    """An anchored or engine-rejected pattern is skipped, never fatal."""
    _write(tmp_path, "etc/conf.py", 40)
    # A leading slash is relativized; a drive-anchored pattern must not raise
    # (on POSIX "c:" is an ordinary segment, on Windows the glob engine rejects
    # it and the guard skips it).
    assert decompose.scope_read_cost(tmp_path, ("/etc/conf.py",)) == 10
    assert decompose.scope_read_cost(tmp_path, ("c:/nowhere/*.py",)) == 0


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
    _export(tmp_path, {"id": "feat", "comments": [{"text": fake.comments["feat"][0]}]})

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
        raise RuntimeError("br show failed")

    _install(monkeypatch, broken)
    unreadable = decompose.resolve_dispatch_sizing(tmp_path, "b-1")
    assert unreadable.sizing is None
    assert unreadable.absence == decompose.SCOPE_UNREADABLE


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


def _record_lane_tokens(repo: Path, bead_id: str, tokens: int, *, estimated: bool = False) -> None:
    """One adapter-reported *lane* dispatch — the shape the bound harvests."""
    run_record.record(
        repo,
        bead_id,
        run_record.build_record(
            agent="claude",
            handoff=False,
            returncode=0,
            duration_s=1.0,
            command=("claude",),
            tokens=tokens,
            estimated=estimated,
            phase="lane",
        ),
    )


def test_the_unsized_bound_falls_back_to_the_declared_seed(tmp_path: Path) -> None:
    """With no lane history there is nothing to measure, and it says so."""
    tokens, source = decompose.unsized_lane_tokens(tmp_path, _sizing())

    assert (tokens, source) == (decompose.UNSIZED_LANE_TOKENS_SEED, "seed")


def test_the_unsized_bound_is_a_sample_some_lane_really_incurred(tmp_path: Path) -> None:
    """A quantile *of the samples*, so the figure is a real observation, never a mean.

    The statistic moved from the median to ``unsized_lane_quantile`` (basicly-jr0l.58);
    what must not change is that the bound is a spend some lane actually incurred, so
    it needs no rounding rule and can always be pointed back at a run record.
    """
    _record_lane_tokens(tmp_path, "b-1", 1_000)
    _record_lane_tokens(tmp_path, "b-2", 9_000)
    _record_lane_tokens(tmp_path, "b-3", 2_000)

    tokens, source = decompose.unsized_lane_tokens(tmp_path, _sizing())

    assert (tokens, source) == (9_000, "measured")
    assert tokens in {1_000, 9_000, 2_000}
    assert tokens != statistics.mean((1_000, 9_000, 2_000))


def test_one_pathological_lane_does_not_own_the_unsized_bound(tmp_path: Path) -> None:
    """A quantile, not a max: the most expensive run ever seen must not price them all.

    This once read "a central estimate, not a max, because the population is bimodal" -
    leaves supposedly 856182-4079243 tokens and packages 7674671-20594047. **More data
    refuted that split** (basicly-jr0l.58): four leaf lanes measured 9418977, 10834801,
    11478450 and 11867602, inside the supposed package band, so the population is one
    wide spread rather than two clusters. The median that split justified was exceeded
    by 47% of the 17 recorded actuals.

    What survives is the narrower property this test actually pins: a single outlier
    sits above the quantile and so does not become every lane's bound.
    """
    for i in range(9):
        _record_lane_tokens(tmp_path, f"b-{i}", 1_000)
    _record_lane_tokens(tmp_path, "b-outlier", 20_000_000)

    tokens, _source = decompose.unsized_lane_tokens(tmp_path, _sizing())

    assert tokens == 1_000, "a single outlier must not become the bound for every lane"


def test_the_unsized_bound_still_refuses_the_overrun_that_motivated_it(
    tmp_path: Path,
) -> None:
    """The one case that must not regress, in its measured numbers (basicly-vz78).

    One lane spent 4079243 tokens against a 3000000 remainder and the gate admitted it,
    because an unsizeable lane contributed nothing to the pass total. Whatever statistic
    the bound uses, that pass has to refuse.
    """
    _record_lane_tokens(tmp_path, "b-1", 4_079_243)

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
        _record_lane_tokens(tmp_path, f"b-{index}", tokens)

    bound, _source = decompose.unsized_lane_tokens(tmp_path, _sizing())

    exceeded = [tokens for tokens in actuals if tokens > bound]
    assert len(exceeded) / len(actuals) <= 0.1, f"{len(exceeded)} of {len(actuals)} exceed {bound}"
    # And the statistic is not merely the max, which would price every lane off the
    # single worst run and refuse passes that genuinely fit.
    assert bound < max(actuals)


def test_the_unsized_bound_follows_the_configured_quantile(tmp_path: Path) -> None:
    """The quantile is config, so a consumer can trade throughput against overrun."""
    for index, tokens in enumerate((1_000, 2_000, 3_000, 4_000, 100_000)):
        _record_lane_tokens(tmp_path, f"b-{index}", tokens)

    low, _ = decompose.unsized_lane_tokens(tmp_path, _sizing(unsized_lane_quantile=0.2))
    high, _ = decompose.unsized_lane_tokens(tmp_path, _sizing(unsized_lane_quantile=1.0))

    assert low == 1_000
    assert high == 100_000


def test_the_unsized_bound_ignores_an_estimated_sample(tmp_path: Path) -> None:
    """A chars/4 figure is not an observation, so it must not set the ceiling."""
    _record_lane_tokens(tmp_path, "b-1", 5_000)
    _record_lane_tokens(tmp_path, "b-2", 90_000, estimated=True)

    assert decompose.unsized_lane_tokens(tmp_path, _sizing()) == (5_000, "measured")


def test_the_unsized_bound_needs_no_declared_scope(tmp_path: Path) -> None:
    """The point of the bound: an actual is an observation whatever the scope says.

    Calibration needs a readable ``## Scope`` because it divides tokens by scope
    read-cost. A raw ceiling does not, which is why this can bound the very beads
    ``dispatch_sizing`` refuses to size (basicly-vz78).
    """
    _record_lane_tokens(tmp_path, "b-1", 7_000)

    # No tracker at all under tmp_path, so no bead here has a readable scope.
    assert decompose.unsized_lane_tokens(tmp_path, _sizing()) == (7_000, "measured")
