from __future__ import annotations

from pathlib import Path

import pytest

from basicly import user_skills
from basicly.skills import GENERATED_MARKER


def _own_skill(root: Path, name: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: mine\n---\nbody\n")
    return folder / "SKILL.md"


def test_the_projection_writes_the_selection_and_keeps_an_unmarked_skill(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "skills"
    mine = _own_skill(root, "img-zoom")
    before = mine.read_text()

    lines = user_skills.project(["cli-tools", "tool-jq"], root)

    assert GENERATED_MARKER in (root / "cli-tools" / "SKILL.md").read_text()
    assert (
        "description: Pick the installed command-line tool"
        in (root / "cli-tools" / "SKILL.md").read_text()
    )
    assert (root / "tool-jq" / "SKILL.md").is_file()
    assert mine.read_text() == before
    assert "wrote cli-tools" in lines


def test_a_smaller_selection_prunes_only_marked_skills(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "skills"
    _own_skill(root, "img-zoom")
    user_skills.project(["cli-tools", "tool-jq", "tool-fd"], root)

    lines = user_skills.project(["cli-tools"], root)

    assert sorted(path.name for path in root.iterdir()) == ["cli-tools", "img-zoom"]
    assert "pruned tool-fd" in lines and "pruned tool-jq" in lines


def test_an_unmarked_skill_of_a_selected_name_is_never_overwritten(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "skills"
    mine = _own_skill(root, "tool-jq")

    lines = user_skills.project(["tool-jq"], root)

    assert "kept tool-jq: an unmarked skill of that name is yours" in lines
    assert "description: mine" in mine.read_text()


def test_a_selection_over_the_user_listing_budget_is_refused(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "skills"

    with pytest.raises(user_skills.UserSkillsError, match="over the 200-token user budget"):
        user_skills.project(["cli-tools", "conventional-commits", "harness-loop"], root)

    assert not root.exists()


def test_a_personal_skill_that_shadows_a_project_skill_is_reported(tmp_path: Path) -> None:
    home = tmp_path / "home" / ".claude" / "skills"
    project = tmp_path / "repo" / ".claude" / "skills"
    user_skills.project(["tool-jq"], home)
    _own_skill(project, "tool-jq")
    _own_skill(project, "tool-fd")

    found = user_skills.shadows(project, home)

    assert len(found) == 1
    assert "shadows the project skill tool-jq (different content)" in found[0]


def test_an_unknown_name_is_refused_with_the_known_names(tmp_path: Path) -> None:
    with pytest.raises(user_skills.UserSkillsError, match="no catalog skill matches tool-nope"):
        user_skills.project(["tool-nope"], tmp_path)


def test_an_unknown_name_beside_known_ones_is_refused_and_nothing_is_written(
    tmp_path: Path,
) -> None:
    with pytest.raises(user_skills.UserSkillsError, match="no catalog skill matches nosuch"):
        user_skills.project(["cli-tools", "tool-jq", "nosuch"], tmp_path)

    assert not tmp_path.exists() or not any(tmp_path.iterdir())


def test_a_misspelled_name_names_the_close_catalog_skill(tmp_path: Path) -> None:
    with pytest.raises(user_skills.UserSkillsError, match="did you mean: tool-jq"):
        user_skills.project(["tool-jqq"], tmp_path)
