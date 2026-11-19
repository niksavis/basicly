from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path

from orch_state import herdr

from orch_lessons import log

CLAUDE_SETTINGS = (Path(".claude/settings.json"), Path(".claude/settings.local.json"))
GUARDED_COMMANDS = ("orch", "orch approve")


@dataclass(frozen=True)
class Report:
    lines: list[str]
    failed: bool


def _bash_pattern(rule: str) -> str | None:
    if rule == "Bash":
        return "*"
    if rule.startswith("Bash(") and rule.endswith(")"):
        content = rule[5:-1]
        return content[:-2] + "*" if content.endswith(":*") else content
    return None


def allows_orch(rule: str) -> bool:
    pattern = _bash_pattern(rule)
    return pattern is not None and any(
        fnmatch.fnmatchcase(command, pattern) for command in GUARDED_COMMANDS
    )


def _allow_rules(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        raise log.RefusedError(f"{path} cannot be read as JSON ({error})") from error
    permissions = data.get("permissions") if isinstance(data, dict) else None
    allow = permissions.get("allow") if isinstance(permissions, dict) else None
    return [rule for rule in allow if isinstance(rule, str)] if isinstance(allow, list) else []


def settings_files(repo: Path, environ: dict[str, str]) -> list[Path]:
    files = [repo / name for name in CLAUDE_SETTINGS]
    home = environ.get("HOME", "")
    if home:
        files.append(Path(home) / ".claude" / "settings.json")
    return files


def orch_allow_lines(files: list[Path]) -> list[str]:
    return [
        f"{path}: allow rule {rule!r} lets a worker run orch; allow orch-wait by name instead"
        for path in files
        for rule in _allow_rules(path)
        if allows_orch(rule)
    ]


def wake_adapter_line(environ: dict[str, str]) -> str:
    pane = herdr.pane_id(environ)
    if environ.get("HERDR_ENV") and pane and herdr.binary(environ):
        return f"wake adapter: herdr (pane {pane})"
    return "wake adapter: none; an idle interactive session gets its mail only at its next turn"


def lesson_lines(cwd: Path, environ: dict[str, str]) -> tuple[list[str], bool]:
    try:
        root = log.find_root(cwd, environ)
    except log.RefusedError as missing:
        return [f"lessons: not checked, {missing}"], False
    old = log.stale(root)
    lines = [
        f"lesson {lesson.id} open since {lesson.time.date()}: {lesson.requirement}"
        for lesson in old
    ]
    lines.append(f"lessons: {len(old)} open longer than {log.STALE_AFTER.days} days")
    return lines, bool(old)


def run(cwd: Path, environ: dict[str, str]) -> Report:
    refused = orch_allow_lines(settings_files(log.repo_root(cwd), environ))
    lessons, stale = lesson_lines(cwd, environ)
    lines = [*refused, wake_adapter_line(environ), *lessons]
    failed = bool(refused) or stale
    lines.append(f"doctor: {'FAILED' if failed else 'OK'}")
    return Report(lines, failed)
