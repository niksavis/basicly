from __future__ import annotations

from pathlib import Path

import pytest

from basicly.loader import load_fragments, load_targets
from basicly.planner import contained_output_path, plan_outputs
from basicly.schema import Fragment, OutputDef, Target, ValidationError

FIXTURES = Path(__file__).parent / "fixtures"


def test_plan_outputs() -> None:
    targets = load_targets(FIXTURES / "targets")
    target_names = {t.name for t in targets}
    fragments = load_fragments(FIXTURES, target_names)
    planned = plan_outputs(fragments, targets, Path("/repo"))

    paths = {p.output_path for p in planned}
    assert Path("/repo/AGENTS.md") in paths
    assert Path("/repo/.claude/CLAUDE.md") in paths
    assert Path("/repo/.github/copilot-instructions.md") in paths
    assert Path("/repo/.github/instructions/python-style.instructions.md") not in paths
    assert Path("/repo/.claude/rules/python-style.md") not in paths


def test_agents_baseline_only_all_fragments() -> None:
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
    targets = load_targets(FIXTURES / "targets")
    target_names = {t.name for t in targets}
    fragments = load_fragments(FIXTURES, target_names)
    planned = plan_outputs(fragments, targets, Path("/repo"))

    agents = next(p for p in planned if p.output_path == Path("/repo/AGENTS.md"))
    ids = [f.id for f in agents.fragments]
    assert ids == ["project-defaults", "core-rules", "python-style"]


def test_exclude_scoped_keeps_scoped_fragments_out_of_baseline() -> None:
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
        max_lines_warning=0,
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


def test_an_absolute_output_path_is_refused_rather_than_silently_replacing_the_root(
    tmp_path: Path,
) -> None:

    with pytest.raises(ValidationError, match="absolute path"):
        contained_output_path(tmp_path, "/etc/basicly-escaped.md", field="output path")


def test_a_traversal_output_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="outside the repo root"):
        contained_output_path(tmp_path, "../../escaped.md", field="output path")


def test_an_empty_output_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="is empty"):
        contained_output_path(tmp_path, "", field="output path")


def test_an_ordinary_relative_path_is_returned_unresolved(tmp_path: Path) -> None:

    out = contained_output_path(tmp_path, ".claude/CLAUDE.md", field="output path")
    assert out == tmp_path / ".claude" / "CLAUDE.md"
    assert out.relative_to(tmp_path) == Path(".claude/CLAUDE.md")


def test_containment_holds_for_a_repo_root_reached_through_a_symlink(tmp_path: Path) -> None:

    real = tmp_path / "real-repo"
    real.mkdir()
    link = tmp_path / "linked-repo"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError, NotImplementedError:  # pragma: no cover - unprivileged Windows
        pytest.skip("symlinks not available on this platform")
    out = contained_output_path(link, "AGENTS.md", field="output path")
    assert out == link / "AGENTS.md"


def test_a_fragment_id_carrying_a_separator_cannot_reach_out_of_the_repo() -> None:

    fragment = Fragment(
        id="../../../etc/escaped",
        description="Escapes through its own id",
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
        max_lines_warning=0,
        outputs=[
            OutputDef(
                name="scoped_rules",
                template="claude/rule_md.j2",
                path_template=".claude/rules/{fragment_id}.md",
                applies_to_filter=["all", "claude"],
                has_scope=True,
            )
        ],
    )
    with pytest.raises(ValidationError, match="escaped"):
        plan_outputs([fragment], [target], Path("/repo"))
