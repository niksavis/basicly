"""Tests for the projection planner."""

from __future__ import annotations

from pathlib import Path

from basicly.loader import load_fragments, load_targets
from basicly.planner import plan_outputs
from basicly.schema import Fragment, OutputDef, Target

FIXTURES = Path(__file__).parent / "fixtures"


def test_plan_outputs() -> None:
    """The planner produces the expected output files for fixture targets."""
    targets = load_targets(FIXTURES / "targets")
    target_names = {t.name for t in targets}
    fragments = load_fragments(FIXTURES, target_names)
    planned = plan_outputs(fragments, targets, Path("/repo"))

    paths = {p.output_path for p in planned}
    assert Path("/repo/AGENTS.md") in paths
    assert Path("/repo/.claude/CLAUDE.md") in paths
    assert Path("/repo/.github/copilot-instructions.md") in paths
    assert Path("/repo/.github/instructions/python-style.instructions.md") in paths
    assert Path("/repo/.claude/rules/python-style.md") not in paths


def test_agents_baseline_only_all_fragments() -> None:
    """The cross-tool baseline only includes applies_to: [all] fragments."""
    targets = load_targets(FIXTURES / "targets")
    target_names = {t.name for t in targets}
    fragments = load_fragments(FIXTURES, target_names)
    planned = plan_outputs(fragments, targets, Path("/repo"))

    agents = next(p for p in planned if p.output_path == Path("/repo/AGENTS.md"))
    ids = [f.id for f in agents.fragments]
    assert "claude-defaults" not in ids
    assert "copilot-defaults" not in ids
    assert ids == ["project-defaults", "core-rules", "python-style"]


def test_sort_order() -> None:
    """Fragments are sorted by priority descending, then category, then id."""
    targets = load_targets(FIXTURES / "targets")
    target_names = {t.name for t in targets}
    fragments = load_fragments(FIXTURES, target_names)
    planned = plan_outputs(fragments, targets, Path("/repo"))

    agents = next(p for p in planned if p.output_path == Path("/repo/AGENTS.md"))
    ids = [f.id for f in agents.fragments]
    assert ids == ["project-defaults", "core-rules", "python-style"]


def test_exclude_scoped_keeps_scoped_fragments_out_of_baseline() -> None:
    """A baseline output with exclude_scoped drops scoped fragments but keeps them scoped-only."""
    unscoped = Fragment(
        id="core-rules",
        description="Core rules",
        category="project",
        applies_to=["all"],
        body="- rule",
    )
    scoped = Fragment(
        id="python-style",
        description="Python style",
        category="code-style",
        applies_to=["all"],
        scope_paths=["**/*.py"],
        body="- style",
    )
    target = Target(
        name="claude",
        enabled=True,
        tone="terse_directive",
        max_size_warning=8000,
        outputs=[
            OutputDef(
                name="baseline",
                template="claude/claude_md.j2",
                path=".claude/CLAUDE.md",
                applies_to_filter=["all", "claude"],
                exclude_scoped=True,
            ),
            OutputDef(
                name="scoped_rules",
                template="claude/rule_md.j2",
                path_template=".claude/rules/{fragment_id}.md",
                applies_to_filter=["all", "claude"],
                has_scope=True,
            ),
        ],
    )

    planned = plan_outputs([unscoped, scoped], [target], Path("/repo"))

    baseline = next(p for p in planned if p.output_path == Path("/repo/.claude/CLAUDE.md"))
    assert [f.id for f in baseline.fragments] == ["core-rules"]
    assert Path("/repo/.claude/rules/python-style.md") in {p.output_path for p in planned}


def test_user_replaces_removes_core_fragment() -> None:
    """A user fragment with replaces should suppress the replaced core fragment."""
    targets = load_targets(FIXTURES / "targets")
    target_names = {t.name for t in targets}
    fragments = load_fragments(FIXTURES, target_names)

    python_style_core = next(f for f in fragments if f.id == "python-style")
    user_override = type(python_style_core)(
        id="python-style-user",
        description="Override python style",
        category="code-style",
        applies_to=["all"],
        priority="medium",
        scope_paths=list(python_style_core.scope_paths),
        body="User override",
        source="user",
        override=True,
        replaces=["python-style"],
    )

    planned = plan_outputs([*fragments, user_override], targets, Path("/repo"))
    agents = next(p for p in planned if p.output_path == Path("/repo/AGENTS.md"))
    ids = [f.id for f in agents.fragments]

    assert "python-style" not in ids
    assert "python-style-user" in ids
