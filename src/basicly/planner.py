from __future__ import annotations

from pathlib import Path

from .schema import Fragment, OutputDef, PlannedOutput, Target, ValidationError


def contained_output_path(repo_root: Path, relative: str, *, field: str) -> Path:

    if not relative:
        raise ValidationError(f"{field} is empty; a projection output needs a path")
    candidate = Path(relative)
    if candidate.is_absolute() or candidate.drive or candidate.root:
        raise ValidationError(
            f"{field} {relative!r} is an absolute path; a projection output must be "
            "relative to the repo root (an absolute path silently replaces it)"
        )
    joined = repo_root / candidate
    root = repo_root.resolve()
    resolved = joined.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValidationError(
            f"{field} {relative!r} resolves to {resolved}, outside the repo root "
            f"{root}; a projection output must stay inside the repo"
        )
    return joined


def _checked_fragment_id(fragment_id: str, *, field: str) -> str:

    if "/" in fragment_id or "\\" in fragment_id or fragment_id.strip(".") == "":
        raise ValidationError(
            f"{field}: fragment id {fragment_id!r} contains a path separator or is a "
            "traversal segment, so it cannot be interpolated into an output path"
        )
    return fragment_id


def plan_outputs(
    fragments: list[Fragment],
    targets: list[Target],
    repo_root: Path,
) -> list[PlannedOutput]:

    active = [f for f in fragments if f.status == "active"]
    active = _apply_user_replacements(active)
    planned: list[PlannedOutput] = []

    for target in targets:
        if not target.enabled:
            continue
        for output in target.outputs:
            if output.path:
                selected = _select_fragments(active, output)
                if selected:
                    planned.append(
                        PlannedOutput(
                            target_name=target.name,
                            output_name=output.name,
                            output_path=contained_output_path(
                                repo_root,
                                output.path,
                                field=f"target {target.name!r} output {output.name!r} path",
                            ),
                            template=output.template,
                            fragments=selected,
                        )
                    )
            elif output.path_template:
                scoped = _select_scoped_fragments(active, output)
                field = f"target {target.name!r} output {output.name!r} path_template"
                for fragment in scoped:
                    output_path = contained_output_path(
                        repo_root,
                        output.path_template.format(
                            fragment_id=_checked_fragment_id(fragment.id, field=field)
                        ),
                        field=field,
                    )
                    planned.append(
                        PlannedOutput(
                            target_name=target.name,
                            output_name=output.name,
                            output_path=output_path,
                            template=output.template,
                            fragments=[fragment],
                        )
                    )

    return planned


def _apply_user_replacements(fragments: list[Fragment]) -> list[Fragment]:
    replaced_core_ids = {
        replaced_id
        for fragment in fragments
        if fragment.source == "user"
        for replaced_id in fragment.replaces
    }
    if not replaced_core_ids:
        return fragments

    return [
        fragment
        for fragment in fragments
        if not (fragment.source == "core" and fragment.id in replaced_core_ids)
    ]


def _select_fragments(fragments: list[Fragment], output: OutputDef) -> list[Fragment]:
    selected = [
        f
        for f in fragments
        if _applies_to_matches(f.applies_to, output.applies_to_filter)
        and (not output.has_scope or f.is_scoped)
        and not (output.exclude_scoped and f.is_scoped)
    ]
    return _sort_fragments(selected)


def _select_scoped_fragments(
    fragments: list[Fragment],
    output: OutputDef,
) -> list[Fragment]:
    selected = [
        f
        for f in fragments
        if f.is_scoped and _applies_to_matches(f.applies_to, output.applies_to_filter)
    ]
    return _sort_fragments(selected)


def _applies_to_matches(fragment_applies_to: list[str], filter_values: list[str]) -> bool:
    return any(target in filter_values for target in fragment_applies_to)


def _sort_fragments(fragments: list[Fragment]) -> list[Fragment]:
    return sorted(
        fragments,
        key=lambda f: (-f.priority_value, f.category, f.id),
    )
