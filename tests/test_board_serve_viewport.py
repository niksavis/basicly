from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from basicly import board_regions, board_serve
from tests.test_board_serve import board_repo

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["board_repo"]


def _many_ready() -> dict[str, object]:
    return {
        "schema": "harness-board/v1",
        "generated_at": "2026-10-29T12:00:00Z",
        "units": [
            {"id": f"demo-{n:02d}", "title": f"ready {n}", "priority": "P2", "ready": True}
            for n in range(40)
        ],
    }


def test_the_served_wall_sizes_its_ready_list_for_the_shortest_supported_viewport(
    board_repo: Path,
) -> None:
    board = board_serve.Board(board_repo, build=_many_ready, actions=None)
    assert board.refresh() is True

    page = board.page(datetime.now(UTC))

    assert page is not None
    drawn = len(re.findall(r'<td class="id clip">', page.decode("utf-8")))
    height, width = board_regions.SHORTEST_VIEWPORT
    fits = board_regions.ready_capacity(height, width, board_regions.QUEUE_PX)
    assert 1 <= drawn <= fits < board_regions.READY_SLOTS_WIDE
