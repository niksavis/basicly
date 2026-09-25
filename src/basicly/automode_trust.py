from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

USER_SETTINGS = Path(".claude") / "settings.json"
DEFAULTS_ENTRY = "$defaults"
TRUST_LABELS = ("trusted repo", "source control")
ENTRY_SHOWN_CHARS = 160

_LABEL_PREFIX = re.compile(r"^[\s*_`>-]*(?P<label>[a-z ]+?)[\s*_`]*:", re.IGNORECASE)
_SCHEME = re.compile(r"^(?:[a-z+]+://)?(?:[^@/\s]+@)?", re.IGNORECASE)
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_WRAPPING_PUNCTUATION = "`'\"()[]<>,;:.!?*"


@dataclass(frozen=True)
class _Reference:
    host: str
    owner: str
    name: str


def _reference(token: str) -> _Reference | None:
    bare = _SCHEME.sub("", token.strip(_WRAPPING_PUNCTUATION)).replace(":", "/")
    parts = [part for part in bare.removesuffix(".git").split("/") if part]
    host = parts.pop(0) if len(parts) > 1 and "." in parts[0] else ""
    if not all(_SEGMENT.match(part) or part == "*" for part in parts):
        return None
    match parts:
        case [owner] if host:
            return _Reference(host, owner, "*")
        case [owner, name]:
            return _Reference(host, owner, name)
        case _:
            return None


def _names_one_repository(entry: str) -> list[_Reference]:
    references = [ref for token in entry.split() if (ref := _reference(token))]
    if any(ref.name == "*" for ref in references) or "all repos" in entry.lower():
        return []
    return references


def _is_trust_entry(entry: str) -> bool:
    match = _LABEL_PREFIX.match(entry)
    return bool(match) and match["label"].strip().lower() in TRUST_LABELS


def _shown(entry: str) -> str:
    flat = " ".join(entry.split())
    return flat if len(flat) <= ENTRY_SHOWN_CHARS else flat[: ENTRY_SHOWN_CHARS - 3] + "..."


def _finding(settings: Path, entry: str, repos: list[_Reference]) -> str:
    owner = repos[0].owner
    host = next((ref.host for ref in repos if ref.host and ref.owner == owner), "")
    account = f"{host}/{owner}" if host else owner
    named = ", ".join(dict.fromkeys(f"{ref.owner}/{ref.name}" for ref in repos))
    return (
        f"auto mode trusts one repository: {settings} sets autoMode.environment without "
        f'"{DEFAULTS_ENTRY}", and its entry "{_shown(entry)}" names only {named}. Without '
        f'"{DEFAULTS_ENTRY}" this list replaces the built-in trust, so auto mode reads every '
        "other repository as external, blocks routine work there and pauses after 3 blocks in "
        f'a row or 20 in total. Fix: add "{DEFAULTS_ENTRY}" to autoMode.environment and widen '
        f'the entry to "Source control: {account} and all repos under it", or run '
        "/auto-mode-setup again."
    )


def single_repository_findings(home: Path) -> list[str]:
    settings = home / USER_SETTINGS
    if not settings.is_file():
        return []
    try:
        document = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [
            f"auto mode trust list: cannot read {settings} ({exc}); fix the file so Claude Code "
            "and this check can read its autoMode settings"
        ]
    auto_mode = document.get("autoMode") if isinstance(document, dict) else None
    environment = auto_mode.get("environment") if isinstance(auto_mode, dict) else None
    if not isinstance(environment, list):
        return []
    entries = [entry for entry in environment if isinstance(entry, str)]
    if DEFAULTS_ENTRY in (entry.strip() for entry in entries):
        return []
    return [
        _finding(settings, entry, repos)
        for entry in entries
        if _is_trust_entry(entry) and (repos := _names_one_repository(entry))
    ]
