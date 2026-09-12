from __future__ import annotations

import re

from basicly import board_diagram
from tests.test_board_wall import REPO_ROOT

PAGE = REPO_ROOT / ".basicly" / "core" / "templates" / "board" / "board_page.html.j2"

NARROW_REGION_PX = 1424.0
WIDE_REGION_PX = 1904.0
LOOP_HEIGHT_BUDGET_PX = 220.0


def _rules(selector: str) -> list[str]:

    css = re.sub(r"/\*.*?\*/", "", PAGE.read_text(encoding="utf-8"), flags=re.DOTALL)
    return [
        body
        for found, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", css)
        if found.strip() == selector
    ]


def _declared(selector: str, prop: str) -> float:
    bodies = _rules(selector)
    assert len(bodies) == 1, f"{selector} has {len(bodies)} rules, so which one is the size"
    found = re.search(rf"{prop}:\s*([\d.]+)px", bodies[0])
    assert found is not None, f"{selector} declares no {prop}"
    return float(found.group(1))


def test_a_station_label_is_no_smaller_than_a_ready_row() -> None:

    scale = NARROW_REGION_PX / board_diagram.VIEW_W
    label = _declared(".flow .station .name", "font-size") * scale
    assert label >= _declared(".ready td", "font-size"), (
        f"a station renders at {label:.1f}px under the ready row it sits above"
    )


def test_the_page_puts_no_ceiling_back_on_the_drawing() -> None:

    bodies = _rules(".flow")
    assert bodies, "the page declares no `.flow` rule at all"
    assert all("max-height" not in body for body in bodies), (
        "a `max-height` on `.flow` is the letterbox this record removed"
    )


def test_the_drawing_costs_no_more_height_than_the_wall_can_spare() -> None:

    drawn = WIDE_REGION_PX / (board_diagram.VIEW_W / board_diagram.VIEW_H)
    assert drawn <= LOOP_HEIGHT_BUDGET_PX, (
        f"the drawing takes {drawn:.0f}px of a 1080px wall, which the ready list pays for"
    )
