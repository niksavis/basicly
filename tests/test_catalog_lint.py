"""Tests for the catalog-lint gate."""

from __future__ import annotations

from pathlib import Path

from basicly.catalog_lint import lint_catalog

REPO = Path(__file__).parent.parent
VALID_SKILL = "schema_version: 1\nname: s\ndescription: d\ninstructions: |\n  body\n"
VALID_FRAGMENT = (
    "schema_version: 1\nid: f\ndescription: d\ncategory: project\n"
    "applies_to: [all]\nbody: |\n  - x\n"
)


def _catalog(tmp_path: Path) -> Path:
    """Build a minimal catalog with real schemas and one valid skill + fragment."""
    schemas = tmp_path / ".basicly/core/schemas"
    schemas.mkdir(parents=True)
    for name in ("skill.schema.json", "fragment.schema.json"):
        (schemas / name).write_text(
            (REPO / ".basicly/core/schemas" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    skill = tmp_path / ".basicly/core/skills/s/skill.yaml"
    skill.parent.mkdir(parents=True)
    skill.write_text(VALID_SKILL, encoding="utf-8")
    frag = tmp_path / ".basicly/core/fragments/project/f.fragment.yaml"
    frag.parent.mkdir(parents=True)
    frag.write_text(VALID_FRAGMENT, encoding="utf-8")
    return tmp_path


def test_clean_catalog_passes(tmp_path: Path) -> None:
    """A well-formed catalog reports no violations."""
    assert lint_catalog(_catalog(tmp_path)) == []


def test_flags_skill_md_source(tmp_path: Path) -> None:
    """A SKILL.md left in a source dir is a violation."""
    root = _catalog(tmp_path)
    (root / ".basicly/core/skills/legacy").mkdir()
    (root / ".basicly/core/skills/legacy/SKILL.md").write_text("x\n", encoding="utf-8")
    assert any("SKILL.md" in v for v in lint_catalog(root))


def test_flags_fragment_md_source(tmp_path: Path) -> None:
    """A *.fragment.md source is a violation."""
    root = _catalog(tmp_path)
    (root / ".basicly/core/fragments/project/legacy.fragment.md").write_text(
        "x\n", encoding="utf-8"
    )
    assert any("fragment.md" in v for v in lint_catalog(root))


def test_flags_yml_extension(tmp_path: Path) -> None:
    """A .yml file anywhere under the catalog is a violation."""
    root = _catalog(tmp_path)
    (root / ".basicly/core/stray.yml").write_text("a: 1\n", encoding="utf-8")
    assert any(".yml" in v for v in lint_catalog(root))


def test_flags_schema_violation(tmp_path: Path) -> None:
    """A source missing a required field fails schema validation."""
    root = _catalog(tmp_path)
    # drop the required 'instructions' field
    (root / ".basicly/core/skills/s/skill.yaml").write_text(
        "schema_version: 1\nname: s\ndescription: d\n", encoding="utf-8"
    )
    violations = lint_catalog(root)
    assert any("skill.yaml" in v for v in violations)


def test_enforced_by_cited_in_body_passes(tmp_path: Path) -> None:
    """A fragment that cites its enforced_by command in the body is clean."""
    root = _catalog(tmp_path)
    (root / ".basicly/core/fragments/project/f.fragment.yaml").write_text(
        "schema_version: 1\nid: f\ndescription: d\ncategory: code-style\n"
        "applies_to: [all]\nenforced_by: [ruff format]\n"
        "body: |\n  Formatting is enforced by `ruff format`.\n",
        encoding="utf-8",
    )
    assert lint_catalog(root) == []


def test_enforced_by_not_cited_is_flagged(tmp_path: Path) -> None:
    """A fragment declaring enforced_by without citing it in the body is a violation."""
    root = _catalog(tmp_path)
    (root / ".basicly/core/fragments/project/f.fragment.yaml").write_text(
        "schema_version: 1\nid: f\ndescription: d\ncategory: code-style\n"
        "applies_to: [all]\nenforced_by: [ruff format]\n"
        "body: |\n  Always indent with four spaces.\n",
        encoding="utf-8",
    )
    violations = lint_catalog(root)
    assert any("enforced_by command 'ruff format' is not cited" in v for v in violations)


def test_no_enforced_by_is_a_noop(tmp_path: Path) -> None:
    """A fragment without enforced_by triggers no enforcement-pointer violation."""
    root = _catalog(tmp_path)
    assert not any("enforced_by" in v for v in lint_catalog(root))
