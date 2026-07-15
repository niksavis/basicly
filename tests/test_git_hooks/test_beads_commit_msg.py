from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_beads_commit_msg_module():
    """Load the beads-commit-msg hook module from its script path."""
    script_path = (
        Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "beads-commit-msg.py"
    )
    spec = importlib.util.spec_from_file_location("beads_commit_msg_hook", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_accepts_known_issue_id() -> None:
    """A message referencing a known issue id should pass."""
    module = _load_beads_commit_msg_module()
    known_ids = {"basicly-idr"}
    is_valid, error = module.validate("feat(basicly): add hook (basicly-idr)", known_ids)
    assert is_valid
    assert error == ""


def test_validate_rejects_missing_issue_id() -> None:
    """A message with no issue-id-shaped token should fail."""
    module = _load_beads_commit_msg_module()
    is_valid, error = module.validate("feat(basicly): add hook", {"basicly-idr"})
    assert not is_valid
    assert "does not reference a beads issue id" in error


def test_validate_rejects_unknown_issue_id() -> None:
    """A message referencing an id absent from known ids should fail."""
    module = _load_beads_commit_msg_module()
    is_valid, error = module.validate("feat(basicly): add hook (basicly-zzz)", {"basicly-idr"})
    assert not is_valid
    assert "unknown beads issue id" in error


def test_validate_skips_check_without_beads_workspace() -> None:
    """When known_ids is None (no .beads workspace), any candidate id passes."""
    module = _load_beads_commit_msg_module()
    is_valid, error = module.validate("feat(basicly): add hook (basicly-idr)", None)
    assert is_valid
    assert error == ""


def test_validate_skips_plain_message_without_beads_workspace() -> None:
    """Without a workspace, a message with no issue id at all passes.

    Regression (basicly-zrj.13.1): the no-candidates rejection used to run
    before the workspace check, blocking every commit in beads-less consumers.
    """
    module = _load_beads_commit_msg_module()
    is_valid, error = module.validate("feat(basicly): add hook", None)
    assert is_valid
    assert error == ""


def test_validate_allows_merge_and_revert_subjects() -> None:
    """Merge and auto-generated revert subjects should be allowed."""
    module = _load_beads_commit_msg_module()
    assert module.validate("Merge branch 'main' into feature", {"basicly-idr"})[0]
    assert module.validate('Revert "bad commit"', {"basicly-idr"})[0]


def test_load_known_issue_ids_reads_jsonl(tmp_path: Path, monkeypatch) -> None:
    """_load_known_issue_ids should parse ids from a .beads/issues.jsonl file."""
    module = _load_beads_commit_msg_module()
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    issues_jsonl = beads_dir / "issues.jsonl"
    issues_jsonl.write_text(
        '{"id":"proj-abc","title":"x"}\n{"id":"proj-def","title":"y"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "ISSUES_JSONL", issues_jsonl)
    assert module._load_known_issue_ids() == {"proj-abc", "proj-def"}


def test_load_known_issue_ids_returns_none_without_workspace(tmp_path: Path, monkeypatch) -> None:
    """_load_known_issue_ids should return None when no issues.jsonl exists."""
    module = _load_beads_commit_msg_module()
    monkeypatch.setattr(module, "ISSUES_JSONL", tmp_path / ".beads" / "issues.jsonl")
    assert module._load_known_issue_ids() is None
