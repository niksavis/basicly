"""What a catalog source costs the always-on surfaces, and whether it says so.

One question: for each catalog source, how many tokens does it add to context that an
agent pays on *every* turn, and does the source's ``token_cost:`` field agree with that
measurement. Nothing else in the tree makes an author compute the number before adding
a line — the projection's own size warnings fire on the finished file, which is one
aggregate no author can attribute to their own edit.

**Measured, never configured.** A fragment's cost is the token delta of a target's
always-on output rendered with the fragment against the same output rendered without
it, so the figure an author is held to is the one the projector actually produces and
it moves when the body moves. The cheaper ``tokens(title + body)`` estimate measured
1-3 tokens low against that delta (the template's heading chrome) — inside the
tolerance, but for no gain: the exact delta costs about 0.05s over this catalog.

**Per surface, not one number.** ``AGENTS.md`` inlines scoped fragments and the other
two always-on files do not, so ``code-is-authoritative`` costs codex 132 tokens and
claude and copilot nothing (measured 2026-08-23). One declared figure would be wrong
for two targets out of three, and the zero is the informative half: it is what tells an
author that scoping a fragment *moved* its cost rather than removing it.

A skill's surface is the host's *listing*, not a target file. Skills project to
``.claude/skills`` and ``.agents/skills``, which are roots rather than target names, so
splitting a skill's cost per target would invent a distinction the projection does not
make. The listing is the part that is always on; the projected ``SKILL.md`` body is read
on demand and costs nothing until something routes there.

**The requirement arrives through a window.** The catalog schema is a contract other
repositories author against, so an absent field is reported and not refused until
:data:`REQUIRED_FROM_VERSION`. The window is keyed on the version in the tree rather
than on a date: a clock makes a gate's verdict depend on when CI happened to run, and a
consumer's window should close when they upgrade, not while they are pinned.

The boundary against :mod:`basicly.catalog_lint` is ruling against collecting — that
module decides which of these lands as a violation and which as a warning, exactly as
it already does for :mod:`basicly.routing_evals`.
"""

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

#: The authored field this module rules on. Named without the word this repo's
#: hardcoded-credential rule keys on, so the constant needs no suppression.
DECLARED_COST_KEY = "token_cost"
#: The one surface a skill's always-on cost lands on: the host's skill listing.
LISTING_SURFACE = "listing"
#: The release at which an absent declaration stops warning and starts failing.
#: Compared against the version in the tree, never against a date.
REQUIRED_FROM_VERSION = "0.11.0"
# A declaration should rot on a rewritten body, not on a reworded sentence. 10% tracks
# a real edit; the floor keeps a 20-token fragment from failing on a single word, where
# 10% rounds to two tokens and every touch would be a disagreement.
_TOLERANCE_FRACTION = 0.10
_TOLERANCE_FLOOR = 8


def _leading_digits(part: str) -> str:
    """The leading digit run of *part* (``"0rc1"`` -> ``"0"``)."""
    digits: list[str] = []
    for char in part:
        if not char.isdigit():
            break
        digits.append(char)
    return "".join(digits)


def _version_tuple(version: str) -> tuple[int, ...]:
    """Leading numeric components of *version*, so ``1.2.0rc1`` still orders."""
    parts: list[int] = []
    for part in version.split("."):
        digits = _leading_digits(part)
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def window_open(version: str = __version__) -> bool:
    """True while an absent declaration is reported rather than refused."""
    return _version_tuple(version) < _version_tuple(REQUIRED_FROM_VERSION)


def tolerance(measured: int) -> int:
    """Token drift a declaration may carry before it counts as rotted."""
    return max(_TOLERANCE_FLOOR, round(measured * _TOLERANCE_FRACTION))


def _always_on_keys(targets: list[Target]) -> set[tuple[str, str]]:
    """``(target, output)`` pairs an agent loads every turn.

    A fixed ``path`` output is one file per target holding many fragments — the
    always-on file. A ``path_template`` output is one file *per fragment*, which the
    host reads only when its scope matches, so a fragment's cost against it is zero by
    construction rather than by omission. Read off the target definition rather than off
    the filename, so a target adding an output is classified and not guessed.
    """
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
    """Token total of each target's always-on output over exactly *fragments*."""
    totals = {target.name: 0 for target in targets}
    for item in plan_outputs(fragments, targets, repo_root):
        if (item.target_name, item.output_name) in always_on:
            totals[item.target_name] += read_cost._text_tokens(_render(templates_dir, item))
    return totals


def fragment_costs(repo_root: Path) -> dict[Path, dict[str, int]]:
    """Per-target always-on cost of every fragment source, by source path.

    Empty when the catalog has no targets or no templates to render with: there is then
    no always-on file for a cost to land in, so there is nothing to measure and nothing
    to rule on. That is a real absence and not a swallowed failure — every caller in
    this module wants the measurement, and a catalog with no target projects nowhere.
    """
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
    """Listing cost of every skill source, by source path.

    The entry a host lists is the name plus, for a model-invoked skill, the description.
    A user-invoked skill contributes its name and nothing else, which is the whole
    saving the invocation axis exists to buy — priced here rather than dropped, because
    an author declaring zero for a listed name would be declaring something false.
    """
    try:
        skills = skill_source.discover_skills(repo_root)
    except ValidationError:
        return {}  # schema validation already reports a malformed source
    costs: dict[Path, dict[str, int]] = {}
    for skill in skills:
        listed = f"{skill.name}\n"
        if skill.invocation == skill_source.MODEL_INVOKED:
            listed = f"{skill.name}\n{skill.description}\n"
        costs[skill.source_path] = {LISTING_SURFACE: read_cost._text_tokens(listed)}
    return costs


def measured_costs(repo_root: Path) -> dict[Path, dict[str, int]]:
    """Every skill and fragment source mapped to its measured per-surface cost."""
    return {**fragment_costs(repo_root), **skill_costs(repo_root)}


def _declared(path: Path) -> dict[str, int] | str | None:
    """The source's declared map, None when absent, or a message when malformed."""
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
    """The declaration an author can paste, rendered inline."""
    return ", ".join(f"{surface}: {count}" for surface, count in sorted(measured.items()))


def _drift_problems(source: str, measured: dict[str, int], declared: dict[str, int]) -> list[str]:
    """Report a declaration naming the wrong surfaces or carrying the wrong numbers."""
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
    """Return ``(violations, warnings)`` for the declared cost of every source.

    An absent declaration is the only finding the window moves. A *wrong* declaration
    fails from the first release, because a number nobody checked is worse than no
    number at all — it reads as computed.
    """
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
    """Declared-cost findings that fail the catalog-lint gate."""
    return problems(repo_root)[0]


def warnings(repo_root: Path) -> list[str]:
    """Declared-cost findings the migration window reports without failing."""
    return problems(repo_root)[1]
