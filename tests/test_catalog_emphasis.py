from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from basicly import catalog_emphasis as emphasis
from basicly.catalog_lint import lint_catalog

REPO = Path(__file__).parent.parent
SCHEMAS = (
    "skill.schema.json",
    "fragment.schema.json",
    "agent.schema.json",
    "block.schema.json",
)
SKILL = (
    "schema_version: 1\nname: s\ninvocation: model\n"
    "description: A description long enough to route.\ninstructions: |\n  body\n"
)


def _fragment(fragment_id: str, body: str) -> str:
    indented = "\n".join(f"  {line}" for line in body.splitlines())
    return (
        f"schema_version: 1\nid: {fragment_id}\ndescription: d\ncategory: project\n"
        f"applies_to: [all]\nbody: |\n{indented}\n"
    )


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    core = tmp_path / ".basicly/core"
    (core / "schemas").mkdir(parents=True)
    for name in SCHEMAS:
        shutil.copy(REPO / ".basicly/core/schemas" / name, core / "schemas" / name)
    shutil.copytree(REPO / ".basicly/core/targets", core / "targets")
    shutil.copytree(REPO / ".basicly/core/templates", core / "templates")
    (core / "skills/s").mkdir(parents=True)
    (core / "skills/s/skill.yaml").write_text(SKILL, encoding="utf-8")
    (core / "fragments/project").mkdir(parents=True)
    return tmp_path


def _write(catalog: Path, fragment_id: str, body: str) -> Path:
    path = catalog / f".basicly/core/fragments/project/{fragment_id}.fragment.yaml"
    path.write_text(_fragment(fragment_id, body), encoding="utf-8")
    return path


def _skill(catalog: Path, instructions: str) -> None:
    indented = "\n".join(f"  {line}" for line in instructions.splitlines())
    (catalog / ".basicly/core/skills/s/skill.yaml").write_text(
        "schema_version: 1\nname: s\ninvocation: model\n"
        f"description: A description long enough to route.\ninstructions: |\n{indented}\n",
        encoding="utf-8",
    )


def test_one_marker_is_the_budget_and_passes(catalog: Path) -> None:
    _write(catalog, "a", "- IMPORTANT: never bypass a gate to force a green build.")

    assert emphasis.violations(catalog) == []


def test_a_second_marker_in_another_fragment_is_refused(catalog: Path) -> None:
    _write(catalog, "a", "- IMPORTANT: never bypass a gate to force a green build.")
    _write(catalog, "b", "- CRITICAL: run the checks again after the final edit.")

    found = emphasis.violations(catalog)

    assert found, "two markers in one projection must be refused"
    assert all("2 emphasis markers against a budget of 1" in line for line in found)


def test_the_refusal_names_every_site(catalog: Path) -> None:
    _write(catalog, "a", "- IMPORTANT: never bypass a gate.")
    _write(catalog, "b", "- CRITICAL: run the checks again.")

    first = emphasis.violations(catalog)[0]

    assert "IMPORTANT in .basicly/core/fragments/project/a.fragment.yaml" in first
    assert "CRITICAL in .basicly/core/fragments/project/b.fragment.yaml" in first


def test_two_markers_in_one_fragment_are_refused(catalog: Path) -> None:
    _write(catalog, "a", "- IMPORTANT: read the gate.\n- NEVER edit the projected file.")

    assert emphasis.violations(catalog)


def test_every_always_on_projection_is_measured(catalog: Path) -> None:
    _write(catalog, "a", "- IMPORTANT: read the gate.")
    _write(catalog, "b", "- CRITICAL: read it twice.")

    surfaces = {line.split(":")[0] for line in emphasis.violations(catalog)}

    assert surfaces == {
        ".claude/CLAUDE.md",
        "AGENTS.md",
        ".github/copilot-instructions.md",
    }


def test_a_marker_inside_a_code_span_is_not_emphasis(catalog: Path) -> None:
    _write(catalog, "a", "- IMPORTANT: never bypass a gate.")
    _write(catalog, "b", "- The constant is `NEVER` and the flag is `--always`.")

    assert emphasis.violations(catalog) == []


def test_a_marker_inside_a_fenced_block_is_not_emphasis(catalog: Path) -> None:
    _write(catalog, "a", "- IMPORTANT: never bypass a gate.")
    _write(catalog, "b", "```python\nMUST = 1\nNEVER = 2\n```")

    assert emphasis.violations(catalog) == []


def test_a_lowercase_word_is_not_a_marker(catalog: Path) -> None:
    _write(catalog, "a", "- IMPORTANT: never bypass a gate.")
    _write(catalog, "b", "- You must never always do this; it is important to note.")

    assert emphasis.violations(catalog) == []


def test_a_skill_carries_its_own_budget(catalog: Path) -> None:
    _skill(catalog, "# S\n\nIMPORTANT: claim the record first.\nNEVER invent an id.")

    found = emphasis.violations(catalog)

    assert any(line.startswith("skill s:") for line in found)


def test_one_marker_in_a_skill_passes(catalog: Path) -> None:
    _skill(catalog, "# S\n\nIMPORTANT: claim the record first.")

    assert emphasis.violations(catalog) == []


def test_the_gate_runs_inside_catalog_lint(catalog: Path) -> None:
    _write(catalog, "a", "- IMPORTANT: never bypass a gate.")
    _write(catalog, "b", "- CRITICAL: run the checks again.")

    assert any("emphasis markers against a budget" in line for line in lint_catalog(catalog))


def test_the_committed_catalog_is_within_budget() -> None:
    assert emphasis.violations(REPO) == []


def test_the_committed_catalog_actually_carries_a_marker() -> None:
    body = (
        REPO / ".basicly/core/fragments/boundaries/require-explicit-confirmation.fragment.yaml"
    ).read_text(encoding="utf-8")

    assert emphasis.markers_in(body) == ["IMPORTANT"]
