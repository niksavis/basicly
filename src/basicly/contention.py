from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from . import corpus_drift, loop_state, merge, needs_input, tracker
from .config import lane_scope


def append_only_report(
    repo_root: Path, lanes: tuple[str, ...], paths: tuple[str, ...]
) -> tuple[str, ...]:

    if not paths:
        return (
            "no append-only path declared ([worktree] append_only_paths) - a path every "
            "lane writes is invisible to the grouping until it is listed",
        )
    header = f"append-only: {', '.join(f'`{path}`' for path in paths)}"
    if len(lanes) < 2:
        return (f"{header} - {len(lanes)} lane(s) in this pass, so nothing contends",)
    scopes = merge.declared_scopes(repo_root, lanes)
    lines = [header]
    for path in paths:
        contending = tuple(lane for lane in lanes if path not in scopes.get(lane, ()))
        if len(contending) < 2:
            continue
        lines.append(
            f"  {len(contending)} lane(s) will each append to `{path}` and none declares it: "
            f"{', '.join(contending)}"
        )
        lines.append(
            "    the later ones rebase onto a moved anchor and bounce, so build them in "
            "sequence, or give one lane the entry"
        )
    return tuple(lines)


def generated_report(commands: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:

    if not commands:
        return (
            "no generated path declared ([worktree.regenerate_commands]) - a landing conflict "
            "on an artifact every lane rebuilds bounces the lane instead of being rebuilt",
        )
    return (
        "generated: a landing conflict confined to these is rebuilt and continues, "
        "spending no rework",
        *(f"           `{path}` <- `{' '.join(argv)}`" for path, argv in sorted(commands.items())),
    )


SCOPE_FACT = "scope"


def with_scope_fence(repo_root: Path, issue_id: str, prompt: str) -> str:

    owned = sibling_scopes(repo_root, issue_id)
    if not owned:
        return prompt
    return f"{prompt}\n\n{_fence(repo_root, issue_id, owned)}"


def sibling_scopes(repo_root: Path, issue_id: str) -> dict[str, tuple[str, ...]]:

    record = tracker.read_record(repo_root, issue_id)
    if record is None:
        return {}
    siblings = {
        sibling
        for parent in _parent_ids(record)
        for sibling, status in corpus_drift.children_of_record(
            tracker.read_record(repo_root, parent) or {}
        ).items()
        if sibling != issue_id and loop_state.is_dispatchable(status)
    }
    scopes = merge.declared_scopes(repo_root, sorted(siblings))
    return {lane: paths for lane, paths in scopes.items() if paths}


def _parent_ids(record: Mapping[str, object]) -> tuple[str, ...]:

    dependencies = record.get("dependencies")
    rows = dependencies if isinstance(dependencies, list) else []
    edges = (tracker.dependency_edge(dep) for dep in rows)
    return tuple(
        edge[0] for edge in edges if edge is not None and edge[1] == loop_state.PARENT_CHILD
    )


def _fence(repo_root: Path, issue_id: str, owned: Mapping[str, tuple[str, ...]]) -> str:

    lines = [
        f"Scope this pass has already handed out. {_admits(repo_root, issue_id)} Every path "
        "below is a still-open sibling lane's declared ground:",
        *(
            f"- {lane} owns {', '.join(f'`{path}`' for path in paths)}"
            for lane, paths in sorted(owned.items())
        ),
        "An edit to one of those is not a shortcut. The landing refuses it before the merge "
        "and holds this record until a human rules on the scope, which costs the pass a "
        "round and the sibling a conflict. When the work genuinely needs one of those "
        "paths, do not edit it and do not widen your own declaration to cover it: write "
        f"{needs_input.SENTINEL_FILE.as_posix()} as "
        '{"fact": "' + SCOPE_FACT + '", "detail": "<the path, the lane that owns it, and '
        'why the work needs it>"} and stop.',
    ]
    return "\n".join(lines)


def _admits(repo_root: Path, issue_id: str) -> str:

    declared = merge.declared_scopes(repo_root, (issue_id,)).get(issue_id, ())
    derived = ", ".join(f"`{path}`" for path in lane_scope(issue_id))
    precedence = (
        f"{derived} are yours by construction and outrank any sibling glob below that "
        "also covers them"
    )
    if not declared:
        return (
            "You declare no `## Scope`, so the landing's own scope check is inert on your "
            f"diff and admits it whole; {precedence}."
        )
    admitted = ", ".join(f"`{path}`" for path in declared)
    return f"The paths this landing admits from you: {admitted}. Also {precedence}."
