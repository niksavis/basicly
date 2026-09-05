"""Draw the wall as one self-contained HTML page, autoescaped, with no external origin.

**Its only input is the document and the verdict already ruled on it.** Nothing here reads a
ledger, a tracker, a store or a writer - `.importlinter`'s `consumer-reads-only-the-snapshot`
contract is the structural half of that and this signature is the other. A consumer that
reaches past the snapshot only ever works against basicly's own producer.

**Autoescaping is on**, unlike :func:`basicly.renderers.common.make_env`, whose ``S701``
suppression reads "nothing here is served to a browser". This output is HTML and a snapshot
carries a foreign producer's strings, so that env cannot be reused.

The regions are :mod:`basicly.board_regions`', :mod:`basicly.board_diagram`'s,
:mod:`basicly.board_loop`'s and :mod:`basicly.board_footer`'s; this module composes them
into one context and draws it, which is why the page's age is a single reading.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader

from . import (
    board_diagram,
    board_footer,
    board_graph,
    board_loop,
    board_regions,
    board_wall,
    catalog,
)
from .board_wall import more

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
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
    viewport: tuple[float | None, float | None] = (None, None),
    acts: tuple[
        Sequence[Mapping[str, Any]],
        int,
        Mapping[str, Mapping[str, Any]],
        Mapping[str, Mapping[str, Any]],
    ] = ((), 0, {}, {}),
) -> dict[str, Any]:
    """Every region of the wall, keyed as the template names it.

    The readings are derived once and handed to every region, so the verdict's inventory is
    the only inventory a region can draw from. *viewport* is board_regions.next_up's own
    (height, width) and *acts* is :mod:`basicly.board_asks`' whole output - its rows, what
    it dropped, a kill form per running lane and a park-or-resume form per record, the last
    two keyed by the id of what they act on. This layer neither reads
    nor guesses either, it carries what its caller gave it (basicly-ffm2yp). Each arrives as
    one tuple because the arity ratchet counts arguments, and the forms arrive as data so
    every string of them is drawn through the autoescape.
    """
    reads = board_wall.readings(document, verdict)
    drawn = board_wall.age(document, now)
    _, loop_note, pass_running = board_loop.loop(reads, now)
    # The row is dropped: the strip drawing it repeated the diagram's own digits (ubwp49).
    _, backlog_phases_note = board_loop.backlog_phases(reads)
    cards, flight_more, flight_note = board_regions.flight(reads, now=now)
    claimed_rows, claimed_more = board_regions.claimed(reads)
    parked_rows, parked_more = board_regions.parked(reads)
    lines, events_more = board_footer.events(reads)
    hist, priorities_more = board_footer.priorities(reads)
    agents, health_more = board_footer.health(reads)
    gates, gates_note = board_footer.gates(reads, drawn)
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
        # A region, not an append after `</main>`: the body is `100vh` with
        # `overflow: hidden`, so the old panel sat 274px past the fold (basicly-ua9o5g).
        "acts": tuple(acts[0]),
        "acts_more": more(acts[1], "asks"),
        # Keyed by lane id, so the card that draws a lane finds its own form and no other.
        "kills": acts[2],
        "parking": acts[3],
        # The workflow drawn, which replaced first a histogram and then a pass row
        # (basicly-6c97zx). `board_loop.loop` is still called for its note and verdict,
        # which no other region carries.
        "diagram": board_diagram.diagram(reads, now),
        "loop_note": loop_note,
        "pass_running": pass_running,
        "backlog_phases_note": backlog_phases_note,
        "cards": cards,
        "flight_more": flight_more,
        "flight_note": flight_note,
        # Somebody has taken these and no lane holds them. Named rather than counted: the
        # page printed `IN PROGRESS 1` three times over and a reader could not learn which
        # record it meant (basicly-5jkxqk).
        "claimed": claimed_rows,
        "claimed_more": claimed_more,
        # Parked work, which no region drew at all: a deferred record is out of the phase
        # counts, the claimed rows and the ready set (basicly-arxhshr).
        "parked": parked_rows,
        "parked_more": parked_more,
        # The ready list is handed the shape the running row left it, which is the one place
        # the layout's two states have to agree with the model's two capacities.
        # The list gives up rows to the `acts` region: a decision the factory is stopped
        # on outranks what could be started next, and without this the page clips (210px).
        "ready": board_regions.next_up(
            reads,
            wide=not cards,
            viewport_height=viewport[0],
            viewport_width=viewport[1],
            reserved=board_regions.acts_reserve(len(acts[0]))
            + board_regions.claimed_reserve(len(claimed_rows))
            + board_regions.parked_reserve(len(parked_rows))
            + board_regions.QUEUE_PX,
        ),
        "backlog": board_footer.backlog(reads),
        # Whether the next work is parallel or a queue, off the edges the footer used to draw
        # as one number. `DEP EDGES 846` beside `BLOCKED 56` settled nothing (basicly-pck9fx).
        "queue": board_graph.queue(reads),
        "priorities": hist,
        "priorities_more": priorities_more,
        "agents": agents,
        "health_more": health_more,
        "events": lines,
        "events_more": events_more,
        "inventory": board_footer.inventory(reads),
        "states": board_wall.STATES,
        # Named and not indexed out of `states`: a position picks up whatever landed there.
        "here_glyph": board_wall.BY_KEY[board_wall.LIVE].glyph,
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
        document: A parsed ``harness-board/v1`` snapshot, the only source of every value.
        verdict: :func:`basicly.board_schema.verdict`'s ruling, carrying the inventory.
        now: The instant the age is taken against, passed so a page is a function of its
            inputs.
        templates_dir: Where :data:`TEMPLATE` lives; the bundled catalog's by default, which
            resolves in a checkout and in a wheel alike.
        viewport: The wall's own (height, width) in CSS pixels (basicly-ffm2yp). None draws
            the reclaimed list at the safe default rather than a guess.
    """
    seen = viewport if viewport is not None else (None, None)
    return render(context(document, verdict, now, viewport=seen), templates_dir)


def render(filled: Mapping[str, Any], templates_dir: Path | None = None) -> str:
    """Draw an already-built :func:`context`, for a caller with something to add to it.

    Split from :func:`page` for the action rows (basicly-ua9o5g): a server holds a token and
    a set of pending asks the static artifact has not.
    """
    return _env(templates_dir).get_template(TEMPLATE).render(filled)
