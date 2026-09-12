from __future__ import annotations

from pathlib import Path

import pytest

from basicly.loader import load_fragments, load_fragments_from_roots, load_targets
from basicly.schema import ValidationError

FIXTURES = Path(__file__).parent / "fixtures"


def _wf(path: Path, front: str, body: str = "body") -> None:
    block = "\n".join(["body: |"] + [f"  {ln}" if ln else "" for ln in body.split("\n")])
    path.write_text(front.rstrip("\n") + "\n" + block + "\n", encoding="utf-8")


def test_load_fragments() -> None:
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
    fragments = load_fragments(FIXTURES, {"claude", "copilot"})
    by_id = {f.id: f for f in fragments}
    assert by_id["python-style"].is_scoped is True
    assert by_id["python-style"].scope_paths == ["**/*.py"]
    assert by_id["project-defaults"].is_scoped is False


def test_missing_required_field(tmp_path: Path) -> None:
    _wf(tmp_path / "bad.fragment.yaml", "id: bad")
    with pytest.raises(ValidationError):
        load_fragments(tmp_path, {"claude"})


def test_unknown_category(tmp_path: Path) -> None:
    _wf(
        tmp_path / "bad.fragment.yaml",
        "id: bad\ndescription: x\ncategory: not-a-category\napplies_to: [all]",
    )
    with pytest.raises(ValidationError):
        load_fragments(tmp_path, {"claude"})


def test_quirks_category_loads(tmp_path: Path) -> None:
    _wf(
        tmp_path / "quirks.fragment.yaml",
        "id: quirks\ndescription: x\ncategory: quirks\napplies_to: [all]",
    )
    fragments = load_fragments(tmp_path, {"claude"})
    assert [f.category for f in fragments] == ["quirks"]


def test_unknown_target_in_applies_to(tmp_path: Path) -> None:
    _wf(
        tmp_path / "bad.fragment.yaml",
        "id: bad\ndescription: x\ncategory: project\napplies_to: [unknown]",
    )
    with pytest.raises(ValidationError):
        load_fragments(tmp_path, {"claude"})


def test_the_unknown_target_error_names_the_set_and_the_field_that_wanted_it(
    tmp_path: Path,
) -> None:

    _wf(
        tmp_path / "bad.fragment.yaml",
        "id: bad\ndescription: x\ncategory: project\napplies_to: [rules]",
    )
    with pytest.raises(ValidationError) as caught:
        load_fragments(tmp_path, {"claude", "copilot"})

    message = str(caught.value)
    assert "registered: all, claude, copilot" in message
    assert "tags: [rules]" in message


def test_load_targets() -> None:
    targets = load_targets(FIXTURES / "targets")
    names = {t.name for t in targets}
    assert names == {"claude", "copilot"}


def test_extension_fields_default_to_safe_values() -> None:
    fragments = load_fragments(FIXTURES, {"claude", "copilot"})
    by_id = {f.id: f for f in fragments}
    fragment = by_id["python-style"]
    assert fragment.source == "core"
    assert fragment.override is False
    assert fragment.replaces == []
    assert fragment.extends == []


def test_extension_fields_are_parsed(tmp_path: Path) -> None:
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


def test_enforced_by_defaults_to_empty() -> None:
    fragments = load_fragments(FIXTURES, {"claude", "copilot"})
    by_id = {f.id: f for f in fragments}
    assert by_id["python-style"].enforced_by == []


def test_enforced_by_is_parsed(tmp_path: Path) -> None:
    _wf(
        tmp_path / "styled.fragment.yaml",
        "id: styled\ndescription: x\ncategory: code-style\napplies_to: [all]\n"
        "enforced_by: [ruff format]",
    )
    fragments = load_fragments(tmp_path, {"claude"})
    by_id = {f.id: f for f in fragments}
    assert by_id["styled"].enforced_by == ["ruff format"]


def test_enforced_by_must_be_string_list(tmp_path: Path) -> None:
    _wf(
        tmp_path / "bad.fragment.yaml",
        "id: bad\ndescription: x\ncategory: project\napplies_to: [all]\nenforced_by: ruff",
    )
    with pytest.raises(ValidationError):
        load_fragments(tmp_path, {"claude"})


def test_invalid_source_value(tmp_path: Path) -> None:
    _wf(
        tmp_path / "bad.fragment.yaml",
        "id: bad\ndescription: x\ncategory: project\napplies_to: [all]\nsource: invalid",
    )
    with pytest.raises(ValidationError):
        load_fragments(tmp_path, {"claude"})


def test_replaces_must_be_string_list(tmp_path: Path) -> None:
    _wf(
        tmp_path / "bad.fragment.yaml",
        "id: bad\ndescription: x\ncategory: project\napplies_to: [all]\nreplaces: not-a-list",
    )
    with pytest.raises(ValidationError):
        load_fragments(tmp_path, {"claude"})


def test_load_from_core_and_overlay_roots(tmp_path: Path) -> None:
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


def test_source_inference_ignores_a_user_component_in_the_checkout_path(tmp_path: Path) -> None:

    root = tmp_path / "home" / "user" / "checkout" / ".basicly" / "fragments"
    root.mkdir(parents=True)
    _wf(
        root / "core.fragment.yaml",
        "id: core-rule\ndescription: Core\ncategory: project\napplies_to: [all]",
    )

    fragments = load_fragments(root, {"claude"})

    assert [f.source for f in fragments] == ["core"]


def test_source_inference_reads_the_legacy_user_subdir_of_a_root(tmp_path: Path) -> None:

    root = tmp_path / "user" / ".basicly" / "fragments"
    (root / "user").mkdir(parents=True)
    _wf(
        root / "user" / "overlay.fragment.yaml",
        "id: user-rule\ndescription: User\ncategory: project\napplies_to: [all]",
    )

    fragments = load_fragments(root, {"claude"})

    assert [f.source for f in fragments] == ["user"]


def test_source_inference_reads_the_overlay_marker_root(tmp_path: Path) -> None:
    root = tmp_path / "user" / ".basicly-local" / "fragments"
    root.mkdir(parents=True)
    _wf(
        root / "overlay.fragment.yaml",
        "id: user-rule\ndescription: User\ncategory: project\napplies_to: [all]",
    )

    fragments = load_fragments(root, {"claude"})

    assert [f.source for f in fragments] == ["user"]


def test_replaces_missing_override_is_rejected(tmp_path: Path) -> None:
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


def test_user_replaces_of_a_removed_core_id_warns_and_loads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    _wf(
        tmp_path / "user.fragment.yaml",
        "id: repl\ndescription: x\ncategory: project\napplies_to: [all]\n"
        "source: user\noverride: true\nreplaces: [does-not-exist]",
    )
    fragments = load_fragments(tmp_path, {"claude"})
    assert [f.id for f in fragments] == ["repl"]
    err = capsys.readouterr().err
    assert "replaces unknown id 'does-not-exist'" in err


def test_core_replaces_unknown_target_is_rejected(tmp_path: Path) -> None:
    _wf(
        tmp_path / "core.fragment.yaml",
        "id: repl\ndescription: x\ncategory: project\napplies_to: [all]\n"
        "source: core\noverride: true\nreplaces: [does-not-exist]",
    )
    with pytest.raises(ValidationError, match="unknown fragment id 'does-not-exist'"):
        load_fragments(tmp_path, {"claude"})


def test_source_newer_schema_version_is_rejected(tmp_path: Path) -> None:
    _wf(
        tmp_path / "future.fragment.yaml",
        "schema_version: 99\nid: fut\ndescription: x\ncategory: project\napplies_to: [all]",
    )
    with pytest.raises(ValidationError, match="upgrade basicly"):
        load_fragments(tmp_path, {"claude"})


def test_mutual_user_replace_is_rejected(tmp_path: Path) -> None:
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


def test_legacy_md_fragment_warns_but_loads_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    _wf(
        tmp_path / "kept.fragment.yaml",
        "id: kept\ndescription: x\ncategory: project\napplies_to: [all]",
    )
    (tmp_path / "old.fragment.md").write_text(
        "---\nid: old\ndescription: legacy\n---\n\nbody\n", encoding="utf-8"
    )

    fragments = load_fragments(tmp_path, {"claude"})

    assert {f.id for f in fragments} == {"kept"}
    err = capsys.readouterr().err
    assert "old.fragment.md" in err
    assert "catalog new fragment" in err
