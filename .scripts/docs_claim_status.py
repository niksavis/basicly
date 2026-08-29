"""The capability status view: one source, rendered, and graded nowhere else (D-30).

Three hand-maintained copies of this view existed and two had already diverged
(basicly-e2mz.37): the architecture document graded the tool-call boundary `partial`
while `status.md` graded the same four hooks `designed` in one row and `shipped` in
another. The rule that kept the copies in step was prose, and nothing gated it.

This owns one whole claim, evidence and judgement both, on the pattern
``docs_claim_surfaces`` set — ``docs_claims`` keeps only the registration. It has two
halves, because a copy diverges in two directions:

* :func:`render_status_view` renders every row of ``docs/architecture/status.yaml`` into
  the generated block in ``status.md``, so the rendered table cannot drift from its
  source.
* :func:`architecture_grades_no_capability` refuses a capability *grading* anywhere in
  the architecture document, so a second copy cannot appear there again. Architecture
  says what a capability is and what it must satisfy; the view says where it has got to.

The vocabulary has one definition, and it is the architecture document's own
component-state table (architecture §2): both halves read it rather than holding a second
copy, so the closed set cannot be extended in one file alone.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from docs_claim_sources import ARCHITECTURE_MD, ClaimError, load_yaml, read_text

from basicly import tracker

if TYPE_CHECKING:
    from pathlib import Path

STATUS_MD = "docs/architecture/status.md"
STATUS_SOURCE = "docs/architecture/status.yaml"

# The header of the table in architecture §2 that *defines* the vocabulary, and so the one
# status-graded column :func:`architecture_grades_no_capability` must allow. Keying on the
# header rather than on a section number keeps the exemption attached to the table if the
# document is renumbered — architecture §3 makes those numbers a contract with the code,
# but nothing stops a table moving between sections.
_VOCABULARY_HEADER = ("State", "Means", "Evidence required to claim it")

# A column under either heading grades whatever its row names. Both spellings are in the
# document today: §38 heads its column `Status`, §37.3 heads its own `State`.
_GRADING_HEADINGS = frozenset({"status", "state"})

_HEADER = ["Capability", "Status", "Record", "Note"]
# A row in one of these states names no work: `shipped` is done and `deferred` is a decision
# not to do it, which its note must state. Every other state is a promise, and a promise with
# no record in the ledger is the roadmap and the tracker disagreeing (basicly-r8civ7).
_STATES_WITHOUT_WORK = frozenset({"shipped", "deferred"})
_FENCE = re.compile(r"^(```|~~~)")


def _cells(row: str) -> list[str]:
    """The content cells of a markdown table row."""
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _is_delimiter(row: str) -> bool:
    """Whether *row* is the ``| --- | --- |`` line that makes the row above it a header."""
    return bool(_cells(row)) and all(set(cell) <= {"-", ":"} and cell for cell in _cells(row))


def _tables(text: str) -> Iterator[tuple[str, list[str], list[list[str]]]]:
    """Every markdown table in *text*, as (nearest heading, header cells, data rows).

    Fenced blocks are skipped: a mermaid edge label is written ``-->|yes|`` and a
    sequence diagram carries pipes that no table parser should see.
    """
    lines = text.splitlines()
    heading = ""
    fenced = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            title = re.match(r"^#{1,6} (.*)$", line)
            if title:
                heading = title.group(1).strip()
            elif (
                line.startswith("|") and index + 1 < len(lines) and _is_delimiter(lines[index + 1])
            ):
                header = _cells(line)
                index += 2
                rows: list[list[str]] = []
                while index < len(lines) and lines[index].startswith("|"):
                    rows.append(_cells(lines[index]))
                    index += 1
                yield heading, header, rows
                continue
        index += 1


def component_states(root: Path) -> tuple[str, ...]:
    """The closed set of component states, read out of the architecture document.

    Raises:
        ClaimError: the defining table is gone or carries no state. A missing anchor must
            fail loudly rather than return an empty set, which would let every grading
            through and report a clean tree forever.
    """
    for _, header, rows in _tables(read_text(root / ARCHITECTURE_MD)):
        if tuple(header) != _VOCABULARY_HEADER:
            continue
        states = tuple(cells[0].strip("`") for cells in rows if cells)
        if not states:
            raise ClaimError(f"{ARCHITECTURE_MD}: the component-state table defines no state")
        return states
    raise ClaimError(
        f"{ARCHITECTURE_MD}: no table headed {' | '.join(_VOCABULARY_HEADER)};"
        " the component-state vocabulary has no single definition"
    )


def _rows(root: Path) -> Iterator[tuple[str, list[list[str]]]]:
    """Each section of the status source, as (name, rendered rows).

    Raises:
        ClaimError: a row is missing a field, grades itself with a word the architecture
            document does not define, or names a capability a second row already names.
            The last one is the defect this whole claim exists for: the divergence that
            started it was one capability carrying two states at once.
    """
    states = component_states(root)
    source = load_yaml(root / STATUS_SOURCE)
    sections = source.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ClaimError(f"{STATUS_SOURCE}: 'sections' must be a non-empty list")

    seen: set[str] = set()
    views: dict[str, object] | None = None
    for section in sections:
        name = section.get("name")
        capabilities = section.get("capabilities")
        if not isinstance(name, str) or not isinstance(capabilities, list) or not capabilities:
            raise ClaimError(f"{STATUS_SOURCE}: every section needs a name and capabilities")
        rows: list[list[str]] = []
        for capability in capabilities:
            title = capability.get("name")
            status = capability.get("status")
            if not isinstance(title, str) or not isinstance(status, str):
                raise ClaimError(f"{STATUS_SOURCE}: {capability!r} needs a name and a status")
            if status not in states:
                raise ClaimError(
                    f"{STATUS_SOURCE}: {title!r} is {status!r};"
                    f" architecture §2 defines {', '.join(states)}"
                )
            if title in seen:
                raise ClaimError(f"{STATUS_SOURCE}: {title!r} is graded by two rows")
            seen.add(title)
            if status not in _STATES_WITHOUT_WORK and views is None:
                views = tracker.all_views(root)
            record = _record_for(
                views, title, status, capability.get("record"), capability.get("note")
            )
            rows.append([
                title,
                status,
                record,
                " ".join(str(capability.get("note") or "").split()),
            ])
        yield name, rows


def _record_for(
    views: dict[str, object] | None, title: str, status: str, record: object, note: object
) -> str:
    """The open ledger record a promised row points at, or the reason none is needed.

    *views* is the ledger folded once for the whole source: a fold per row cost 70 s.

    Raises:
        ClaimError: a row that promises work names no record, names one the ledger does not
            hold, or names one that is closed - the last is a row the tree already holds.
    """
    if status in _STATES_WITHOUT_WORK:
        if status == "deferred" and not str(note or "").strip():
            raise ClaimError(f"{STATUS_SOURCE}: {title!r} is deferred with no note saying why")
        return "" if record is None else str(record)
    if not isinstance(record, str) or not record.strip():
        raise ClaimError(f"{STATUS_SOURCE}: {title!r} is {status} and names no record")
    view = (views or {}).get(record)
    if view is None:
        raise ClaimError(
            f"{STATUS_SOURCE}: {title!r} names {record}, which the ledger does not hold"
        )
    if str(getattr(view, "status", "")) == "closed":
        raise ClaimError(f"{STATUS_SOURCE}: {title!r} names {record}, which is closed")
    return record


def render_status_view(root: Path) -> list[str]:
    """The whole view: one heading and one table per section of the source.

    The block spans the headings as well as the tables, so adding a section is a source
    edit rather than a document edit. ``docs_claims._table`` renders one blank-line-padded
    table and is not importable from here — ``docs_claims`` imports this module — so the
    three table lines are spelled out below.
    """
    body: list[str] = []
    for name, rows in _rows(root):
        body.extend([
            "",
            f"## {name}",
            "",
            f"| {' | '.join(_HEADER)} |",
            f"| {' | '.join('---' for _ in _HEADER)} |",
            *(f"| {' | '.join(row)} |" for row in rows),
        ])
    body.append("")
    return body


def _graded_word(cell: str, states: tuple[str, ...]) -> str | None:
    """The component state *cell* grades something with, if any.

    Word-wise rather than by equality: the cell that carried this defect into the
    architecture document read ``partial · no bead``, which is a grading plus a note.
    """
    words = set(re.findall(r"[a-z]+", cell.lower()))
    return next((state for state in states if state in words), None)


def architecture_grades_no_capability(root: Path) -> list[str]:
    """No table in the architecture document may grade a row with a component state.

    The document defines the vocabulary and states what each capability must satisfy; the
    status view states where each one has got to. A grading here is a second copy of a row
    that already exists in ``status.yaml``, and it goes stale when the *code* moves rather
    than when a decision does — which is what makes an architecture sentence false under a
    refactor that changed no decision.

    A decision record's own ``Status`` column is untouched by this, and needs no exemption:
    ``accepted``, ``proposed`` and ``superseded`` are not component states, and a decision
    record changes state exactly when a decision does.
    """
    states = component_states(root)
    problems: list[str] = []
    for heading, header, rows in _tables(read_text(root / ARCHITECTURE_MD)):
        if tuple(header) == _VOCABULARY_HEADER:
            continue
        graded = [index for index, cell in enumerate(header) if cell.lower() in _GRADING_HEADINGS]
        for cells in rows:
            for index in graded:
                if index >= len(cells):
                    continue
                state = _graded_word(cells[index], states)
                if state:
                    problems.append(
                        f"'{heading}' grades {cells[0]} as {state!r};"
                        f" a component state belongs in {STATUS_SOURCE} and nowhere else"
                    )
    return problems
