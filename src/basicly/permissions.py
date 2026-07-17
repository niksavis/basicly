"""Project the baseline agent-permissions deny-list into consumer configs (basicly-u0zg).

The catalog's strongest in-flight guardrail — a tool-call deny-list (destructive
git, gate-bypass flags, ``.env`` access) — historically lived only in this repo's
hand-authored ``.claude/settings.json`` and was never projected, so consumers who
``basicly install`` inherited none of it (foundry spike Dimension 2). This module
makes the deny-list a catalog-managed artifact, described tool-agnostically in
``permissions.yaml`` and projected per target the way hooks are.

One deliberate difference from the hooks manager: a hook is identified by the
script it runs, so ``hooks-build`` can prune its own stale entries. A deny entry
is a flat native pattern with no such per-entry provenance marker, so projection
is **ensure-present**, not clobber-and-replace — the managed patterns are merged
into the co-owned deny-list (dedup, order-preserving), consumer-added entries are
left untouched, and drift is a subset match (every managed pattern present;
extras allowed). Nothing managed is pruned: an extra deny is fail-safe, and
without a marker there is no way to tell a consumer's identical entry from ours.
The per-target rendering (Claude ``permissions.deny`` here; Copilot/Codex in
basicly-u0zg.2) lives in the target modules; this module owns the source model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .catalog import bundled_catalog_root

PERMISSIONS_DIRNAME = "permissions"
PERMISSIONS_MANIFEST = "permissions.yaml"

CLAUDE_TARGET = "claude"
# Copilot and Codex targets are added in basicly-u0zg.2.
PERMISSION_TARGETS = (CLAUDE_TARGET,)


@dataclass(frozen=True)
class DenyRule:
    """One semantic deny guardrail with its per-target native patterns."""

    id: str
    description: str
    claude: tuple[str, ...] = ()


def _catalog_permissions_dir() -> Path:
    return bundled_catalog_root() / PERMISSIONS_DIRNAME


def load_deny_rules(permissions_dir: Path | None = None) -> list[DenyRule]:
    """Load deny rules from ``permissions.yaml`` in the given (or bundled) dir.

    Validated imperatively (the lightweight ``hooks.yaml`` pattern, no JSON
    schema): each rule needs an ``id`` and ``description``, and ``claude`` — when
    present — must be a list of non-empty strings.
    """
    permissions_dir = permissions_dir or _catalog_permissions_dir()
    manifest = permissions_dir / PERMISSIONS_MANIFEST
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    entries = data.get("deny")
    if not isinstance(entries, list):
        raise ValueError(f"{manifest}: 'deny' must be a list")

    rules: list[DenyRule] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{manifest}: each deny rule must be a mapping")
        missing = [key for key in ("id", "description") if key not in entry]
        if missing:
            raise ValueError(f"{manifest}: deny rule is missing {', '.join(missing)}")
        claude = entry.get("claude") or []
        if not isinstance(claude, list) or not all(isinstance(p, str) and p for p in claude):
            raise ValueError(
                f"{manifest}: deny rule '{entry['id']}' 'claude' "
                "must be a list of non-empty strings"
            )
        rules.append(
            DenyRule(
                id=str(entry["id"]),
                description=str(entry["description"]),
                claude=tuple(claude),
            )
        )
    return rules


def claude_deny_patterns(rules: list[DenyRule]) -> list[str]:
    """The flat, de-duplicated Claude deny patterns, in source order."""
    seen: set[str] = set()
    patterns: list[str] = []
    for rule in rules:
        for pattern in rule.claude:
            if pattern not in seen:
                seen.add(pattern)
                patterns.append(pattern)
    return patterns
