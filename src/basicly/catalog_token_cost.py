from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__, read_cost, skill_source
from .catalog_source import load_mapping, rel
from .config import load_project_paths
from .loader import load_fragments_from_roots, load_targets
from .planner import plan_outputs
from .schema import ValidationError

if TYPE_CHECKING:
    from .schema import Fragment, PlannedOutput, Target

DECLARED_COST_KEY = "token_cost"
LISTING_SURFACE = "listing"
REQUIRED_FROM_VERSION = "0.11.0"
_TOLERANCE_FRACTION = 0.10
_TOLERANCE_FLOOR = 8


def _leading_digits(part: str) -> str:
    digits: list[str] = []
    for char in part:
        if not char.isdigit():
            break
        digits.append(char)
    return "".join(digits)


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in version.split("."):
        digits = _leading_digits(part)
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def window_open(version: str = __version__) -> bool:
    return _version_tuple(version) < _version_tuple(REQUIRED_FROM_VERSION)


def tolerance(measured: int) -> int:
    return max(_TOLERANCE_FLOOR, round(measured * _TOLERANCE_FRACTION))


def _always_on_keys(targets: list[Target]) -> set[tuple[str, str]]:

    return {
        (target.name, output.name) for target in targets for output in target.outputs if output.path
    }


def _render(templates_dir: Path, item: PlannedOutput) -> str:
    module = importlib.import_module(f"basicly.renderers.{item.target_name}")
    return str(module.render(item, templates_dir, __version__))


def _target_totals(
    repo_root: Path,
    templates_dir: Path,
    fragments: list[Fragment],
    targets: list[Target],
    always_on: set[tuple[str, str]],
) -> dict[str, int]:
    totals = {target.name: 0 for target in targets}
    for item in plan_outputs(fragments, targets, repo_root):
        if (item.target_name, item.output_name) in always_on:
            totals[item.target_name] += read_cost._text_tokens(_render(templates_dir, item))
    return totals


def fragment_costs(repo_root: Path) -> dict[Path, dict[str, int]]:

    paths = load_project_paths(repo_root)
    targets_dir = repo_root / paths.targets_dir
    templates_dir = repo_root / paths.templates_dir
    if not targets_dir.is_dir() or not templates_dir.is_dir():
        return {}
    targets = load_targets(targets_dir)
    if not targets:
        return {}
    roots: list[tuple[Path, str | None]] = [(repo_root / paths.core_fragments_dir, "core")]
    fragments = load_fragments_from_roots(roots, {target.name for target in targets})
    always_on = _always_on_keys(targets)
    base = _target_totals(repo_root, templates_dir, fragments, targets, always_on)

    costs: dict[Path, dict[str, int]] = {}
    for fragment in fragments:
        if fragment.source_path is None:
            continue
        without = [other for other in fragments if other.id != fragment.id]
        totals = _target_totals(repo_root, templates_dir, without, targets, always_on)
        costs[fragment.source_path] = {
            name: max(0, base[name] - totals.get(name, 0)) for name in base
        }
    return costs


def skill_costs(repo_root: Path) -> dict[Path, dict[str, int]]:

    try:
        skills = skill_source.discover_skills(repo_root)
    except ValidationError:
        return {}
    costs: dict[Path, dict[str, int]] = {}
    for skill in skills:
        listed = f"{skill.name}\n"
        if skill.invocation == skill_source.MODEL_INVOKED:
            listed = f"{skill.name}\n{skill.description}\n"
        costs[skill.source_path] = {LISTING_SURFACE: read_cost._text_tokens(listed)}
    return costs


def measured_costs(repo_root: Path) -> dict[Path, dict[str, int]]:
    return {**fragment_costs(repo_root), **skill_costs(repo_root)}


def _declared(path: Path) -> dict[str, int] | str | None:
    data = load_mapping(path)
    if data is None or DECLARED_COST_KEY not in data:
        return None
    value = data[DECLARED_COST_KEY]
    if not isinstance(value, dict):
        return f"`{DECLARED_COST_KEY}:` must be a mapping of surface to token count"
    bad = sorted(
        str(key)
        for key, count in value.items()
        if not isinstance(count, int) or isinstance(count, bool) or count < 0
    )
    if bad:
        return f"`{DECLARED_COST_KEY}:` values must be non-negative integers ({', '.join(bad)})"
    return {str(key): int(count) for key, count in value.items()}


def _as_yaml(measured: dict[str, int]) -> str:
    return ", ".join(f"{surface}: {count}" for surface, count in sorted(measured.items()))


def _drift_problems(source: str, measured: dict[str, int], declared: dict[str, int]) -> list[str]:
    if set(declared) != set(measured):
        return [
            f"{source}: `{DECLARED_COST_KEY}:` names surfaces {sorted(declared)} but this "
            f"source projects to {sorted(measured)} — declare the cost per surface "
            f"({_as_yaml(measured)})"
        ]
    return [
        f"{source}: `{DECLARED_COST_KEY}.{surface}` declares {declared[surface]} tokens but "
        f"the projection measures {count} (drift {abs(declared[surface] - count)} over a "
        f"{tolerance(count)}-token tolerance) — update it to {count}"
        for surface, count in sorted(measured.items())
        if abs(declared[surface] - count) > tolerance(count)
    ]


def problems(repo_root: Path, version: str = __version__) -> tuple[list[str], list[str]]:

    violations: list[str] = []
    warnings: list[str] = []
    absent = warnings if window_open(version) else violations
    for path, measured in sorted(measured_costs(repo_root).items()):
        source = rel(path, repo_root)
        declared = _declared(path)
        if declared is None:
            absent.append(
                f"{source}: no `{DECLARED_COST_KEY}:` declared — an always-on line is paid "
                f"every turn, so state what this one costs ({_as_yaml(measured)})"
            )
        elif isinstance(declared, str):
            violations.append(f"{source}: {declared}")
        else:
            violations.extend(_drift_problems(source, measured, declared))
    return violations, warnings


def violations(repo_root: Path) -> list[str]:
    return problems(repo_root)[0]


def warnings(repo_root: Path) -> list[str]:
    return problems(repo_root)[1]
