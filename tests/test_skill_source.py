"""A skill as its author wrote it: `skill.yaml` loaded, validated, or refused by field.

Moved out of `test_skills.py` when the §9.4 naming gate was made binding
(basicly-u2hl.14), along the boundary the module draws — *authored form* against
*projected form*. Everything that renders, syncs or mirrors a `SkillDefinition` onto
disk stays with `skills`; what is here is which sources load, and which field a refusal
names.

Asserted through :mod:`basicly.skill_source` and, for the names `skills` re-exports,
through that module too: every caller in the tree imports `discover_skills` and
`SKILLS_SOURCE_DIR` from `skills`, so a split that dropped a re-export would break
callers while this file stayed green.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import skill_source, skills
from basicly.schema import ValidationError
from basicly.skill_source import SKILLS_SOURCE_DIR, discover_skills

MINIMAL = "schema_version: 1\nname: {slug}\ninvocation: model\ndescription: d\n"
BODY = "instructions: |\n  # {slug}\n\n  ## When To Use\n  - Example.\n"


def _source(repo_root: Path, slug: str, content: str) -> Path:
    """Write one skill source under the collection directory."""
    path = repo_root / SKILLS_SOURCE_DIR / slug / skill_source.SKILL_SOURCE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _valid(repo_root: Path, slug: str, *extra: str) -> Path:
    """A source that loads, optionally carrying *extra* lines before the body."""
    return _source(
        repo_root, slug, MINIMAL.format(slug=slug) + "".join(extra) + BODY.format(slug=slug)
    )


# --- what loads ----------------------------------------------------------------


def test_a_source_loads_its_name_description_and_instructions(tmp_path: Path) -> None:
    """The positive control every refusal below is measured against."""
    _valid(tmp_path, "tool-ripgrep")

    skills_found = discover_skills(tmp_path)

    assert [skill.slug for skill in skills_found] == ["tool-ripgrep"]
    assert skills_found[0].name == "tool-ripgrep"
    assert skills_found[0].description == "d"
    assert skills_found[0].instructions.startswith("# tool-ripgrep")


def test_a_repo_with_no_collection_directory_yields_nothing(tmp_path: Path) -> None:
    """A consumer that installed no catalog is not a validation failure."""
    assert discover_skills(tmp_path) == []


def test_sources_come_back_in_slug_order(tmp_path: Path) -> None:
    """Ordered, because the projector's output and every gate's diff depend on it."""
    for slug in ("zulu", "alpha", "mike"):
        _valid(tmp_path, slug)

    assert [skill.slug for skill in discover_skills(tmp_path)] == ["alpha", "mike", "zulu"]


def test_the_source_dir_is_the_directory_holding_the_yaml(tmp_path: Path) -> None:
    """The projector copies the bundle from here, so it must be the parent, not the file."""
    path = _valid(tmp_path, "bundled")

    (skill,) = discover_skills(tmp_path)

    assert skill.source_dir == path.parent


def test_a_bundled_agent_skills_layout_is_left_to_the_projector(tmp_path: Path) -> None:
    """`scripts/`, `references/`, `assets/` are copied verbatim and validated nowhere here."""
    _valid(tmp_path, "bundled")
    bundle = tmp_path / SKILLS_SOURCE_DIR / "bundled" / "references"
    bundle.mkdir()
    (bundle / "REF.md").write_text("not a skill source\n", encoding="utf-8")

    assert [skill.slug for skill in discover_skills(tmp_path)] == ["bundled"]


def test_a_stray_markdown_source_is_not_discovered(tmp_path: Path) -> None:
    """The source is non-discoverable on purpose (architecture §4.2).

    A broadly-scanning agent must not be able to load the catalog source as a second
    copy of the skill, which is only true while nothing loads a `.md` here either.
    """
    md = tmp_path / SKILLS_SOURCE_DIR / "legacy" / "SKILL.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("---\nname: legacy\ndescription: d\n---\n\nbody\n", encoding="utf-8")

    assert discover_skills(tmp_path) == []


# --- what is refused, and by which field ---------------------------------------


@pytest.mark.parametrize("field", ["name", "instructions"])
def test_a_missing_required_field_names_itself(tmp_path: Path, field: str) -> None:
    """The message names the field, because the repair is editing that line."""
    lines = ["schema_version: 1", "name: x", "invocation: model", "instructions: |\n  # x"]
    kept = [line for line in lines if not line.startswith(f"{field}:")]
    _source(tmp_path, "x", "\n".join(kept) + "\n")

    with pytest.raises(ValidationError, match=f"'{field}'"):
        discover_skills(tmp_path)


def test_an_unknown_invocation_value_is_refused(tmp_path: Path) -> None:
    """A third position on a two-position axis would be unenforceable downstream."""
    _source(
        tmp_path,
        "odd",
        "schema_version: 1\nname: odd\ninvocation: occasionally\ninstructions: |\n  # x\n",
    )

    with pytest.raises(ValidationError, match="must be one of"):
        discover_skills(tmp_path)


def test_a_user_invoked_source_needs_no_description(tmp_path: Path) -> None:
    """The loader cannot require a description it is the point of this entry not to have.

    The pairing rule — model needs one, user must not have one — is `catalog_lint`'s, so
    that the failure can explain itself; the loader only has to admit the shape.
    """
    _source(
        tmp_path,
        "handrun",
        "schema_version: 1\nname: handrun\ninvocation: user\ninstructions: |\n  # x\n",
    )

    (skill,) = discover_skills(tmp_path)

    assert skill.invocation == skill_source.USER_INVOKED
    assert skill.description == ""


def test_unparseable_yaml_names_the_file_rather_than_raising_a_yaml_error(
    tmp_path: Path,
) -> None:
    """A caller catching `ValidationError` must not also have to catch yaml's."""
    _source(tmp_path, "broken", "name: [unclosed\n")

    with pytest.raises(ValidationError, match="invalid YAML"):
        discover_skills(tmp_path)


def test_a_source_that_is_not_a_mapping_is_refused(tmp_path: Path) -> None:
    """Valid YAML that is not a skill; the loader must not index into a list."""
    _source(tmp_path, "listy", "- not a mapping\n")

    with pytest.raises(ValidationError, match="must be a YAML mapping"):
        discover_skills(tmp_path)


# --- the optional spec fields ---------------------------------------------------


def test_the_optional_spec_fields_load_when_present(tmp_path: Path) -> None:
    """They round-trip into the projected frontmatter; here, only that they parse."""
    _valid(
        tmp_path,
        "pdf",
        "license: Apache-2.0\n",
        "compatibility: Requires Python 3.14+ and uv\n",
        "allowed-tools: Bash(git:*) Read\n",
        'metadata:\n  author: example-org\n  version: "1.0"\n',
    )

    (skill,) = discover_skills(tmp_path)

    assert skill.license == "Apache-2.0"
    assert skill.compatibility == "Requires Python 3.14+ and uv"
    assert skill.allowed_tools == "Bash(git:*) Read"
    assert skill.metadata == (("author", "example-org"), ("version", "1.0"))


def test_omitting_the_optional_fields_yields_the_minimal_definition(tmp_path: Path) -> None:
    """`None`, not `""`: the renderer emits a key for one and omits it for the other."""
    _valid(tmp_path, "bare")

    (skill,) = discover_skills(tmp_path)

    assert (skill.license, skill.compatibility, skill.allowed_tools) == (None, None, None)
    assert skill.metadata == ()


def test_an_empty_optional_field_is_refused_rather_than_read_as_absent(tmp_path: Path) -> None:
    """Present-and-blank is an authoring mistake; treating it as absent would hide it."""
    _valid(tmp_path, "blank", 'license: ""\n')

    with pytest.raises(ValidationError, match="'license'"):
        discover_skills(tmp_path)


def test_a_compatibility_string_past_the_cap_is_refused(tmp_path: Path) -> None:
    """The spec caps it, so the failure belongs at load rather than at the consumer."""
    _valid(tmp_path, "verbose", f"compatibility: {'x' * 501}\n")

    with pytest.raises(ValidationError, match="exceeds 500 characters"):
        discover_skills(tmp_path)


def test_a_non_string_metadata_value_names_the_key_and_the_repair(tmp_path: Path) -> None:
    """The spec is string-to-string, and `version: 1.0` is the mistake people make.

    YAML types it as a float, so the message says to quote it rather than only that it
    is wrong.
    """
    _valid(tmp_path, "typed", "metadata:\n  version: 1.0\n")

    with pytest.raises(ValidationError, match="quote numbers"):
        discover_skills(tmp_path)


def test_metadata_that_is_not_a_mapping_is_refused(tmp_path: Path) -> None:
    """The spec is a map of string keys to string values, and a list is neither."""
    _valid(tmp_path, "listy", "metadata:\n  - author\n")

    with pytest.raises(ValidationError, match="must be a mapping"):
        discover_skills(tmp_path)


# --- what `skills` re-exports ---------------------------------------------------


def test_skills_re_exports_the_loader_every_caller_imports() -> None:
    """One object, so patching `skills.discover_skills` patches the loader that runs."""
    assert skills.discover_skills is skill_source.discover_skills
    assert skills.SkillDefinition is skill_source.SkillDefinition
    assert skills.SKILLS_SOURCE_DIR is skill_source.SKILLS_SOURCE_DIR
