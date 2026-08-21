"""Draw the wall as one self-contained HTML page, autoescaped, with no external origin.

**Its only input is the document and the verdict already ruled on it.** Nothing here reads a
ledger, a tracker, a store or a writer - `.importlinter`'s `consumer-reads-only-the-snapshot`
contract is the structural half of that and this signature is the other. A consumer that
reaches past the snapshot only ever works against basicly's own producer.

**Autoescaping is on**, unlike :func:`basicly.renderers.common.make_env`, whose ``S701``
suppression reads "nothing here is served to a browser". This output is HTML, a snapshot may
carry a foreign producer's strings, and so that env cannot be reused.

The regions are :mod:`basicly.board_regions`' and :mod:`basicly.board_footer`'s, and this
module composes them into one context and renders it - which is why the page's age is a
single reading, taken here rather than once per region.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader

from . import board_footer, board_regions, board_wall, catalog

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from pathlib import Path

    from .board_schema import SnapshotVerdict

TEMPLATE_DIR = "templates/board"
TEMPLATE = "board_page.html.j2"


def context(document: Mapping[str, Any], verdict: SnapshotVerdict, now: datetime) -> dict[str, Any]:
    """Every region of the wall, keyed as the template names it.

    The readings are derived once and handed to every region, so the verdict's inventory is
    the only inventory a region can draw from.
    """
    reads = board_wall.readings(document, verdict)
    drawn = board_wall.age(document, now)
    phases, loop_note = board_regions.loop(reads)
    cards, flight_more, flight_note = board_regions.flight(reads)
    lines, events_more = board_footer.events(reads)
    return {
        "age": drawn,
        "head": board_regions.head(reads),
        "band": board_regions.band(reads, drawn, now),
        "phases": phases,
        "loop_note": loop_note,
        "cards": cards,
        "flight_more": flight_more,
        "flight_note": flight_note,
        "ready": board_regions.next_up(reads),
        "backlog": board_footer.backlog(reads),
        "priorities": board_footer.priorities(reads),
        "strips": board_footer.strips(reads),
        "events": lines,
        "events_more": events_more,
        "inventory": board_footer.inventory(reads),
        "legend": board_footer.legend(),
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
    """
    return _env(templates_dir).get_template(TEMPLATE).render(context(document, verdict, now))
