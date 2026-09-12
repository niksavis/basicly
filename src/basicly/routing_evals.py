from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from . import catalog_routing, config, skill_source
from .catalog_source import SKILLS_DIR, load_mapping, rel, schema_validator, schema_violations


@dataclass(frozen=True)
class RoutingOutcome:
    report: catalog_routing.RoutingReport
    floor: float | None
    violations: tuple[str, ...]
    warnings: tuple[str, ...]

    def summary(self) -> str:

        against = f"floor {self.floor:.1%}" if self.floor is not None else "no floor declared"
        return (
            f"routing: rank-1 rate {self.report.rank1_hits}/{self.report.positives} "
            f"= {self.report.rank1_rate:.1%} ({against})"
        )


def _model_invoked_descriptions(repo_root: Path) -> dict[str, str]:

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

    descriptions = _model_invoked_descriptions(repo_root)
    positives, negatives, violations = _load_eval_cases(repo_root, set(descriptions))
    report = catalog_routing.evaluate(descriptions, positives, negatives)
    try:
        floor, high_water = config.load_routing_floor(repo_root)
    except ValueError as exc:
        return RoutingOutcome(report, None, (*violations, *report.failures, str(exc)), ())
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
