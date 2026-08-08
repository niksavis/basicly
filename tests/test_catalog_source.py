"""Reading a catalog source: where it lives, and what comes back when it will not load.

Split out of `test_catalog_lint.py` when the §9.4 naming gate was made binding
(basicly-u2hl.14), along the same boundary the module draws: *reading* against *ruling*.
Nothing here knows an invariant a catalog must satisfy — the tests that do stay with
`lint_catalog`, including the one that drives the `path: message` guarantee end-to-end
through a real lint run.

The schemas are written per test rather than copied from `.basicly/core/schemas`: the
question is what a validator does with a violation, and a two-property schema states the
case where the shipped one leaves it to be inferred.
"""

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
    """Write :data:`SCHEMA` where the module looks for it, and build its validator."""
    schemas = root / catalog_source.SCHEMAS_DIR
    schemas.mkdir(parents=True, exist_ok=True)
    (schemas / "thing.schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
    return catalog_source.schema_validator(root, "thing.schema.json")


def _source(root: Path, content: str) -> Path:
    """A catalog source on disk, under the core tree the constants name."""
    path = root / catalog_source.SKILLS_DIR / "s" / "skill.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# --- One run must spell one source one way (basicly-ky5z) ----------------------


def test_a_path_inside_the_repo_is_reported_relative_and_posix(tmp_path: Path) -> None:
    """`as_posix`, so the same source reads the same way on every platform.

    Asserted against a literal rather than against `path.relative_to(...)`, which would
    agree with the implementation by construction and pass if both used backslashes.
    """
    assert catalog_source.rel(tmp_path / "a" / "b.yaml", tmp_path) == "a/b.yaml"


def test_a_path_outside_the_repo_is_left_alone(tmp_path: Path) -> None:
    """There is no relative spelling to fall back to, so the absolute one is the answer.

    Both sides render through `str`, so the assertion holds on a flavour whose separator
    is not `/` — the code under test is right there and a POSIX literal would not be.
    """
    outside = tmp_path.parent / "elsewhere" / "x.yaml"

    assert catalog_source.rel(outside, tmp_path) == str(outside)


# --- The readers ---------------------------------------------------------------


def test_the_directory_constants_all_sit_under_the_core_tree() -> None:
    """A source root outside `.basicly/core` would be read by a gate and shipped by none."""
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
    """The positive control: without it a reader that reports everything looks correct."""
    validator = _validator(tmp_path)
    path = _source(tmp_path, "name: s\ndescription: d\n")

    assert catalog_source.schema_violations(path, validator, tmp_path) == []


def test_a_schema_violation_is_reported_as_a_repo_relative_line(tmp_path: Path) -> None:
    """`path: message`, with the path spelled by `rel` and not by the validator."""
    validator = _validator(tmp_path)
    path = _source(tmp_path, "name: 1\ndescription: d\n")

    violations = catalog_source.schema_violations(path, validator, tmp_path)

    assert len(violations) == 1, violations
    assert violations[0].startswith(".basicly/core/skills/s/skill.yaml: ")
    assert str(tmp_path) not in violations[0]


def test_unparseable_yaml_is_a_violation_and_not_a_traceback(tmp_path: Path) -> None:
    """A malformed source is a finding the gate reports, not a crash that hides the rest."""
    validator = _validator(tmp_path)
    path = _source(tmp_path, "name: [unclosed\n")

    violations = catalog_source.schema_violations(path, validator, tmp_path)

    assert len(violations) == 1, violations
    assert "invalid YAML" in violations[0]


def test_an_owned_required_property_is_left_to_the_check_that_owns_it(tmp_path: Path) -> None:
    """One defect, one diagnostic: the raw jsonschema line for it is dropped.

    Shaped the way `catalog_lint` calls it — one owned property absent — with the
    positive control in the same call: `name` carries a wrong *value*, and that must
    still report, or a reader suppressing everything would look correct here.
    """
    validator = _validator(tmp_path)
    path = _source(tmp_path, "name: 1\n")

    violations = catalog_source.schema_violations(
        path, validator, tmp_path, owned_required=frozenset({"description"})
    )

    assert len(violations) == 1, violations
    assert "'description'" not in violations[0]


def test_a_wrong_value_for_an_owned_property_still_reports(tmp_path: Path) -> None:
    """Only *absence* is delegated. Suppressing the value error would lose the defect."""
    validator = _validator(tmp_path)
    path = _source(tmp_path, "name: s\ndescription: 7\n")

    violations = catalog_source.schema_violations(
        path, validator, tmp_path, owned_required=frozenset({"description"})
    )

    assert len(violations) == 1, violations


def test_load_mapping_returns_the_mapping(tmp_path: Path) -> None:
    """The ordinary case, so the two `None` answers below are told apart from it."""
    assert catalog_source.load_mapping(_source(tmp_path, "name: s\n")) == {"name": "s"}


def test_load_mapping_is_silent_on_a_source_already_reported_by_name(tmp_path: Path) -> None:
    """Malformed, and not a mapping, both answer `None`.

    Raising here would replace the `path: message` line `schema_violations` already
    produced with a traceback that names the reader instead of the source.
    """
    assert catalog_source.load_mapping(_source(tmp_path, "name: [unclosed\n")) is None
    assert catalog_source.load_mapping(_source(tmp_path, "- not a mapping\n")) is None
