"""Tier-2 on disk: run the colocated routing evals over a repo's catalog.

One responsibility, and it is the run. :mod:`basicly.catalog_routing` is pure
computation over a dict of descriptions and a list of cases; this module is what finds
them — the model-invoked descriptions across the skill sources, the ``evals.yaml``
beside each one — and returns the outcome with the floor it has to clear.

The description corpus is deliberately *not* filtered by ``[catalog] technologies``.
Routing quality is a property of the catalog this repo ships to every consumer, so a
technology this repo happens not to select would otherwise ship with an unchecked
description — and a gate whose verdict depends on the running repo's configuration is
not the reproducible measurement ``docs/design/catalog-efficacy-design.md`` §3.3 asks
for.

Split out of ``catalog_lint`` when the module-size ratchet caught that module growing.
The boundary is *Tier 2* against *Tier 1*, which is the seam both modules' docstrings
already drew: ``catalog_lint`` asks whether a source is well-formed, this asks whether
the entry fires when it should. Both read through :mod:`basicly.catalog_source`, so
neither needs an import into the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from . import catalog_routing, config, skill_source
from .catalog_source import SKILLS_DIR, load_mapping, rel, schema_validator, schema_violations


@dataclass(frozen=True)
class RoutingOutcome:
    """One Tier-2 run over a repo's catalog: the metric, its floor, and findings."""

    report: catalog_routing.RoutingReport
    floor: float | None
    violations: tuple[str, ...]
    warnings: tuple[str, ...]

    def summary(self) -> str:
        """The one-line CI metric: rank-1 rate against the floor it must clear.

        A rate printed without the threshold beside it is a number, not a
        metric: the reader cannot tell a pass from a near miss, and nobody can
        judge how much headroom a raise would spend.
        """
        against = f"floor {self.floor:.1%}" if self.floor is not None else "no floor declared"
        return (
            f"routing: rank-1 rate {self.report.rank1_hits}/{self.report.positives} "
            f"= {self.report.rank1_rate:.1%} ({against})"
        )


def _model_invoked_descriptions(repo_root: Path) -> dict[str, str]:
    """Slug -> description for every model-invoked skill source.

    The whole authored set, deliberately unfiltered — see the module docstring for
    why the running repo's technology selection must not decide this gate's verdict.
    """
    descriptions: dict[str, str] = {}
    for path in sorted((repo_root / SKILLS_DIR).glob("*/skill.yaml")):
        data = load_mapping(path)
        if data is None:
            continue
        description = data.get("description")
        if data.get("invocation") == skill_source.MODEL_INVOKED and isinstance(description, str):
            descriptions[path.parent.name] = description
    return descriptions


def _load_eval_cases(
    repo_root: Path, ranked: set[str]
) -> tuple[list[catalog_routing.PositiveCase], list[catalog_routing.NegativeCase], list[str]]:
    """Load and validate every ``evals.yaml`` present under the skill sources.

    A *missing* case file is not reported here — making one a Tier-1 failure is
    basicly-m4zv.3's job, and failing on it now would red the gate for every
    entry before the corpus exists. A file that *is* present must be complete:
    an eval nobody can trust is worse than none, because it reads as coverage.
    """
    positives: list[catalog_routing.PositiveCase] = []
    negatives: list[catalog_routing.NegativeCase] = []
    violations: list[str] = []
    sources = sorted((repo_root / SKILLS_DIR).glob(f"*/{skill_source.EVAL_SOURCE_FILE}"))
    if not sources:
        return positives, negatives, violations
    validator = schema_validator(repo_root, "evals.schema.json")
    for path in sources:
        slug = path.parent.name
        source = rel(path, repo_root)
        schema_errors = schema_violations(path, validator, repo_root)
        if schema_errors:
            violations.extend(schema_errors)
            continue
        if slug not in ranked:
            violations.append(
                f"{source}: '{slug}' is not a model-invoked entry, so nothing can route to it — "
                "a user-invoked entry carries no description and needs no routing evidence"
            )
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        entry_positives, entry_negatives = catalog_routing.entry_cases(slug, data)
        positives.extend(entry_positives)
        for negative in entry_negatives:
            if negative.owner == slug:
                violations.append(
                    f"{source}: negative prompt {negative.prompt!r} names '{slug}' as its own "
                    "owner — a negative belongs to a different entry, or it asserts nothing"
                )
                continue
            if negative.owner not in ranked:
                violations.append(
                    f"{source}: negative prompt {negative.prompt!r} names owner "
                    f"'{negative.owner}', which is not a model-invoked catalog entry"
                )
                continue
            negatives.append(negative)
    return positives, negatives, violations


def routing_outcome(repo_root: Path) -> RoutingOutcome:
    """Run the Tier-2 routing eval over ``repo_root``'s catalog (basicly-m4zv.2).

    Three assertions plus the CI metric: positive prompts rank their owner in
    top-k, negative prompts are outranked by their declared owner, no
    description pair collides, and the rank-1 rate clears a floor that may be
    raised but never lowered.
    """
    descriptions = _model_invoked_descriptions(repo_root)
    positives, negatives, violations = _load_eval_cases(repo_root, set(descriptions))
    report = catalog_routing.evaluate(descriptions, positives, negatives)
    try:
        floor, high_water = config.load_routing_floor(repo_root)
    except ValueError as exc:
        return RoutingOutcome(report, None, (*violations, *report.failures, str(exc)), ())
    # The floor gate activates with the corpus. A catalog with no eval cases has
    # no rank-1 rate to defend, and demanding a floor for a rate nobody can
    # measure would fail a freshly installed consumer on arithmetic over an
    # empty set. Making the corpus itself mandatory is basicly-m4zv.3's job.
    floor_findings = (
        catalog_routing.floor_violations(report.rank1_rate, floor, high_water)
        if report.positives
        else []
    )
    return RoutingOutcome(
        report=report,
        floor=floor,
        violations=(*violations, *report.failures, *floor_findings),
        warnings=report.collision_warnings,
    )
