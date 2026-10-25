from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from . import hook_python
from .projection import atomic_write_text

if TYPE_CHECKING:
    from .lefthook_config import HookSpec
    from .permissions import ClaudePermissions

CLAUDE_SETTINGS_PATH = Path(".claude/settings.json")
WORKTREE_KEY = "worktree"
BG_ISOLATION_KEY = "bgIsolation"
BG_ISOLATION_NONE = "none"

PERMISSIONS_KEY = "permissions"
ALLOW_KEY = "allow"
DENY_KEY = "deny"

HOOKS_KEY = "hooks"
SUBAGENT_CACHE_TTL_KEY = "subagentPromptCacheTtl"
SUBAGENT_CACHE_TTL = "1h"
SKILL_LISTING_BUDGET_KEY = "skillListingBudgetFraction"
SKILL_LISTING_BUDGET = 0.02
PROJECT_DIR_PLACEHOLDER = "${CLAUDE_PROJECT_DIR}"
HOOK_INTERPRETER = hook_python.COMMAND
AGENT_HOOK_EVENTS = {
    "pretooluse": "PreToolUse",
    "posttooluse": "PostToolUse",
    "sessionstart": "SessionStart",
}


def _load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def current_bg_isolation(repo_root: Path) -> str | None:
    settings = _load_settings(repo_root / CLAUDE_SETTINGS_PATH)
    section = settings.get(WORKTREE_KEY)
    if isinstance(section, dict):
        value = section.get(BG_ISOLATION_KEY)
        if isinstance(value, str):
            return value
    return None


def set_bg_isolation_none(repo_root: Path) -> bool:

    if current_bg_isolation(repo_root) == BG_ISOLATION_NONE:
        return False

    path = repo_root / CLAUDE_SETTINGS_PATH
    settings = _load_settings(path)
    section = settings.get(WORKTREE_KEY)
    if not isinstance(section, dict):
        section = {}
    section[BG_ISOLATION_KEY] = BG_ISOLATION_NONE
    settings[WORKTREE_KEY] = section

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(settings, indent=2) + "\n")
    return True


def _default_key(repo_root: Path, key: str, value: object) -> bool:

    path = repo_root / CLAUDE_SETTINGS_PATH
    settings = _load_settings(path)
    if key in settings:
        return False
    settings[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(settings, indent=2) + "\n")
    return True


def default_subagent_cache_ttl(repo_root: Path) -> bool:
    return _default_key(repo_root, SUBAGENT_CACHE_TTL_KEY, SUBAGENT_CACHE_TTL)


def default_skill_listing_budget(repo_root: Path) -> bool:
    return _default_key(repo_root, SKILL_LISTING_BUDGET_KEY, SKILL_LISTING_BUDGET)


def skill_listing_budget(repo_root: Path) -> float:

    value = _load_settings(repo_root / CLAUDE_SETTINGS_PATH).get(SKILL_LISTING_BUDGET_KEY)
    if isinstance(value, int | float) and not isinstance(value, bool) and 0 < value <= 1:
        return float(value)
    return SKILL_LISTING_BUDGET


def _rule_list(perms: dict, key: str) -> list[str]:
    rules = perms.get(key)
    return list(rules) if isinstance(rules, list) else []


def _with_missing(rules: list[str], managed: tuple[str, ...]) -> list[str]:
    return rules + [pattern for pattern in dict.fromkeys(managed) if pattern not in rules]


def merge_permissions(settings: dict, managed: ClaudePermissions) -> dict:

    merged = dict(settings)
    perms = merged.get(PERMISSIONS_KEY)
    perms = dict(perms) if isinstance(perms, dict) else {}

    allow = _with_missing(_rule_list(perms, ALLOW_KEY), managed.allow)
    if allow or ALLOW_KEY in perms:
        perms[ALLOW_KEY] = allow
    perms[DENY_KEY] = _with_missing(_rule_list(perms, DENY_KEY), managed.deny)

    merged[PERMISSIONS_KEY] = perms
    return merged


def permission_mismatches(repo_root: Path, managed: ClaudePermissions) -> list[str]:
    settings = _load_settings(repo_root / CLAUDE_SETTINGS_PATH)
    perms = settings.get(PERMISSIONS_KEY)
    perms = perms if isinstance(perms, dict) else {}
    allow = set(_rule_list(perms, ALLOW_KEY))
    deny = set(_rule_list(perms, DENY_KEY))
    return [
        f"managed allow pattern {rule!r} missing" for rule in managed.allow if rule not in allow
    ] + [f"managed deny pattern {rule!r} missing" for rule in managed.deny if rule not in deny]


def sync_permissions(repo_root: Path, managed: ClaudePermissions) -> bool:

    if not permission_mismatches(repo_root, managed):
        return False

    path = repo_root / CLAUDE_SETTINGS_PATH
    merged = merge_permissions(_load_settings(path), managed)

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(merged, indent=2) + "\n")
    return True


def _agent_hook_command(spec: HookSpec, hooks_relpath: str) -> str:
    script = f"{PROJECT_DIR_PLACEHOLDER}/{hooks_relpath}/lefthook-runtime.py"
    return f'{HOOK_INTERPRETER} "{script}" claude {spec.stage}'


def _event_groups(specs: list[HookSpec], hooks_relpath: str) -> dict[str, dict]:
    return {
        AGENT_HOOK_EVENTS[spec.stage]: {
            "matcher": "*",
            "hooks": [{"type": "command", "command": _agent_hook_command(spec, hooks_relpath)}],
        }
        for spec in specs
    }


def _references_managed_script(group: object, script_paths: set[str]) -> bool:
    if not isinstance(group, dict):
        return False
    return any(
        isinstance(hook, dict)
        and isinstance(hook.get("command"), str)
        and any(path in hook["command"] for path in script_paths)
        for hook in group.get("hooks", [])
    )


def merge_agent_hooks(
    settings: dict,
    specs: list[HookSpec],
    hooks_relpath: str,
    strip_scripts: set[str] | None = None,
) -> dict:
    merged = dict(settings)
    section = dict(merged.get(HOOKS_KEY) or {})
    paths = (strip_scripts or {f"{hooks_relpath}/{spec.script}" for spec in specs}) | {
        f"{hooks_relpath}/lefthook-runtime.py",
        ".basicly/core/kit/tier/claude_tier_hook.py",
        (PurePosixPath(hooks_relpath).parent / "kit/tier/claude_tier_hook.py").as_posix(),
    }
    for event in AGENT_HOOK_EVENTS.values():
        kept = []
        for group in section.get(event, []):
            if not _references_managed_script(group, paths):
                kept.append(group)
                continue
            foreign = [
                hook
                for hook in group.get("hooks", [])
                if not _references_managed_script({"hooks": [hook]}, paths)
            ]
            if foreign:
                kept.append({**group, "hooks": foreign})
        if kept:
            section[event] = kept
        else:
            section.pop(event, None)
    for event, group in _event_groups(specs, hooks_relpath).items():
        section.setdefault(event, []).append(group)
    if section:
        merged[HOOKS_KEY] = section
    else:
        merged.pop(HOOKS_KEY, None)
    return merged


def agent_hook_mismatches(repo_root: Path, specs: list[HookSpec], hooks_relpath: str) -> list[str]:
    section = _load_settings(repo_root / CLAUDE_SETTINGS_PATH).get(HOOKS_KEY, {})
    return [
        f"managed agent event {event!r} missing or out of sync"
        for event, group in _event_groups(specs, hooks_relpath).items()
        if group not in section.get(event, [])
    ]


def excluded_agent_hooks_present(
    repo_root: Path, excluded_specs: list[HookSpec], hooks_relpath: str
) -> list[str]:
    section = _load_settings(repo_root / CLAUDE_SETTINGS_PATH).get(HOOKS_KEY, {})
    return [
        f"retired direct agent hook {spec.id!r} remains"
        for spec in excluded_specs
        if any(
            _references_managed_script(group, {f"{hooks_relpath}/{spec.script}"})
            for groups in section.values()
            for group in groups
        )
    ]


def sync_agent_hooks(
    repo_root: Path,
    specs: list[HookSpec],
    hooks_relpath: str,
    excluded_specs: list[HookSpec] | None = None,
) -> bool:
    path = repo_root / CLAUDE_SETTINGS_PATH
    settings = _load_settings(path)
    scripts = {f"{hooks_relpath}/{spec.script}" for spec in (*specs, *(excluded_specs or []))}
    merged = merge_agent_hooks(settings, specs, hooks_relpath, scripts)
    if settings == merged:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(merged, indent=2) + "\n")
    return True


def remove_agent_hooks(repo_root: Path, specs: list[HookSpec], hooks_relpath: str) -> bool:
    path = repo_root / CLAUDE_SETTINGS_PATH
    if not path.exists():
        return False
    settings = _load_settings(path)
    scripts = {f"{hooks_relpath}/{spec.script}" for spec in specs}
    updated = merge_agent_hooks(settings, [], hooks_relpath, scripts)
    if updated == settings:
        return False
    atomic_write_text(path, json.dumps(updated, indent=2) + "\n")
    return True
