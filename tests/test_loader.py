"""Tests for the fragment and target loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly.loader import load_fragments, load_fragments_from_roots, load_targets
from basicly.schema import ValidationError

FIXTURES = Path(__file__).parent / "fixtures"


def _wf(path: Path, front: str, body: str = "body") -> None:
    """Write a fragment YAML source: front matter fields + a body block scalar."""
    block = "\n".join(["body: |"] + [f"  {ln}" if ln else "" for ln in body.split("\n")])
    path.write_text(front.rstrip("\n") + "\n" + block + "\n", encoding="utf-8")


def test_load_fragments() -> None:
    """All fixture fragments are loaded with correct ids."""
    fragments = load_fragments(FIXTURES, {"claude", "copilot"})
    ids = {f.id for f in fragments}
    assert ids == {
        "project-defaults",
        "core-rules",
        "python-style",
        "claude-defaults",
        "copilot-defaults",
    }


def test_fragment_fields() -> None:
    """Scoped and unscoped fragments are parsed correctly."""
    fragments = load_fragments(FIXTURES, {"claude", "copilot"})
    by_id = {f.id: f for f in fragments}
    assert by_id["python-style"].is_scoped is True
    assert by_id["python-style"].scope_paths == ["**/*.py"]
    assert by_id["project-defaults"].is_scoped is False


def test_missing_required_field(tmp_path: Path) -> None:
    """A fragment missing required fields raises ValidationError."""
    _wf(tmp_path / "bad.fragment.yaml", "id: bad")
    with pytest.raises(ValidationError):
        load_fragments(tmp_path, {"claude"})


def test_unknown_category(tmp_path: Path) -> None:
    """An unknown category value raises ValidationError."""
    _wf(
        tmp_path / "bad.fragment.yaml",
        "id: bad\ndescription: x\ncategory: not-a-category\napplies_to: [all]",
    )
    with pytest.raises(ValidationError):
        load_fragments(tmp_path, {"claude"})


def test_unknown_target_in_applies_to(tmp_path: Path) -> None:
    """An applies_to value that is not a registered target raises ValidationError."""
    _wf(
        tmp_path / "bad.fragment.yaml",
        "id: bad\ndescription: x\ncategory: project\napplies_to: [unknown]",
    )
    with pytest.raises(ValidationError):
        load_fragments(tmp_path, {"claude"})


def test_load_targets() -> None:
    """All fixture target registries are loaded."""
    targets = load_targets(FIXTURES / "targets")
    names = {t.name for t in targets}
    assert names == {"claude", "copilot"}


def test_extension_fields_default_to_safe_values() -> None:
    """Fragments without extension fields get safe defaults."""
    fragments = load_fragments(FIXTURES, {"claude", "copilot"})
    by_id = {f.id: f for f in fragments}
    fragment = by_id["python-style"]
    assert fragment.source == "core"
    assert fragment.override is False
    assert fragment.replaces == []
    assert fragment.extends == []


def test_extension_fields_are_parsed(tmp_path: Path) -> None:
    """Extension fields are loaded when present."""
    _wf(
        tmp_path / "core.fragment.yaml",
        "id: python-style\ndescription: Core style\ncategory: code-style\napplies_to: [all]",
        "core",
    )
    _wf(
        tmp_path / "user.fragment.yaml",
        "id: user-style\ndescription: User style\ncategory: code-style\napplies_to: [all]\n"
        "source: user\noverride: true\nreplaces: [python-style]\nextends: [project-defaults]",
    )
    fragments = load_fragments(tmp_path, {"claude"})
    by_id = {f.id: f for f in fragments}
    f = by_id["user-style"]
    assert f.source == "user"
    assert f.override is True
    assert f.replaces == ["python-style"]
    assert f.extends == ["project-defaults"]


def test_invalid_source_value(tmp_path: Path) -> None:
    """An invalid source value raises ValidationError."""
    _wf(
        tmp_path / "bad.fragment.yaml",
        "id: bad\ndescription: x\ncategory: project\napplies_to: [all]\nsource: invalid",
    )
    with pytest.raises(ValidationError):
        load_fragments(tmp_path, {"claude"})


def test_replaces_must_be_string_list(tmp_path: Path) -> None:
    """A non-list replaces value raises ValidationError."""
    _wf(
        tmp_path / "bad.fragment.yaml",
        "id: bad\ndescription: x\ncategory: project\napplies_to: [all]\nreplaces: not-a-list",
    )
    with pytest.raises(ValidationError):
        load_fragments(tmp_path, {"claude"})


def test_load_from_core_and_overlay_roots(tmp_path: Path) -> None:
    """Fragments from multiple roots are loaded with inferred source values."""
    core_root = tmp_path / ".basicly" / "core" / "fragments"
    overlay_root = tmp_path / ".basicly-local" / "fragments"
    core_root.mkdir(parents=True)
    overlay_root.mkdir(parents=True)

    _wf(
        core_root / "core.fragment.yaml",
        "id: core-rule\ndescription: Core\ncategory: project\napplies_to: [all]",
        "core",
    )
    _wf(
        overlay_root / "user.fragment.yaml",
        "id: user-rule\ndescription: User\ncategory: project\napplies_to: [all]",
        "user",
    )

    fragments = load_fragments_from_roots(
        [(core_root, "core"), (overlay_root, "user")],
        {"claude"},
    )
    by_id = {f.id: f for f in fragments}

    assert by_id["core-rule"].source == "core"
    assert by_id["user-rule"].source == "user"


def test_replaces_missing_override_is_rejected(tmp_path: Path) -> None:
    """A fragment that lists replaces without override: true is a hard error."""
    _wf(
        tmp_path / "core.fragment.yaml",
        "id: base\ndescription: x\ncategory: project\napplies_to: [all]",
    )
    _wf(
        tmp_path / "user.fragment.yaml",
        "id: repl\ndescription: x\ncategory: project\napplies_to: [all]\n"
        "source: user\nreplaces: [base]",
    )
    with pytest.raises(ValidationError, match="override: true"):
        load_fragments(tmp_path, {"claude"})


def test_replaces_unknown_target_is_rejected(tmp_path: Path) -> None:
    """A replaces id that no loaded fragment defines is a hard error."""
    _wf(
        tmp_path / "user.fragment.yaml",
        "id: repl\ndescription: x\ncategory: project\napplies_to: [all]\n"
        "source: user\noverride: true\nreplaces: [does-not-exist]",
    )
    with pytest.raises(ValidationError, match="unknown fragment id 'does-not-exist'"):
        load_fragments(tmp_path, {"claude"})


def test_mutual_user_replace_is_rejected(tmp_path: Path) -> None:
    """Two user fragments replacing each other is a hard error."""
    _wf(
        tmp_path / "a.fragment.yaml",
        "id: frag-a\ndescription: x\ncategory: project\napplies_to: [all]\n"
        "source: user\noverride: true\nreplaces: [frag-b]",
    )
    _wf(
        tmp_path / "b.fragment.yaml",
        "id: frag-b\ndescription: x\ncategory: project\napplies_to: [all]\n"
        "source: user\noverride: true\nreplaces: [frag-a]",
    )
    with pytest.raises(ValidationError, match="mutual replace"):
        load_fragments(tmp_path, {"claude"})


def test_valid_user_replace_of_core_is_accepted(tmp_path: Path) -> None:
    """A well-formed user replacement of an existing core fragment loads cleanly."""
    _wf(
        tmp_path / "core.fragment.yaml",
        "id: base\ndescription: x\ncategory: project\napplies_to: [all]",
    )
    _wf(
        tmp_path / "user.fragment.yaml",
        "id: repl\ndescription: x\ncategory: project\napplies_to: [all]\n"
        "source: user\noverride: true\nreplaces: [base]",
    )
    fragments = load_fragments(tmp_path, {"claude"})
    assert {f.id for f in fragments} == {"base", "repl"}
