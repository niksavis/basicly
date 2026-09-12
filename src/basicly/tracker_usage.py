from __future__ import annotations

GROUP_SUBCOMMANDS = frozenset({"comments", "config", "dep", "gate"})

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

    if not args or args[0].startswith("-"):
        return "", list(args)
    if args[0] in GROUP_SUBCOMMANDS and len(args) > 1 and not args[1].startswith("-"):
        return f"{args[0]} {args[1]}", list(args[2:])
    return args[0], list(args[1:])


def classify_access(subcommand: str) -> str:
    if subcommand in READ_SUBCOMMANDS:
        return "read"
    if subcommand in WRITE_SUBCOMMANDS:
        return "write"
    return "unclassified"
