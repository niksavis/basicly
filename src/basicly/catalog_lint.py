from __future__ import annotations

import re
from pathlib import Path

import yaml

from . import (
    agents,
    catalog_emphasis,
    catalog_token_cost,
    read_cost,
    routing_evals,
    rubrics,
    skill_coverage,
    skill_source,
)
from .catalog_source import (
    AGENTS_DIR,
    CORE_DIR,
    FRAGMENTS_DIR,
    HOOKS_DIR,
    RUBRICS_DIR,
    SKILLS_DIR,
    load_mapping,
    rel,
    schema_validator,
    schema_violations,
)
from .schema import MODEL_TIERS, TECHNOLOGIES, ValidationError

_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DEEP_REF_RE = re.compile(r"(?:references|scripts|assets)/[^\s()\[\]]+/[^\s()\[\]]+")
_MAX_SKILL_BODY_LINES = 500

_AXIS_OWNED_REQUIRED = frozenset({"invocation"})

_TIER_OWNED_REQUIRED = frozenset({"tier"})


def _check_enforcement_pointer(path: Path, repo_root: Path) -> list[str]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    commands = data.get("enforced_by") or []
    body = data.get("body") or ""
    if not isinstance(commands, list) or not isinstance(body, str):
        return []
    return [
        f"{rel(path, repo_root)}: enforced_by command '{command}' is not cited in the body"
        for command in commands
        if isinstance(command, str) and command not in body
    ]


def _validate_agent_schemas(repo_root: Path) -> list[str]:
    violations: list[str] = []
    agent_sources = sorted((repo_root / AGENTS_DIR).glob(f"*/{agents.AGENT_SOURCE_FILE}"))
    if agent_sources:
        validator = schema_validator(repo_root, "agent.schema.json")
        for path in agent_sources:
            violations.extend(
                schema_violations(path, validator, repo_root, owned_required=_TIER_OWNED_REQUIRED)
            )
    block_sources = sorted(
        (repo_root / AGENTS_DIR / agents.BLOCKS_DIR_NAME).glob(agents.BLOCK_SOURCE_GLOB)
    )
    if block_sources:
        validator = schema_validator(repo_root, "block.schema.json")
        for path in block_sources:
            violations.extend(schema_violations(path, validator, repo_root))
    return violations


def _tier_violations(path: Path, data: object, repo_root: Path) -> list[str]:

    if not isinstance(data, dict):
        return []
    tier = data.get("tier")
    if tier is None:
        return []
    if not isinstance(tier, str) or tier.strip() not in MODEL_TIERS:
        return [
            f"{rel(path, repo_root)}: model tier {tier!r} is not in the portable "
            f"vocabulary; declare `tier: {' | '.join(MODEL_TIERS)}`"
        ]
    return []


def _check_overlay_agent_tiers(repo_root: Path) -> list[str]:

    schema_validated = repo_root / AGENTS_DIR
    violations: list[str] = []
    for root, _source in agents.default_agent_roots(repo_root):
        if root == schema_validated or not root.is_dir():
            continue
        for path in sorted(root.glob(f"*/{agents.AGENT_SOURCE_FILE}")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError:
                continue
            violations.extend(_tier_violations(path, data, repo_root))
    return violations


def _check_agent_tier_declared(repo_root: Path) -> list[str]:

    violations: list[str] = []
    for root, _source in agents.default_agent_roots(repo_root):
        if not root.is_dir():
            continue
        for path in sorted(root.glob(f"*/{agents.AGENT_SOURCE_FILE}")):
            data = load_mapping(path)
            if data is None or "tier" in data or agents.DEPRECATED_MODEL_KEY in data:
                continue
            violations.append(
                f"{rel(path, repo_root)}: no model tier declared; an omitted tier "
                "inherits the spawning session's model rather than defaulting to a "
                f"cheap one — declare `tier: {' | '.join(MODEL_TIERS)}`"
            )
    return violations


def lint_catalog(repo_root: Path) -> list[str]:
    violations: list[str] = []
    core = repo_root / CORE_DIR
    if not core.exists():
        return violations

    violations.extend(
        f"{rel(path, repo_root)}: skill sources must be skill.yaml, not SKILL.md"
        for path in sorted((repo_root / SKILLS_DIR).rglob("SKILL.md"))
    )
    violations.extend(
        f"{rel(path, repo_root)}: fragment sources must be *.fragment.yaml, not *.fragment.md"
        for path in sorted((repo_root / FRAGMENTS_DIR).rglob("*.fragment.md"))
    )
    violations.extend(
        f"{rel(path, repo_root)}: agent sources must be agent.yaml or *.block.yaml, "
        "not markdown (the projector renders the markdown into every agent root)"
        for path in sorted((repo_root / AGENTS_DIR).rglob("*.md"))
        if path.name != "README.md"
    )
    violations.extend(
        f"{rel(path, repo_root)}: rubric sources must be *.rubric.yaml, not markdown"
        for path in sorted((repo_root / RUBRICS_DIR).rglob("*.md"))
    )

    violations.extend(
        f"{rel(path, repo_root)}: use the .yaml extension, not .yml"
        for path in sorted(core.rglob("*.yml"))
    )

    skill_validator = schema_validator(repo_root, "skill.schema.json")
    fragment_validator = schema_validator(repo_root, "fragment.schema.json")
    for path in sorted((repo_root / SKILLS_DIR).glob("*/skill.yaml")):
        violations.extend(
            schema_violations(path, skill_validator, repo_root, owned_required=_AXIS_OWNED_REQUIRED)
        )
    for path in sorted((repo_root / FRAGMENTS_DIR).rglob("*.fragment.yaml")):
        violations.extend(schema_violations(path, fragment_validator, repo_root))

    violations.extend(_validate_agent_schemas(repo_root))
    violations.extend(_check_agent_tier_declared(repo_root))
    violations.extend(_check_overlay_agent_tiers(repo_root))
    violations.extend(_validate_rubrics(repo_root))

    for path in sorted((repo_root / FRAGMENTS_DIR).rglob("*.fragment.yaml")):
        violations.extend(_check_enforcement_pointer(path, repo_root))

    violations.extend(agents.lint_agent_sources(repo_root))

    violations.extend(_check_technology_vocabulary(repo_root))

    violations.extend(_check_skill_spec(repo_root))

    violations.extend(_check_invocation_axis(repo_root))

    violations.extend(routing_evals.routing_outcome(repo_root).violations)

    violations.extend(_check_coverage_vocabulary(repo_root))

    violations.extend(catalog_token_cost.violations(repo_root))

    violations.extend(catalog_emphasis.violations(repo_root))

    return violations


def _check_coverage_vocabulary(repo_root: Path) -> list[str]:

    try:
        skills = skill_source.discover_skills(repo_root)
    except ValidationError:
        return []
    return skill_coverage.vocabulary_problems(skills)


def _check_skill_spec(repo_root: Path) -> list[str]:

    violations: list[str] = []
    for path in sorted((repo_root / SKILLS_DIR).glob("*/skill.yaml")):
        data = load_mapping(path)
        if data is None:
            continue
        source = rel(path, repo_root)
        slug = path.parent.name
        name = data.get("name")
        if isinstance(name, str):
            if name != slug:
                violations.append(
                    f"{source}: skill name '{name}' must match its directory '{slug}'"
                )
            if len(name) > 64 or not _SKILL_NAME_RE.match(name):
                violations.append(
                    f"{source}: skill name '{name}' must be 1-64 lowercase a-z0-9/hyphen "
                    "characters with no leading, trailing, or consecutive hyphen"
                )
    return violations


def _check_invocation_axis(repo_root: Path) -> list[str]:

    violations: list[str] = []
    for path in sorted((repo_root / SKILLS_DIR).glob("*/skill.yaml")):
        data = load_mapping(path)
        if data is None:
            continue
        source = rel(path, repo_root)
        invocation = data.get("invocation")
        has_description = isinstance(data.get("description"), str) and data["description"].strip()
        if invocation is None:
            violations.append(
                f"{source}: no 'invocation' declared — add `invocation: model` for an entry the "
                "agent should discover and route to, or `invocation: user` for one only a human "
                "types (which must then carry no description). `invocation: model` preserves the "
                "behaviour of any entry that already has a description"
            )
        elif invocation == skill_source.MODEL_INVOKED and not has_description:
            violations.append(
                f"{source}: a model-invoked entry needs a description — it is what the agent "
                "reads to decide whether to route here"
            )
        elif invocation == skill_source.USER_INVOKED and has_description:
            violations.append(
                f"{source}: a user-invoked entry must not carry a description — nothing can route "
                "to it, so the description is context load bought for no reach"
            )
    return violations


_LISTING_BUDGET_FRACTION = 100
_LISTING_REFERENCE_FAMILY = "claude"
_LISTING_REFERENCE_WINDOW = 200_000


def listing_budget_warnings(repo_root: Path) -> list[str]:

    entries = [
        skill
        for skill in skill_source.discover_skills(repo_root)
        if skill.invocation == skill_source.MODEL_INVOKED
    ]
    if not entries:
        return []
    listing = "".join(f"{skill.name}\n{skill.description}\n" for skill in entries)
    tokens = read_cost._text_tokens(listing)
    window = _LISTING_REFERENCE_WINDOW
    budget = window // _LISTING_BUDGET_FRACTION
    if tokens <= budget:
        return []
    return [
        f"skill listing is {tokens} tokens against a {budget}-token budget "
        f"(1% of the {window}-token {_LISTING_REFERENCE_FAMILY} window a consumer gets), "
        f"from {len(entries)} model-invoked entries. The host drops descriptions "
        f"least-invoked first, so the entries this overrun silences are the ones "
        f"already hardest to reach. Retire a dead skill or move it to user-invoked."
    ]


def skill_warnings(repo_root: Path) -> list[str]:

    warnings: list[str] = list(routing_evals.routing_outcome(repo_root).warnings)
    warnings.extend(listing_budget_warnings(repo_root))
    warnings.extend(catalog_token_cost.warnings(repo_root))
    for path in sorted((repo_root / SKILLS_DIR).glob("*/skill.yaml")):
        data = load_mapping(path)
        if data is None:
            continue
        source = rel(path, repo_root)
        instructions = data.get("instructions")
        if isinstance(instructions, str):
            lines = len(instructions.splitlines())
            if lines > _MAX_SKILL_BODY_LINES:
                warnings.append(
                    f"{source}: SKILL.md body is {lines} lines; keep it under "
                    f"~{_MAX_SKILL_BODY_LINES} (move detail into references/)"
                )
            warnings.extend(
                f"{source}: file reference '{match}' is more than one level deep; "
                "keep references one level from SKILL.md"
                for match in _DEEP_REF_RE.findall(instructions)
            )
    return warnings


def _validate_rubrics(repo_root: Path) -> list[str]:
    rubrics_dir = repo_root / RUBRICS_DIR
    if not rubrics_dir.is_dir():
        return []
    try:
        rubrics.load_rubrics(rubrics_dir)
    except ValueError as exc:
        return [str(exc)]
    return []


def _technology_violations(path: Path, data: object, repo_root: Path) -> list[str]:
    if not isinstance(data, dict):
        return []
    technologies = data.get("technologies")
    if technologies is None:
        return []
    if not isinstance(technologies, list) or not all(
        isinstance(item, str) for item in technologies
    ):
        return [f"{rel(path, repo_root)}: technologies must be a list of strings"]
    unknown = sorted(set(technologies) - TECHNOLOGIES)
    if unknown:
        return [
            f"{rel(path, repo_root)}: unknown technologies: {', '.join(unknown)} "
            f"(allowed: {', '.join(sorted(TECHNOLOGIES))})"
        ]
    return []


def _check_technology_vocabulary(repo_root: Path) -> list[str]:
    violations: list[str] = []
    sources = [
        *sorted((repo_root / SKILLS_DIR).glob("*/skill.yaml")),
        *sorted((repo_root / FRAGMENTS_DIR).rglob("*.fragment.yaml")),
        *sorted((repo_root / AGENTS_DIR).glob(f"*/{agents.AGENT_SOURCE_FILE}")),
    ]
    for path in sources:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        violations.extend(_technology_violations(path, data, repo_root))

    hooks_manifest = repo_root / HOOKS_DIR / "hooks.yaml"
    if hooks_manifest.exists():
        try:
            data = yaml.safe_load(hooks_manifest.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            data = None
        entries = data.get("hooks") if isinstance(data, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            violations.extend(_technology_violations(hooks_manifest, entry, repo_root))

    return violations
