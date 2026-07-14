"""Tests for Claude worktree.bgIsolation settings management (onb.1.6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import claude_settings, cli

SETTINGS = Path(".claude/settings.json")


def _write_settings(repo_root: Path, data: dict) -> None:
    path = repo_root / SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _read_settings(repo_root: Path) -> dict:
    return json.loads((repo_root / SETTINGS).read_text(encoding="utf-8"))


def test_current_bg_isolation_none_when_unset(tmp_path: Path) -> None:
    """A missing file or missing key reports None."""
    assert claude_settings.current_bg_isolation(tmp_path) is None
    _write_settings(tmp_path, {"permissions": {"allow": ["Bash"]}})
    assert claude_settings.current_bg_isolation(tmp_path) is None


def test_set_bg_isolation_preserves_other_keys(tmp_path: Path) -> None:
    """The write merges in, leaving existing settings intact."""
    _write_settings(tmp_path, {"includeCoAuthoredBy": False, "permissions": {"allow": ["Bash"]}})

    assert claude_settings.set_bg_isolation_none(tmp_path) is True

    data = _read_settings(tmp_path)
    assert data["worktree"] == {"bgIsolation": "none"}
    assert data["includeCoAuthoredBy"] is False
    assert data["permissions"] == {"allow": ["Bash"]}
    assert claude_settings.current_bg_isolation(tmp_path) == "none"


def test_set_bg_isolation_is_idempotent(tmp_path: Path) -> None:
    """A second write reports no change."""
    _write_settings(tmp_path, {"worktree": {"bgIsolation": "none"}})
    assert claude_settings.set_bg_isolation_none(tmp_path) is False


def test_cli_bg_isolation_requires_consent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --yes the command explains and writes nothing."""
    _write_settings(tmp_path, {"permissions": {"allow": ["Bash"]}})
    monkeypatch.chdir(tmp_path)

    assert cli.main(["worktree", "bg-isolation"]) == 0
    assert "worktree" not in _read_settings(tmp_path)


def test_cli_bg_isolation_writes_with_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With --yes the committed settings gain worktree.bgIsolation=none."""
    _write_settings(tmp_path, {"permissions": {"allow": ["Bash"]}})
    monkeypatch.chdir(tmp_path)

    assert cli.main(["worktree", "bg-isolation", "--yes"]) == 0
    data = _read_settings(tmp_path)
    assert data["worktree"] == {"bgIsolation": "none"}
    assert data["permissions"] == {"allow": ["Bash"]}


def test_cli_bg_isolation_noop_when_already_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Already-none is reported as nothing-to-do, even with --yes."""
    _write_settings(tmp_path, {"worktree": {"bgIsolation": "none"}})
    monkeypatch.chdir(tmp_path)

    assert cli.main(["worktree", "bg-isolation", "--yes"]) == 0
    assert _read_settings(tmp_path) == {"worktree": {"bgIsolation": "none"}}
