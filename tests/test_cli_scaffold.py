"""Tests for the catalog scaffold commands (catalog new skill / catalog new fragment)."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from basicly import cli
from basicly.config import load_project_paths
from basicly.loader import load_fragments
from basicly.skills import discover_skills


def _catalog_subparser() -> argparse._SubParsersAction:
    """The ``catalog`` group's subparser action from the built parser."""
    top = next(a for a in cli._build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    return next(
        a for a in top.choices["catalog"]._actions if isinstance(a, argparse._SubParsersAction)
    )


def test_catalog_group_exposes_the_authoring_verbs() -> None:
    """`basicly catalog` groups exactly lint/verify/review/new/list (basicly-b6j)."""
    assert set(_catalog_subparser().choices) == {"lint", "verify", "review", "new", "list"}


@pytest.mark.parametrize(
    "flat",
    [
        "catalog-lint",
        "catalog-verify",
        "review",
        "list",
        "skills-list",
        "agents-list",
        "skills-new",
        "agents-new",
        "fragment-new",
    ],
)
def test_old_flat_names_are_removed(flat: str) -> None:
    """The pre-b6j flat names no longer parse — breaking change, no aliases."""
    with pytest.raises(SystemExit):
        cli._build_parser().parse_args([flat])


def test_skills_new_creates_loadable_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scaffolding a skill via `catalog new skill` writes a loadable skill.yaml."""
    monkeypatch.chdir(tmp_path)

    args = ["catalog", "new", "skill", "demo-skill", "--description", "A demo skill."]
    assert cli.main(args) == 0
    assert (tmp_path / ".basicly/core/skills/demo-skill/skill.yaml").is_file()

    skills = discover_skills(tmp_path)
    assert [s.slug for s in skills] == ["demo-skill"]
    assert skills[0].name == "demo-skill"
    assert skills[0].description == "A demo skill."


def test_skills_new_refuses_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running `catalog new skill` does not clobber an existing source."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(["catalog", "new", "skill", "demo-skill"]) == 0
    assert cli.main(["catalog", "new", "skill", "demo-skill"]) == 1


def test_fragment_new_creates_loadable_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scaffolding via `catalog new fragment` writes a loadable source in the category dir."""
    monkeypatch.chdir(tmp_path)

    args = ["catalog", "new", "fragment", "demo-frag", "--category", "tools", "--description", "D."]
    assert cli.main(args) == 0
    path = tmp_path / ".basicly/core/fragments/tools/demo-frag.fragment.yaml"
    assert path.is_file()

    fragments = load_fragments(tmp_path / ".basicly/core/fragments", set())
    assert [f.id for f in fragments] == ["demo-frag"]
    assert fragments[0].category == "tools"


def test_fragment_new_refuses_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running `catalog new fragment` does not clobber an existing source."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(["catalog", "new", "fragment", "demo-frag"]) == 0
    assert cli.main(["catalog", "new", "fragment", "demo-frag"]) == 1


def test_overlay_stubs_are_loadable_drafts(tmp_path: Path) -> None:
    """The scaffolded overview/commands stubs parse as valid draft fragments."""
    paths = load_project_paths(tmp_path)
    cli._scaffold_overlay_stubs(tmp_path, paths)

    user_root = tmp_path / ".basicly-local" / "fragments" / "user"
    fragments = load_fragments(user_root, set())
    assert sorted(f.id for f in fragments) == ["commands", "project-overview"]
    assert all(f.status == "draft" for f in fragments)


def test_overlay_stubs_never_overwrite(tmp_path: Path) -> None:
    """A filled-in stub survives re-running the scaffold."""
    paths = load_project_paths(tmp_path)
    cli._scaffold_overlay_stubs(tmp_path, paths)

    stub = (
        tmp_path / ".basicly-local" / "fragments" / "user" / "commands" / "commands.fragment.yaml"
    )
    marker = stub.read_text(encoding="utf-8") + "# user edit\n"
    stub.write_text(marker, encoding="utf-8")

    cli._scaffold_overlay_stubs(tmp_path, paths)
    assert stub.read_text(encoding="utf-8") == marker
