from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import catalog_source

if TYPE_CHECKING:
    from jsonschema import Draft202012Validator

SCHEMA = {
    "type": "object",
    "required": ["name", "description"],
    "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
}


def _validator(root: Path) -> Draft202012Validator:
    schemas = root / catalog_source.SCHEMAS_DIR
    schemas.mkdir(parents=True, exist_ok=True)
    (schemas / "thing.schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
    return catalog_source.schema_validator(root, "thing.schema.json")


def _source(root: Path, content: str) -> Path:
    path = root / catalog_source.SKILLS_DIR / "s" / "skill.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_a_path_inside_the_repo_is_reported_relative_and_posix(tmp_path: Path) -> None:

    assert catalog_source.rel(tmp_path / "a" / "b.yaml", tmp_path) == "a/b.yaml"


def test_a_path_outside_the_repo_is_left_alone(tmp_path: Path) -> None:

    outside = tmp_path.parent / "elsewhere" / "x.yaml"

    assert catalog_source.rel(outside, tmp_path) == str(outside)


def test_the_directory_constants_all_sit_under_the_core_tree() -> None:
    roots = (
        catalog_source.SKILLS_DIR,
        catalog_source.FRAGMENTS_DIR,
        catalog_source.AGENTS_DIR,
        catalog_source.HOOKS_DIR,
        catalog_source.RUBRICS_DIR,
        catalog_source.SCHEMAS_DIR,
    )

    assert all(catalog_source.CORE_DIR in root.parents for root in roots)


def test_a_valid_source_has_no_violations(tmp_path: Path) -> None:
    validator = _validator(tmp_path)
    path = _source(tmp_path, "name: s\ndescription: d\n")

    assert catalog_source.schema_violations(path, validator, tmp_path) == []


def test_a_schema_violation_is_reported_as_a_repo_relative_line(tmp_path: Path) -> None:
    validator = _validator(tmp_path)
    path = _source(tmp_path, "name: 1\ndescription: d\n")

    violations = catalog_source.schema_violations(path, validator, tmp_path)

    assert len(violations) == 1, violations
    assert violations[0].startswith(".basicly/core/skills/s/skill.yaml: ")
    assert str(tmp_path) not in violations[0]


def test_unparseable_yaml_is_a_violation_and_not_a_traceback(tmp_path: Path) -> None:
    validator = _validator(tmp_path)
    path = _source(tmp_path, "name: [unclosed\n")

    violations = catalog_source.schema_violations(path, validator, tmp_path)

    assert len(violations) == 1, violations
    assert "invalid YAML" in violations[0]


def test_an_owned_required_property_is_left_to_the_check_that_owns_it(tmp_path: Path) -> None:

    validator = _validator(tmp_path)
    path = _source(tmp_path, "name: 1\n")

    violations = catalog_source.schema_violations(
        path, validator, tmp_path, owned_required=frozenset({"description"})
    )

    assert len(violations) == 1, violations
    assert "'description'" not in violations[0]


def test_a_wrong_value_for_an_owned_property_still_reports(tmp_path: Path) -> None:
    validator = _validator(tmp_path)
    path = _source(tmp_path, "name: s\ndescription: 7\n")

    violations = catalog_source.schema_violations(
        path, validator, tmp_path, owned_required=frozenset({"description"})
    )

    assert len(violations) == 1, violations


def test_load_mapping_returns_the_mapping(tmp_path: Path) -> None:
    assert catalog_source.load_mapping(_source(tmp_path, "name: s\n")) == {"name": "s"}


def test_load_mapping_is_silent_on_a_source_already_reported_by_name(tmp_path: Path) -> None:

    assert catalog_source.load_mapping(_source(tmp_path, "name: [unclosed\n")) is None
    assert catalog_source.load_mapping(_source(tmp_path, "- not a mapping\n")) is None
