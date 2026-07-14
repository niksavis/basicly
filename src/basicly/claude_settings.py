"""Claude Code settings management for the harness (Claude target only).

Claude Code's background-isolation guard (``worktree.bgIsolation``, default on)
forces a background agent to isolate into ``.claude/worktrees/`` before editing,
which conflicts with the harness's own sibling ``<repo>.worktrees/`` isolation
(EnterWorktree cannot target a sibling path). To run the harness under Claude
Code the guard must be ``none`` — the harness provides isolation itself.

The value is written to the *committed* ``.claude/settings.json`` (the team-wide
default that ships with the repo). Per Claude's verified settings precedence
(local ``.claude/settings.local.json`` overrides project ``.claude/settings.json``
overrides user global), any user may override it locally without touching the
committed default. Codex and Copilot have no equivalent setting.
"""

from __future__ import annotations

import json
from pathlib import Path

CLAUDE_SETTINGS_PATH = Path(".claude/settings.json")
WORKTREE_KEY = "worktree"
BG_ISOLATION_KEY = "bgIsolation"
BG_ISOLATION_NONE = "none"


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
