from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

USAGE_FILE = Path(".basicly/usage/tool-usage.json")
VERIFY_CHECKS_FILE = Path(".basicly/usage/verify-checks.json")
SKILL_PREFIX = "skill:"
VERIFY_CHECK_PREFIX = "verify-check:"

LOCAL_BIN_DIRS = (
    Path("node_modules/.bin"),
    Path(".venv/bin"),
    Path(".venv/Scripts"),
)

_FENCE = re.compile(r"^\s*```([A-Za-z0-9_+-]*)\s*$")
_SHELL_FENCE_LANGUAGES = frozenset({"bash", "console", "sh", "shell", "zsh"})
_COMMAND_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class UsageEntry:
    name: str
    count: int
    last_used: str


@dataclass(frozen=True)
class UsageReport:
    tools: tuple[UsageEntry, ...]
    skills: tuple[UsageEntry, ...]
    never_used_skills: tuple[str, ...]
    unresolved: tuple[UsageEntry, ...]


def _load_counters(path: Path) -> dict[str, dict] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def load_usage(repo_root: Path) -> dict[str, dict] | None:
    return _load_counters(repo_root / USAGE_FILE)


def load_verify_checks(repo_root: Path) -> dict[str, dict] | None:

    return _load_counters(repo_root / VERIFY_CHECKS_FILE)


def record_verify_check(repo_root: Path, name: str) -> None:

    path = repo_root / VERIFY_CHECKS_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        gitignore = path.parent / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n", encoding="utf-8")
        counters = _load_counters(path) or {}
        entry = counters.get(name)
        count = entry.get("count") if isinstance(entry, dict) else None
        if not isinstance(count, int) or isinstance(count, bool):
            count = 0
        counters[name] = {
            "count": count + 1,
            "last_used": datetime.now(UTC).date().isoformat(),
        }
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(counters, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return


def catalog_commands(instruction_texts: Iterable[str]) -> frozenset[str]:

    names: set[str] = set()
    for text in instruction_texts:
        language: str | None = None
        for line in text.splitlines():
            fence = _FENCE.match(line)
            if fence:
                language = None if language is not None else fence.group(1).lower()
                continue
            if language not in _SHELL_FENCE_LANGUAGES:
                continue
            stripped = line.strip().removeprefix("$ ")
            if not stripped or stripped.startswith("#"):
                continue
            head = Path(stripped.split()[0]).name
            if _COMMAND_NAME.match(head):
                names.add(head)
    return frozenset(names)


def _local_bin_path(repo_root: Path) -> str | None:
    present = [repo_root / directory for directory in LOCAL_BIN_DIRS]
    return os.pathsep.join(str(d) for d in present if d.is_dir()) or None


def _resolves(name: str, catalog: Collection[str], local_bin: str | None) -> bool:
    if not name:
        return False
    if name in catalog:
        return True
    if shutil.which(name) is not None:
        return True
    return local_bin is not None and shutil.which(name, path=local_bin) is not None


def build_report(
    repo_root: Path,
    catalog_skill_slugs: list[str],
    commands: Collection[str] = (),
) -> UsageReport | None:

    raw = load_usage(repo_root)
    if raw is None:
        return None

    local_bin = _local_bin_path(repo_root)
    tools: list[UsageEntry] = []
    skills: list[UsageEntry] = []
    unresolved: list[UsageEntry] = []
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        count = entry.get("count")
        if not isinstance(count, int):
            continue
        record = UsageEntry(name, count, str(entry.get("last_used", "")))
        if name.startswith(SKILL_PREFIX):
            skills.append(UsageEntry(name[len(SKILL_PREFIX) :], count, record.last_used))
        elif _resolves(name, commands, local_bin):
            tools.append(record)
        else:
            unresolved.append(record)

    used_skill_names = {entry.name for entry in skills}
    never_used = tuple(slug for slug in sorted(catalog_skill_slugs) if slug not in used_skill_names)
    by_count = lambda entry: (-entry.count, entry.name)  # noqa: E731
    return UsageReport(
        tools=tuple(sorted(tools, key=by_count)),
        skills=tuple(sorted(skills, key=by_count)),
        never_used_skills=never_used,
        unresolved=tuple(sorted(unresolved, key=by_count)),
    )
