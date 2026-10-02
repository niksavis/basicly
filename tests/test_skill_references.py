from __future__ import annotations

import re
from pathlib import Path

import pytest

from basicly.schema import ValidationError
from basicly.skill_source import SKILLS_SOURCE_DIR, discover_skills
from basicly.skills import (
    GENERATED_MARKER,
    RETIRED_REASON_PREFIX,
    UNMANAGED_REASON_PREFIX,
    check_synced_skills,
    resolve_skill_roots,
    sync_skills,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCOPED = "resource_technologies:\n  references/zsh.md: [zsh]\n"
_TABLE = (
    "instructions: |\n  # Tools\n\n  Pick a tool.\n\n  | Tool | Reference |\n  | --- | --- |\n"
    "  | jq | [jq](references/jq.md) |\n  | zsh | [zsh](references/zsh.md) |\n"
)


def _skill(repo: Path, extra: str = _SCOPED, body: str = _TABLE) -> Path:
    folder = repo / SKILLS_SOURCE_DIR / "tools"
    (folder / "references").mkdir(parents=True)
    (folder / "references" / "jq.md").write_text("# jq\n", encoding="utf-8")
    (folder / "references" / "zsh.md").write_text("# zsh\n", encoding="utf-8")
    source = folder / "skill.yaml"
    source.write_text(
        f"schema_version: 1\nname: tools\ninvocation: model\ndescription: Tools.\n{extra}{body}",
        encoding="utf-8",
    )
    return source


def test_a_selection_without_the_technology_drops_the_reference_and_its_row(
    tmp_path: Path,
) -> None:
    _skill(tmp_path)
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"])
    folder = roots[0] / "tools"

    sync_skills(tmp_path, roots, selection=frozenset({"zsh"}))
    assert (folder / "references" / "zsh.md").is_file()
    assert "references/zsh.md" in (folder / "SKILL.md").read_text(encoding="utf-8")

    _result, pruned = sync_skills(tmp_path, roots, selection=frozenset({"python"}))

    body = (folder / "SKILL.md").read_text(encoding="utf-8")
    assert pruned == [folder / "references" / "zsh.md"]
    assert "references/zsh.md" not in body and "| jq | [jq](references/jq.md) |" in body
    assert (folder / "references" / "jq.md").is_file()
    assert check_synced_skills(tmp_path, roots, selection=frozenset({"python"})) == []
    assert (folder / "references" / "zsh.md", "missing") in check_synced_skills(
        tmp_path, roots, selection=frozenset({"zsh"})
    )


def test_no_selection_projects_every_scoped_reference(tmp_path: Path) -> None:
    _skill(tmp_path)
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"])

    sync_skills(tmp_path, roots)

    assert (roots[0] / "tools" / "references" / "zsh.md").is_file()
    assert check_synced_skills(tmp_path, roots) == []


@pytest.mark.parametrize(
    ("extra", "body", "refusal"),
    [
        ("resource_technologies:\n  references/nope.md: [zsh]\n", _TABLE, "'references/nope.md'"),
        ("resource_technologies:\n  references/zsh.md: []\n", _TABLE, "no technologies"),
        ("resource_technologies:\n  references/zsh.md: [zhs]\n", _TABLE, "unknown technologies"),
        ("resource_technologies: [references/zsh.md]\n", _TABLE, "must map a resource path"),
        (
            _SCOPED,
            "instructions: |\n  # Tools\n\n  Read references/zsh.md for zsh.\n",
            "not a table row or a list item",
        ),
    ],
)
def test_a_wrong_resource_scope_is_refused_by_name(
    tmp_path: Path, extra: str, body: str, refusal: str
) -> None:
    _skill(tmp_path, extra, body)

    with pytest.raises(ValidationError, match=re.escape(refusal)):
        discover_skills(tmp_path)


def test_a_generated_skill_without_a_source_is_retired_and_a_hand_written_one_stays(
    tmp_path: Path,
) -> None:
    _skill(tmp_path)
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills", ".agents/skills"])
    old = []
    for root in roots:
        retired = root / "tool-jq" / "SKILL.md"
        retired.parent.mkdir(parents=True)
        retired.write_text(f"---\nname: tool-jq\n---\n{GENERATED_MARKER}\n\nold\n", "utf-8")
        old.append(retired)
    mine = roots[0] / "tool-mine" / "SKILL.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("---\nname: tool-mine\n---\n\nmine\n", encoding="utf-8")
    reasons = dict(check_synced_skills(tmp_path, roots))
    assert all(reasons[path].startswith(RETIRED_REASON_PREFIX) for path in old)

    _result, pruned = sync_skills(tmp_path, roots)

    assert set(old) <= set(pruned) and not any(path.parent.exists() for path in old)
    assert mine.is_file() and mine not in pruned
    remaining = check_synced_skills(tmp_path, roots)
    assert [path for path, _ in remaining] == [mine]
    assert remaining[0][1].startswith(UNMANAGED_REASON_PREFIX)


def test_the_cli_tools_table_links_every_reference_it_ships() -> None:
    cli_tools = next(skill for skill in discover_skills(REPO_ROOT) if skill.slug == "cli-tools")
    shipped = {
        path.relative_to(cli_tools.source_dir).as_posix()
        for path in (cli_tools.source_dir / "references").glob("*.md")
    }
    linked = set(re.findall(r"\]\((references/[a-z-]+\.md)\)", cli_tools.instructions))

    assert shipped == linked
    assert len(shipped) == 25
    assert {resource for resource, _ in cli_tools.resource_technologies} <= shipped
    assert not [s.slug for s in discover_skills(REPO_ROOT) if s.slug.startswith("tool-")]
