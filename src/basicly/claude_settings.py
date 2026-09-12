from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .projection import atomic_write_text

if TYPE_CHECKING:
    from .hooks import HookSpec

CLAUDE_SETTINGS_PATH = Path(".claude/settings.json")
WORKTREE_KEY = "worktree"
BG_ISOLATION_KEY = "bgIsolation"
BG_ISOLATION_NONE = "none"

PERMISSIONS_KEY = "permissions"
DENY_KEY = "deny"

HOOKS_KEY = "hooks"
PROJECT_DIR_PLACEHOLDER = "${CLAUDE_PROJECT_DIR}"
HOOK_INTERPRETER = "uv run --no-project --no-python-downloads python"
AGENT_HOOK_EVENTS = {
    "pretooluse": "PreToolUse",
    "posttooluse": "PostToolUse",
    "sessionstart": "SessionStart",
}
AGENT_HOOK_MATCHER = "Edit|Write|NotebookEdit"


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


def merge_permission_deny(settings: dict, patterns: list[str]) -> dict:

    merged = dict(settings)
    perms = merged.get(PERMISSIONS_KEY)
    perms = dict(perms) if isinstance(perms, dict) else {}
    deny = perms.get(DENY_KEY)
    deny = list(deny) if isinstance(deny, list) else []

    present = set(deny)
    for pattern in patterns:
        if pattern not in present:
            deny.append(pattern)
            present.add(pattern)

    perms[DENY_KEY] = deny
    merged[PERMISSIONS_KEY] = perms
    return merged


def permission_deny_mismatches(repo_root: Path, patterns: list[str]) -> list[str]:
    settings = _load_settings(repo_root / CLAUDE_SETTINGS_PATH)
    perms = settings.get(PERMISSIONS_KEY)
    perms = perms if isinstance(perms, dict) else {}
    deny = perms.get(DENY_KEY)
    present = set(deny) if isinstance(deny, list) else set()
    return [
        f"managed deny pattern {pattern!r} missing"
        for pattern in patterns
        if pattern not in present
    ]


def sync_permission_deny(repo_root: Path, patterns: list[str]) -> bool:

    if not patterns:
        return False
    if not permission_deny_mismatches(repo_root, patterns):
        return False

    path = repo_root / CLAUDE_SETTINGS_PATH
    settings = _load_settings(path)
    merged = merge_permission_deny(settings, patterns)

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(merged, indent=2) + "\n")
    return True


def _agent_hook_command(spec: HookSpec, hooks_relpath: str) -> str:

    script = f"{PROJECT_DIR_PLACEHOLDER}/{hooks_relpath}/{spec.script}"
    return f'{HOOK_INTERPRETER} "{script}"'


def _event_key(spec: HookSpec) -> str:
    event = AGENT_HOOK_EVENTS.get(spec.stage)
    if event is None:
        raise ValueError(
            f"claude hook '{spec.id}' has stage {spec.stage!r}; "
            f"allowed: {sorted(AGENT_HOOK_EVENTS)}"
        )
    return event


def _managed_group(spec: HookSpec, hooks_relpath: str) -> dict:
    return {
        "matcher": spec.matcher or AGENT_HOOK_MATCHER,
        "hooks": [{"type": "command", "command": _agent_hook_command(spec, hooks_relpath)}],
    }


def _references_managed_script(group: object, script_paths: set[str]) -> bool:

    if not isinstance(group, dict):
        return False
    for hook in group.get("hooks") or []:
        if isinstance(hook, dict):
            command = hook.get("command")
            if isinstance(command, str) and any(path in command for path in script_paths):
                return True
    return False


def merge_agent_hooks(
    settings: dict,
    specs: list[HookSpec],
    hooks_relpath: str,
    strip_scripts: set[str] | None = None,
) -> dict:

    merged = dict(settings)
    hooks_section = merged.get(HOOKS_KEY)
    hooks_section = dict(hooks_section) if isinstance(hooks_section, dict) else {}

    script_paths = strip_scripts or {f"{hooks_relpath}/{spec.script}" for spec in specs}
    for event in AGENT_HOOK_EVENTS.values():
        existing = hooks_section.get(event)
        if isinstance(existing, list):
            hooks_section[event] = [
                group for group in existing if not _references_managed_script(group, script_paths)
            ]

    for spec in specs:
        event = _event_key(spec)
        groups = hooks_section.get(event)
        groups = groups if isinstance(groups, list) else []
        groups.append(_managed_group(spec, hooks_relpath))
        hooks_section[event] = groups

    merged[HOOKS_KEY] = hooks_section
    return merged


def agent_hook_mismatches(repo_root: Path, specs: list[HookSpec], hooks_relpath: str) -> list[str]:

    settings = _load_settings(repo_root / CLAUDE_SETTINGS_PATH)
    hooks_section = settings.get(HOOKS_KEY)
    hooks_section = hooks_section if isinstance(hooks_section, dict) else {}

    mismatches: list[str] = []
    for spec in specs:
        expected = _managed_group(spec, hooks_relpath)
        groups = hooks_section.get(_event_key(spec))
        groups = groups if isinstance(groups, list) else []
        found = any(
            isinstance(group, dict)
            and group.get("matcher") == expected["matcher"]
            and any(
                isinstance(hook, dict)
                and hook.get("type") == "command"
                and hook.get("command") == expected["hooks"][0]["command"]
                for hook in group.get("hooks") or []
            )
            for group in groups
        )
        if not found:
            mismatches.append(f"managed agent hook '{spec.id}' missing or out of sync")
    return mismatches


def excluded_agent_hooks_present(
    repo_root: Path, excluded_specs: list[HookSpec], hooks_relpath: str
) -> list[str]:
    settings = _load_settings(repo_root / CLAUDE_SETTINGS_PATH)
    hooks_section = settings.get(HOOKS_KEY)
    hooks_section = hooks_section if isinstance(hooks_section, dict) else {}
    groups: list = []
    for event in AGENT_HOOK_EVENTS.values():
        event_groups = hooks_section.get(event)
        if isinstance(event_groups, list):
            groups.extend(event_groups)

    return [
        f"managed agent hook '{spec.id}' excluded by technology selection"
        for spec in excluded_specs
        if any(
            _references_managed_script(group, {f"{hooks_relpath}/{spec.script}"})
            for group in groups
        )
    ]


def sync_agent_hooks(
    repo_root: Path,
    specs: list[HookSpec],
    hooks_relpath: str,
    excluded_specs: list[HookSpec] | None = None,
) -> bool:

    excluded_specs = excluded_specs or []
    if not specs and not excluded_specs:
        return False
    if not agent_hook_mismatches(repo_root, specs, hooks_relpath) and not (
        excluded_agent_hooks_present(repo_root, excluded_specs, hooks_relpath)
    ):
        return False

    path = repo_root / CLAUDE_SETTINGS_PATH
    settings = _load_settings(path)
    strip_scripts = {f"{hooks_relpath}/{spec.script}" for spec in (*specs, *excluded_specs)}
    merged = merge_agent_hooks(settings, specs, hooks_relpath, strip_scripts)

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(merged, indent=2) + "\n")
    return True


def remove_agent_hooks(repo_root: Path, specs: list[HookSpec], hooks_relpath: str) -> bool:

    path = repo_root / CLAUDE_SETTINGS_PATH
    if not path.exists() or not specs:
        return False
    settings = _load_settings(path)
    hooks_section = settings.get(HOOKS_KEY)
    if not isinstance(hooks_section, dict):
        return False

    script_paths = {f"{hooks_relpath}/{spec.script}" for spec in specs}
    changed = False
    for event in AGENT_HOOK_EVENTS.values():
        existing = hooks_section.get(event)
        if not isinstance(existing, list):
            continue
        kept = [g for g in existing if not _references_managed_script(g, script_paths)]
        if len(kept) == len(existing):
            continue
        changed = True
        if kept:
            hooks_section[event] = kept
        else:
            hooks_section.pop(event, None)
    if not changed:
        return False

    if hooks_section:
        settings[HOOKS_KEY] = hooks_section
    else:
        settings.pop(HOOKS_KEY, None)
    atomic_write_text(path, json.dumps(settings, indent=2) + "\n")
    return True
