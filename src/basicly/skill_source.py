"""A skill as its author wrote it: the ``skill.yaml`` source, loaded and validated.

One responsibility, and it is the load. :func:`discover_skills` turns a source
collection directory into :class:`SkillDefinition` objects or a
:class:`~basicly.schema.ValidationError` naming the file and the field, and nothing
here writes anything, renders anything, or knows a projection root exists.

The source is deliberately non-discoverable — ``skill.yaml``, never ``SKILL.md`` — so a
broadly-scanning agent cannot load the catalog source as a second copy of the skill
(architecture §4.2). That is why the invocation axis and the source file names live
here rather than beside the renderer: they are facts about the authored form, and
:mod:`basicly.catalog_lint` asks about them without going anywhere near projection.

A source directory may bundle the full Agent Skills layout
(https://agentskills.io/specification) — ``scripts/``, ``references/``, ``assets/`` and
any other file. This module ignores those: they are copied verbatim by the projector,
and nothing about them is validated at load time.

Split out of ``skills`` when the module-size ratchet caught that module growing. The
boundary is *authored form* against *projected form*: :mod:`basicly.skills` renders and
mirrors a :class:`SkillDefinition` onto disk and imports this module to get one, which
is why nothing here imports back into it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .schema import ValidationError, validate_technologies

SKILLS_SOURCE_DIR = Path(".basicly/core/skills")
SKILL_SOURCE_FILE = "skill.yaml"
# Tier-2 routing evidence (basicly-m4zv.2), colocated with the entry it is about
# so a reviewer sees the description and the prompts that must reach it in one
# diff. A catalog *source*, not a bundled resource: it is read by `catalog lint`
# and never projected, because an agent loading a skill has no use for the eval
# corpus and every reason not to pay for it.
EVAL_SOURCE_FILE = "evals.yaml"

# The invocation axis (basicly-m4zv.1).
MODEL_INVOKED = "model"
USER_INVOKED = "user"
INVOCATIONS = frozenset({MODEL_INVOKED, USER_INVOKED})


@dataclass(frozen=True)
class SkillDefinition:
    """A source skill loaded from .basicly/core/skills.

    ``technologies`` is basicly-internal scoping (§9) and is NOT emitted into the
    projected SKILL.md frontmatter. The optional spec fields (``license``,
    ``compatibility``, ``allowed_tools``, ``metadata``) round-trip into the
    frontmatter; omitting them yields the minimal ``name``/``description`` header.
    """

    slug: str
    name: str
    # The invocation axis (basicly-m4zv.1). A model-invoked entry keeps a
    # description and is advertised to the agent, paying context load every turn;
    # a user-invoked entry carries none and is reached by a human typing it.
    # Declared rather than inferred, because "does this route correctly" is not a
    # well-posed question until an entry knows whether anything can route to it —
    # which is why this is the prerequisite for the Tier-2 routing evals.
    invocation: str
    # Empty for a user-invoked entry; the pairing is enforced by catalog_lint.
    description: str
    instructions: str
    source_path: Path
    technologies: tuple[str, ...] = ()
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: str | None = None
    metadata: tuple[tuple[str, str], ...] = ()

    @property
    def source_dir(self) -> Path:
        """The skill's source directory (parent of ``skill.yaml``)."""
        return self.source_path.parent


def _require_str(value: object, field: str, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"missing required field '{field}'", path)
    return value


def _optional_str(
    value: object, field: str, path: Path, *, max_len: int | None = None
) -> str | None:
    """Validate an optional spec string field (absent -> None)."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"field '{field}' must be a non-empty string", path)
    if max_len is not None and len(value) > max_len:
        raise ValidationError(f"field '{field}' exceeds {max_len} characters", path)
    return value


def _load_metadata(value: object, path: Path) -> tuple[tuple[str, str], ...]:
    """Validate the optional ``metadata`` map (string keys -> string values)."""
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise ValidationError("field 'metadata' must be a mapping", path)
    items: list[tuple[str, str]] = []
    for key, entry in value.items():
        if not isinstance(key, str):
            raise ValidationError("metadata keys must be strings", path)
        if not isinstance(entry, str):
            raise ValidationError(
                f"metadata value for '{key}' must be a string (quote numbers, e.g. \"1.0\")", path
            )
        items.append((key, entry))
    return tuple(items)


def discover_skills(
    repo_root: Path,
    source_dir: Path = SKILLS_SOURCE_DIR,
) -> list[SkillDefinition]:
    """Load and validate all skills from the source collection directory."""
    base_dir = repo_root / source_dir
    if not base_dir.exists():
        return []

    skills: list[SkillDefinition] = []
    seen_slugs: set[str] = set()

    for path in sorted(base_dir.glob(f"*/{SKILL_SOURCE_FILE}")):
        slug = path.parent.name
        if slug in seen_slugs:
            raise ValidationError(f"duplicate skill slug '{slug}'", path)
        seen_slugs.add(slug)

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValidationError(f"invalid YAML: {exc}", path) from exc
        if not isinstance(data, dict):
            raise ValidationError("skill source must be a YAML mapping", path)

        technologies = validate_technologies(data.get("technologies") or [], path)

        invocation = _require_str(data.get("invocation"), "invocation", path).strip()
        if invocation not in INVOCATIONS:
            raise ValidationError(
                f"field 'invocation' must be one of {', '.join(sorted(INVOCATIONS))}, "
                f"got {invocation!r}",
                path,
            )
        # A user-invoked entry legitimately has no description, so this cannot go
        # through _require_str. The pairing (model needs one, user must not have
        # one) is a catalog_lint rule so the failure can explain itself.
        raw_description = data.get("description")
        description = raw_description.strip() if isinstance(raw_description, str) else ""

        skills.append(
            SkillDefinition(
                slug=slug,
                name=_require_str(data.get("name"), "name", path).strip(),
                invocation=invocation,
                description=description,
                instructions=_require_str(data.get("instructions"), "instructions", path),
                source_path=path,
                technologies=tuple(technologies),
                license=_optional_str(data.get("license"), "license", path),
                compatibility=_optional_str(
                    data.get("compatibility"), "compatibility", path, max_len=500
                ),
                allowed_tools=_optional_str(data.get("allowed-tools"), "allowed-tools", path),
                metadata=_load_metadata(data.get("metadata"), path),
            )
        )

    return skills
