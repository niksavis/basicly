"""Read-side of the tool-usage telemetry: turn counters into a report.

The ``tool-usage`` PostToolUse hook accumulates per-entry counters in
``.basicly/usage/tool-usage.json`` (plain tool names for shell pipeline
heads, ``skill:<name>`` entries for Skill invocations). This module joins
those counters against the skill catalog so the data can answer the question
it was collected for: which shipped tools and skills are actually used, and
which are candidates for culling.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

USAGE_FILE = Path(".basicly/usage/tool-usage.json")
SKILL_PREFIX = "skill:"


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


def load_usage(repo_root: Path) -> dict[str, dict] | None:
    """The raw counter map, or None when no usage file exists (hook inactive)."""
    usage_path = repo_root / USAGE_FILE
    if not usage_path.exists():
        return None
    try:
        data = json.loads(usage_path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


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
