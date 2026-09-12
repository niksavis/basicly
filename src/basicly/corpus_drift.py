from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from . import tracker

CONTEXT_HEADING = "## context"

CLOSED_STATUS = "closed"

UNVERIFIED = "UNVERIFIED"

UNVERIFIED_MARK = (
    f"[{UNVERIFIED} — {{closed}} of this epic's children have closed and this claim "
    "names none of them; possibly superseded, not a current fact]"
)

_BULLET_RE = re.compile(r"^(?P<marker>[-*])\s+(?P<text>.*)$")
_HEADING_PREFIX = "## "
_FENCE = "```"
_ID_EDGE = r"[\w.\-]"
_UNVERIFIED_RE = re.compile(rf"\b{UNVERIFIED}\b", re.IGNORECASE)


@dataclass(frozen=True)
class Bullet:
    text: str
    line: int


@dataclass(frozen=True)
class Finding:
    issue_id: str
    bullet: str
    closed_children: tuple[str, ...]
    accounted_children: tuple[str, ...]


def problem_bullets(description: str) -> tuple[Bullet, ...]:

    bullets: list[Bullet] = []
    in_context = False
    in_fence = False
    for index, line in enumerate(description.splitlines()):
        if line.startswith(_FENCE):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith(_HEADING_PREFIX):
            in_context = line.strip().lower() == CONTEXT_HEADING
            continue
        if not in_context:
            continue
        match = _BULLET_RE.match(line)
        if match:
            bullets.append(Bullet(match.group("text").strip(), index))
        elif bullets and line.startswith((" ", "\t")) and line.strip():
            held = bullets[-1]
            bullets[-1] = Bullet(f"{held.text} {line.strip()}", held.line)
    return tuple(bullets)


def children_of_record(record: Mapping[str, object]) -> dict[str, str]:

    children: dict[str, str] = {}
    dependents = record.get("dependents")
    for dep in dependents if isinstance(dependents, list) else []:
        edge = tracker.dependency_edge(dep)
        if edge is None or edge[1] != "parent-child":
            continue
        children[edge[0]] = str(dep.get("status") or "") if isinstance(dep, dict) else ""
    return children


def children_by_parent(records: Iterable[Mapping[str, object]]) -> dict[str, dict[str, str]]:

    children: dict[str, dict[str, str]] = {}
    for record in records:
        child_id = record.get("id")
        if not isinstance(child_id, str):
            continue
        dependencies = record.get("dependencies")
        for dep in dependencies if isinstance(dependencies, list) else []:
            edge = tracker.dependency_edge(dep)
            if edge is None or edge[1] != "parent-child":
                continue
            children.setdefault(edge[0], {})[child_id] = str(record.get("status") or "")
    return children


def _named_children(text: str, child_ids: Iterable[str]) -> set[str]:
    return {
        child_id
        for child_id in child_ids
        if re.search(rf"(?<!{_ID_EDGE}){re.escape(child_id)}(?!{_ID_EDGE})", text)
    }


def _accounted(text: str, child_ids: Iterable[str]) -> bool:
    return bool(_UNVERIFIED_RE.search(text)) or bool(_named_children(text, child_ids))


def _closed(children: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(sorted(cid for cid, status in children.items() if status == CLOSED_STATUS))


def unaccounted_bullets(description: str, children: Mapping[str, str]) -> tuple[Bullet, ...]:

    if not _closed(children):
        return ()
    child_ids = frozenset(children)
    return tuple(
        bullet for bullet in problem_bullets(description) if not _accounted(bullet.text, child_ids)
    )


def epic_findings(
    issue_id: str, description: str, children: Mapping[str, str]
) -> tuple[Finding, ...]:
    closed = _closed(children)
    accounted = tuple(
        sorted({
            child_id
            for bullet in problem_bullets(description)
            for child_id in _named_children(bullet.text, children)
        })
    )
    return tuple(
        Finding(issue_id, bullet.text, closed, accounted)
        for bullet in unaccounted_bullets(description, children)
    )


def annotate(description: str, children: Mapping[str, str]) -> str:

    flagged = unaccounted_bullets(description, children)
    if not flagged:
        return description
    mark = UNVERIFIED_MARK.format(closed=len(_closed(children)))
    lines = description.splitlines()
    for bullet in flagged:
        line = lines[bullet.line]
        match = _BULLET_RE.match(line)
        if match:
            lines[bullet.line] = f"{match.group('marker')} {mark} {match.group('text')}"
    trailing = "\n" if description.endswith("\n") else ""
    return "\n".join(lines) + trailing
