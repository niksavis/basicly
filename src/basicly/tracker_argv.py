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
# Each name is the ledger's own field key, measured 2026-08-16. Three families fail the
# test above and stay out: `--claim` carries no value, `--due`/`--defer` are re-based
# against the host clock into `due_at`/`defer_until`, and `--estimate` lands under
# `estimated_minutes`, which no record in this repo's ledger holds.
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
    "--labels": "labels",
}
UPDATE_STATUS_FLAGS = frozenset({"-s", "--status"})

# Whether each accumulating label flag adds its value. Out of `UPDATE_FIELD_FLAGS`
# because that table is replacement-only: `owned_write._resolve_labels` resolves these
# against the record's own set under the ledger lock and rewrites both into one
# `--labels`, so nothing below the seam sees them (basicly-wpc8).
UPDATE_LABEL_FLAGS = {"--add-label": True, "--remove-label": False}

LABEL_SEPARATOR = ","
LABELS_FIELD = "labels"


def labels_of(value: object) -> tuple[str, ...]:
    """A folded ``labels`` field as the labels it names, whichever shape holds it.

    Two shapes are legitimate: a ``created`` event stores the list the import extracted,
    and a ``field`` event cannot, because ``value`` is one of ``events.TRUNCATABLE_KEYS``
    and the schema refuses a container under a capped key.

    **Split, never iterated** — a bare string iterates as its characters, which is how a
    lane inherits twelve one-letter labels. A label carrying a separator cannot arise:
    a write splits its flag's value on the same one.
    """
    if isinstance(value, str):
        return tuple(part for part in (raw.strip() for raw in value.split(LABEL_SEPARATOR)) if part)
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value)
    return ()


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

# Every field flag a create takes, long spelling only, for the refusal that names them —
# read off the table above, so a flag added there joins that advice by existing.
CREATE_LONG_FLAGS = tuple(sorted(flag for flag in CREATE_FIELD_FLAGS if flag.startswith("--")))

# Flags whose value is the following token, per subcommand. Needed to find the
# positional a write is about: `br gate report` puts the issue id *last*, after four
# or five flag/value pairs, so "the last argument" is only right by accident and
# "every token that is not a flag" would collect `--note`'s free text as one.
VALUE_FLAGS: dict[str, frozenset[str]] = {
    "create": frozenset(CREATE_FIELD_FLAGS) | {"-a", "--assignee"},
    "update": frozenset(UPDATE_FIELD_FLAGS) | UPDATE_STATUS_FLAGS | frozenset(UPDATE_LABEL_FLAGS),
    "close": frozenset({"--reason"}),
    "dep add": frozenset({"-t", "--type"}),
    "dep remove": frozenset({"-t", "--type"}),
    "gate report": frozenset({"--gate", "--provider", "--status", "--note", "--actor"}),
}

# The flag that says a re-record is deliberate, which `re_record` reads. Valueless, so it
# needs no entry above: the grammar already reads a bare `--flag` as a pair with no value.
REPEAT_FLAG = "--again"

# The verbs whose flags are wholly tabulated, so one they read nothing from can be named
# rather than dropped. Derived above, so a flag added there joins by existing. Out by
# construction: `update` refuses an unknown flag itself, `comments add` takes its body by
# position where a leading `-` is text, and `create` mints its id per call, so a flag it
# half-reads collides with nothing.
GUARDED_FLAGS: dict[str, frozenset[str]] = {
    surface: VALUE_FLAGS[surface] | {REPEAT_FLAG}
    for surface in ("close", "dep add", "dep remove", "gate report")
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


def unreadable_flags(surface: str, args: Sequence[str]) -> list[str]:
    """Every flag in *args* that *surface* reads nothing from, in the order given.

    Empty for a surface absent from :data:`GUARDED_FLAGS`, which says that verb answers for
    its own flags rather than that nothing is wrong.
    """
    known = GUARDED_FLAGS.get(surface)
    if known is None:
        return []
    return [flag for flag, _ in flag_pairs(args, VALUE_FLAGS[surface]) if flag not in known]


def without_flags(
    args: Sequence[str], flags: Collection[str], value_flags: Collection[str]
) -> list[str]:
    """*args* with every flag in *flags* dropped, and the value each consumes with it.

    A flag in *value_flags* takes the following token, unless it carried its own after ``=``.
    """
    kept: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        name, sep, _ = arg.partition("=")
        if name in flags:
            skip = not sep and name in value_flags
            continue
        kept.append(arg)
    return kept
