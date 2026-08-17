"""The tracker write vocabulary: what a surface is, and whether it reads or writes.

Two consumers need different halves of one answer: `tracker._refuse_write_in_read_only` asks
whether an argv writes, and `mirror.drafts` asks it to decide what has no translation.

**This module used to measure a subprocess** — every spawn into a committed sample, which
is how the dependency was sized at 44 sites. There are none left (basicly-vkh0.42.7).
"""

from __future__ import annotations

# The subcommands whose first word is a group, so the surface is two words. Joined by
# :func:`split_invocation`, because a bare "list" would match "comments list".
GROUP_SUBCOMMANDS = frozenset({"comments", "config", "dep", "gate"})

# Surfaces that only read. Deliberately not exhaustive: an unknown surface is
# `unclassified` rather than guessed, which is what makes the read-only guard fail closed.
READ_SUBCOMMANDS = frozenset({
    "blocked",
    "comments list",
    "config get",
    "dep cycles",
    "dep list",
    "dep tree",
    "gate list",
    "list",
    "ready",
    "schema",
    "scheduler",
    "show",
    "stats",
    "where",
})
WRITE_SUBCOMMANDS = frozenset({
    "close",
    "comments add",
    "config set",
    "create",
    "delete",
    "dep add",
    "dep remove",
    "gate report",
    "reopen",
    "update",
})


def split_invocation(args: list[str]) -> tuple[str, list[str]]:
    """*args* as ``(surface, remainder)``.

    Two words for a group, one otherwise, empty when the argv opens with a flag.
    """
    if not args or args[0].startswith("-"):
        return "", list(args)
    if args[0] in GROUP_SUBCOMMANDS and len(args) > 1 and not args[1].startswith("-"):
        return f"{args[0]} {args[1]}", list(args[2:])
    return args[0], list(args[1:])


def classify_access(subcommand: str) -> str:
    """``read``, ``write`` or ``unclassified`` for *subcommand*."""
    if subcommand in READ_SUBCOMMANDS:
        return "read"
    if subcommand in WRITE_SUBCOMMANDS:
        return "write"
    return "unclassified"
