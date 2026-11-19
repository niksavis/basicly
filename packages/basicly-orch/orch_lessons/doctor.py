from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess  # nosec B404
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from orch_state import herdr

from orch_lessons import log

CLAUDE_SETTINGS = (Path(".claude/settings.json"), Path(".claude/settings.local.json"))
PROBE = ("orch", "approve", "--request", "r")
PROBE_TEXT = " ".join(PROBE)
CLAUDE_DENY_FIX = "Bash(orch approve *)"
CODEX_DENY_FIX = 'prefix_rule(pattern=["orch", "approve"], decision="forbidden")'
COPILOT_PERMISSIONS = Path(".copilot/permissions-config.json")

Runner = Callable[[list[str]], str]


@dataclass(frozen=True)
class Report:
    lines: list[str]
    failed: bool


def _stem_match(command: str, stem: str) -> bool:
    return command == stem or command.startswith(stem + " ")


def bash_rule_matches(rule: str, command: str) -> bool:
    if rule in ("Bash", "Bash(*)"):
        return True
    if not (rule.startswith("Bash(") and rule.endswith(")")):
        return False
    content = rule[5:-1]
    for suffix in (":*", " *"):
        if content.endswith(suffix):
            return _stem_match(command, content[: -len(suffix)])
    if "*" in content:
        return fnmatch.fnmatchcase(command, content)
    return command == content


def allows_orch(rule: str) -> bool:
    return bash_rule_matches(rule, PROBE_TEXT)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise log.RefusedError(f"{path} cannot be read as JSON ({error})") from error


def _rules(path: Path, kind: str) -> list[str]:
    data = _read_json(path)
    permissions = data.get("permissions") if isinstance(data, dict) else None
    found = permissions.get(kind) if isinstance(permissions, dict) else None
    return [rule for rule in found if isinstance(rule, str)] if isinstance(found, list) else []


def settings_files(repo: Path, environ: dict[str, str]) -> list[Path]:
    files = [repo / name for name in CLAUDE_SETTINGS]
    home = environ.get("HOME", "")
    if home:
        files.append(Path(home) / ".claude" / "settings.json")
    return files


def claude_lines(files: list[Path]) -> tuple[list[str], bool]:
    denies = [(path, rule) for path in files for rule in _rules(path, "deny") if allows_orch(rule)]
    allows = [(path, rule) for path in files for rule in _rules(path, "allow") if allows_orch(rule)]
    if denies:
        path, rule = denies[0]
        return [f"claude: deny rule {rule!r} in {path} covers orch approve"], False
    return [
        f"{path}: allow rule {rule!r} lets a session run orch approve; add the deny rule "
        f"{CLAUDE_DENY_FIX!r}, which wins over every allow rule"
        for path, rule in allows
    ], bool(allows)


def run_codex(argv: list[str]) -> str:
    done = subprocess.run(  # noqa: S603 # nosec B603 - list argv, no shell, codex resolved by which()
        argv, capture_output=True, text=True, check=False, timeout=30
    )
    return done.stdout


def codex_lines(
    repo: Path,
    environ: dict[str, str],
    runner: Runner = run_codex,
    find: Callable[[str, str | None], str | None] = lambda name, path: shutil.which(
        name, path=path
    ),
) -> tuple[list[str], bool]:
    home = environ.get("HOME", "")
    folders = [repo / ".codex" / "rules", *([Path(home) / ".codex" / "rules"] if home else [])]
    files = sorted(path for folder in folders for path in folder.glob("*.rules"))
    if not files:
        return [], False
    codex = find("codex", environ.get("PATH"))
    if codex is None:
        return ["codex rules: not checked, codex is not on PATH"], False
    argv = [codex, "execpolicy", "check"]
    for path in files:
        argv += ["--rules", str(path)]
    try:
        result = json.loads(runner([*argv, *PROBE]) or "{}")
    except ValueError:
        return ["codex rules: not checked, codex execpolicy check gave no JSON"], False
    if result.get("decision") != "allow":
        return [], False
    names = ", ".join(str(path) for path in files)
    return [
        f"codex rules {names}: they allow orch approve; add {CODEX_DENY_FIX}, "
        "the most restrictive decision wins"
    ], True


def _covers(location: str, repo: Path) -> bool:
    return repo == Path(location) or Path(location) in repo.parents


def copilot_lines(repo: Path, environ: dict[str, str]) -> tuple[list[str], bool]:
    home = environ.get("HOME", "")
    if not home:
        return [], False
    path = Path(home) / COPILOT_PERMISSIONS
    data = _read_json(path)
    locations = data.get("locations") if isinstance(data, dict) else None
    if not isinstance(locations, dict):
        return [], False
    lines = [
        f"{path}: location {location} saves an approval for {name!r}; Copilot then runs "
        "orch approve without asking; remove it"
        for location, entry in locations.items()
        if isinstance(entry, dict) and _covers(str(location), repo)
        for approval in entry.get("tool_approvals") or []
        if isinstance(approval, dict) and approval.get("kind") == "commands"
        for name in approval.get("commandIdentifiers") or []
        if isinstance(name, str) and _stem_match(PROBE_TEXT, name.removesuffix(":*"))
    ]
    return lines, bool(lines)


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


def run(cwd: Path, environ: dict[str, str], runner: Runner = run_codex) -> Report:
    repo = log.repo_root(cwd)
    checks = [
        claude_lines(settings_files(repo, environ)),
        codex_lines(repo, environ, runner),
        copilot_lines(repo, environ),
    ]
    unchecked = [] if environ.get("HOME") else ["user settings: not checked, HOME is not set"]
    lessons, stale = lesson_lines(cwd, environ)
    lines = [
        *(line for found, _ in checks for line in found),
        *unchecked,
        wake_adapter_line(environ),
        *lessons,
    ]
    failed = any(refused for _, refused in checks) or stale
    lines.append(f"doctor: {'FAILED' if failed else 'OK'}")
    return Report(lines, failed)
