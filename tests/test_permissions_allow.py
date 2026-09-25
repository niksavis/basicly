from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import claude_settings, permissions

SETTINGS = Path(".claude/settings.json")

AUTO_MODE_DROPPED = (
    "Bash",
    "Bash(*)",
    "Bash(*:*)",
    "PowerShell",
    "PowerShell(*)",
    "Bash(python*)",
    "Bash(python3 *)",
    "Bash(python3.14 -m pytest)",
    "Bash(/usr/bin/python3 *)",
    "Bash(node*)",
    "Bash(node script.js)",
    "Bash(bash*)",
    "Bash(bash -c *)",
    "Bash(sh *)",
    "Bash(zsh:*)",
    "Bash(uv run *)",
    "Bash(uv run pytest)",
    "Bash(uvx *)",
    "Bash(npm run *)",
    "Bash(npx *)",
    "Bash(pnpm exec *)",
    "Bash(yarn run *)",
    "Bash(pip install *)",
    "Bash(pip3 *)",
    "Bash(poetry run *)",
    "Bash(env *)",
    "Bash(xargs *)",
    "Bash(* --version)",
    "Agent",
    "Agent(Explore)",
    "Monitor",
    "Monitor(*)",
)

NARROW = (
    "Read",
    "Edit",
    "WebFetch",
    "Bash(git status *)",
    "Bash(git branch)",
    "Bash(jq *)",
    "Bash(basicly check)",
    "Bash(basicly verify --mode fast)",
)

MANAGED = permissions.ClaudePermissions(
    allow=("Read", "Bash(git status *)"),
    deny=("Bash(rm -rf*)",),
    retired_allow=("Bash",),
)


def _write_perms(tmp_path: Path, text: str) -> Path:
    d = tmp_path / "permissions"
    d.mkdir()
    (d / permissions.PERMISSIONS_MANIFEST).write_text(text, encoding="utf-8")
    return d


def _write_settings(repo_root: Path, data: dict) -> None:
    path = repo_root / SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@pytest.mark.parametrize("pattern", AUTO_MODE_DROPPED)
def test_auto_mode_drop_reason_names_each_dropped_class(pattern: str) -> None:
    assert permissions.auto_mode_drop_reason(pattern)


@pytest.mark.parametrize("pattern", NARROW)
def test_auto_mode_drop_reason_passes_a_narrow_rule(pattern: str) -> None:
    assert permissions.auto_mode_drop_reason(pattern) is None


def test_bundled_allow_holds_no_rule_that_auto_mode_drops() -> None:
    managed = permissions.load_claude_permissions()
    assert managed.allow
    dropped = {p: permissions.auto_mode_drop_reason(p) for p in managed.allow}
    assert {p: r for p, r in dropped.items() if r} == {}
    assert not set(managed.allow) & set(AUTO_MODE_DROPPED)


def test_bundled_allow_carries_read_git_and_basicly_check_rules() -> None:
    managed = permissions.load_claude_permissions()
    for pattern in ("Read", "Glob", "Grep", "Bash(git status *)", "Bash(basicly check)"):
        assert pattern in managed.allow
    assert "Bash" in managed.retired_allow
    assert "Bash(rm -rf*)" in managed.deny


def test_loader_refuses_an_allow_that_auto_mode_drops(tmp_path: Path) -> None:
    text = (
        "allow:\n  - id: runs\n    description: d\n    claude: ['Bash(uv run *)']\n"
        "deny:\n  - id: r\n    description: d\n    claude: ['Bash(rm -rf*)']\n"
    )
    with pytest.raises(ValueError, match=r"'Bash\(uv run \*\)' is refused: 'uv' is a package"):
        permissions.load_claude_permissions(_write_perms(tmp_path, text))


def test_loader_refuses_a_copilot_list_on_an_allow(tmp_path: Path) -> None:
    text = (
        "allow:\n  - id: a\n    description: d\n    copilot: ['shell(git status)']\n"
        "deny:\n  - id: r\n    description: d\n    claude: ['Bash(rm -rf*)']\n"
    )
    with pytest.raises(ValueError, match="allow rule 'a' has \\['copilot'\\]"):
        permissions.load_claude_permissions(_write_perms(tmp_path, text))


def test_loader_refuses_a_pattern_both_allowed_and_retired(tmp_path: Path) -> None:
    text = (
        "allow:\n  - id: a\n    description: d\n    claude: ['Read']\n"
        "retired_allow:\n  - id: b\n    description: d\n    claude: ['Read']\n"
        "deny:\n  - id: r\n    description: d\n    claude: ['Bash(rm -rf*)']\n"
    )
    with pytest.raises(ValueError, match="both allowed and retired"):
        permissions.load_claude_permissions(_write_perms(tmp_path, text))


def test_merge_keeps_consumer_rules_and_drops_only_the_retired_allow() -> None:
    settings = {
        "permissions": {
            "allow": ["Bash", "Bash(make lint)", "Read"],
            "ask": ["Bash(git push *)"],
            "deny": ["Bash(sudo *)"],
        }
    }
    perms = claude_settings.merge_permissions(settings, MANAGED)["permissions"]
    assert perms["allow"] == ["Bash(make lint)", "Read", "Bash(git status *)"]
    assert perms["ask"] == ["Bash(git push *)"]
    assert perms["deny"] == ["Bash(sudo *)", "Bash(rm -rf*)"]


def test_merge_writes_no_allow_key_when_nothing_is_allowed() -> None:
    managed = permissions.ClaudePermissions(deny=("Read(.env)",))
    assert claude_settings.merge_permissions({}, managed)["permissions"] == {"deny": ["Read(.env)"]}


def test_mismatches_name_a_missing_allow_and_a_present_retired_allow(tmp_path: Path) -> None:
    _write_settings(tmp_path, {"permissions": {"allow": ["Bash"], "deny": ["Bash(rm -rf*)"]}})
    assert claude_settings.permission_mismatches(tmp_path, MANAGED) == [
        "managed allow pattern 'Read' missing",
        "managed allow pattern 'Bash(git status *)' missing",
        "retired allow pattern 'Bash' present",
    ]
    assert claude_settings.sync_permissions(tmp_path, MANAGED) is True
    assert claude_settings.permission_mismatches(tmp_path, MANAGED) == []
    assert claude_settings.sync_permissions(tmp_path, MANAGED) is False
