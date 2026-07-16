"""Load and validate fragments and target registries."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from .schema import (
    CATEGORIES,
    DEFAULT_SCOPE,
    PRIORITY_MAP,
    SOURCE_SCHEMA_VERSION,
    STATUSES,
    Fragment,
    OutputDef,
    Target,
    ValidationError,
    validate_technologies,
)

FRAGMENT_SOURCE_GLOB = "*.fragment.yaml"

# The pre-migration source format. No longer loaded — but its presence in a
# fragment root means user content is silently inert, which must be surfaced.
LEGACY_FRAGMENT_GLOB = "*.fragment.md"

REQUIRED_FRAGMENT_FIELDS = {"id", "description", "category", "applies_to"}


def load_fragments(fragments_dir: Path, target_names: set[str]) -> list[Fragment]:
    """Load all fragment files from the fragments directory."""
    return load_fragments_from_roots([(fragments_dir, None)], target_names)


def load_fragments_from_roots(
    fragment_roots: list[tuple[Path, str | None]],
    target_names: set[str],
) -> list[Fragment]:
    """Load all fragment files from one or more fragment roots."""
    fragments: list[Fragment] = []
    seen_ids: dict[str, Path] = {}

    for root, source_hint in fragment_roots:
        if not root.exists():
            continue
        _warn_legacy_sources(root)
        for path in sorted(root.rglob(FRAGMENT_SOURCE_GLOB)):
            fragment = _load_fragment(path, source_hint)
            _validate_fragment(fragment, path, target_names)
            if fragment.id in seen_ids:
                first_path = seen_ids[fragment.id]
                raise ValidationError(
                    f"duplicate fragment id '{fragment.id}' (first defined in {first_path})",
                    path,
                )
            seen_ids[fragment.id] = path
            fragments.append(fragment)

    _validate_replacements(fragments)
    return fragments


def _warn_legacy_sources(root: Path) -> None:
    """Warn loudly when a fragment root still holds legacy ``*.fragment.md`` files.

    Only YAML sources load since the format migration; a legacy file is user
    content that has silently stopped affecting builds. Advisory (never fails
    the load) — the fix is the user's: migrate each file to
    ``<id>.fragment.yaml`` (``basicly fragment-new`` scaffolds one).
    """
    legacy = sorted(root.rglob(LEGACY_FRAGMENT_GLOB))
    if not legacy:
        return
    names = ", ".join(str(path.relative_to(root)) for path in legacy)
    print(
        f"Warning: {len(legacy)} legacy .fragment.md file(s) under {root} are ignored "
        f"(only .fragment.yaml sources load since the format migration): {names}. "
        "Migrate each to <id>.fragment.yaml — `basicly fragment-new` scaffolds one.",
        file=sys.stderr,
    )


def _validate_replacements(fragments: list[Fragment]) -> None:
    """Enforce replaces/override integrity across the merged fragment set.

    A fragment that lists ids in ``replaces`` must set ``override: true``, every
    replaced id must exist, and two user fragments may not replace each other.
    """
    by_id = {fragment.id: fragment for fragment in fragments}

    for fragment in fragments:
        if not fragment.replaces:
            continue

        if not fragment.override:
            raise ValidationError(
                f"fragment '{fragment.id}' declares 'replaces' but is missing "
                "'override: true'; a replacement is only honored with override enabled",
                fragment.source_path,
            )

        for replaced_id in fragment.replaces:
            replaced = by_id.get(replaced_id)
            if replaced is None:
                if fragment.source == "user":
                    # A core upgrade may remove the replaced fragment; the
                    # overlay must not brick every command. The replace is
                    # ignored until the user updates their overlay.
                    print(
                        f"Warning: overlay fragment '{fragment.id}' "
                        f"({fragment.source_path}) replaces unknown id "
                        f"'{replaced_id}' — likely removed by a core upgrade; "
                        "the replace is ignored. Review the overlay fragment.",
                        file=sys.stderr,
                    )
                    continue
                raise ValidationError(
                    f"fragment '{fragment.id}' replaces unknown fragment id '{replaced_id}'",
                    fragment.source_path,
                )
            if (
                fragment.source == "user"
                and replaced.source == "user"
                and fragment.id in replaced.replaces
            ):
                raise ValidationError(
                    f"mutual replace between user fragments '{fragment.id}' and "
                    f"'{replaced_id}'; only one may replace the other",
                    fragment.source_path,
                )


def _load_fragment(path: Path, source_hint: str | None = None) -> Fragment:
    try:
        front = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(f"invalid YAML: {exc}", path) from exc

    if not isinstance(front, dict):
        raise ValidationError("fragment source must be a YAML mapping", path)

    body = (front.get("body") or "").strip("\n")
    scope = front.get("scope", {})
    scope_paths = scope.get("paths", list(DEFAULT_SCOPE)) if scope else list(DEFAULT_SCOPE)
    inferred_source = source_hint or _infer_source_from_path(path)
    source = front.get("source", inferred_source)

    _validate_schema_version(front.get("schema_version"), path)
    return Fragment(
        id=front.get("id", ""),
        description=front.get("description", ""),
        category=front.get("category", ""),
        applies_to=front.get("applies_to", []),
        priority=front.get("priority", "medium"),
        scope_paths=scope_paths,
        tags=front.get("tags", []),
        technologies=front.get("technologies") or [],
        status=front.get("status", "active"),
        title=front.get("title"),
        body=body,
        source_path=path,
        source=source,
        override=bool(front.get("override", False)),
        replaces=front.get("replaces", []),
        extends=front.get("extends", []),
        enforced_by=front.get("enforced_by", []),
    )


def _validate_schema_version(value: object, path: Path) -> None:
    """Reject a source authored for a newer schema than this basicly knows.

    Missing is accepted (pre-versioning overlays); catalog-lint enforces the
    field on core sources. Newer must be a hard, actionable error — silently
    misreading a future format is worse than failing.
    """
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"schema_version must be an integer, got {value!r}", path)
    if value > SOURCE_SCHEMA_VERSION:
        raise ValidationError(
            f"source declares schema_version {value}, newer than this basicly "
            f"understands ({SOURCE_SCHEMA_VERSION}); upgrade basicly",
            path,
        )


def _infer_source_from_path(path: Path) -> str:
    """Infer source based on path conventions when front matter omits source."""
    parts = {part.lower() for part in path.parts}
    if ".basicly-local" in parts or "user" in parts:
        return "user"
    return "core"


def _validate_fragment(
    fragment: Fragment,
    path: Path,
    target_names: set[str],
) -> None:
    missing = REQUIRED_FRAGMENT_FIELDS - {
        k
        for k, v in {
            "id": fragment.id,
            "description": fragment.description,
            "category": fragment.category,
            "applies_to": fragment.applies_to,
        }.items()
        if v
    }
    if missing:
        raise ValidationError(
            f"missing required fields: {', '.join(sorted(missing))}",
            path,
        )

    if fragment.category not in CATEGORIES:
        raise ValidationError(f"unknown category '{fragment.category}'", path)

    if fragment.priority not in PRIORITY_MAP:
        raise ValidationError(f"unknown priority '{fragment.priority}'", path)

    if fragment.status not in STATUSES:
        raise ValidationError(f"unknown status '{fragment.status}'", path)

    for target in fragment.applies_to:
        if target != "all" and target not in target_names:
            raise ValidationError(
                f"applies_to value '{target}' is not a registered target",
                path,
            )

    if fragment.source not in {"core", "user"}:
        raise ValidationError(f"source must be 'core' or 'user', got '{fragment.source}'", path)

    if not isinstance(fragment.override, bool):
        raise ValidationError("override must be a boolean", path)

    if not isinstance(fragment.replaces, list) or not all(
        isinstance(x, str) for x in fragment.replaces
    ):
        raise ValidationError("replaces must be a list of strings", path)

    if not isinstance(fragment.extends, list) or not all(
        isinstance(x, str) for x in fragment.extends
    ):
        raise ValidationError("extends must be a list of strings", path)

    if not isinstance(fragment.enforced_by, list) or not all(
        isinstance(x, str) for x in fragment.enforced_by
    ):
        raise ValidationError("enforced_by must be a list of strings", path)

    validate_technologies(fragment.technologies, path)


def load_targets(targets_dir: Path) -> list[Target]:
    """Load all target registry YAML files."""
    targets: list[Target] = []

    for path in sorted(targets_dir.glob("*.yaml")):
        target = _load_target(path)
        targets.append(target)

    return targets


def _load_target(path: Path) -> Target:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(f"invalid YAML: {exc}", path) from exc

    outputs: list[OutputDef] = []
    for name, output in (data.get("outputs") or {}).items():
        filter_def = output.get("filter", {})
        outputs.append(
            OutputDef(
                name=name,
                template=output["template"],
                path=output.get("path"),
                path_template=output.get("path_template"),
                applies_to_filter=filter_def.get("applies_to", []),
                has_scope=filter_def.get("has_scope", False),
                exclude_scoped=filter_def.get("exclude_scoped", False),
            )
        )

    return Target(
        name=data["name"],
        enabled=data.get("enabled", True),
        tone=data.get("tone", "directive"),
        max_size_warning=data.get("max_size_warning", 0),
        outputs=outputs,
    )
