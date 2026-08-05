"""Execution telemetry: the counters, and the report that joins them to the catalog.

The ``tool-usage`` PostToolUse hook accumulates per-entry counters in
``.basicly/usage/tool-usage.json`` (plain tool names for shell pipeline
heads, ``skill:<name>`` entries for Skill invocations). This module joins
those counters against the skill catalog so the data can answer the question
it was collected for: which shipped tools and skills are actually used, and
which are candidates for culling.

It also owns the second counter file, ``.basicly/usage/verify-checks.json``:
one entry per ``[[verify.checks]]`` check the verify engine has run and
watched pass. That one is written here rather than by a hook because a check
is never typed at a shell — it exists only as a declaration, so the engine
running it is the only event that can witness it (basicly-3yi3).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

USAGE_FILE = Path(".basicly/usage/tool-usage.json")
VERIFY_CHECKS_FILE = Path(".basicly/usage/verify-checks.json")
SKILL_PREFIX = "skill:"
# Namespace for a verify check inside the union :func:`basicly.release.recorded_executions`
# builds. A check's name is unique per declaration but shares that map with tool names,
# and unprefixed a `pytest` someone typed would answer for the check called `pytest` —
# the wrapper-witness defect in a new place.
VERIFY_CHECK_PREFIX = "verify-check:"


@dataclass(frozen=True)
class UsageEntry:
    """One counter: a tool or skill name with its count and last-used date."""

    name: str
    count: int
    last_used: str


@dataclass(frozen=True)
class UsageReport:
    """Tool and skill usage joined against the catalog's skill slugs."""

    tools: tuple[UsageEntry, ...]
    skills: tuple[UsageEntry, ...]
    never_used_skills: tuple[str, ...]


def _load_counters(path: Path) -> dict[str, dict] | None:
    """The raw counter map at *path*, or None when it is absent or unreadable."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def load_usage(repo_root: Path) -> dict[str, dict] | None:
    """The raw counter map, or None when no usage file exists (hook inactive)."""
    return _load_counters(repo_root / USAGE_FILE)


def load_verify_checks(repo_root: Path) -> dict[str, dict] | None:
    """Per-check execution counters, or None when the engine has never run here.

    Keyed by the check's ``name`` — the declaration itself, which nothing but the
    engine running that declaration can increment. None and an empty map are the same
    thing to this reader and deliberately kept apart for the caller: the release gate
    fails closed on *no ledger at all*, and must not read an absent file as a pass.
    """
    return _load_counters(repo_root / VERIFY_CHECKS_FILE)


def record_verify_check(repo_root: Path, name: str) -> None:
    """Count one engine execution of the verify check *name*; never raises.

    Telemetry, never a gate — the caller is mid-verdict, so every failure path here is
    swallowed rather than turned into a failed check. The usage directory self-ignores
    (``.gitignore`` holding ``*``, written on first use exactly as the ``tool-usage``
    hook writes it) because the release this record unblocks also refuses a dirty tree.

    Writes are atomic through a pid-scoped temporary file: two verify runs in one
    checkout would otherwise interleave a truncated write with the other's rename, and
    a lost count here is a false refusal at tag time — the very defect this records
    against. A corrupt or non-integer counter restarts at zero rather than propagating.
    """
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


def build_report(repo_root: Path, catalog_skill_slugs: list[str]) -> UsageReport | None:
    """Join the counters against the catalog; None when no data was recorded."""
    raw = load_usage(repo_root)
    if raw is None:
        return None

    tools: list[UsageEntry] = []
    skills: list[UsageEntry] = []
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        count = entry.get("count")
        if not isinstance(count, int):
            continue
        record = UsageEntry(name, count, str(entry.get("last_used", "")))
        if name.startswith(SKILL_PREFIX):
            skills.append(UsageEntry(name[len(SKILL_PREFIX) :], count, record.last_used))
        else:
            tools.append(record)

    used_skill_names = {entry.name for entry in skills}
    never_used = tuple(slug for slug in sorted(catalog_skill_slugs) if slug not in used_skill_names)
    by_count = lambda entry: (-entry.count, entry.name)  # noqa: E731
    return UsageReport(
        tools=tuple(sorted(tools, key=by_count)),
        skills=tuple(sorted(skills, key=by_count)),
        never_used_skills=never_used,
    )
