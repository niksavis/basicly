"""GitHub Copilot's tool vocabulary, pinned as reviewed data.

Split from ``agents.py`` when the module-size ratchet refused it
(basicly-u2hl.52). It is one responsibility and names itself without an "and":
what GitHub publishes as the config vocabulary for a custom agent's ``tools``
list, how a declared name resolves through it, and which of those names grant a
write. Everything here is data plus one lookup; the policy that reads it stays
in ``agents.py``.
"""

from __future__ import annotations

# GitHub's published tool-alias table for copilot custom agents, pinned here as
# reviewed data (reviewed 2026-07-31 against
# docs.github.com/en/copilot/reference/custom-agents-configuration, basicly-8sxf).
# The key fact: copilot accepts Claude Code's own PascalCase tool names as
# first-class aliases and matches them case-insensitively, so projecting to the
# copilot root needs *no* translation layer — the names we already declare
# resolve on both families. Both a comma-separated string and a YAML array are
# accepted; an unset `tools` defaults to all tools, which is why this module
# refuses a source without an explicit allowlist.
#
# The table is pinned rather than assumed because copilot drops an unrecognised
# entry *silently* — measured on the copilot CLI, whose `--log-level debug` logs
# the granted tool schemas — where Claude Code refuses to launch and names the
# unresolved entries. `resolve_copilot_tool` plus the lint rule in
# `lint_agent_sources` restore the loud failure at authoring time. GitHub
# publishes no enumerated tool list, so this alias table is the entire
# vocabulary we can check a declared name against.
#
# Do NOT merge this with copilot's other tool vocabularies: VS Code chat uses
# `#`-prefixed namespaced names and tool *sets* (`#read/readFile`), and the
# copilot CLI's internal names are different again (view, grep, glob, bash,
# create, edit, skill, sql). They are separate vocabularies for separate
# surfaces; only this one is the config format for an agent file.
COPILOT_TOOL_ALIASES: dict[str, frozenset[str]] = {
    "execute": frozenset({"shell", "Bash", "powershell"}),
    "read": frozenset({"Read", "NotebookRead"}),
    "edit": frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"}),
    "search": frozenset({"Grep", "Glob"}),
    "agent": frozenset({"custom-agent", "Task"}),
    "web": frozenset({"WebSearch", "WebFetch"}),
    "todo": frozenset({"TodoWrite"}),
}
# Folded alias -> primary, so a declared name resolves in one lookup. A primary
# is its own alias: `tools: [read]` is as valid as `tools: [Read]`.
_COPILOT_TOOL_BY_ALIAS = {
    alias.casefold(): primary
    for primary, aliases in COPILOT_TOOL_ALIASES.items()
    for alias in (primary, *aliases)
}
# The same names as *authored*, for the lint remedy: a folded key is not
# something an author can copy into a source, and the PascalCase spellings are
# the ones a Claude-shaped source already uses.
_COPILOT_TOOL_NAMES = tuple(
    sorted(
        set(COPILOT_TOOL_ALIASES)
        | {alias for aliases in COPILOT_TOOL_ALIASES.values() for alias in aliases},
        key=str.casefold,
    )
)
# What our allowlist does NOT control on copilot, all measured 2026-07-31 on the
# copilot CLI against the logged tool schemas (basicly-8sxf). Record honestly:
# a "read-only" agent there is narrower than an unconstrained one but is not the
# read-only set this module names.
#   - `skill` and `sql` are granted UNCONDITIONALLY and the allowlist cannot
#     suppress them, so every agent we certify read-only holds two tools we never
#     declared (`sql` writes a per-session SQLite db, not the repo).
#   - `Bash` resolves to four tools — bash, read_bash, stop_bash, list_bash —
#     the same capability class over a wider surface.
#   - `NotebookEdit` alone resolves to both `create` AND `edit`, i.e. general
#     filesystem write: Claude's narrowest write tool is copilot's broadest.
#   - Expansion is not uniform: `Glob` did not pull in grep. A name with a 1:1
#     CLI counterpart maps narrowly, one without falls back to the broader
#     primary set.
# The guarantee that does hold: an unrecognised entry fails SAFE. An
# all-unrecognised list resolved to zero requested tools, with no grant-all
# fallback, so a typo costs function, not the read-only posture.
#
# A posture that declares the agent read-only must not grant mutating tools.
# Matched case-insensitively (basicly-e9jc): copilot's aliases are case
# insensitive, so a lowercase `edit` grants exactly the writes `Edit` does and
# has to fail the same check. Notes on the membership:
#   - `MultiEdit` is off Claude Code's published tool list but copilot still
#     accepts it as an alias of `edit`, so dropping it would only reopen a hole.
#   - `Create` is the copilot CLI's file-creating primary with no claude
#     equivalent — the same write grant under a name this set would otherwise
#     miss. It is deliberately not in COPILOT_TOOL_ALIASES: that table is
#     GitHub's published config vocabulary, and `create` is not in it.
WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "Create"})
# Unioned with every alias of copilot's `edit` primary so the pinned table drives
# the posture check: if GitHub adds a write alias, updating COPILOT_TOOL_ALIASES
# extends the check instead of leaving a hole only a reader would notice.
_WRITE_TOOLS_FOLDED = frozenset(
    tool.casefold() for tool in (*WRITE_TOOLS, "edit", *COPILOT_TOOL_ALIASES["edit"])
)


def resolve_copilot_tool(tool: str) -> str | None:
    """The copilot primary a declared tool name resolves to, or None if nothing.

    Resolution is case-insensitive, per GitHub's published alias table. `None`
    means copilot would drop the entry with no error, so callers should treat it
    as an authoring defect rather than a working grant.
    """
    return _COPILOT_TOOL_BY_ALIAS.get(tool.casefold())
