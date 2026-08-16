"""The shape of a ``br`` write's argv: which flags take a value, and what each names.

Split out of ``mirror`` when the module-size ratchet caught it at the cap. The boundary
is *reading an argv* against *translating one*: nothing here knows what an event is, and
nothing here raises — a caller decides what an unreadable argv means.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

# `br update`'s flags, as the ledger fact each records. Two mappings because `status`
# has its own event kind. A flag belongs here when br stores the argv's own value under
# one export key; a flag absent here is not dropped but raised on, since a mirrored
# write silently missing half of what br recorded stays invisible until the differential
# runs.
#
# Each name is br's own export key, measured 2026-08-16. Four families fail the test
# above and stay out: the label flags accumulate rather than replace (`--set-labels a
# --set-labels b` leaves both), `--claim` carries no value, `--due`/`--defer` are
# re-based against the host clock into `due_at`/`defer_until`, and `--estimate` lands
# under `estimated_minutes`, which no record in this repo's export holds.
UPDATE_FIELD_FLAGS = {
    "--title": "title",
    "-d": "description",
    "--description": "description",
    "--body": "description",
    "--design": "design",
    "--acceptance": "acceptance_criteria",
    "--acceptance-criteria": "acceptance_criteria",
    "--notes": "notes",
    "-t": "issue_type",
    "--type": "issue_type",
    "-p": "priority",
    "--priority": "priority",
    "--assignee": "assignee",
    "--owner": "owner",
    "--external-ref": "external_ref",
}
UPDATE_STATUS_FLAGS = frozenset({"-s", "--status"})

# `br create`'s flags, as the fields the created record carries.
CREATE_FIELD_FLAGS = {
    "-t": "issue_type",
    "--type": "issue_type",
    "-p": "priority",
    "--priority": "priority",
    "-l": "labels",
    "--label": "labels",
    "-d": "description",
    "--description": "description",
    "--parent": "parent",
}

# Flags whose value is the following token, per subcommand. Needed to find the
# positional a write is about: `br gate report` puts the issue id *last*, after four
# or five flag/value pairs, so "the last argument" is only right by accident and
# "every token that is not a flag" would collect `--note`'s free text as one.
VALUE_FLAGS: dict[str, frozenset[str]] = {
    "create": frozenset(CREATE_FIELD_FLAGS) | {"-a", "--assignee"},
    "update": frozenset(UPDATE_FIELD_FLAGS) | UPDATE_STATUS_FLAGS,
    "close": frozenset({"--reason"}),
    "dep add": frozenset({"-t", "--type"}),
    "gate report": frozenset({"--gate", "--provider", "--status", "--note", "--actor"}),
}


def positionals(args: Sequence[str], value_flags: Collection[str]) -> list[str]:
    """The positional words in *args*, with each value-taking flag's value consumed.

    ``--flag=value`` carries its own value, so only the space-separated form skips the
    next token. An argument after a flag this subcommand takes no value for stays a
    positional, so the caller can see it rather than having it silently absorbed.
    """
    found: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg.startswith("-"):
            skip = "=" not in arg and arg in value_flags
            continue
        found.append(arg)
    return found


def flag_pairs(args: Sequence[str], value_flags: Collection[str]) -> list[tuple[str, str]]:
    """Each ``(flag, value)`` in *args*, in the order given, both spellings accepted."""
    pairs: list[tuple[str, str]] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg.startswith("-"):
            name, sep, inline = arg.partition("=")
            if sep:
                pairs.append((name, inline))
            elif name in value_flags and index + 1 < len(args):
                pairs.append((name, args[index + 1]))
                index += 1
            else:
                pairs.append((name, ""))
        index += 1
    return pairs
