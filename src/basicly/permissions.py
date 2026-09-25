from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .catalog import bundled_catalog_root

PERMISSIONS_DIRNAME = "permissions"
PERMISSIONS_MANIFEST = "permissions.yaml"

CLAUDE_TARGET = "claude"
PERMISSION_TARGETS = (CLAUDE_TARGET,)

SHELL_TOOLS = frozenset({"Bash", "PowerShell"})
AUTO_MODE_DROPPED_TOOLS = frozenset({"Agent", "Monitor"})
INTERPRETERS = frozenset(
    {"python", "node", "deno", "bun", "ruby", "perl", "php", "lua", "bash", "sh", "zsh"}
    | {"dash", "ksh", "fish", "pwsh", "powershell", "osascript"}
)
PACKAGE_MANAGERS = frozenset(
    {"uv", "uvx", "pip", "pipx", "poetry", "npm", "npx", "pnpm", "pnpx", "yarn", "bunx"}
    | {"cargo", "go", "gem", "bundle"}
)
COMMAND_WRAPPERS = frozenset({"env", "eval", "exec", "sudo", "xargs", "watch", "setsid"})

_RULE_RE = re.compile(r"(?P<tool>[A-Za-z_]\w*)(?:\((?P<content>.*)\))?", re.DOTALL)
_VERSIONED_PROGRAM_RE = re.compile(r"(?P<name>[a-z]+?)[0-9.]*")


@dataclass(frozen=True)
class DenyRule:
    id: str
    description: str
    claude: tuple[str, ...] = ()
    copilot: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClaudePermissions:
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    retired_allow: tuple[str, ...] = ()


def _catalog_permissions_dir() -> Path:
    return bundled_catalog_root() / PERMISSIONS_DIRNAME


def _str_list(entry: dict, key: str, rule: str, manifest: Path) -> tuple[str, ...]:
    values = entry.get(key) or []
    if not isinstance(values, list) or not all(isinstance(p, str) and p for p in values):
        raise ValueError(f"{manifest}: {rule} '{key}' must be a list of non-empty strings")
    return tuple(values)


def _read_manifest(permissions_dir: Path | None) -> tuple[Path, dict]:
    manifest = (permissions_dir or _catalog_permissions_dir()) / PERMISSIONS_MANIFEST
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    return manifest, data


def _entries(data: dict, section: str, manifest: Path, *, required: bool) -> list[dict]:
    entries = data.get(section)
    if entries is None and not required:
        return []
    if not isinstance(entries, list):
        raise ValueError(f"{manifest}: '{section}' must be a list")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{manifest}: each {section} rule must be a mapping")
        missing = [key for key in ("id", "description") if key not in entry]
        if missing:
            raise ValueError(f"{manifest}: {section} rule is missing {', '.join(missing)}")
    return entries


def load_deny_rules(permissions_dir: Path | None = None) -> list[DenyRule]:
    manifest, data = _read_manifest(permissions_dir)
    rules: list[DenyRule] = []
    for entry in _entries(data, "deny", manifest, required=True):
        rule = f"deny rule '{entry['id']}'"
        rules.append(
            DenyRule(
                id=str(entry["id"]),
                description=str(entry["description"]),
                claude=_str_list(entry, "claude", rule, manifest),
                copilot=_str_list(entry, "copilot", rule, manifest),
            )
        )
    return rules


def _claude_only_patterns(data: dict, section: str, manifest: Path) -> list[str]:
    patterns: list[str] = []
    for entry in _entries(data, section, manifest, required=False):
        rule = f"{section} rule '{entry['id']}'"
        other_keys = sorted(set(entry) - {"id", "description", CLAUDE_TARGET})
        if other_keys:
            raise ValueError(
                f"{manifest}: {rule} has {other_keys}; an allow rule projects to claude only"
            )
        patterns.extend(_str_list(entry, CLAUDE_TARGET, rule, manifest))
    return list(dict.fromkeys(patterns))


def _program_class(program: str) -> str | None:
    name = program.rsplit("/", 1)[-1]
    versioned = _VERSIONED_PROGRAM_RE.fullmatch(name)
    base = versioned["name"] if versioned else name
    for label, members in (
        ("an interpreter", INTERPRETERS),
        ("a package manager", PACKAGE_MANAGERS),
        ("a command wrapper", COMMAND_WRAPPERS),
    ):
        if name in members or base in members:
            return label
    return None


def auto_mode_drop_reason(pattern: str) -> str | None:
    match = _RULE_RE.fullmatch(pattern.strip())
    if match is None:
        return None
    tool, content = match["tool"], match["content"]
    if tool in AUTO_MODE_DROPPED_TOOLS:
        return f"auto mode drops every {tool} allow rule"
    if tool in SHELL_TOOLS:
        return _shell_command_drop_reason(tool, content or "", pattern)
    return None


def _shell_command_drop_reason(tool: str, content: str, pattern: str) -> str | None:
    words = content.removesuffix(":*").split()
    if not words or words == ["*"]:
        return f"a blanket {tool} allow grants arbitrary code execution"
    if "*" in words[0]:
        return f"a wildcard in the program position of {pattern!r} matches any program"
    program_class = _program_class(words[0])
    if program_class:
        return f"{words[0]!r} is {program_class}, so {pattern!r} can run arbitrary code"
    return None


def load_claude_permissions(permissions_dir: Path | None = None) -> ClaudePermissions:
    manifest, data = _read_manifest(permissions_dir)
    allow = _claude_only_patterns(data, "allow", manifest)
    retired = _claude_only_patterns(data, "retired_allow", manifest)
    for pattern in allow:
        reason = auto_mode_drop_reason(pattern)
        if reason:
            raise ValueError(f"{manifest}: allow pattern {pattern!r} is refused: {reason}")
    both = sorted(set(allow) & set(retired))
    if both:
        raise ValueError(f"{manifest}: {both} are both allowed and retired; keep one")
    return ClaudePermissions(
        allow=tuple(allow),
        deny=tuple(claude_deny_patterns(load_deny_rules(permissions_dir))),
        retired_allow=tuple(retired),
    )


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
