"""The full ``br``/``bv`` surface, so "never used" is a measured set (basicly-vkh0.2).

:mod:`basicly.tracker_usage` answers *which surfaces we exercise*. That is half the
input Phase 6 needs. The other half is the complement — **which surfaces exist and
we never touch** — because deciding not to build those is the main way owning the
tracker stays tractable (`docs/design/work-tracker.md` §6). A usage ledger alone
cannot answer it: absence of a record is indistinguishable from absence of a
command.

**The inventory comes from the tool's own help output, and that matters legally as
well as practically.** `docs/design/work-tracker.md` §7 records a clean-room
boundary: the replacement must not be derived from ``beads_rust`` source, and
``br``'s *documented CLI contract* is one of the three sanctioned inputs. ``br
--help`` is exactly that contract, so generating the inventory from it stays inside
the boundary. No source is read.

**Generated once, committed, and read offline.** The report always reads the
committed inventory rather than probing ``br`` live: the probe needs ``br`` on PATH
and costs ~50 spawns, and a report that silently changes shape depending on which
machine runs it is not evidence. ``basicly usage tracker --refresh-surface``
regenerates it; the file carries the ``br`` version it was generated from, so a
drift from :data:`basicly.br.PINNED_VERSION` is visible instead of silent.

**Deliberately no timestamp.** Regenerating against an unchanged ``br`` must
produce a byte-identical file, or the artifact churns in every diff and stops being
reviewable.

**``bv`` is flag-only.** It is a TUI viewer with ``--robot-*`` flags and no
subcommands at all, so its surface is recorded as flags. Keeping the asymmetry
explicit is the point: a reader must not conclude from an empty ``bv`` command list
that the probe failed.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# Sits beside the usage ledger because it is the other half of the same question,
# and in `ledger/` rather than `usage/` because that directory ignores everything
# inside it and this artifact is meant to be committed.
INVENTORY_FILE = Path(".basicly/ledger/tracker-surface.json")

SCHEMA = "basicly.tracker-surface.v1"

# A command name in br's help: lowercase, digits and hyphens. Used both to parse
# the help block and to reject a token that cannot be a surface — a shell
# redirection (`2>&1`) or an unexpanded variable (`$g`) reaching the ledger as a
# subcommand is what motivated it (see tracker_usage.is_surface_word).
_COMMAND_NAME = re.compile(r"^[a-z][a-z0-9-]*$")

# A long flag; short flags are aliases of one and add no surface to freeze.
#
# The lookahead is load-bearing. Help text wraps a long flag across lines
# (``--agents-\n      update``), and a trailing-hyphen fragment must yield *no*
# flag rather than a shortened one. Forbidding a final hyphen with a character
# class alone is not enough: the match simply backtracks, so ``--agents-`` became
# ``--agents`` — a real bv flag, and therefore a wrong reading that looks right.
_LONG_FLAG = re.compile(r"--[a-z][a-z0-9-]*[a-z0-9](?![a-z0-9-])")

_HELP_TIMEOUT_S = 10


def parse_commands(help_text: str) -> list[str]:
    """Command names from the ``Commands:`` block of a clap ``--help``.

    Only lines at the *same indent* as the first command line are accepted, so a
    wrapped description line cannot contribute its first word as a command.
    ``help`` is dropped: it is clap's own, not a tracker surface.
    """
    lines = help_text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "Commands:")
    except StopIteration:
        return []

    names: list[str] = []
    indent: int | None = None
    for line in lines[start + 1 :]:
        if not line.strip():
            break
        current = len(line) - len(line.lstrip())
        if indent is None:
            indent = current
        elif current != indent:
            continue  # a wrapped description, not a command
        name = line.split()[0]
        if name != "help" and _COMMAND_NAME.match(name):
            names.append(name)
    return names


def parse_flags(help_text: str) -> list[str]:
    """Every distinct long flag mentioned in *help_text*, sorted."""
    return sorted(set(_LONG_FLAG.findall(help_text)))


def _help(binary: str, *args: str) -> str:
    """``<binary> <args> --help`` stdout+stderr, or "" when it cannot be run.

    An argv list, never a shell string: a shell string would need quoting and
    ``shlex`` mangles a Windows path (basicly-5tjk). Failure returns empty so a
    missing binary degrades the inventory rather than raising — the caller reports
    what it could not probe.
    """
    try:
        proc = subprocess.run(  # noqa: S603 — argv list, no shell; see the docstring
            [binary, *args, "--help"],
            capture_output=True,
            text=True,
            timeout=_HELP_TIMEOUT_S,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return ""
    return (proc.stdout or "") + (proc.stderr or "")


def _version(binary: str) -> str:
    """First line of ``<binary> --version``, or "" when unavailable."""
    try:
        proc = subprocess.run(  # noqa: S603 — argv list, no shell; `binary` is a known name
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=_HELP_TIMEOUT_S,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return ""
    first = (proc.stdout or proc.stderr or "").strip().splitlines()
    return first[0].strip() if first else ""


def discover(br_binary: str = "br", bv_binary: str = "bv") -> dict:
    """Probe the installed binaries for their full surface.

    Walks every top-level ``br`` command and asks each for its own help; a command
    that answers with a ``Commands:`` block is a *group*, and its children are
    recorded as two-word surfaces (``dep add``) because they are separate
    operations the replacement has to reproduce individually.
    """
    br_help = _help(br_binary)
    top = parse_commands(br_help)

    groups: list[str] = []
    commands: list[str] = []
    for name in top:
        commands.append(name)
        children = parse_commands(_help(br_binary, name))
        if children:
            groups.append(name)
            commands.extend(f"{name} {child}" for child in children)

    bv_help = _help(bv_binary)
    return {
        "schema": SCHEMA,
        "provenance": {
            "source": "br --help, br <command> --help, bv --help",
            "note": (
                "Generated from the documented CLI contract only. No beads_rust "
                "source is read - see docs/design/work-tracker.md section 7."
            ),
        },
        "br": {
            "version": _version(br_binary),
            "groups": sorted(groups),
            "commands": sorted(commands),
            "global_flags": parse_flags(br_help),
        },
        "bv": {
            "version": _version(bv_binary),
            # Flag-only by design, not an empty probe result.
            "commands": [],
            "flags": parse_flags(bv_help),
        },
    }


def save(repo_root: Path, inventory: dict) -> Path:
    """Write *inventory* to the committed path; returns the path written."""
    path = Path(repo_root) / INVENTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def load(repo_root: Path) -> dict | None:
    """The committed inventory, or None when absent or unreadable."""
    try:
        raw = (Path(repo_root) / INVENTORY_FILE).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        inventory = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return inventory if isinstance(inventory, dict) else None


def known_surfaces(inventory: dict) -> dict[str, set[str]]:
    """Every surface the inventory knows, keyed by binary.

    ``bv``'s flags are its surfaces: :func:`basicly.tracker_usage.split_invocation`
    records a leading flag as the subcommand precisely because a flag-only binary
    has no other name for what was invoked, so both sides compare like for like.
    """
    result: dict[str, set[str]] = {}
    br = inventory.get("br") or {}
    result["br"] = {str(name) for name in (br.get("commands") or [])}
    bv = inventory.get("bv") or {}
    result["bv"] = {str(name) for name in (bv.get("flags") or [])} | {
        str(name) for name in (bv.get("commands") or [])
    }
    return result


def groups(inventory: dict) -> set[str]:
    """Top-level ``br`` commands that take a second word naming the operation."""
    br = inventory.get("br") or {}
    return {str(name) for name in (br.get("groups") or [])}


def never_used(inventory: dict, measured: set[tuple[str, str]]) -> dict[str, list[str]]:
    """Surfaces the inventory lists that *measured* never contains, per binary.

    *measured* is ``(binary, subcommand)`` pairs, which is what
    :func:`basicly.tracker_usage.summarize` produces. This is the set Phase 6 gets
    to not build, so it is reported explicitly rather than left as a count.
    """
    used_by_binary: dict[str, set[str]] = {}
    for binary, subcommand in measured:
        used_by_binary.setdefault(binary, set()).add(subcommand)
    return {
        binary: sorted(surfaces - used_by_binary.get(binary, set()))
        for binary, surfaces in known_surfaces(inventory).items()
    }


def unknown_used(inventory: dict, measured: set[tuple[str, str]]) -> list[tuple[str, str]]:
    """Measured surfaces the inventory does not list, sorted.

    Never empty-by-construction and never ignored: an entry here means either the
    installed ``br`` drifted from the inventory, or the recorder invented a surface
    that does not exist. Both are defects the freeze must not inherit, so they are
    surfaced rather than filtered away.
    """
    known = known_surfaces(inventory)
    return sorted(
        (binary, subcommand)
        for binary, subcommand in measured
        if subcommand not in known.get(binary, set())
    )
