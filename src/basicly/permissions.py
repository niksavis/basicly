from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .catalog import bundled_catalog_root

PERMISSIONS_DIRNAME = "permissions"
PERMISSIONS_MANIFEST = "permissions.yaml"

CLAUDE_TARGET = "claude"
PERMISSION_TARGETS = (CLAUDE_TARGET,)


@dataclass(frozen=True)
class DenyRule:
    id: str
    description: str
    claude: tuple[str, ...] = ()
    copilot: tuple[str, ...] = ()


def _catalog_permissions_dir() -> Path:
    return bundled_catalog_root() / PERMISSIONS_DIRNAME


def _str_list(entry: dict, key: str, rule_id: str, manifest: Path) -> tuple[str, ...]:
    values = entry.get(key) or []
    if not isinstance(values, list) or not all(isinstance(p, str) and p for p in values):
        raise ValueError(
            f"{manifest}: deny rule '{rule_id}' '{key}' must be a list of non-empty strings"
        )
    return tuple(values)


def load_deny_rules(permissions_dir: Path | None = None) -> list[DenyRule]:

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
    seen: set[str] = set()
    patterns: list[str] = []
    for rule in rules:
        for pattern in getattr(rule, attr):
            if pattern not in seen:
                seen.add(pattern)
                patterns.append(pattern)
    return patterns


def claude_deny_patterns(rules: list[DenyRule]) -> list[str]:
    return _flat_patterns(rules, "claude")


def copilot_deny_specs(rules: list[DenyRule]) -> list[str]:
    return _flat_patterns(rules, "copilot")
