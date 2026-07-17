"""Tests for the agent-permissions deny-list projection (basicly-u0zg).

Two layers: the catalog source model (permissions.load_deny_rules /
claude_deny_patterns) and the Claude projection into the co-owned
.claude/settings.json (claude_settings.merge/mismatches/sync). The invariant is
ensure-present, never clobber: managed patterns are guaranteed present, and a
consumer's own allow/deny/hooks entries always survive a rebuild.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import claude_settings, permissions

SETTINGS = Path(".claude/settings.json")

SAMPLE_YAML = """\
deny:
  - id: destructive-rm
    description: block recursive force-remove
    claude:
      - "Bash(rm -rf*)"
      - "Bash(rm -fr*)"
  - id: env-read
    description: block reading dotenv files
    claude:
      - "Read(.env)"
      - "Bash(rm -rf*)"
"""


def _write_perms(tmp_path: Path, text: str) -> Path:
    d = tmp_path / "permissions"
    d.mkdir()
    (d / permissions.PERMISSIONS_MANIFEST).write_text(text, encoding="utf-8")
    return d


def _write_settings(repo_root: Path, data: dict) -> None:
    path = repo_root / SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _read_settings(repo_root: Path) -> dict:
    return json.loads((repo_root / SETTINGS).read_text(encoding="utf-8"))


# --- source model -----------------------------------------------------------


def test_load_deny_rules_parses_rules_and_claude_patterns(tmp_path: Path) -> None:
    """A well-formed manifest loads into DenyRule records with their patterns."""
    rules = permissions.load_deny_rules(_write_perms(tmp_path, SAMPLE_YAML))
    assert [r.id for r in rules] == ["destructive-rm", "env-read"]
    assert rules[0].claude == ("Bash(rm -rf*)", "Bash(rm -fr*)")


def test_claude_deny_patterns_flattens_dedups_in_order(tmp_path: Path) -> None:
    """Patterns flatten across rules, de-duplicated, preserving first-seen order."""
    rules = permissions.load_deny_rules(_write_perms(tmp_path, SAMPLE_YAML))
    assert permissions.claude_deny_patterns(rules) == [
        "Bash(rm -rf*)",
        "Bash(rm -fr*)",
        "Read(.env)",
    ]


def test_load_deny_rules_rejects_missing_deny_list(tmp_path: Path) -> None:
    """A manifest without a 'deny' list is a hard error, not a silent empty load."""
    with pytest.raises(ValueError, match="'deny' must be a list"):
        permissions.load_deny_rules(_write_perms(tmp_path, "other: 1\n"))


def test_load_deny_rules_rejects_rule_missing_keys(tmp_path: Path) -> None:
    """A rule missing id/description is rejected."""
    with pytest.raises(ValueError, match="missing"):
        permissions.load_deny_rules(_write_perms(tmp_path, "deny:\n  - claude: ['x']\n"))


def test_load_deny_rules_rejects_bad_claude_patterns(tmp_path: Path) -> None:
    """A non-string / empty claude pattern is rejected."""
    bad = "deny:\n  - id: r\n    description: d\n    claude: ['']\n"
    with pytest.raises(ValueError, match="list of non-empty strings"):
        permissions.load_deny_rules(_write_perms(tmp_path, bad))


# --- Claude projection (ensure-present, never clobber) -----------------------


def test_merge_adds_managed_patterns_to_empty_settings() -> None:
    """Managed patterns land under permissions.deny in a fresh settings dict."""
    merged = claude_settings.merge_permission_deny({}, ["Bash(rm -rf*)", "Read(.env)"])
    assert merged["permissions"]["deny"] == ["Bash(rm -rf*)", "Read(.env)"]


def test_merge_preserves_consumer_entries_and_dedups() -> None:
    """A consumer's own deny/allow entries survive; a duplicate is not re-added."""
    settings = {
        "permissions": {"allow": ["Bash"], "deny": ["Bash(rm -rf*)", "Bash(sudo*)"]},
        "hooks": {"PreToolUse": ["x"]},
    }
    merged = claude_settings.merge_permission_deny(settings, ["Bash(rm -rf*)", "Read(.env)"])
    # Existing order preserved, only the truly-missing managed pattern appended.
    assert merged["permissions"]["deny"] == ["Bash(rm -rf*)", "Bash(sudo*)", "Read(.env)"]
    assert merged["permissions"]["allow"] == ["Bash"]  # allow untouched
    assert merged["hooks"] == {"PreToolUse": ["x"]}  # hooks untouched


def test_mismatches_report_only_missing_patterns(tmp_path: Path) -> None:
    """Mismatches list exactly the managed patterns absent from the file."""
    assert claude_settings.permission_deny_mismatches(tmp_path, ["Bash(rm -rf*)"]) == [
        "managed deny pattern 'Bash(rm -rf*)' missing"
    ]
    _write_settings(tmp_path, {"permissions": {"deny": ["Bash(rm -rf*)"]}})
    assert claude_settings.permission_deny_mismatches(tmp_path, ["Bash(rm -rf*)"]) == []
    assert claude_settings.permission_deny_mismatches(
        tmp_path, ["Bash(rm -rf*)", "Read(.env)"]
    ) == ["managed deny pattern 'Read(.env)' missing"]


def test_sync_writes_then_is_idempotent(tmp_path: Path) -> None:
    """First sync writes the deny-list; a second sync is a no-op."""
    patterns = ["Bash(rm -rf*)", "Read(.env)"]
    assert claude_settings.sync_permission_deny(tmp_path, patterns) is True
    assert _read_settings(tmp_path)["permissions"]["deny"] == patterns
    assert claude_settings.sync_permission_deny(tmp_path, patterns) is False


def test_sync_preserves_consumer_config(tmp_path: Path) -> None:
    """Sync merges into an existing file without disturbing consumer content."""
    _write_settings(
        tmp_path,
        {
            "permissions": {"allow": ["Bash"], "deny": ["Bash(sudo*)"]},
            "hooks": {"PostToolUse": ["keep"]},
        },
    )
    claude_settings.sync_permission_deny(tmp_path, ["Bash(rm -rf*)"])
    result = _read_settings(tmp_path)
    assert result["permissions"]["deny"] == ["Bash(sudo*)", "Bash(rm -rf*)"]
    assert result["permissions"]["allow"] == ["Bash"]
    assert result["hooks"] == {"PostToolUse": ["keep"]}


def test_sync_no_patterns_is_noop(tmp_path: Path) -> None:
    """Nothing to project => no file written."""
    assert claude_settings.sync_permission_deny(tmp_path, []) is False
    assert not (tmp_path / SETTINGS).exists()


def test_merge_and_mismatches_tolerate_a_tampered_file() -> None:
    """A hand-corrupted permissions/deny shape is coerced, never crashes the merge."""
    # permissions is not a dict, deny (once coerced) is rebuilt from the managed set.
    merged = claude_settings.merge_permission_deny({"permissions": "corrupt"}, ["Bash(rm -rf*)"])
    assert merged["permissions"]["deny"] == ["Bash(rm -rf*)"]
    # deny is not a list => treated as absent, managed patterns still land.
    merged = claude_settings.merge_permission_deny(
        {"permissions": {"deny": {"not": "a list"}}}, ["Read(.env)"]
    )
    assert merged["permissions"]["deny"] == ["Read(.env)"]


# --- the shipped source keeps the .env guardrail complete (basicly-u0zg) -----


def test_bundled_source_denies_every_env_mutation_tool() -> None:
    """Regression: `.env` must be blocked for every file-writing tool, not just Edit."""
    patterns = set(permissions.claude_deny_patterns(permissions.load_deny_rules()))
    for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        assert f"{tool}(.env)" in patterns, f"{tool} leaves a .env write path open"
