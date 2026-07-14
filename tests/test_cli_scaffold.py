"""Tests for the catalog scaffold commands (skills-new / fragment-new)."""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import cli
from basicly.loader import load_fragments
from basicly.skills import discover_skills


def test_skills_new_creates_loadable_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """skills-new writes a skill.yaml the loader accepts."""
    monkeypatch.chdir(tmp_path)

    assert cli.main(["skills-new", "demo-skill", "--description", "A demo skill."]) == 0
    assert (tmp_path / ".basicly/core/skills/demo-skill/skill.yaml").is_file()

    skills = discover_skills(tmp_path)
    assert [s.slug for s in skills] == ["demo-skill"]
    assert skills[0].name == "demo-skill"
    assert skills[0].description == "A demo skill."


def test_skills_new_refuses_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """skills-new does not clobber an existing source."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(["skills-new", "demo-skill"]) == 0
    assert cli.main(["skills-new", "demo-skill"]) == 1


def test_fragment_new_creates_loadable_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fragment-new writes a <id>.fragment.yaml the loader accepts, in the category dir."""
    monkeypatch.chdir(tmp_path)

    assert (
        cli.main(["fragment-new", "demo-frag", "--category", "tools", "--description", "D."]) == 0
    )
    path = tmp_path / ".basicly/core/fragments/tools/demo-frag.fragment.yaml"
    assert path.is_file()

    fragments = load_fragments(tmp_path / ".basicly/core/fragments", set())
    assert [f.id for f in fragments] == ["demo-frag"]
    assert fragments[0].category == "tools"


def test_fragment_new_refuses_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """fragment-new does not clobber an existing source."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(["fragment-new", "demo-frag"]) == 0
    assert cli.main(["fragment-new", "demo-frag"]) == 1
