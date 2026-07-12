from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_commit_msg_module():
    """Load the commit-msg hook module from its script path."""
    script_path = (
        Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "commit-msg.py"
    )
    spec = importlib.util.spec_from_file_location("commit_msg_hook", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_accepts_valid_conventional_message() -> None:
    """A valid conventional subject should pass validation."""
    module = _load_commit_msg_module()
    assert module.validate("chore(basicly): update generated manifest")


def test_validate_rejects_invalid_type_scope_and_trailing_punctuation() -> None:
    """A malformed type/scope and punctuation should fail validation."""
    module = _load_commit_msg_module()
    assert not module.validate("chote(word description): message;")


def test_validate_rejects_scope_with_spaces() -> None:
    """Scopes containing spaces should fail validation."""
    module = _load_commit_msg_module()
    assert not module.validate("chore(word description): message")


def test_validate_rejects_uppercase_description_start() -> None:
    """Descriptions that start uppercase should fail validation."""
    module = _load_commit_msg_module()
    assert not module.validate("chore(scope): Message")


def test_validate_rejects_too_short_description() -> None:
    """Descriptions shorter than the configured minimum should fail."""
    module = _load_commit_msg_module()
    assert not module.validate("fix: ab")


def test_validate_allows_merge_and_revert_subjects() -> None:
    """Merge and auto-generated revert subjects should be allowed."""
    module = _load_commit_msg_module()
    assert module.validate("Merge branch 'main' into feature")
    assert module.validate('Revert "bad commit"')


def test_validate_allows_trailing_beads_issue_id() -> None:
    """A single trailing beads issue id parenthetical should be allowed."""
    module = _load_commit_msg_module()
    assert module.validate("feat(basicly): add fragment loader (basicly-idr)")


def test_validate_allows_multiple_trailing_beads_issue_ids() -> None:
    """Multiple comma-separated trailing beads issue ids should be allowed."""
    module = _load_commit_msg_module()
    assert module.validate("fix: correct sorting order (basicly-idr, basicly-abc)")


def test_validate_rejects_malformed_trailing_parenthetical() -> None:
    """A trailing parenthetical that isn't a valid issue-id list should fail."""
    module = _load_commit_msg_module()
    assert not module.validate("fix: correct sorting order (not an id)")


def test_validate_allows_breaking_change_bang_without_scope() -> None:
    """A '!' before the colon should be allowed to mark a breaking change."""
    module = _load_commit_msg_module()
    assert module.validate("feat!: drop support for legacy config")


def test_validate_allows_breaking_change_bang_with_scope() -> None:
    """A '!' after a scope should be allowed to mark a breaking change."""
    module = _load_commit_msg_module()
    assert module.validate("feat(basicly)!: remove deprecated config format")


def test_validate_allows_breaking_change_bang_with_issue_id() -> None:
    """A '!' should compose with a trailing beads issue id."""
    module = _load_commit_msg_module()
    assert module.validate("feat(basicly)!: remove deprecated config format (basicly-idr)")
