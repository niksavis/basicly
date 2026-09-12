from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader
from markdown_it import MarkdownIt
from markupsafe import Markup

from . import (
    board_assets,
    board_diagram,
    board_footer,
    board_graph,
    board_icons,
    board_loop,
    board_record,
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
TEMPLATE_RECORD = "board_record.html.j2"
TEMPLATE_KANBAN = "board_kanban.html.j2"
TEMPLATE_BACKLOG = "board_backlog.html.j2"
KANBAN_HREF = "loop.html"
BACKLOG_HREF = "backlog.html"


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
        Mapping[str, Mapping[str, Any]],
    ] = ((), 0, {}, {}, {}),
) -> dict[str, Any]:

    reads = board_wall.readings(document, verdict)
    drawn = board_wall.age(document, now)
    _, loop_note, pass_running = board_loop.loop(reads, now)
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
        "throughput": board_footer.throughput(reads, board_wall.day(drawn.generated_at)),
        "band": board_regions.band(reads, drawn, now),
        "acts": tuple(acts[0]),
        "acts_more": more(acts[1], "asks"),
        "kills": acts[2],
        "parking": acts[3],
        "starting": acts[4],
        "diagram": board_diagram.diagram(reads, now),
        "loop_note": loop_note,
        "pass_running": pass_running,
        "backlog_phases_note": backlog_phases_note,
        "cards": cards,
        "flight_more": flight_more,
        "flight_note": flight_note,
        "claimed": claimed_rows,
        "claimed_more": claimed_more,
        "parked": parked_rows,
        "parked_more": parked_more,
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
        "queue": board_graph.queue(reads),
        "priorities": hist,
        "priorities_more": priorities_more,
        "agents": agents,
        "health_more": health_more,
        "events": lines,
        "events_more": events_more,
        "inventory": board_footer.inventory(reads),
        "states": board_wall.STATES,
        "here_glyph": board_wall.BY_KEY[board_wall.LIVE].glyph,
        "schema": document.get("schema", board_wall.UNKNOWN),
        "kanban_href": KANBAN_HREF,
        "backlog_href": BACKLOG_HREF,
        "record_dir": board_record.HREF_DIR,
        "record_ext": board_record.HREF_SUFFIX,
        "linkable": frozenset(board_record.ids(document)),
        "titles": board_regions.unit_titles(reads),
    }


def root(templates_dir: Path | None = None) -> Path:
    return templates_dir or catalog.bundled_catalog_root() / TEMPLATE_DIR


def markdown(text: object) -> Markup:

    rendered = MarkdownIt("commonmark", {"html": False}).enable("table").render(str(text))
    return Markup(rendered)  # noqa: S704 — the renderer above escaped its input's markup


def _env(templates_dir: Path | None = None) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(root(templates_dir))),
        autoescape=True,
        keep_trailing_newline=True,
    )
    env.filters["markdown"] = markdown
    return env


def page(
    document: Mapping[str, Any],
    verdict: SnapshotVerdict,
    *,
    now: datetime,
    templates_dir: Path | None = None,
    viewport: tuple[float | None, float | None] | None = None,
) -> str:

    seen = viewport if viewport is not None else (None, None)
    return render(context(document, verdict, now, viewport=seen), templates_dir)


def _styled(filled: Mapping[str, Any], *, nested: bool = False) -> dict[str, Any]:

    return {**filled, "stylesheet": board_assets.href(board_assets.STYLESHEET, nested=nested)}


def render_record(filled: Mapping[str, Any], templates_dir: Path | None = None) -> str:

    return _env(templates_dir).get_template(TEMPLATE_RECORD).render(_styled(filled, nested=True))


def render_kanban(filled: Mapping[str, Any], templates_dir: Path | None = None) -> str:
    return _env(templates_dir).get_template(TEMPLATE_KANBAN).render(_styled(filled))


def render_backlog(filled: Mapping[str, Any], templates_dir: Path | None = None) -> str:
    return _env(templates_dir).get_template(TEMPLATE_BACKLOG).render(_styled(filled))


def render(filled: Mapping[str, Any], templates_dir: Path | None = None) -> str:

    drawn = {**_styled(filled), "icons": board_icons.marks(root(templates_dir))}
    return _env(templates_dir).get_template(TEMPLATE).render(drawn)
