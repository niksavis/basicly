"""The drawn loop measured at the size a reader gets, not the size it declares.

The owner's report was *"the diagram size and font does not fit the backlog 'next up' font
size"*, and every assertion in the sibling module missed it: a figure inside a `viewBox` is
not a figure on the page. The surface is scaled to fit its region, so a label renders at its
declared size **times that scale**, and a `font-size` inside the viewBox cannot reach it.
The scale is set by the surface's aspect ratio, so these three assertions are about shape.

The two region widths are measured with `check_render_overflow.py`'s own browser rather than
taken from the viewport: the region is the viewport less the page's padding, and guessing
that inset is how a 44px error once read as a clip.
"""

from __future__ import annotations

import re

from basicly import board_diagram
from tests.test_board_wall import REPO_ROOT

PAGE = REPO_ROOT / ".basicly" / "core" / "templates" / "board" / "board_page.html.j2"

NARROW_REGION_PX = 1424.0
WIDE_REGION_PX = 1904.0
# What the loop region may spend at the wide wall before it takes rows off the ready list.
LOOP_HEIGHT_BUDGET_PX = 220.0


def _rules(selector: str) -> list[str]:
    """Every rule body the page declares for exactly *selector*, media queries included.

    Selector-exact and not a substring: the page also carries `.loop:not(.quiet) .flow`,
    which withholds the drawing at a short wall and is not a rule about `.flow` itself.
    """
    css = re.sub(r"/\*.*?\*/", "", PAGE.read_text(encoding="utf-8"), flags=re.DOTALL)
    return [
        body
        for found, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", css)
        if found.strip() == selector
    ]


def _declared(selector: str, prop: str) -> float:
    """The one px value the page declares for *prop* under *selector*, as a number."""
    bodies = _rules(selector)
    assert len(bodies) == 1, f"{selector} has {len(bodies)} rules, so which one is the size"
    found = re.search(rf"{prop}:\s*([\d.]+)px", bodies[0])
    assert found is not None, f"{selector} declares no {prop}"
    return float(found.group(1))


def test_a_station_label_is_no_smaller_than_a_ready_row() -> None:
    """The owner's report as arithmetic.

    It fails if anyone grows the surface, shrinks the label, or grows the `next up` row
    against it, which is the whole of basicly-ubwp49 held in one line.
    """
    scale = NARROW_REGION_PX / board_diagram.VIEW_W
    label = _declared(".flow .station .name", "font-size") * scale
    assert label >= _declared(".ready td", "font-size"), (
        f"a station renders at {label:.1f}px under the ready row it sits above"
    )


def test_the_page_puts_no_ceiling_back_on_the_drawing() -> None:
    """A `max-height` is the letterbox, and the letterbox is the whole defect.

    With the height free the width binds and the surface fills its region. A ceiling makes
    the *height* bind: the drawing shrinks, centres, and left 43% of the width empty at
    1920 with the labels at 13.6px.
    """
    bodies = _rules(".flow")
    assert bodies, "the page declares no `.flow` rule at all"
    assert all("max-height" not in body for body in bodies), (
        "a `max-height` on `.flow` is the letterbox this record removed"
    )


def test_the_drawing_costs_no_more_height_than_the_wall_can_spare() -> None:
    """Filling the width has a price, and the aspect ratio sets it.

    At the first draft's 4.2:1 this drawing would take 455px of a 1080px wall that carries
    six other regions.
    """
    drawn = WIDE_REGION_PX / (board_diagram.VIEW_W / board_diagram.VIEW_H)
    assert drawn <= LOOP_HEIGHT_BUDGET_PX, (
        f"the drawing takes {drawn:.0f}px of a 1080px wall, which the ready list pays for"
    )
