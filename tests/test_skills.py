"""Tests for skill collection projection helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly.schema import ValidationError
from basicly.skills import (
    GENERATED_MARKER,
    SKILLS_SOURCE_DIR,
    SkillDefinition,
    check_synced_skills,
    discover_skills,
    render_skill_md,
    resolve_skill_roots,
    sync_skills,
)


def _write_skill(
    repo_root: Path, slug: str, name: str, description: str, technologies: str | None = None
) -> None:
    path = repo_root / SKILLS_SOURCE_DIR / slug / "skill.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            "# yaml-language-server: $schema=../../schemas/skill.schema.json",
            "schema_version: 1",
            f"name: {name}",
            f"description: {description}",
            *([f"technologies: {technologies}"] if technologies else []),
            "instructions: |",
            f"  # {name}",
            "",
            "  ## When To Use",
            "  - Example.",
        ])
        + "\n",
        encoding="utf-8",
    )


def test_discover_skills_loads_source(tmp_path: Path) -> None:
    """discover_skills reads skill.yaml sources and parses name/description/instructions."""
    _write_skill(tmp_path, "tool-ripgrep", "tool-ripgrep", "Use ripgrep for fast code search.")

    skills = discover_skills(tmp_path)

    assert [skill.slug for skill in skills] == ["tool-ripgrep"]
    assert skills[0].name == "tool-ripgrep"
    assert skills[0].description == "Use ripgrep for fast code search."
    assert skills[0].instructions.startswith("# tool-ripgrep")


def test_discover_skills_requires_fields(tmp_path: Path) -> None:
    """discover_skills fails when a required field is missing."""
    path = tmp_path / SKILLS_SOURCE_DIR / "tool-ripgrep" / "skill.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("name: x\ndescription: y\n", encoding="utf-8")  # no instructions

    with pytest.raises(ValidationError):
        discover_skills(tmp_path)


def test_discover_skills_ignores_non_yaml_source(tmp_path: Path) -> None:
    """A stray SKILL.md left in a source dir is not discovered (no double-load)."""
    md = tmp_path / SKILLS_SOURCE_DIR / "legacy" / "SKILL.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("---\nname: legacy\ndescription: d\n---\n\nbody\n", encoding="utf-8")

    assert discover_skills(tmp_path) == []


def test_render_skill_md_frontmatter_marker_and_body() -> None:
    """render_skill_md emits YAML frontmatter, the generated marker, then the body."""
    skill = SkillDefinition("s", "s", "A skill.", "# Body\n\ntext\n", Path("skill.yaml"))
    out = render_skill_md(skill)

    assert (
        out == f"---\nname: s\ndescription: A skill.\n---\n{GENERATED_MARKER}\n\n# Body\n\ntext\n"
    )
    # stripping the marker line yields the plain frontmatter+body (fidelity contract)
    assert (
        out.replace(GENERATED_MARKER + "\n", "", 1)
        == "---\nname: s\ndescription: A skill.\n---\n\n# Body\n\ntext\n"
    )


def test_sync_and_check_skills(tmp_path: Path) -> None:
    """sync_skills renders SKILL.md (with marker) to roots and check validates parity."""
    _write_skill(tmp_path, "tool-ripgrep", "tool-ripgrep", "Use ripgrep for fast code search.")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"], use_default_roots=False)

    result, _pruned = sync_skills(tmp_path, roots)

    assert len(result.written) == 1
    target = roots[0] / "tool-ripgrep" / "SKILL.md"
    assert GENERATED_MARKER in target.read_text(encoding="utf-8")
    assert len(check_synced_skills(tmp_path, roots)) == 0

    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert check_synced_skills(tmp_path, roots) == [(target, "content mismatch")]


def test_sync_skills_filters_and_prunes_by_selection(tmp_path: Path) -> None:
    """A tagged skill outside the selection is skipped and its projection pruned."""
    _write_skill(tmp_path, "tool-uv", "tool-uv", "Python tooling.", technologies="[python]")
    _write_skill(tmp_path, "tool-git", "tool-git", "Git usage.")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"], use_default_roots=False)
    excluded = roots[0] / "tool-uv" / "SKILL.md"

    # Full projection first (no selection recorded): both skills ship.
    sync_skills(tmp_path, roots)
    assert excluded.is_file()

    # Narrowing to zsh flags the stray projection, then the build prunes it.
    selection = frozenset({"zsh"})
    assert check_synced_skills(tmp_path, roots, selection=selection) == [
        (excluded, "excluded by technology selection")
    ]
    result, pruned = sync_skills(tmp_path, roots, selection=selection)
    assert pruned == [excluded]
    assert not excluded.parent.exists()
    assert (roots[0] / "tool-git" / "SKILL.md").is_file()  # universal always ships
    assert check_synced_skills(tmp_path, roots, selection=selection) == []

    # A matching selection ships the tagged skill like any other.
    result, pruned = sync_skills(tmp_path, roots, selection=frozenset({"python"}))
    assert pruned == [] and excluded in result.written
