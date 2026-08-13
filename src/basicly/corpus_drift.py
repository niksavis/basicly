"""Which of an epic's problem-statement claims a machine may still read as fact.

An epic's problem statement is the decider's intake corpus
(:func:`basicly.decider_contract.intake_corpus`), so a bullet its own closed children
have already fixed is not commentary — it is the fact base an agent reasons from, and
it is the one part of a bead nothing ever revisits. Measured on ``basicly-u2hl``
(2026-08-08): four of eight bullets were superseded by its own closed children and one
was refuted outright, after which two escalations quoted the refuted bullet verbatim,
reasoned from it, and abstained while both lanes were already mergeable.

**A claim is accounted for by naming a child, never by resembling one.** Attribution by
text similarity was measured against that same case and refused: TF-IDF over the closed
children's titles ranked the true superseder first for 1 of the 4 known pairs and scored
an unsuperseded bullet at 0.50 against a child with nothing to do with it, and term
coverage over their full descriptions reached 2 of 4 with false pairs at 0.78. So nothing
here guesses which child killed which bullet. A bullet is accounted for when it names a
child of its own epic — the form the hand correction already used, ``SHIPPED 2026-08-08
(basicly-u2hl.4): ...`` — or when it is marked unverified. Everything else reaches a
decider as a possibly-superseded claim instead of a current fact, which is the only
direction that fails safe: marking a live claim unverified costs an abstention, while
presenting a dead one as fact is what cost two.

The core is pure — a description plus a child-status map — because its two callers hold
different sources. The corpus annotation reads the record ``br show`` already returned;
the gate reads the committed export, which is what a fresh clone has and needs no
tracker binary.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from . import br

# The section a bead states its problem in. Matched case-insensitively against a
# heading line, so a statement is looked for in one declared place rather than
# wherever prose happens to hold bullets.
CONTEXT_HEADING = "## context"

CLOSED_STATUS = "closed"

# The author's own escape hatch, and deliberately the cheap one: a bullet nobody has
# re-established is honestly marked rather than deleted or re-argued.
UNVERIFIED = "UNVERIFIED"

# What an unaccounted bullet reaches a decider as.
UNVERIFIED_MARK = (
    f"[{UNVERIFIED} — {{closed}} of this epic's children have closed and this claim "
    "names none of them; possibly superseded, not a current fact]"
)

_BULLET_RE = re.compile(r"^(?P<marker>[-*])\s+(?P<text>.*)$")
_HEADING_PREFIX = "## "
_FENCE = "```"
# What may not sit against an id for it to have been named. Searching for the child ids
# the tracker actually reported, rather than for an id-shaped pattern, keeps the rule free
# of any assumption about how a tracker spells one — and the boundary is what stops a
# bullet naming `.52` from reading as also naming `.5`.
_ID_EDGE = r"[\w.\-]"
_UNVERIFIED_RE = re.compile(rf"\b{UNVERIFIED}\b", re.IGNORECASE)


@dataclass(frozen=True)
class Bullet:
    """One problem-statement bullet: its text, and where the description holds it."""

    text: str
    # 0-based index of the bullet's first line, so an annotation edits that line
    # instead of re-serialising a description a human wrote.
    line: int


@dataclass(frozen=True)
class Finding:
    """One bullet a decider would read as current fact and should not."""

    issue_id: str
    bullet: str
    closed_children: tuple[str, ...]
    accounted_children: tuple[str, ...]


def problem_bullets(description: str) -> tuple[Bullet, ...]:
    """Every top-level bullet under ``## Context``, continuation lines folded in.

    Fenced blocks are skipped: a quoted claim inside a code fence is evidence
    about a bullet, not a bullet, and flagging one would ask an author to mark up
    a transcript.
    """
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
    """The parent-child dependents of one ``br show`` record, id to status.

    The record already carries each child's status, so a corpus read costs the one
    tracker call it was always going to make.
    """
    children: dict[str, str] = {}
    dependents = record.get("dependents")
    for dep in dependents if isinstance(dependents, list) else []:
        edge = br.dependency_edge(dep)
        if edge is None or edge[1] != "parent-child":
            continue
        children[edge[0]] = str(dep.get("status") or "") if isinstance(dep, dict) else ""
    return children


def children_by_parent(records: Iterable[Mapping[str, object]]) -> dict[str, dict[str, str]]:
    """Parent id to its children's statuses, inverted out of the exported records.

    The export spells the edge from the child's side only, so the gate has to invert
    it; :func:`basicly.br.dependency_edge` is what reads both of br's spellings.
    """
    children: dict[str, dict[str, str]] = {}
    for record in records:
        child_id = record.get("id")
        if not isinstance(child_id, str):
            continue
        dependencies = record.get("dependencies")
        for dep in dependencies if isinstance(dependencies, list) else []:
            edge = br.dependency_edge(dep)
            if edge is None or edge[1] != "parent-child":
                continue
            children.setdefault(edge[0], {})[child_id] = str(record.get("status") or "")
    return children


def _named_children(text: str, child_ids: Iterable[str]) -> set[str]:
    """The children *text* names, each matched whole rather than as a substring."""
    return {
        child_id
        for child_id in child_ids
        if re.search(rf"(?<!{_ID_EDGE}){re.escape(child_id)}(?!{_ID_EDGE})", text)
    }


def _accounted(text: str, child_ids: Iterable[str]) -> bool:
    """Whether a bullet ties itself to a child's outcome or admits it is unverified."""
    return bool(_UNVERIFIED_RE.search(text)) or bool(_named_children(text, child_ids))


def _closed(children: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(sorted(cid for cid, status in children.items() if status == CLOSED_STATUS))


def unaccounted_bullets(description: str, children: Mapping[str, str]) -> tuple[Bullet, ...]:
    """The bullets no child accounts for, once at least one child has closed.

    With nothing closed there is nothing that could have superseded a claim, so a
    freshly decomposed epic is silent rather than asked to mark up a statement it
    has just written.
    """
    if not _closed(children):
        return ()
    child_ids = frozenset(children)
    return tuple(
        bullet for bullet in problem_bullets(description) if not _accounted(bullet.text, child_ids)
    )


def epic_findings(
    issue_id: str, description: str, children: Mapping[str, str]
) -> tuple[Finding, ...]:
    """One finding per bullet of *issue_id* a decider would read as current fact."""
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
    """*description* with every unaccounted bullet marked as possibly superseded.

    Marked in place, at the head of the bullet: a decider reads the corpus top to
    bottom, so a correction anywhere else is a correction it never reaches — which
    is exactly how the refuted claim was quoted as fact.
    """
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
