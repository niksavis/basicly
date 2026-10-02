from __future__ import annotations

import re
from pathlib import Path

import pytest

from basicly import cli, user_skills
from basicly.skill_source import SkillDefinition
from basicly.skills import GENERATED_MARKER


def _own_skill(root: Path, name: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: mine\n---\nbody\n")
    return folder / "SKILL.md"


def _old_layout_skill(root: Path, name: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\n---\n{GENERATED_MARKER}\n\nbody\n")
    return folder


def _linked_references(skill_md: Path) -> set[str]:
    return set(re.findall(r"\]\((references/[a-z-]+\.md)\)", skill_md.read_text()))


def test_the_projection_writes_the_selection_and_keeps_an_unmarked_skill(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "skills"
    mine = _own_skill(root, "img-zoom")
    before = mine.read_text()

    lines = user_skills.project(["cli-tools"], root)

    assert GENERATED_MARKER in (root / "cli-tools" / "SKILL.md").read_text()
    assert (
        "description: Pick the installed command-line tool"
        in (root / "cli-tools" / "SKILL.md").read_text()
    )
    assert (root / "cli-tools" / "references" / "jq.md").is_file()
    assert mine.read_text() == before
    assert "wrote cli-tools" in lines


def test_the_user_home_gets_every_reference_that_the_cli_tools_table_links(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".claude" / "skills"

    user_skills.project(["cli-tools"], root)

    projected = {
        path.relative_to(root / "cli-tools").as_posix()
        for path in (root / "cli-tools" / "references").glob("*.md")
    }
    assert projected == _linked_references(root / "cli-tools" / "SKILL.md")
    assert len(projected) == 25
    assert {"references/zsh.md", "references/uv.md", "references/git.md"} <= projected


def test_an_upgrade_from_the_tool_skill_layout_prunes_them_and_keeps_an_unmarked_skill(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".claude" / "skills"
    old = [_old_layout_skill(root, name) for name in ("tool-jq", "tool-fd", "tool-zsh")]
    mine = _own_skill(root, "tool-mine")

    lines = user_skills.project(["cli-tools"], root)

    assert not any(folder.exists() for folder in old)
    assert {"pruned tool-fd", "pruned tool-jq", "pruned tool-zsh"} <= set(lines)
    assert "description: mine" in mine.read_text()
    assert sorted(path.name for path in root.iterdir()) == ["cli-tools", "tool-mine"]


def test_a_smaller_selection_prunes_only_marked_skills(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "skills"
    _own_skill(root, "img-zoom")
    user_skills.project(["cli-tools", "no-comments"], root)

    lines = user_skills.project(["cli-tools"], root)

    assert sorted(path.name for path in root.iterdir()) == ["cli-tools", "img-zoom"]
    assert "pruned no-comments" in lines


def test_an_unmarked_skill_of_a_selected_name_is_never_overwritten(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "skills"
    mine = _own_skill(root, "cli-tools")

    lines = user_skills.project(["cli-tools"], root)

    assert "kept cli-tools: an unmarked skill of that name is yours" in lines
    assert "description: mine" in mine.read_text()


def test_a_selection_over_the_user_listing_budget_is_refused_with_its_cost(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "skills"
    cost = user_skills.listing_cost(user_skills.selected(["*"]))

    with pytest.raises(user_skills.UserSkillsError) as refused:
        user_skills.project(["*"], root)

    assert cost > user_skills.USER_LISTING_BUDGET == 900
    assert f"cost {cost} listing tokens, over the 900-token user budget" in str(refused.value)
    assert not root.exists()


def test_the_cli_tools_selection_costs_one_listing_entry_whatever_its_references(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".claude" / "skills"
    chosen = user_skills.selected(["cli-tools"])

    lines = user_skills.project(["cli-tools"], root)

    assert [skill.slug for skill in chosen] == ["cli-tools"] and lines == ["wrote cli-tools"]
    assert user_skills.listing_cost(chosen) <= user_skills.USER_LISTING_BUDGET // 4


def test_a_personal_skill_that_shadows_a_project_skill_is_reported(tmp_path: Path) -> None:
    home = tmp_path / "home" / ".claude" / "skills"
    project = tmp_path / "repo" / ".claude" / "skills"
    user_skills.project(["cli-tools"], home)
    _own_skill(project, "cli-tools")
    _own_skill(project, "img-zoom")

    found = user_skills.shadows(project, home)

    assert len(found) == 1
    assert "shadows the project skill cli-tools (different content)" in found[0]


def test_an_unknown_name_is_refused_with_the_known_names(tmp_path: Path) -> None:
    with pytest.raises(user_skills.UserSkillsError, match="no catalog skill matches nope"):
        user_skills.project(["nope"], tmp_path)


def test_an_unknown_name_beside_known_ones_is_refused_and_nothing_is_written(
    tmp_path: Path,
) -> None:
    with pytest.raises(user_skills.UserSkillsError, match="no catalog skill matches nosuch"):
        user_skills.project(["cli-tools", "no-comments", "nosuch"], tmp_path)

    assert not tmp_path.exists() or not any(tmp_path.iterdir())


def test_a_misspelled_name_names_the_close_catalog_skill(tmp_path: Path) -> None:
    with pytest.raises(user_skills.UserSkillsError, match="did you mean: cli-tools"):
        user_skills.project(["cli-tool"], tmp_path)


@pytest.mark.parametrize("pattern", ["tool-*", "tool-ripgrep"])
def test_a_folded_tool_skill_name_is_refused_with_the_cli_tools_command(
    tmp_path: Path, pattern: str
) -> None:
    with pytest.raises(user_skills.UserSkillsError) as refused:
        user_skills.project(["cli-tools", pattern], tmp_path)

    assert f"no catalog skill matches {pattern}" in str(refused.value)
    assert "select cli-tools, which carries every one: basicly skills-user cli-tools" in str(
        refused.value
    )
    assert not tmp_path.exists() or not any(tmp_path.iterdir())


def test_a_second_projection_of_cli_tools_reports_no_drift(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "skills"
    user_skills.project(["cli-tools"], root)

    lines = user_skills.project(["cli-tools"], root)

    assert lines == ["unchanged cli-tools"]


def _tool_skill(slug: str, opening: str) -> SkillDefinition:
    body = f"# {slug}\n\n{opening}## Rules\n\n- **A rule.** A reason.\n"
    return SkillDefinition(slug, slug, "user", "", body, Path(slug) / "skill.yaml")


def test_a_tool_skill_with_no_first_paragraph_is_refused_by_name_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = [_tool_skill("tool-aa", "Do a thing.\n\n"), _tool_skill("tool-bare", "")]
    monkeypatch.setattr(user_skills, "_catalog_skills", lambda: catalog)

    status = cli.main(["skills-user", "tool-*", "--home", str(tmp_path)])

    assert status == 1
    assert "skill 'tool-bare' has no first body paragraph" in capsys.readouterr().err
    assert not (tmp_path / ".claude").exists()
