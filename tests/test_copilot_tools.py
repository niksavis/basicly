"""GitHub's published tool vocabulary, and what our checks read from it.

Split from ``test_agents.py`` with the module it covers (basicly-u2hl.52, §9.4).
The table is pinned reviewed data rather than a live lookup, so these assertions
are what stop it drifting from the published page silently.
"""

from __future__ import annotations

from basicly.copilot_tools import resolve_copilot_tool


def test_copilot_tool_aliases_resolve_the_names_we_ship() -> None:
    """The pinned alias table is asserted against, not just documented.

    Every tool our core agents declare must resolve to a copilot primary, because
    copilot drops an unrecognised entry silently. The expected primaries are
    spelled out rather than derived from the table, so a wrong edit to the table
    fails here instead of agreeing with itself.
    """
    assert resolve_copilot_tool("Read") == "read"
    assert resolve_copilot_tool("Grep") == "search"
    assert resolve_copilot_tool("Glob") == "search"
    assert resolve_copilot_tool("Bash") == "execute"
    # Case-insensitive per GitHub's published table, and a primary is its own alias.
    assert resolve_copilot_tool("bASH") == "execute"
    assert resolve_copilot_tool("read") == "read"
    assert resolve_copilot_tool("NotAToolAtAll") is None
