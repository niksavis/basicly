from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from . import skill_source
from .catalog_source import rel
from .config import load_project_paths
from .loader import load_fragments_from_roots, load_targets
from .planner import plan_outputs
from .schema import ValidationError

if TYPE_CHECKING:
    from .schema import Fragment

MARKERS = (
    "IMPORTANT",
    "CRITICAL",
    "ATTENTION",
    "CAUTION",
    "WARNING",
    "NOTE",
    "NEVER",
    "ALWAYS",
    "MUST",
    "REQUIRED",
    "DO NOT",
)

BUDGET = 1

_MARKER_RE = re.compile(r"\b(" + "|".join(MARKERS) + r")\b")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_SPAN_RE = re.compile(r"`[^`\n]*`")


class Site(NamedTuple):
    source: str
    marker: str


def _prose(body: str) -> str:
    return _SPAN_RE.sub(" ", _FENCE_RE.sub(" ", body))


def markers_in(body: str) -> list[str]:
    return _MARKER_RE.findall(_prose(body))


def _fragment_sites(repo_root: Path, fragments: list[Fragment]) -> list[Site]:
    sites: list[Site] = []
    for fragment in fragments:
        source = (
            rel(fragment.source_path, repo_root) if fragment.source_path else f"<{fragment.id}>"
        )
        sites.extend(Site(source, marker) for marker in markers_in(fragment.body))
    return sites


def _refusal(surface: str, sites: list[Site]) -> str:
    listed = ", ".join(f"{site.marker} in {site.source}" for site in sites)
    return (
        f"{surface}: {len(sites)} emphasis markers against a budget of {BUDGET} — {listed}. "
        "Emphasis is scarce: a second marker makes the first one ordinary, so the projection "
        "ends up shouting everywhere and read as flat. Drop all but the one rule that most "
        "needs to survive a long session, and state the rest plainly."
    )


def _projection_violations(repo_root: Path) -> list[str]:
    paths = load_project_paths(repo_root)
    targets_dir = repo_root / paths.targets_dir
    if not targets_dir.is_dir():
        return []
    targets = load_targets(targets_dir)
    if not targets:
        return []
    roots: list[tuple[Path, str | None]] = [(repo_root / paths.core_fragments_dir, "core")]
    fragments = load_fragments_from_roots(roots, {target.name for target in targets})

    violations: list[str] = []
    for item in plan_outputs(fragments, targets, repo_root):
        sites = _fragment_sites(repo_root, item.fragments)
        if len(sites) > BUDGET:
            violations.append(_refusal(rel(item.output_path, repo_root), sites))
    return violations


def _skill_violations(repo_root: Path) -> list[str]:
    try:
        skills = skill_source.discover_skills(repo_root)
    except ValidationError:
        return []
    violations: list[str] = []
    for skill in sorted(skills, key=lambda entry: entry.name):
        source = rel(skill.source_path, repo_root)
        sites = [Site(source, marker) for marker in markers_in(skill.instructions)]
        if len(sites) > BUDGET:
            violations.append(_refusal(f"skill {skill.name}", sites))
    return violations


def violations(repo_root: Path) -> list[str]:
    return [*_projection_violations(repo_root), *_skill_violations(repo_root)]
