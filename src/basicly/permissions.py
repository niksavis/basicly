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
A projected config-file deny is Claude-only: Copilot CLI has no config-file deny
(only a session-scoped ``--deny-tool`` flag; github/copilot-cli#2398) and Codex
forbids project-scope override of ``sandbox_mode``/``approval_policy`` by design,
so those two are enforceable only at invocation time — tracked at the runner seam
by basicly-lqz5 (copilot ``--deny-tool``) and basicly-t0kt (codex ``--sandbox``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .catalog import bundled_catalog_root

PERMISSIONS_DIRNAME = "permissions"
PERMISSIONS_MANIFEST = "permissions.yaml"

CLAUDE_TARGET = "claude"
# Claude is the only config-file deny target; Copilot/Codex are invocation-only
# (see the module docstring: basicly-lqz5 / basicly-t0kt at the runner seam).
PERMISSION_TARGETS = (CLAUDE_TARGET,)


@dataclass(frozen=True)
class DenyRule:
    """One semantic deny guardrail with its per-target native patterns."""

    id: str
    description: str
    claude: tuple[str, ...] = ()
    # Copilot `--deny-tool` specs injected at invocation by the runner
    # (basicly-lqz5). Empty for rules with no faithful copilot form (the `.env`
    # read/edit rules); see the manifest header for the prefix-match limitation.
    copilot: tuple[str, ...] = ()


def _catalog_permissions_dir() -> Path:
    return bundled_catalog_root() / PERMISSIONS_DIRNAME


def _str_list(entry: dict, key: str, rule_id: str, manifest: Path) -> tuple[str, ...]:
    """Validate an optional per-target pattern list: absent or a list of non-empty strings."""
    values = entry.get(key) or []
    if not isinstance(values, list) or not all(isinstance(p, str) and p for p in values):
        raise ValueError(
            f"{manifest}: deny rule '{rule_id}' '{key}' must be a list of non-empty strings"
        )
    return tuple(values)


def load_deny_rules(permissions_dir: Path | None = None) -> list[DenyRule]:
    """Load deny rules from ``permissions.yaml`` in the given (or bundled) dir.

    Validated imperatively (the lightweight ``hooks.yaml`` pattern, no JSON
    schema): each rule needs an ``id`` and ``description``, and each per-target
    pattern list (``claude``, ``copilot``) — when present — must be a list of
    non-empty strings.
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
        rule_id = str(entry["id"])
        rules.append(
            DenyRule(
                id=rule_id,
                description=str(entry["description"]),
                claude=_str_list(entry, "claude", rule_id, manifest),
                copilot=_str_list(entry, "copilot", rule_id, manifest),
            )
        )
    return rules


def _flat_patterns(rules: list[DenyRule], attr: str) -> list[str]:
    """The flat, de-duplicated per-target patterns across *rules*, in source order."""
    seen: set[str] = set()
    patterns: list[str] = []
    for rule in rules:
        for pattern in getattr(rule, attr):
            if pattern not in seen:
                seen.add(pattern)
                patterns.append(pattern)
    return patterns


def claude_deny_patterns(rules: list[DenyRule]) -> list[str]:
    """The flat, de-duplicated Claude deny patterns, in source order."""
    return _flat_patterns(rules, "claude")


def copilot_deny_specs(rules: list[DenyRule]) -> list[str]:
    """The flat, de-duplicated Copilot ``--deny-tool`` specs, in source order."""
    return _flat_patterns(rules, "copilot")
