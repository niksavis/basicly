"""Draw a parsed ``harness-board/v1`` document as one self-contained HTML page.

**Its only input is the document and the verdict already ruled on it.** Nothing here reads
`.basicly/ledger/`, `.basicly/usage/`, a tracker, a store or a writer - the `consumer-reads-only-
the-snapshot` contract in `.importlinter` is the structural half of that, and this module's
signature is the other. A consumer that reaches past the snapshot only ever works against
basicly's own producer, which is the parity the contract exists to prevent.

**The section inventory is the verdict's, never a second list.** `board_schema` derives it from
the shipped schema's own property list, so the panels a page carries follow the schema and
cannot drift from it. :data:`LAYOUT` is a *placement* map - which of the eight wall regions a
panel sits in - and it can only place a section the verdict already named. A section it lists
that the schema has dropped renders nothing.

**Every panel carries the document's age, and that is a contract rather than a courtesy.** The
board is as fresh as the producer that wrote its snapshot; a value drawn without its age is the
overclaim the whole design is written against, so :func:`age` is composed into every panel and
there is no code path that renders one without it.

Autoescaping is **on**, unlike :func:`basicly.renderers.common.make_env`, whose ``S701``
suppression reads "nothing here is served to a browser". This output is HTML, a snapshot may
carry a foreign producer's strings, and so that env cannot be reused.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader

from . import board_fields, catalog

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from datetime import datetime

    from .board_schema import SnapshotVerdict

TEMPLATE_DIR = "templates/board"
TEMPLATE = "board_page.html.j2"

# What an absent section says, verbatim. A zero or an empty box claims the producer measured
# nothing; this says it did not measure.
ABSENT_TEXT = "not emitted by this producer"

RENDERABLE = "renderable"
WITHHELD = "withheld"
ABSENT = "absent"
LIVE = "live"
STALE = "stale"

# No value on this page is drawn as bare text - `-` reads as a dash in a number column.
UNKNOWN = "not measured"


@dataclass(frozen=True)
class State:
    """One state and the three channels it is encoded on.

    Colour is the third channel and never the only one: a glyph and a border style carry the
    same distinction, so the page survives a monochrome projector and a colour-blind reader.
    """

    key: str
    glyph: str
    border_style: str
    colour: str


STATES: tuple[State, ...] = (
    State(RENDERABLE, "[ok]", "solid", "var(--green)"),
    State(WITHHELD, "[!!]", "double", "var(--orange)"),
    State(ABSENT, "[--]", "dashed", "var(--text-dim)"),
    State(LIVE, "[ok]", "solid", "var(--green)"),
    State(STALE, "[!!]", "double", "var(--amber)"),
)

_BY_KEY = {state.key: state for state in STATES}

# The eight regions of the wall layout, and which sections each prefers. A layout, not an
# inventory: a name here draws a panel only when the verdict already carries that section, and
# a section no row claims lands in the last region rather than going undrawn.
LAYOUT: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("header", "board", ("generator", "repo", "session")),
    ("asks", "Asks", ("asks",)),
    ("lanes", "Lanes", ("lanes",)),
    ("spend", "Spend", ("spend",)),
    ("gates", "Gates", ("gates",)),
    ("health", "Health", ("health",)),
    ("backlog", "Backlog", ("backlog", "units", "graph")),
    ("events", "Events", ("events",)),
)

# Where a proportional bar is honest: a part and a whole the *same* section carries. Nothing
# is drawn against a constant, because this repo has already shipped a wrong `context_window`
# and a bar against a wrong ceiling is worse than the raw number.
BARS = {
    "session": ("spent_tokens", "token_budget"),
    "backlog": ("closed", "total"),
    "lanes": ("context_used", "context_window"),
}

_FULL = 100.0
_MINUTE = 60
_HOUR = 3600


@dataclass(frozen=True)
class Bar:
    """A proportional bar, drawn only where both of its terms were measured."""

    width: float
    label: str
    over: bool


@dataclass(frozen=True)
class Row:
    """One labelled value, with the bar and the glyph it earned."""

    label: str
    value: str
    glyph: str = ""
    bar: Bar | None = None


@dataclass(frozen=True)
class Card:
    """One body inside a panel: an object section has one, a list section one per item."""

    title: str
    rows: tuple[Row, ...]


@dataclass(frozen=True)
class Panel:
    """One section as it is drawn, its state on three channels and its note on absence."""

    section: str
    state: State
    cards: tuple[Card, ...]
    note: str


@dataclass(frozen=True)
class Region:
    """One of the eight wall regions and the panels placed in it."""

    key: str
    title: str
    panels: tuple[Panel, ...]


@dataclass(frozen=True)
class Age:
    """How old the document is, phrased for six metres and stamped for an auditor."""

    generated_at: str
    phrase: str
    state: State
    stale_after: str


def _elapsed(seconds: float) -> str:
    """*seconds* as the coarsest phrase that still answers "is this screen frozen"."""
    if seconds < _MINUTE:
        return f"{int(seconds)}s ago"
    if seconds < _HOUR:
        return f"{int(seconds // _MINUTE)}m {int(seconds % _MINUTE)}s ago"
    return f"{int(seconds // _HOUR)}h {int(seconds % _HOUR // _MINUTE)}m ago"


def age(document: Mapping[str, Any], now: datetime) -> Age:
    """The document's freshness, as every panel prints it.

    An unparseable stamp is :data:`STALE` rather than a blank: a viewer that cannot date the
    file it is drawing has no grounds to call it live.
    """
    stamp = document.get("generated_at")
    written = board_fields.instant(stamp) if isinstance(stamp, str) else None
    fresh = document.get("freshness")
    limit = fresh.get("stale_after_s") if isinstance(fresh, dict) else None
    bound = float(limit) if isinstance(limit, (int, float)) else None
    if written is None:
        return Age(str(stamp or UNKNOWN), "age unknown", _BY_KEY[STALE], UNKNOWN)
    seconds = max(0.0, (now - written).total_seconds())
    state = STALE if bound is not None and seconds > bound else LIVE
    return Age(
        board_fields.stamp(written),
        _elapsed(seconds),
        _BY_KEY[state],
        f"{bound:g}s" if bound is not None else UNKNOWN,
    )


def _numeric(value: object) -> float | None:
    """*value* as a float where it is a real number, else None. Booleans are not numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def bar(part: object, whole: object) -> Bar | None:
    """A bar for *part* of *whole*, or None where either term is absent or unmeasured.

    None on a zero whole too: a percentage of nothing is not a small bar, it is no ratio at
    all. The caller then prints the raw number, which is what the absence actually supports.
    """
    top, bottom = _numeric(part), _numeric(whole)
    if top is None or bottom is None or bottom <= 0:
        return None
    share = top / bottom * _FULL
    return Bar(min(share, _FULL), f"{share:.0f}%", share > _FULL)


def _scalar(value: object) -> tuple[str, str]:
    """*value* as (text, glyph) - the glyph is the second channel on a state-like value."""
    if value is None:
        return UNKNOWN, _BY_KEY[ABSENT].glyph
    if isinstance(value, bool):
        return ("yes", _BY_KEY[LIVE].glyph) if value else ("no", _BY_KEY[STALE].glyph)
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip("."), ""
    if isinstance(value, int):
        return f"{value:,}", ""
    text = str(value)
    return text, {"pass": "[ok]", "fail": "[!!]", "not_run": "[--]"}.get(text, "")


def _rows(value: object, prefix: str = "") -> Iterator[Row]:
    """*value* flattened to labelled rows, nested keys joined with a dot.

    Generic on purpose. A hand-written body per section is a second inventory that goes stale
    the moment the schema gains a field, and the schema is the thing this page follows.
    """
    if isinstance(value, dict):
        for key, held in value.items():
            yield from _rows(held, f"{prefix}{key}.")
    elif isinstance(value, list):
        for index, held in enumerate(value):
            yield from _rows(held, f"{prefix}{index}.")
    else:
        text, glyph = _scalar(value)
        yield Row(prefix.rstrip(".") or "value", text, glyph)


def _bar_rows(section: str, held: Mapping[str, Any], rows: tuple[Row, ...]) -> tuple[Row, ...]:
    """*rows* with the section's declared bar attached to its numerator, where both terms hold."""
    pair = BARS.get(section)
    if pair is None:
        return rows
    drawn = bar(held.get(pair[0]), held.get(pair[1]))
    if drawn is None:
        return rows
    return tuple(
        Row(row.label, row.value, row.glyph, drawn) if row.label == pair[0] else row for row in rows
    )


def _card(section: str, title: str, held: object) -> Card:
    """One card, its bar attached where the section declares one and the terms are there."""
    rows = tuple(_rows(held))
    if isinstance(held, dict):
        rows = _bar_rows(section, held, rows)
    return Card(title, rows)


def _cards(section: str, held: object) -> tuple[Card, ...]:
    """*held* as cards: one per item for a list section, one for anything else.

    An empty list is a card saying so, because ``[]`` from a producer that can see the thing
    is a different claim from a section it never emitted.
    """
    if isinstance(held, list):
        if not held:
            return (Card("", (Row("count", "0"),)),)
        return tuple(
            _card(section, str(item.get("id") or item.get("wait_id") or index), item)
            if isinstance(item, dict)
            else _card(section, str(index), item)
            for index, item in enumerate(held)
        )
    return (_card(section, "", held),)


def _panel(section: str, state: str, document: Mapping[str, Any], note: str) -> Panel:
    """One section's panel; an absent or withheld section carries its note and no card."""
    if state == RENDERABLE:
        return Panel(section, _BY_KEY[state], _cards(section, document.get(section)), "")
    return Panel(section, _BY_KEY[state], (), note)


def _states(verdict: SnapshotVerdict) -> dict[str, tuple[str, str]]:
    """Every section the verdict named, as ``section -> (state, note)``."""
    withheld = {
        held.name: ", ".join(held.violations) for held in verdict.sections if not held.conformant
    }
    return {
        **dict.fromkeys(verdict.renderable, (RENDERABLE, "")),
        **{name: (WITHHELD, note) for name, note in withheld.items()},
        **dict.fromkeys(verdict.absent, (ABSENT, ABSENT_TEXT)),
    }


def regions(document: Mapping[str, Any], verdict: SnapshotVerdict) -> tuple[Region, ...]:
    """The eight wall regions, holding one panel per section the verdict named.

    The panel set is the verdict's and the placement is :data:`LAYOUT`'s, which is why a
    section the layout names but the schema no longer declares draws nothing at all.
    """
    ruled = _states(verdict)
    placed = {name for _, _, names in LAYOUT for name in names}
    residue = [name for name in ruled if name not in placed]
    out = []
    for index, (key, title, names) in enumerate(LAYOUT):
        held = [name for name in names if name in ruled]
        if index == len(LAYOUT) - 1:
            held.extend(sorted(residue))
        panels = tuple(_panel(name, ruled[name][0], document, ruled[name][1]) for name in held)
        out.append(Region(key, title, panels))
    return tuple(out)


def _env(templates_dir: Path | None = None) -> Environment:
    """The page's own Jinja environment, autoescaping because the output is HTML."""
    root = templates_dir or catalog.bundled_catalog_root() / TEMPLATE_DIR
    return Environment(
        loader=FileSystemLoader(str(root)), autoescape=True, keep_trailing_newline=True
    )


def page(
    document: Mapping[str, Any],
    verdict: SnapshotVerdict,
    *,
    now: datetime,
    templates_dir: Path | None = None,
) -> str:
    """One self-contained HTML page for *document*, referencing no external origin.

    Args:
        document: A parsed ``harness-board/v1`` snapshot. The only source of every value.
        verdict: :func:`basicly.board_schema.verdict`'s ruling on it, which carries the
            section inventory the panels follow.
        now: The instant the age is computed against. Passed rather than read so a page is a
            function of its inputs.
        templates_dir: The directory holding :data:`TEMPLATE`. Defaults to the bundled
            catalog's, which resolves in a source checkout and in an installed wheel alike.
    """
    drawn = age(document, now)
    return (
        _env(templates_dir)
        .get_template(TEMPLATE)
        .render(
            regions=regions(document, verdict),
            age=drawn,
            states=STATES,
            schema=document.get("schema", UNKNOWN),
            absent_text=ABSENT_TEXT,
        )
    )
