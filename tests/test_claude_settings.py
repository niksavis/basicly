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


GUARD = claude_settings.HookSpec(
    id="protect-generated", script="protect-generated.py", stage="pretooluse", manager="claude"
)
HOOKS_RELPATH = ".basicly/core/hooks"
EXPECTED_COMMAND = "uv run python .basicly/core/hooks/protect-generated.py"


def test_sync_agent_hooks_writes_and_preserves_other_keys(tmp_path: Path) -> None:
    """The projection adds the PreToolUse wiring without disturbing settings."""
    _write_settings(tmp_path, {"permissions": {"allow": ["Bash"]}})

    assert claude_settings.sync_agent_hooks(tmp_path, [GUARD], HOOKS_RELPATH) is True

    data = _read_settings(tmp_path)
    assert data["permissions"] == {"allow": ["Bash"]}
    groups = data["hooks"]["PreToolUse"]
    assert groups == [
        {
            "matcher": claude_settings.AGENT_HOOK_MATCHER,
            "hooks": [{"type": "command", "command": EXPECTED_COMMAND}],
        }
    ]


def test_sync_agent_hooks_is_idempotent(tmp_path: Path) -> None:
    """A second sync reports no change and leaves a single managed group."""
    assert claude_settings.sync_agent_hooks(tmp_path, [GUARD], HOOKS_RELPATH) is True
    assert claude_settings.sync_agent_hooks(tmp_path, [GUARD], HOOKS_RELPATH) is False
    assert len(_read_settings(tmp_path)["hooks"]["PreToolUse"]) == 1


def test_merge_preserves_foreign_pretooluse_groups(tmp_path: Path) -> None:
    """Consumer-authored agent hooks survive the managed projection."""
    foreign = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
    _write_settings(tmp_path, {"hooks": {"PreToolUse": [foreign]}})

    assert claude_settings.sync_agent_hooks(tmp_path, [GUARD], HOOKS_RELPATH) is True

    groups = _read_settings(tmp_path)["hooks"]["PreToolUse"]
    assert foreign in groups
    assert len(groups) == 2


def test_agent_hook_mismatches_flags_missing_and_stale(tmp_path: Path) -> None:
    """A missing or altered managed entry is reported; a synced one is not."""
    assert claude_settings.agent_hook_mismatches(tmp_path, [GUARD], HOOKS_RELPATH)

    claude_settings.sync_agent_hooks(tmp_path, [GUARD], HOOKS_RELPATH)
    assert claude_settings.agent_hook_mismatches(tmp_path, [GUARD], HOOKS_RELPATH) == []

    data = _read_settings(tmp_path)
    data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = "echo tampered"
    _write_settings(tmp_path, data)
    assert claude_settings.agent_hook_mismatches(tmp_path, [GUARD], HOOKS_RELPATH)


COUNTER = claude_settings.HookSpec(
    id="tool-usage", script="tool-usage.py", stage="posttooluse", manager="claude", matcher="Bash"
)


def test_posttooluse_spec_lands_in_its_own_event_with_its_matcher(tmp_path: Path) -> None:
    """A posttooluse spec projects under PostToolUse with its Bash matcher."""
    assert claude_settings.sync_agent_hooks(tmp_path, [GUARD, COUNTER], HOOKS_RELPATH) is True

    data = _read_settings(tmp_path)
    assert data["hooks"]["PreToolUse"] == [
        {
            "matcher": claude_settings.AGENT_HOOK_MATCHER,
            "hooks": [{"type": "command", "command": EXPECTED_COMMAND}],
        }
    ]
    assert data["hooks"]["PostToolUse"] == [
        {
            "matcher": "Bash",
            "hooks": [
                {
                    "type": "command",
                    "command": "uv run python .basicly/core/hooks/tool-usage.py",
                }
            ],
        }
    ]

    # Idempotent across both events; removal strips both and prunes empties.
    assert claude_settings.sync_agent_hooks(tmp_path, [GUARD, COUNTER], HOOKS_RELPATH) is False
    assert claude_settings.agent_hook_mismatches(tmp_path, [GUARD, COUNTER], HOOKS_RELPATH) == []
    assert claude_settings.remove_agent_hooks(tmp_path, [GUARD, COUNTER], HOOKS_RELPATH) is True
    assert "hooks" not in _read_settings(tmp_path)


def test_remove_agent_hooks_strips_managed_only(tmp_path: Path) -> None:
    """Uninstall drops managed groups, keeps foreign ones, and prunes empties."""
    foreign = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
    _write_settings(tmp_path, {"hooks": {"PreToolUse": [foreign]}, "other": 1})
    claude_settings.sync_agent_hooks(tmp_path, [GUARD], HOOKS_RELPATH)

    assert claude_settings.remove_agent_hooks(tmp_path, [GUARD], HOOKS_RELPATH) is True
    data = _read_settings(tmp_path)
    assert data["hooks"]["PreToolUse"] == [foreign]
    assert data["other"] == 1

    # With no foreign groups left, the empty containers disappear entirely.
    _write_settings(tmp_path, {"other": 1})
    claude_settings.sync_agent_hooks(tmp_path, [GUARD], HOOKS_RELPATH)
    assert claude_settings.remove_agent_hooks(tmp_path, [GUARD], HOOKS_RELPATH) is True
    assert _read_settings(tmp_path) == {"other": 1}


def test_consumer_hook_with_same_basename_survives(tmp_path: Path) -> None:
    """A consumer hook running its own protect-generated.py is not managed."""
    consumer_group = {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "python scripts/protect-generated.py"}],
    }
    _write_settings(
        tmp_path,
        {"hooks": {"PreToolUse": [consumer_group]}},
    )
    claude_settings.sync_agent_hooks(tmp_path, [GUARD], HOOKS_RELPATH)
    data = json.loads((tmp_path / claude_settings.CLAUDE_SETTINGS_PATH).read_text())
    commands = [hook["command"] for group in data["hooks"]["PreToolUse"] for hook in group["hooks"]]
    assert "python scripts/protect-generated.py" in commands

    assert claude_settings.remove_agent_hooks(tmp_path, [GUARD], HOOKS_RELPATH) is True
    data = json.loads((tmp_path / claude_settings.CLAUDE_SETTINGS_PATH).read_text())
    commands = [hook["command"] for group in data["hooks"]["PreToolUse"] for hook in group["hooks"]]
    assert commands == ["python scripts/protect-generated.py"]
