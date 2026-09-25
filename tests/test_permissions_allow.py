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
)

REPO_OWN_ALLOWS = ("Bash", "Edit", "Write", "WebSearch", "WebFetch", "Bash(make lint)")


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
    assert "Bash(rm -rf*)" in managed.deny


def test_bundled_allow_ships_no_edit_write_or_web_allow() -> None:
    shipped = set(permissions.load_claude_permissions().allow)
    assert shipped.isdisjoint({"Bash", "Edit", "Write", "WebSearch", "WebFetch"})


def test_bundled_bash_allows_name_only_git_and_basicly_so_none_reads_a_denied_file() -> None:
    bash = [
        rule for rule in permissions.load_claude_permissions().allow if rule.startswith("Bash(")
    ]
    programs = {rule.removeprefix("Bash(").split(" ", 1)[0].rstrip(")") for rule in bash}
    assert bash
    assert programs == {"git", "basicly"}


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


def test_merge_with_the_bundled_catalog_keeps_a_repo_bash_edit_and_webfetch() -> None:
    settings = {
        "permissions": {
            "allow": list(REPO_OWN_ALLOWS),
            "ask": ["Bash(git push *)"],
            "deny": ["Bash(sudo *)"],
        }
    }
    managed = permissions.load_claude_permissions()
    perms = claude_settings.merge_permissions(settings, managed)["permissions"]
    assert perms["allow"][: len(REPO_OWN_ALLOWS)] == list(REPO_OWN_ALLOWS)
    assert perms["allow"][len(REPO_OWN_ALLOWS) :] == [
        p for p in managed.allow if p not in REPO_OWN_ALLOWS
    ]
    assert perms["ask"] == ["Bash(git push *)"]
    assert perms["deny"][0] == "Bash(sudo *)"


def test_merge_writes_no_allow_key_when_nothing_is_allowed() -> None:
    managed = permissions.ClaudePermissions(deny=("Read(.env)",))
    assert claude_settings.merge_permissions({}, managed)["permissions"] == {"deny": ["Read(.env)"]}


def test_a_repo_blanket_bash_is_no_mismatch_against_the_bundled_catalog(tmp_path: Path) -> None:
    managed = permissions.load_claude_permissions()
    allow = ["Bash", "Edit", "WebFetch", *managed.allow]
    _write_settings(tmp_path, {"permissions": {"allow": allow, "deny": list(managed.deny)}})
    assert claude_settings.permission_mismatches(tmp_path, managed) == []


def test_sync_adds_a_missing_allow_and_is_idempotent(tmp_path: Path) -> None:
    _write_settings(tmp_path, {"permissions": {"allow": ["Bash"], "deny": ["Bash(rm -rf*)"]}})
    assert claude_settings.permission_mismatches(tmp_path, MANAGED) == [
        "managed allow pattern 'Read' missing",
        "managed allow pattern 'Bash(git status *)' missing",
    ]
    assert claude_settings.sync_permissions(tmp_path, MANAGED) is True
    assert claude_settings.permission_mismatches(tmp_path, MANAGED) == []
    assert claude_settings.sync_permissions(tmp_path, MANAGED) is False
