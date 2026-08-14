"""Catalog source lint — the deterministic gate that keeps the YAML contract.

Enforces nine invariants across the managed core catalog so the double-load fix
and the single-extension decision cannot regress (architecture §4.2):

1. No discoverable-name *sources*: no ``SKILL.md`` under ``core/skills``, no
   ``*.fragment.md`` under ``core/fragments``, and no markdown under
   ``core/agents`` (rendered files belong at target roots only).
2. One YAML extension: no ``*.yml`` under ``core`` (the catalog uses ``.yaml``).
3. Every source validates against its JSON Schema in ``core/schemas``. That walk
   globs ``core`` only, so the agents *overlay* — which no schema walk covers —
   gets its model tier checked against the same vocabulary directly
   (basicly-axqe).
4. Enforcement pointer (§3.1): a fragment that declares ``enforced_by`` must cite
   each listed command in its body — point at enforcement, don't restate it.
5. Agent composition: block refs resolve, read-only postures grant no write
   tools, composed bodies stay under the portable size cap, and no source pins a
   provider model id instead of a portable model tier.
6. Technology tags stay inside the controlled vocabulary (§9 scoping).
7. Agent Skills spec naming/size constraints JSON Schema cannot express.
8. Invocation axis (basicly-m4zv.1): every skill declares ``model`` or ``user``,
   and only a model-invoked entry carries a description.
9. Tier-2 routing (basicly-m4zv.2): the eval cases colocated with each
   model-invoked entry route to their owner, no two descriptions collide, and
   the rank-1 rate clears a floor this gate refuses to lower.

``README.md`` and other documentation files are not sources and are left alone.

Two neighbours own what this module deliberately does not. Reading a source at all —
where it lives, whether it parses, what its JSON Schema says about it — is
:mod:`basicly.catalog_source`; rule 9 above is asserted by :mod:`basicly.routing_evals`
and merely *collected* here. The boundary is *ruling* against *reading* on one side and
Tier 1 against Tier 2 on the other, and both were drawn when the module-size ratchet
caught this module growing.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from . import agents, read_cost, routing_evals, rubrics, skill_source
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
from .runner import _CONTEXT_WINDOWS
from .schema import MODEL_TIERS, TECHNOLOGIES

# Agent Skills spec (https://agentskills.io/specification) name rule: 1-64 chars,
# lowercase a-z0-9 and single hyphens, no leading/trailing/consecutive hyphen.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# A reference into a bundled resource dir more than one level deep from SKILL.md
# (the spec asks for one-level-deep file references). The path segments exclude
# markdown link punctuation (), [], so a normal link like
# [x](references/x.md) cannot be misread as a two-level path across its `](`.
_DEEP_REF_RE = re.compile(r"(?:references|scripts|assets)/[^\s()\[\]]+/[^\s()\[\]]+")
# Progressive-disclosure guideline: keep the SKILL.md body under ~500 lines.
_MAX_SKILL_BODY_LINES = 500

# Skill properties whose absence _check_invocation_axis reports in full, so the
# raw jsonschema "is a required property" line is dropped for them.
_AXIS_OWNED_REQUIRED = frozenset({"invocation"})

# Agent properties whose absence _check_agent_tier_declared reports in full, for
# the same reason and with the same effect on the diagnostic count.
_TIER_OWNED_REQUIRED = frozenset({"tier"})


def _check_enforcement_pointer(path: Path, repo_root: Path) -> list[str]:
    """Flag enforced_by commands (§3.1) that the fragment body does not cite."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return []  # schema validation already reports malformed YAML
    if not isinstance(data, dict):
        return []
    commands = data.get("enforced_by") or []
    body = data.get("body") or ""
    if not isinstance(commands, list) or not isinstance(body, str):
        return []  # schema validation already reports the type error
    return [
        f"{rel(path, repo_root)}: enforced_by command '{command}' is not cited in the body"
        for command in commands
        if isinstance(command, str) and command not in body
    ]


def _validate_agent_schemas(repo_root: Path) -> list[str]:
    """Schema-validate core agent and block sources (validators built lazily)."""
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
    """Flag a `tier:` value outside ``schema.MODEL_TIERS`` on one agent source.

    A non-string value is a violation too, not an absence: normalizing it away
    would let ``tier: 0`` read as "no tier declared", and a "cannot tell" must
    never read as "safe to proceed" — ``models.resolve_model`` refuses an unknown
    tier, so the typo has to be caught here where it names a file to fix.
    """
    if not isinstance(data, dict):
        return []  # the agent lint (rule 5) reports a malformed source as a violation
    tier = data.get("tier")
    if tier is None:
        return []  # absence is _check_agent_tier_declared's diagnostic, on every root
    if not isinstance(tier, str) or tier.strip() not in MODEL_TIERS:
        return [
            f"{rel(path, repo_root)}: model tier {tier!r} is not in the portable "
            f"vocabulary; declare `tier: {' | '.join(MODEL_TIERS)}`"
        ]
    return []


def _check_overlay_agent_tiers(repo_root: Path) -> list[str]:
    """Enforce the model tier vocabulary on the agent roots no schema walk covers.

    ``_validate_agent_schemas`` globs ``.basicly/core/agents`` only, so the
    ``tier`` enum in ``agent.schema.json`` never reached the ``.basicly-local``
    overlay: a core source with ``tier: turbo`` was rejected while the same
    overlay source was accepted silently (basicly-axqe). Every root except the
    schema-validated one is checked, so a third root added later is covered by
    construction rather than by remembering to extend this.

    The core path deliberately keeps reporting through the schema enum, so one
    defect still yields one diagnostic; the vocabulary stays single-sourced
    because that enum is tripwired against ``schema.MODEL_TIERS`` by a test.
    """
    schema_validated = repo_root / AGENTS_DIR
    violations: list[str] = []
    for root, _source in agents.default_agent_roots(repo_root):
        if root == schema_validated or not root.is_dir():
            continue
        for path in sorted(root.glob(f"*/{agents.AGENT_SOURCE_FILE}")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError:
                continue  # rule 5 reports malformed YAML as a violation of its own
            violations.extend(_tier_violations(path, data, repo_root))
    return violations


def _check_agent_tier_declared(repo_root: Path) -> list[str]:
    """Refuse an agent source that declares no model tier, on every agent root.

    An omitted tier is not a cheap default: the spawned agent inherits the
    session's model, usually the most expensive one, so the routing rule defeats
    itself silently (factory-loop D26). ``agent.schema.json`` carries the same
    rule as ``required``, but its "'tier' is a required property" line names
    neither the vocabulary nor the reason and reaches the core root only, so it
    is suppressed (``_TIER_OWNED_REQUIRED``) and this owns the message — core and
    overlay then read identically.

    A source pinning the deprecated ``model:`` is left alone: the agent lint
    already tells it to declare ``tier:`` instead, and one defect yields one
    diagnostic.
    """
    violations: list[str] = []
    for root, _source in agents.default_agent_roots(repo_root):
        if not root.is_dir():
            continue
        for path in sorted(root.glob(f"*/{agents.AGENT_SOURCE_FILE}")):
            data = load_mapping(path)  # None when malformed; rule 5 reports that
            if data is None or "tier" in data or agents.DEPRECATED_MODEL_KEY in data:
                continue
            violations.append(
                f"{rel(path, repo_root)}: no model tier declared; an omitted tier "
                "inherits the spawning session's model rather than defaulting to a "
                f"cheap one — declare `tier: {' | '.join(MODEL_TIERS)}`"
            )
    return violations


def lint_catalog(repo_root: Path) -> list[str]:
    """Return a list of catalog-lint violations (empty when the catalog is clean)."""
    violations: list[str] = []
    core = repo_root / CORE_DIR
    if not core.exists():
        return violations

    # 1. no discoverable-name sources
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

    # 2. single YAML extension
    violations.extend(
        f"{rel(path, repo_root)}: use the .yaml extension, not .yml"
        for path in sorted(core.rglob("*.yml"))
    )

    # 3. schema validation
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

    # 4. enforcement-pointer check (§3.1)
    for path in sorted((repo_root / FRAGMENTS_DIR).rglob("*.fragment.yaml")):
        violations.extend(_check_enforcement_pointer(path, repo_root))

    # 5. agent composition lint over the merged core+overlay set
    violations.extend(agents.lint_agent_sources(repo_root))

    # 6. technology tags stay inside the controlled vocabulary (§9 scoping)
    violations.extend(_check_technology_vocabulary(repo_root))

    # 7. Agent Skills spec naming/size constraints JSON Schema cannot express
    violations.extend(_check_skill_spec(repo_root))

    # 8. invocation axis: the description/invocation pairing (basicly-m4zv.1)
    violations.extend(_check_invocation_axis(repo_root))

    # 9. Tier-2 routing evals over the model-invoked set (basicly-m4zv.2)
    violations.extend(routing_evals.routing_outcome(repo_root).violations)

    return violations


def _check_skill_spec(repo_root: Path) -> list[str]:
    """Enforce Agent Skills naming/length rules JSON Schema cannot express.

    ``name`` must match the spec regex AND the containing directory; ``metadata``
    values must be strings. Length limits on ``description``/``compatibility`` are
    schema-enforced; the name-vs-directory identity and the regex are not.

    That first sentence was **false for `description` until 2026-08-09**: only
    ``compatibility`` carried a ``maxLength``. It now carries 1536, the point where
    the host truncates a listing entry — and truncation takes the tail, which is the
    "use when" half that does the routing (basicly-u2hl.45).
    """
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
    """Enforce the invocation axis and its description pairing (basicly-m4zv.1).

    Owns every diagnostic about the field, presence included — the schema's raw
    lines are suppressed via ``_AXIS_OWNED_REQUIRED`` because ``'invocation' is a
    required property`` names no valid value and no migration, and a
    ``not: required`` message reads as "should not be valid under
    {'required': ['description']}". An author hitting the gate deserves to be told
    what to do.

    Presence is a **breaking** requirement for a catalog authored before the axis
    existed (basicly-m4zv.9: the owner ruled it stays required rather than
    defaulting, with the migration documented in the changelog), so the message
    has to carry the fix a consumer applies rather than merely refusing.

    Both pairing directions are violations, and the second is the one that
    matters: a user-invoked entry carrying a description pays context load every
    turn for reach it does not have, which is exactly the waste the axis was
    introduced to find.
    """
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


# The host budgets the whole skill listing at 1% of the context window and, on
# overflow, drops descriptions **starting with the least-invoked skills** (measured
# against Claude Code 2.1.226, 2026-08-09). Those two facts compose into a feedback
# loop rather than a flat cost: the entries nobody invokes are the first to lose the
# description that would let anything invoke them.
_LISTING_BUDGET_FRACTION = 100
# Reported against the window a *consumer* gets, not the one this repo runs. The
# adapter default is what a fresh install inherits; `basicly.toml` raises claude to
# 1_000_000 here, which hides the overrun entirely from anyone measuring locally —
# which is exactly how it went unnoticed.
_LISTING_REFERENCE_FAMILY = "claude"


def listing_budget_warnings(repo_root: Path) -> list[str]:
    """Warn when the projected skill listing overruns the host's listing budget.

    Advisory, deliberately. The overrun is a *cost* to weigh — the remedy is to
    retire or user-invoke a skill, which is authoring work, and a gate that fails
    the build over it would block every unrelated commit until someone did it.

    Reported with the arithmetic shown, because a bare "over budget" cannot be acted
    on: the reader needs the entry count, the token total and the budget to decide
    whether to cut one long description or three dead skills.
    """
    entries = [
        skill
        for skill in skill_source.discover_skills(repo_root)
        if skill.invocation == skill_source.MODEL_INVOKED
    ]
    if not entries:
        return []
    # Name plus description is what the host lists; a user-invoked entry contributes
    # only its name, which is the saving the invocation axis exists to buy.
    listing = "".join(f"{skill.name}\n{skill.description}\n" for skill in entries)
    tokens = read_cost._text_tokens(listing)
    window = _CONTEXT_WINDOWS[_LISTING_REFERENCE_FAMILY]
    budget = window // _LISTING_BUDGET_FRACTION
    if tokens <= budget:
        return []
    return [
        f"skill listing is {tokens} tokens against a {budget}-token budget "
        f"(1% of {_LISTING_REFERENCE_FAMILY}'s {window} adapter-default window), "
        f"from {len(entries)} model-invoked entries. The host drops descriptions "
        f"least-invoked first, so the entries this overrun silences are the ones "
        f"already hardest to reach. Retire a dead skill or move it to user-invoked."
    ]


def skill_warnings(repo_root: Path) -> list[str]:
    """Return non-blocking Agent Skills progressive-disclosure advisories.

    Advisory (never fails the gate): a SKILL.md body over ~500 lines, a file
    reference more than one level deep — both are spec *recommendations* — and a
    description pair over the Tier-2 collision *warning* line, which is the
    early sign of the drift the error ceiling later refuses.
    """
    warnings: list[str] = list(routing_evals.routing_outcome(repo_root).warnings)
    warnings.extend(listing_budget_warnings(repo_root))
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
    """Report a violation when a rubric source fails to load/validate."""
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
    """Flag `technologies:` values outside the controlled vocabulary."""
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
            continue  # schema validation already reports malformed YAML
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
