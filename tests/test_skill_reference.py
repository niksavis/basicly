from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import skill_reference
from tests.test_dispatch_brief import _project


def test_deferred_skill_refuses_changed_content_and_outside_paths(tmp_path: Path) -> None:
    _project(tmp_path, role="implementer", declares=["safe"], bodies={"safe": "first"})
    brief, _ = skill_reference.skill_brief(tmp_path, ["safe"])
    ref = json.loads(brief)["skills"][0]
    assert skill_reference.read_skill_reference(tmp_path, ref["path"], ref["sha256"]) == "first"
    (tmp_path / ref["path"]).write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        skill_reference.read_skill_reference(tmp_path, ref["path"], ref["sha256"])
    outside = tmp_path / "private.txt"
    outside.write_text("private", encoding="utf-8")
    with pytest.raises(ValueError, match="projected skill"):
        skill_reference.read_skill_reference(tmp_path, "private.txt", ref["sha256"])


def test_codex_skill_reference_uses_its_host_projection(tmp_path: Path) -> None:
    _project(tmp_path, role="implementer", declares=["safe"], bodies={"safe": "claude"})
    target = tmp_path / ".agents/skills/safe/SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("codex", encoding="utf-8")
    brief, missing = skill_reference.skill_brief(tmp_path, ["safe"], family="codex")
    ref = json.loads(brief)["skills"][0]
    assert not missing
    assert ref["path"] == ".agents/skills/safe/SKILL.md"
    assert skill_reference.read_skill_reference(tmp_path, ref["path"], ref["sha256"]) == "codex"


@pytest.mark.parametrize("name", ["../private", "/private", "..", "a/b", "a\\b"])
def test_declared_skill_names_cannot_escape_projection(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="skill name"):
        skill_reference.skill_brief(tmp_path, [name])


def test_deferred_skill_cannot_read_a_symlink_outside_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _project(repo, role="implementer", declares=[], bodies={})
    external = tmp_path / "external"
    external.mkdir()
    (external / "SKILL.md").write_text("outside", encoding="utf-8")
    (repo / ".claude/skills").mkdir()
    try:
        (repo / ".claude/skills/unsafe").symlink_to(external, target_is_directory=True)
    except OSError, NotImplementedError:
        pytest.skip("symlinks not available on this platform")
    with pytest.raises(ValueError, match="inside its host"):
        skill_reference.skill_brief(repo, ["unsafe"])


def test_large_skill_manifest_does_not_grow_with_its_body(tmp_path: Path) -> None:
    body = "Unique required procedure.\n" * 10000
    _project(tmp_path, role="implementer", declares=["large"], bodies={"large": body})
    brief, _ = skill_reference.skill_brief(tmp_path, ["large"])
    reference = json.loads(brief)["skills"][0]
    assert reference["bytes"] == len(body.encode("utf-8"))
    assert len(brief.encode("utf-8")) < 1500
    assert (
        skill_reference.read_skill_reference(tmp_path, reference["path"], reference["sha256"])
        == body
    )
