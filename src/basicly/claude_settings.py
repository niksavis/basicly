"""Claude Code settings management for the harness (Claude target only).

Claude Code's background-isolation guard (``worktree.bgIsolation``, default on)
forces a background agent to isolate into ``.claude/worktrees/`` before editing,
which conflicts with the harness's own sibling ``<repo>.worktrees/`` isolation
(EnterWorktree cannot target a sibling path). To run the harness under Claude
Code the guard must be ``none`` — the harness provides isolation itself.

This module also projects the catalog's ``manager: claude`` hook specs into the
``hooks`` section of the same file: Claude Code agent hooks gate at *tool time*
(a PreToolUse command exiting 2 blocks the tool call), which is how the
protect-generated guard stops an agent from hand-editing projected files before
any commit-time gate could see the damage.

Values are written to the *committed* ``.claude/settings.json`` (the team-wide
default that ships with the repo). Per Claude's verified settings precedence
(local ``.claude/settings.local.json`` overrides project ``.claude/settings.json``
overrides user global), any user may override it locally without touching the
committed default. Codex and Copilot have no equivalent setting.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from .hooks import HookSpec

CLAUDE_SETTINGS_PATH = Path(".claude/settings.json")
WORKTREE_KEY = "worktree"
BG_ISOLATION_KEY = "bgIsolation"
BG_ISOLATION_NONE = "none"

HOOKS_KEY = "hooks"
PRE_TOOL_USE_KEY = "PreToolUse"
# The file-writing tool family; each guard script decides per-payload.
AGENT_HOOK_MATCHER = "Edit|Write|MultiEdit|NotebookEdit"


def _load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def current_bg_isolation(repo_root: Path) -> str | None:
    """Return the committed ``worktree.bgIsolation`` value, or None when unset."""
    settings = _load_settings(repo_root / CLAUDE_SETTINGS_PATH)
    section = settings.get(WORKTREE_KEY)
    if isinstance(section, dict):
        value = section.get(BG_ISOLATION_KEY)
        if isinstance(value, str):
            return value
    return None


def set_bg_isolation_none(repo_root: Path) -> bool:
    """Set ``worktree.bgIsolation=none`` in the committed ``.claude/settings.json``.

    Merges into existing settings, preserving every other key. Returns True when
    the file was changed, False when it was already ``none``.
    """
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
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return True


def _agent_hook_command(spec: HookSpec, hooks_relpath: str) -> str:
    """Return the shell command Claude Code runs for a managed agent hook.

    Mirrors the pre-commit entries: ``uv run python`` with a quoted script path,
    so the same interpreter/venv conventions apply to both hook managers.
    """
    return f"uv run python {shlex.quote(f'{hooks_relpath}/{spec.script}')}"


def _managed_group(spec: HookSpec, hooks_relpath: str) -> dict:
    return {
        "matcher": AGENT_HOOK_MATCHER,
        "hooks": [{"type": "command", "command": _agent_hook_command(spec, hooks_relpath)}],
    }


def _references_managed_script(group: object, script_names: set[str]) -> bool:
    """True when a PreToolUse group runs one of the managed hook scripts."""
    if not isinstance(group, dict):
        return False
    for hook in group.get("hooks") or []:
        if isinstance(hook, dict):
            command = hook.get("command")
            if isinstance(command, str) and any(name in command for name in script_names):
                return True
    return False


def merge_agent_hooks(
    settings: dict,
    specs: list[HookSpec],
    hooks_relpath: str,
    strip_scripts: set[str] | None = None,
) -> dict:
    """Return settings with basicly's managed PreToolUse hooks merged in.

    Managed groups (matched by the hook script they run) are stripped and a
    fresh group per spec is appended, so re-running is idempotent and any
    consumer-authored hooks are preserved untouched. ``strip_scripts`` widens
    the strip set beyond the rendered specs so a hook a technology selection
    excludes is removed rather than stranded.
    """
    merged = dict(settings)
    hooks_section = merged.get(HOOKS_KEY)
    hooks_section = dict(hooks_section) if isinstance(hooks_section, dict) else {}

    script_names = strip_scripts or {spec.script for spec in specs}
    existing = hooks_section.get(PRE_TOOL_USE_KEY)
    kept = [
        group
        for group in (existing if isinstance(existing, list) else [])
        if not _references_managed_script(group, script_names)
    ]

    kept.extend(_managed_group(spec, hooks_relpath) for spec in specs)
    hooks_section[PRE_TOOL_USE_KEY] = kept
    merged[HOOKS_KEY] = hooks_section
    return merged


def agent_hook_mismatches(repo_root: Path, specs: list[HookSpec], hooks_relpath: str) -> list[str]:
    """Return a reason per managed agent hook missing from the committed settings.

    A managed hook matches when some PreToolUse group carries the expected
    matcher and command; extra consumer keys and groups are allowed.
    """
    settings = _load_settings(repo_root / CLAUDE_SETTINGS_PATH)
    hooks_section = settings.get(HOOKS_KEY)
    groups = hooks_section.get(PRE_TOOL_USE_KEY) if isinstance(hooks_section, dict) else None
    groups = groups if isinstance(groups, list) else []

    mismatches: list[str] = []
    for spec in specs:
        expected = _managed_group(spec, hooks_relpath)
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


def excluded_agent_hooks_present(repo_root: Path, excluded_specs: list[HookSpec]) -> list[str]:
    """Return a reason per excluded managed agent hook still wired in the settings."""
    settings = _load_settings(repo_root / CLAUDE_SETTINGS_PATH)
    hooks_section = settings.get(HOOKS_KEY)
    groups = hooks_section.get(PRE_TOOL_USE_KEY) if isinstance(hooks_section, dict) else None
    groups = groups if isinstance(groups, list) else []

    return [
        f"managed agent hook '{spec.id}' excluded by technology selection"
        for spec in excluded_specs
        if any(_references_managed_script(group, {spec.script}) for group in groups)
    ]


def sync_agent_hooks(
    repo_root: Path,
    specs: list[HookSpec],
    hooks_relpath: str,
    excluded_specs: list[HookSpec] | None = None,
) -> bool:
    """Project managed agent hooks into ``.claude/settings.json``.

    Returns True when the file changed, False when already in sync. No-op
    (returns False) when there is nothing to project or prune. Hooks in
    ``excluded_specs`` (excluded by a technology selection) are stripped.
    """
    excluded_specs = excluded_specs or []
    if not specs and not excluded_specs:
        return False
    if not agent_hook_mismatches(repo_root, specs, hooks_relpath) and not (
        excluded_agent_hooks_present(repo_root, excluded_specs)
    ):
        return False

    path = repo_root / CLAUDE_SETTINGS_PATH
    settings = _load_settings(path)
    strip_scripts = {spec.script for spec in (*specs, *excluded_specs)}
    merged = merge_agent_hooks(settings, specs, hooks_relpath, strip_scripts)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return True


def remove_agent_hooks(repo_root: Path, specs: list[HookSpec]) -> bool:
    """Strip basicly's managed agent hooks from the settings (uninstall path).

    Drops every PreToolUse group referencing a managed script; empty ``hooks``
    containers left behind are removed. Returns True when the file changed.
    """
    path = repo_root / CLAUDE_SETTINGS_PATH
    if not path.exists() or not specs:
        return False
    settings = _load_settings(path)
    hooks_section = settings.get(HOOKS_KEY)
    if not isinstance(hooks_section, dict):
        return False
    existing = hooks_section.get(PRE_TOOL_USE_KEY)
    if not isinstance(existing, list):
        return False

    script_names = {spec.script for spec in specs}
    kept = [g for g in existing if not _references_managed_script(g, script_names)]
    if len(kept) == len(existing):
        return False

    if kept:
        hooks_section[PRE_TOOL_USE_KEY] = kept
    else:
        hooks_section.pop(PRE_TOOL_USE_KEY, None)
    if hooks_section:
        settings[HOOKS_KEY] = hooks_section
    else:
        settings.pop(HOOKS_KEY, None)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return True
