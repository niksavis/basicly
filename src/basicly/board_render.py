"""Draw the wall as one self-contained HTML page, autoescaped, with no external origin.

**Its only input is the document and the verdict already ruled on it.** Nothing here reads a
ledger, a tracker, a store or a writer - `.importlinter`'s `consumer-reads-only-the-snapshot`
contract is the structural half of that and this signature is the other. A consumer that
reaches past the snapshot only ever works against basicly's own producer.

**Autoescaping is on**, unlike :func:`basicly.renderers.common.make_env`, whose ``S701``
suppression reads "nothing here is served to a browser". This output is HTML, a snapshot may
carry a foreign producer's strings, and so that env cannot be reused.

The regions are :mod:`basicly.board_regions`', :mod:`basicly.board_loop`'s and
:mod:`basicly.board_footer`'s, and this module composes them into one context and renders
it - which is why the page's age is a single reading, taken here rather than once per
region.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader

from . import board_footer, board_loop, board_regions, board_wall, catalog

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from pathlib import Path

    from .board_schema import SnapshotVerdict

TEMPLATE_DIR = "templates/board"
TEMPLATE = "board_page.html.j2"


def context(
    document: Mapping[str, Any],
    verdict: SnapshotVerdict,
    now: datetime,
    *,
    viewport_height: float | None = None,
    viewport_width: float | None = None,
) -> dict[str, Any]:
    """Every region of the wall, keyed as the template names it.

    The readings are derived once and handed to every region, so the verdict's inventory is
    the only inventory a region can draw from. *viewport_height* and *viewport_width* are
    board_regions.next_up's own arguments: this layer neither reads nor guesses either, it
    only carries what its caller gave it (basicly-ffm2yp).
    """
    reads = board_wall.readings(document, verdict)
    drawn = board_wall.age(document, now)
    phases, loop_note, pass_running = board_loop.loop(reads, now)
    backlog_phases, backlog_phases_note = board_loop.backlog_phases(reads)
    cards, flight_more, flight_note = board_regions.flight(reads, now=now)
    lines, events_more = board_footer.events(reads)
    hist, priorities_more = board_footer.priorities(reads)
    agents, health_more = board_footer.health(reads)
    gates, gates_note = board_footer.gates(reads)
    return {
        "age": drawn,
        "producer": board_wall.cell(reads["generator"], "producer", ("tool", "version")),
        "head": board_regions.head(reads),
        "gates": gates,
        "gates_note": gates_note,
        "spend": board_footer.spend(reads),
        # The throughput figure is dated against the *document's* own day, never the reader's:
        # a page opened after midnight would otherwise report the producer's yesterday as an
        # empty today.
        "throughput": board_footer.throughput(reads, board_wall.day(drawn.generated_at)),
        "band": board_regions.band(reads, drawn, now),
        "phases": phases,
        "loop_note": loop_note,
        # The loop region draws the pass and the backlog census draws the backlog, and
        # the two are separate keys because they are separate populations: binning one
        # under the other made the region total equal `backlog.active` (basicly-a68ggd).
        "pass_running": pass_running,
        "backlog_phases": backlog_phases,
        "backlog_phases_note": backlog_phases_note,
        "cards": cards,
        "flight_more": flight_more,
        "flight_note": flight_note,
        # The ready list is handed the shape the running row left it, which is the one place
        # the layout's two states have to agree with the model's two capacities.
        "ready": board_regions.next_up(
            reads, wide=not cards, viewport_height=viewport_height, viewport_width=viewport_width
        ),
        "backlog": board_footer.backlog(reads),
        "priorities": hist,
        "priorities_more": priorities_more,
        "agents": agents,
        "health_more": health_more,
        "events": lines,
        "events_more": events_more,
        "inventory": board_footer.inventory(reads),
        "states": board_wall.STATES,
        # Named rather than indexed out of `states`: the loop row marks its current phase and
        # its unmeasured ones with a glyph, and a template reaching in by position would pick
        # up whatever a later state landed at that index.
        "here_glyph": board_wall.BY_KEY[board_wall.LIVE].glyph,
        "none_glyph": board_wall.BY_KEY[board_wall.ABSENT].glyph,
        "schema": document.get("schema", board_wall.UNKNOWN),
    }


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
    viewport: tuple[float | None, float | None] | None = None,
) -> str:
    """One self-contained HTML page for *document*, referencing no external origin.

    Args:
        document: A parsed ``harness-board/v1`` snapshot. The only source of every value.
        verdict: :func:`basicly.board_schema.verdict`'s ruling on it, which carries the
            section inventory every region follows.
        now: The instant the age is computed against. Passed rather than read so a page is a
            function of its inputs.
        templates_dir: The directory holding :data:`TEMPLATE`. Defaults to the bundled
            catalog's, which resolves in a source checkout and in an installed wheel alike.
        viewport: The wall's own (height, width) in CSS pixels, where a caller has one to
            give (basicly-ffm2yp) - one pair rather than two arguments, so `page` still
            fits under the arity ratchet. None renders the reclaimed ready list at the
            conservative default rather than a guess, because this layer cannot see a screen.
    """
    height, width = viewport if viewport is not None else (None, None)
    filled = context(document, verdict, now, viewport_height=height, viewport_width=width)
    return _env(templates_dir).get_template(TEMPLATE).render(filled)
