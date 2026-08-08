"""What surface a ``br``/``bv`` invocation names.

One responsibility, and it is the naming: an argv goes in and the surface it
exercised comes out, plus the two predicates that say whether a token can be a
surface name at all. Nothing here observes anything — no clock, no spool, no
ledger — which is what makes every rule below a pure function of the argv.

The rules are not obvious and each one was paid for. A positional that survived
shell tokenisation (``2>&1``, ``$g``) reached the committed ledger as a surface;
a leading long flag is the only name a flag-only binary's invocation has; and a
two-word group (``dep add``) is a different operation from its sibling, so
collapsing it to ``dep`` understates exactly the count a surface freeze reads.

Split out of ``tracker_usage`` when the module-size ratchet caught that module
growing. The boundary is *naming* against *observation*: this module decides
what an invocation is called, :mod:`basicly.tracker_usage` decides what to do
with the name — record it, promote it, count it — and that is why nothing here
imports back into it. Its near neighbour on the other side is
:mod:`basicly.tracker_surface`, which answers which surfaces *exist* rather than
which one was just invoked; the two agree on the shape of a name and are held to
it by ``tests/test_tracker_surface.py``.
"""

from __future__ import annotations

import re

# A positional that could name a br subcommand. See :func:`is_surface_word` for
# why a stricter filter than "does not start with -" is required.
_SURFACE_WORD = re.compile(r"^[a-z][a-z0-9-]*$")

# A leading long flag standing in for a subcommand: `bv` has none at all, so a
# flag is the only name its invocation has.
_LONG_FLAG_SURFACE = re.compile(r"^--[a-z][a-z0-9-]*[a-z0-9]$")

# Top-level br commands that take a second word naming a distinct operation, so
# the pair is one surface. Generated from `br --help` and mirrored in
# `.basicly/ledger/tracker-surface.json`; `test_tracker_surface` asserts the two
# agree, which is what turns "keep this in step" into a gate.
#
# The set previously held five entries and was wrong in both directions: it missed
# `audit`, `doctor`, `epic`, `history`, `label` and `query` — so `br label add` and
# `br label list` collapsed to one surface named `label`, understating exactly the
# count a freeze reads — and it listed `catalog`, which is a *basicly* command and
# has never existed in br (basicly-vkh0.2).
GROUP_SUBCOMMANDS = frozenset({
    "audit",
    "comments",
    "config",
    "coordination",
    "dep",
    "doctor",
    "epic",
    "gate",
    "history",
    "label",
    "query",
    "robot-docs",
})


def is_surface_word(token: str) -> bool:
    """True when *token* could name a ``br`` subcommand.

    Every ``br`` command name is lowercase letters, digits and hyphens. Anything
    else in a positional slot is shell text that survived tokenisation, not a
    surface — and it reached the committed ledger: ``br --version 2>&1`` recorded
    the surface ``2>&1``, and ``br $g --help`` inside a shell loop recorded ``$g``
    (six such rows across four fake surfaces, basicly-vkh0.2). A freeze list is
    exactly the artifact that must not contain them, since its whole purpose is to
    say which surfaces exist.

    Rejecting the token rather than the whole invocation is deliberate: for
    ``br --version 2>&1`` the real surface is the leading flag, and dropping only
    the junk word still records it.
    """
    return bool(_SURFACE_WORD.match(token))


def is_valid_surface(subcommand: str) -> bool:
    """True when *subcommand* is a shape :func:`split_invocation` can legitimately emit.

    Two shapes are legitimate: one or two command words (``show``, ``dep add``), or
    a single long flag for a binary that has no subcommands (``bv --robot-next``,
    ``br --version``). Everything else is recorder junk. Used at the promote
    boundary so the committed ledger cannot inherit it.
    """
    if not subcommand:
        return False
    if subcommand.startswith("-"):
        return bool(_LONG_FLAG_SURFACE.match(subcommand))
    words = subcommand.split()
    return len(words) <= 2 and all(is_surface_word(word) for word in words)


def split_invocation(args: list[str] | tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """The subcommand and the flag *names* in *args*.

    A leading flag (``br --version``) is a surface in its own right and becomes
    the subcommand, because there is no other name for it. Two-word subcommands
    (``dep add``, ``comments list``, ``gate report``) are joined so the pair is
    one surface — they are separate operations and freezing them as bare ``dep``
    would lose the distinction the replacement has to reproduce.

    ``--flag=value`` is truncated at the ``=``; a value in the next position is
    simply not collected, since only names are recorded.
    """
    words = [arg for arg in args if not arg.startswith("-") and is_surface_word(arg)]
    flags = sorted({arg.split("=", 1)[0] for arg in args if arg.startswith("-")})

    if not words:
        # No positional at all: the leading flag *is* the surface (`br --version`).
        return (flags[0] if flags else "", tuple(flags))

    subcommand = words[0]
    if len(words) > 1 and subcommand in GROUP_SUBCOMMANDS:
        subcommand = f"{subcommand} {words[1]}"
    return subcommand, tuple(flags)
