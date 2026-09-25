from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from basicly import cli, user_skills
from basicly.skill_source import SkillDefinition
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


def test_a_selection_over_the_user_listing_budget_is_refused_with_its_cost(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "skills"
    cost = user_skills.listing_cost(user_skills.selected(["*"]))

    with pytest.raises(user_skills.UserSkillsError) as refused:
        user_skills.project(["*"], root)

    assert cost > user_skills.USER_LISTING_BUDGET == 900
    assert f"cost {cost} listing tokens, over the 900-token user budget" in str(refused.value)
    assert not root.exists()


def test_the_cli_tools_and_tool_selection_fits_the_budget_with_every_description_counted(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".claude" / "skills"
    chosen = user_skills.selected(["cli-tools", "tool-*"])
    described = user_skills.listing_cost([s for s in chosen if s.slug == "cli-tools"])

    lines = user_skills.project(["cli-tools", "tool-*"], root)

    assert described < user_skills.listing_cost(chosen) <= user_skills.USER_LISTING_BUDGET
    assert len(lines) == len(chosen) >= 26


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


def _description_line(skill_md: Path) -> str:
    frontmatter = skill_md.read_text(encoding="utf-8").split("---\n")[1]
    return next(line for line in frontmatter.splitlines() if line.startswith("description:"))


def test_every_tool_skill_gets_its_first_body_paragraph_as_its_description(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".claude" / "skills"
    tools = user_skills.selected(["tool-*"])

    user_skills.project(["tool-*"], root)

    assert len(tools) >= 25
    for skill in tools:
        first = [block for block in skill.instructions.split("\n\n") if block.strip()][1]
        line = _description_line(root / skill.slug / "SKILL.md")
        assert yaml.safe_load(line) == {"description": first.strip()}, skill.slug
    assert _description_line(root / "tool-fd" / "SKILL.md") == (
        "description: Find files by name, extension or type. Use it instead of `find` and to "
        "feed paths to other tools."
    )


def test_a_second_projection_of_the_tool_skills_reports_no_drift(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "skills"
    user_skills.project(["tool-*"], root)

    lines = user_skills.project(["tool-*"], root)

    assert lines and all(line.startswith("unchanged ") for line in lines), lines


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
