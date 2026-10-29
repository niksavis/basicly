from __future__ import annotations

import re
from pathlib import Path

PAGE = Path(__file__).parent.parent / ".basicly" / "core" / "kit" / "board" / "web" / "index.html"


def _function(name: str) -> str:
    text = PAGE.read_text(encoding="utf-8")
    start = text.index(f"function {name}(")
    return text[start : text.index("\n}\n", start)]


def test_an_open_draft_does_not_stop_the_list_from_refreshing() -> None:
    watch = _function("watchVersion")
    refresh = _function("refreshQuietly")

    assert "busy()" not in watch
    assert re.search(r"if \(document\.hidden\) return;", refresh)
    assert "keepPlace()" in refresh


def test_a_dropped_connection_is_said_and_cleared_on_reconnect() -> None:
    watch = _function("watchVersion")

    assert "the connection dropped" in watch
    assert 'outage("")' in watch
