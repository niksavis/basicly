from __future__ import annotations

from basicly.copilot_tools import resolve_copilot_tool


def test_copilot_tool_aliases_resolve_the_names_we_ship() -> None:

    assert resolve_copilot_tool("Read") == "read"
    assert resolve_copilot_tool("Grep") == "search"
    assert resolve_copilot_tool("Glob") == "search"
    assert resolve_copilot_tool("Bash") == "execute"
    assert resolve_copilot_tool("bASH") == "execute"
    assert resolve_copilot_tool("read") == "read"
    assert resolve_copilot_tool("NotAToolAtAll") is None
