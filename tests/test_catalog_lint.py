"""Tests for the catalog-lint gate."""

from __future__ import annotations

from pathlib import Path

import yaml

from basicly.catalog_lint import lint_catalog, skill_warnings

REPO = Path(__file__).parent.parent
VALID_SKILL = (
    "schema_version: 1\nname: s\ninvocation: model\ndescription: d\ninstructions: |\n  body\n"
)
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
        "schema_version: 1\nname: s\ninvocation: model\ndescription: d\n", encoding="utf-8"
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


def test_valid_technologies_pass(tmp_path: Path) -> None:
    """Vocabulary-conformant technologies on a skill and a fragment are clean."""
    root = _catalog(tmp_path)
    (root / ".basicly/core/skills/s/skill.yaml").write_text(
        "schema_version: 1\nname: s\ninvocation: model\ndescription: d\ntechnologies: [python]\n"
        "instructions: |\n  body\n",
        encoding="utf-8",
    )
    (root / ".basicly/core/fragments/project/f.fragment.yaml").write_text(
        VALID_FRAGMENT.replace("applies_to: [all]\n", "applies_to: [all]\ntechnologies: [zsh]\n"),
        encoding="utf-8",
    )
    assert lint_catalog(root) == []


def test_flags_unknown_technology(tmp_path: Path) -> None:
    """A technologies value outside the controlled vocabulary is a violation."""
    root = _catalog(tmp_path)
    (root / ".basicly/core/skills/s/skill.yaml").write_text(
        "schema_version: 1\nname: s\ninvocation: model\ndescription: d\ntechnologies: [cobol]\n"
        "instructions: |\n  body\n",
        encoding="utf-8",
    )
    violations = lint_catalog(root)
    assert any("unknown technologies: cobol" in v for v in violations)


def test_flags_unknown_technology_in_hooks_manifest(tmp_path: Path) -> None:
    """The hooks manifest participates in the vocabulary check (it has no schema)."""
    root = _catalog(tmp_path)
    hooks = root / ".basicly/core/hooks"
    hooks.mkdir(parents=True)
    (hooks / "hooks.yaml").write_text(
        "hooks:\n  - id: x\n    script: x.py\n    stage: pre-commit\n    technologies: [fortran]\n",
        encoding="utf-8",
    )
    violations = lint_catalog(root)
    assert any("unknown technologies: fortran" in v for v in violations)


def test_flags_skill_name_directory_mismatch(tmp_path: Path) -> None:
    """A skill whose name field differs from its directory is a violation (spec: name==dir)."""
    root = _catalog(tmp_path)
    skill = root / ".basicly/core/skills/mismatch/skill.yaml"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "schema_version: 1\nname: other\n"
        "invocation: model\ndescription: d\ninstructions: |\n  body\n",
        encoding="utf-8",
    )
    assert any("must match its directory" in v for v in lint_catalog(root))


def test_flags_invalid_skill_name(tmp_path: Path) -> None:
    """A name with uppercase/consecutive hyphens violates the Agent Skills naming rule."""
    root = _catalog(tmp_path)
    skill = root / ".basicly/core/skills/bad--name/skill.yaml"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "schema_version: 1\nname: bad--name\n"
        "invocation: model\ndescription: d\ninstructions: |\n  body\n",
        encoding="utf-8",
    )
    assert any("no leading, trailing, or consecutive hyphen" in v for v in lint_catalog(root))


def test_skill_body_over_limit_warns_but_does_not_fail(tmp_path: Path) -> None:
    """An oversized SKILL.md body is a warning (advisory), not a hard lint violation."""
    root = _catalog(tmp_path)
    body = "\n".join(f"  line {n}" for n in range(600))
    skill = root / ".basicly/core/skills/big/skill.yaml"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        f"schema_version: 1\nname: big\n"
        f"invocation: model\ndescription: d\ninstructions: |\n{body}\n",
        encoding="utf-8",
    )
    assert lint_catalog(root) == []  # not a hard failure
    assert any("keep it under" in w for w in skill_warnings(root))


def test_deep_file_reference_warns(tmp_path: Path) -> None:
    """A file reference more than one level deep is surfaced as a warning."""
    root = _catalog(tmp_path)
    skill = root / ".basicly/core/skills/refs/skill.yaml"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "schema_version: 1\nname: refs\ninvocation: model\ndescription: d\ninstructions: |\n"
        "  See references/sub/deep.md for details.\n",
        encoding="utf-8",
    )
    assert any("more than one level deep" in w for w in skill_warnings(root))


def test_one_level_markdown_link_does_not_warn(tmp_path: Path) -> None:
    """A normal one-level markdown link must not be misread as a two-level path."""
    root = _catalog(tmp_path)
    skill = root / ".basicly/core/skills/refs/skill.yaml"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "schema_version: 1\nname: refs\ninvocation: model\ndescription: d\ninstructions: |\n"
        "  See [the guide](references/guide.md) and run scripts/fix.sh.\n",
        encoding="utf-8",
    )
    assert not any("more than one level deep" in w for w in skill_warnings(root))


# --- Invocation axis (basicly-m4zv.1) -----------------------------------------


def _skill_source(root: Path, slug: str, body: str) -> Path:
    path = root / ".basicly/core/skills" / slug / "skill.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


_INSTRUCTIONS = "instructions: |\n  # x\n\n  text\n"


def test_a_missing_invocation_declaration_fails_the_lint(tmp_path: Path) -> None:
    """Until an entry knows whether anything can route to it, 2b is not well-posed."""
    root = _catalog(tmp_path)
    _skill_source(root, "undeclared", f"schema_version: 1\nname: undeclared\n{_INSTRUCTIONS}")

    violations = lint_catalog(root)

    assert any("'invocation' is a required property" in v for v in violations), violations


def test_an_unknown_invocation_value_fails_the_lint(tmp_path: Path) -> None:
    """The axis has exactly two positions; a third would be unenforceable."""
    root = _catalog(tmp_path)
    _skill_source(
        root,
        "bogus",
        f"schema_version: 1\nname: bogus\ninvocation: sometimes\ndescription: d\n{_INSTRUCTIONS}",
    )

    assert any("sometimes" in v for v in lint_catalog(root))


def test_a_user_invoked_entry_carrying_a_description_fails_the_lint(tmp_path: Path) -> None:
    """This is the waste the axis exists to find: context load bought for no reach."""
    root = _catalog(tmp_path)
    _skill_source(
        root,
        "handrun",
        f"schema_version: 1\nname: handrun\ninvocation: user\ndescription: d\n{_INSTRUCTIONS}",
    )

    violations = [v for v in lint_catalog(root) if "handrun" in v]

    assert any("must not carry a description" in v for v in violations), violations


def test_a_model_invoked_entry_without_a_description_fails_the_lint(tmp_path: Path) -> None:
    """A model-invoked entry with no description cannot be routed to, yet still costs a name."""
    root = _catalog(tmp_path)
    _skill_source(
        root, "silent", f"schema_version: 1\nname: silent\ninvocation: model\n{_INSTRUCTIONS}"
    )

    violations = [v for v in lint_catalog(root) if "silent" in v]

    assert any("needs a description" in v for v in violations), violations


def test_every_shipped_skill_declares_the_axis() -> None:
    """The core catalog is the first consumer of its own rule."""
    for path in sorted((REPO / ".basicly/core/skills").glob("*/skill.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data.get("invocation") in {"model", "user"}, f"{path.parent.name} has no axis"
