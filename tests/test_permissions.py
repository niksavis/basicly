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


def test_load_deny_rules_parses_rules_and_claude_patterns(tmp_path: Path) -> None:
    rules = permissions.load_deny_rules(_write_perms(tmp_path, SAMPLE_YAML))
    assert [r.id for r in rules] == ["destructive-rm", "env-read"]
    assert rules[0].claude == ("Bash(rm -rf*)", "Bash(rm -fr*)")


def test_claude_deny_patterns_flattens_dedups_in_order(tmp_path: Path) -> None:
    rules = permissions.load_deny_rules(_write_perms(tmp_path, SAMPLE_YAML))
    assert permissions.claude_deny_patterns(rules) == [
        "Bash(rm -rf*)",
        "Bash(rm -fr*)",
        "Read(.env)",
    ]


def test_load_deny_rules_rejects_missing_deny_list(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'deny' must be a list"):
        permissions.load_deny_rules(_write_perms(tmp_path, "other: 1\n"))


def test_load_deny_rules_rejects_rule_missing_keys(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing"):
        permissions.load_deny_rules(_write_perms(tmp_path, "deny:\n  - claude: ['x']\n"))


def test_load_deny_rules_rejects_bad_claude_patterns(tmp_path: Path) -> None:
    bad = "deny:\n  - id: r\n    description: d\n    claude: ['']\n"
    with pytest.raises(ValueError, match="list of non-empty strings"):
        permissions.load_deny_rules(_write_perms(tmp_path, bad))


COPILOT_YAML = """\
deny:
  - id: destructive-rm
    description: block recursive force-remove
    claude:
      - "Bash(rm -rf*)"
    copilot:
      - "shell(rm -rf)"
      - "shell(rm -fr)"
  - id: force-push
    description: block force pushes
    copilot:
      - "shell(git push --force)"
      - "shell(rm -rf)"
  - id: env-read
    description: block reading dotenv files
    claude:
      - "Read(.env)"
"""


def test_load_deny_rules_parses_copilot_specs(tmp_path: Path) -> None:
    rules = {r.id: r for r in permissions.load_deny_rules(_write_perms(tmp_path, COPILOT_YAML))}
    assert rules["destructive-rm"].copilot == ("shell(rm -rf)", "shell(rm -fr)")
    assert rules["env-read"].copilot == ()


def test_copilot_deny_specs_flattens_dedups_in_order(tmp_path: Path) -> None:
    rules = permissions.load_deny_rules(_write_perms(tmp_path, COPILOT_YAML))
    assert permissions.copilot_deny_specs(rules) == [
        "shell(rm -rf)",
        "shell(rm -fr)",
        "shell(git push --force)",
    ]


def test_load_deny_rules_rejects_bad_copilot_specs(tmp_path: Path) -> None:
    bad = "deny:\n  - id: r\n    description: d\n    copilot: ['']\n"
    with pytest.raises(ValueError, match="'copilot' must be a list of non-empty strings"):
        permissions.load_deny_rules(_write_perms(tmp_path, bad))


def test_merge_adds_managed_patterns_to_empty_settings() -> None:
    merged = claude_settings.merge_permission_deny({}, ["Bash(rm -rf*)", "Read(.env)"])
    assert merged["permissions"]["deny"] == ["Bash(rm -rf*)", "Read(.env)"]


def test_merge_preserves_consumer_entries_and_dedups() -> None:
    settings = {
        "permissions": {"allow": ["Bash"], "deny": ["Bash(rm -rf*)", "Bash(sudo*)"]},
        "hooks": {"PreToolUse": ["x"]},
    }
    merged = claude_settings.merge_permission_deny(settings, ["Bash(rm -rf*)", "Read(.env)"])
    assert merged["permissions"]["deny"] == ["Bash(rm -rf*)", "Bash(sudo*)", "Read(.env)"]
    assert merged["permissions"]["allow"] == ["Bash"]
    assert merged["hooks"] == {"PreToolUse": ["x"]}


def test_mismatches_report_only_missing_patterns(tmp_path: Path) -> None:
    assert claude_settings.permission_deny_mismatches(tmp_path, ["Bash(rm -rf*)"]) == [
        "managed deny pattern 'Bash(rm -rf*)' missing"
    ]
    _write_settings(tmp_path, {"permissions": {"deny": ["Bash(rm -rf*)"]}})
    assert claude_settings.permission_deny_mismatches(tmp_path, ["Bash(rm -rf*)"]) == []
    assert claude_settings.permission_deny_mismatches(
        tmp_path, ["Bash(rm -rf*)", "Read(.env)"]
    ) == ["managed deny pattern 'Read(.env)' missing"]


def test_sync_writes_then_is_idempotent(tmp_path: Path) -> None:
    patterns = ["Bash(rm -rf*)", "Read(.env)"]
    assert claude_settings.sync_permission_deny(tmp_path, patterns) is True
    assert _read_settings(tmp_path)["permissions"]["deny"] == patterns
    assert claude_settings.sync_permission_deny(tmp_path, patterns) is False


def test_sync_preserves_consumer_config(tmp_path: Path) -> None:
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
    assert claude_settings.sync_permission_deny(tmp_path, []) is False
    assert not (tmp_path / SETTINGS).exists()


def test_merge_and_mismatches_tolerate_a_tampered_file() -> None:
    merged = claude_settings.merge_permission_deny({"permissions": "corrupt"}, ["Bash(rm -rf*)"])
    assert merged["permissions"]["deny"] == ["Bash(rm -rf*)"]
    merged = claude_settings.merge_permission_deny(
        {"permissions": {"deny": {"not": "a list"}}}, ["Read(.env)"]
    )
    assert merged["permissions"]["deny"] == ["Read(.env)"]


def test_bundled_source_denies_env_mutation_via_edit_only() -> None:

    patterns = set(permissions.claude_deny_patterns(permissions.load_deny_rules()))
    for glob in (".env", ".env.*", "**/.env", "**/.env.*"):
        assert f"Edit({glob})" in patterns, f"Edit({glob}) leaves a .env write path open"
    for tool in ("Write", "MultiEdit", "NotebookEdit"):
        assert not any(p.startswith(f"{tool}(") for p in patterns), (
            f"{tool}(...) file rules are not matched by Claude Code permission checks"
        )


def test_bundled_source_copilot_specs_cover_shell_rules_only() -> None:

    rules = {r.id: r for r in permissions.load_deny_rules()}
    for rule_id in ("destructive-rm", "force-push", "bypass-gates", "destructive-git"):
        assert rules[rule_id].copilot, f"{rule_id} should carry copilot deny specs"
    for rule_id in ("env-read", "env-edit"):
        assert rules[rule_id].copilot == (), f"{rule_id} has no faithful copilot form"
    specs = permissions.copilot_deny_specs(permissions.load_deny_rules())
    assert specs and all(s.startswith("shell(") and s.endswith(")") for s in specs)
