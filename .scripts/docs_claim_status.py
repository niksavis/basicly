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

_VOCABULARY_HEADER = ("State", "Means", "Evidence required to claim it")

_GRADING_HEADINGS = frozenset({"status", "state"})

_HEADER = ["Capability", "Status", "Record", "Note"]
_STATES_WITHOUT_WORK = frozenset({"shipped", "deferred"})
_FENCE = re.compile(r"^(```|~~~)")


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _is_delimiter(row: str) -> bool:
    return bool(_cells(row)) and all(set(cell) <= {"-", ":"} and cell for cell in _cells(row))


def _tables(text: str) -> Iterator[tuple[str, list[str], list[list[str]]]]:

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

    words = set(re.findall(r"[a-z]+", cell.lower()))
    return next((state for state in states if state in words), None)


def architecture_grades_no_capability(root: Path) -> list[str]:

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
