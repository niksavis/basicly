from __future__ import annotations

from collections.abc import Collection, Sequence

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

UPDATE_LABEL_FLAGS = {"--add-label": True, "--remove-label": False}

LABEL_SEPARATOR = ","
LABELS_FIELD = "labels"


def labels_of(value: object) -> tuple[str, ...]:

    if isinstance(value, str):
        return tuple(part for part in (raw.strip() for raw in value.split(LABEL_SEPARATOR)) if part)
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value)
    return ()


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

CREATE_LONG_FLAGS = tuple(sorted(flag for flag in CREATE_FIELD_FLAGS if flag.startswith("--")))

VALUE_FLAGS: dict[str, frozenset[str]] = {
    "create": frozenset(CREATE_FIELD_FLAGS) | {"-a", "--assignee"},
    "update": frozenset(UPDATE_FIELD_FLAGS) | UPDATE_STATUS_FLAGS | frozenset(UPDATE_LABEL_FLAGS),
    "close": frozenset({"--reason"}),
    "dep add": frozenset({"-t", "--type"}),
    "dep remove": frozenset({"-t", "--type"}),
    "gate report": frozenset({"--gate", "--provider", "--status", "--note", "--actor"}),
}

REPEAT_FLAG = "--again"

GUARDED_FLAGS: dict[str, frozenset[str]] = {
    surface: VALUE_FLAGS[surface] | {REPEAT_FLAG}
    for surface in ("close", "dep add", "dep remove", "gate report")
}


def positionals(args: Sequence[str], value_flags: Collection[str]) -> list[str]:

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

    known = GUARDED_FLAGS.get(surface)
    if known is None:
        return []
    return [flag for flag, _ in flag_pairs(args, VALUE_FLAGS[surface]) if flag not in known]


def without_flags(
    args: Sequence[str], flags: Collection[str], value_flags: Collection[str]
) -> list[str]:

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
