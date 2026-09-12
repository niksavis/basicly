from __future__ import annotations

COPILOT_TOOL_ALIASES: dict[str, frozenset[str]] = {
    "execute": frozenset({"shell", "Bash", "powershell"}),
    "read": frozenset({"Read", "NotebookRead"}),
    "edit": frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"}),
    "search": frozenset({"Grep", "Glob"}),
    "agent": frozenset({"custom-agent", "Task"}),
    "web": frozenset({"WebSearch", "WebFetch"}),
    "todo": frozenset({"TodoWrite"}),
}
_COPILOT_TOOL_BY_ALIAS = {
    alias.casefold(): primary
    for primary, aliases in COPILOT_TOOL_ALIASES.items()
    for alias in (primary, *aliases)
}
_COPILOT_TOOL_NAMES = tuple(
    sorted(
        set(COPILOT_TOOL_ALIASES)
        | {alias for aliases in COPILOT_TOOL_ALIASES.values() for alias in aliases},
        key=str.casefold,
    )
)
WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "Create"})
_WRITE_TOOLS_FOLDED = frozenset(
    tool.casefold() for tool in (*WRITE_TOOLS, "edit", *COPILOT_TOOL_ALIASES["edit"])
)


def resolve_copilot_tool(tool: str) -> str | None:

    return _COPILOT_TOOL_BY_ALIAS.get(tool.casefold())
