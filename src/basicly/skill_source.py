from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .schema import ValidationError, validate_technologies

SKILLS_SOURCE_DIR = Path(".basicly/core/skills")
SKILL_SOURCE_FILE = "skill.yaml"
EVAL_SOURCE_FILE = "evals.yaml"

MODEL_INVOKED = "model"
USER_INVOKED = "user"
INVOCATIONS = frozenset({MODEL_INVOKED, USER_INVOKED})


@dataclass(frozen=True)
class SkillDefinition:
    slug: str
    name: str
    invocation: str
    description: str
    instructions: str
    source_path: Path
    technologies: tuple[str, ...] = ()
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: str | None = None
    metadata: tuple[tuple[str, str], ...] = ()
    covered_work_types: tuple[str, ...] = ()
    covered_phases: tuple[str, ...] = ()
    claude: tuple[tuple[str, object], ...] = ()

    @property
    def source_dir(self) -> Path:
        return self.source_path.parent


def _require_str(value: object, field: str, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"missing required field '{field}'", path)
    return value


def _optional_str(
    value: object, field: str, path: Path, *, max_len: int | None = None
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"field '{field}' must be a non-empty string", path)
    if max_len is not None and len(value) > max_len:
        raise ValidationError(f"field '{field}' exceeds {max_len} characters", path)
    return value


RESERVED_SKILL_FRONTMATTER_KEYS = frozenset({"name", "description"})


def _load_claude_passthrough(value: object, path: Path) -> tuple[tuple[str, object], ...]:

    if value is None:
        return ()
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValidationError("field 'claude' must be a mapping of frontmatter keys", path)
    shadowed = sorted(set(value) & RESERVED_SKILL_FRONTMATTER_KEYS)
    if shadowed:
        raise ValidationError(
            f"claude passthrough may not shadow the rendered key(s) {', '.join(shadowed)}",
            path,
        )
    return tuple(value.items())


def _load_metadata(value: object, path: Path) -> tuple[tuple[str, str], ...]:
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


def _load_covers(value: object, path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:

    if value is None:
        return (), ()
    if not isinstance(value, dict):
        raise ValidationError("field 'covers' must be a mapping", path)
    if unknown := sorted(set(value) - {"work_types", "phases"}):
        raise ValidationError(f"field 'covers' has unknown key(s) {', '.join(unknown)}", path)
    axes: list[tuple[str, ...]] = []
    for axis in ("work_types", "phases"):
        entry = value.get(axis)
        if entry is None:
            axes.append(())
            continue
        if not isinstance(entry, list) or not all(
            isinstance(item, str) and item.strip() for item in entry
        ):
            raise ValidationError(f"covers.{axis} must be a list of non-empty strings", path)
        axes.append(tuple(item.strip() for item in entry))
    if not any(axes):
        raise ValidationError(
            "field 'covers' must declare work_types or phases; an empty block would "
            "match every dispatch",
            path,
        )
    return axes[0], axes[1]


def discover_skills(
    repo_root: Path,
    source_dir: Path = SKILLS_SOURCE_DIR,
) -> list[SkillDefinition]:
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
        raw_description = data.get("description")
        description = raw_description.strip() if isinstance(raw_description, str) else ""
        covers = _load_covers(data.get("covers"), path)

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
                covered_work_types=covers[0],
                covered_phases=covers[1],
                claude=_load_claude_passthrough(data.get("claude"), path),
            )
        )

    return skills
