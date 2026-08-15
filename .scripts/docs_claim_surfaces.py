"""The consumer surfaces, checked for commands the CLI does not ship (basicly-a4q3.4).

Split out of ``docs_claims`` for the reason its sibling ``docs_claim_sources`` was:
the module-size ratchet caught that script growing, and a module already over the
cap may only shrink. The boundary here is one whole claim rather than one layer —
this owns the evidence *and* the judgement for consumer-surface command claims, so
``docs_claims`` keeps only the registration.

Nothing else checks this direction: the architecture document is gated on every
shipped command being documented, not on every documented command shipping. A false
claim in code is caught by a gate; on a README it is caught by a consumer.
"""

from __future__ import annotations

import argparse
import re
from typing import TYPE_CHECKING

from docs_claim_sources import ClaimError, read_text, subparsers

from basicly import cli

if TYPE_CHECKING:
    from pathlib import Path

# The surfaces a consumer meets before they run anything, and so the ones where a
# command that does not exist is read as a promise rather than caught by a gate.
CONSUMER_SURFACES = ("README.md", "site/index.html")

# Bare fences count: every one on the current surfaces is shell, and excluding them
# would lose real claims to save a hypothetical one.
_SHELL_INFO = frozenset({"", "sh", "bash", "shell", "zsh", "console", "powershell", "ps1"})
_FENCE = re.compile(
    r"^(?P<fence>```|~~~)(?P<info>[^\n`~]*)\n(?P<body>.*?)^(?P=fence)", re.MULTILINE | re.DOTALL
)
_INLINE = re.compile(r"`[^`\n]+`")
_HTML_CODE = re.compile(r"<code[^>]*>.*?</code>|<pre[^>]*>.*?</pre>", re.DOTALL)
_INVOCATION = re.compile(r"\bbasicly[ \t]+([a-z][a-z-]*)(?:[ \t]+([a-z][a-z-]*))?")


def code_spans(text: str, path: str) -> list[str]:
    """The code-formatted regions of *path*, which are the only ones that claim.

    These surfaces say "basicly requires python 3.14" in prose and carry an
    `alt="basicly logo"` attribute, so an unfiltered scan reports three commands
    nobody advertised — and a gate that cries wolf gets its surface excluded rather
    than its claim fixed. Formatting alone is not enough either: a fenced ``text``
    block is code-formatted and still a sentence, so a fence must also be shell.
    """
    if path.endswith(".html"):
        return _HTML_CODE.findall(text)
    spans = [
        match["body"]
        for match in _FENCE.finditer(text)
        if match["info"].strip().split(" ")[0].lower() in _SHELL_INFO
    ]
    spans.extend(_INLINE.findall(text))
    return spans


def cli_command_paths() -> tuple[set[tuple[str, ...]], frozenset[str]]:
    """What the CLI ships: every command path, and the leaves taking no argument.

    The second half is what lets a trailing word be judged. ``basicly brief <id>``
    and ``basicly install teleport`` look identical to a scanner until you know
    that ``brief`` declares a positional and ``install`` declares none.
    """
    top = subparsers(cli._build_parser())
    if top is None:  # pragma: no cover - the CLI is a subcommand parser by construction
        raise ClaimError("the CLI parser declares no subcommands")

    paths: set[tuple[str, ...]] = set()
    bare: set[str] = set()
    for name, parser in top.choices.items():
        paths.add((name,))
        group = subparsers(parser)
        if group is not None:
            paths.update((name, sub) for sub in group.choices)
            continue
        if not any(
            not action.option_strings and not isinstance(action, argparse._SubParsersAction)
            for action in parser._actions
        ):
            bare.add(name)
    return paths, frozenset(bare)


def consumer_commands_exist(root: Path, surface: str) -> list[str]:
    """A `basicly <command>` shown in code on *surface* must be one the CLI ships.

    A trailing word is judged three ways: against a group's subcommands, against a
    leaf that takes no argument at all, and — for a leaf that declares a positional —
    not at all, because there it is the argument. A fourth token is never read; by
    then it is an argument under any reading.
    """
    shipped, bare = cli_command_paths()
    groups = {path[0] for path in shipped if len(path) > 1}
    unknown: set[str] = set()
    for span in code_spans(read_text(root / surface), surface):
        for command, sub in _INVOCATION.findall(span):
            if (command,) not in shipped:
                unknown.add(f"basicly {command}")
            elif sub and (command in bare or (command in groups and (command, sub) not in shipped)):
                unknown.add(f"basicly {command} {sub}")
    if unknown:
        return [f"names commands the CLI does not ship: {', '.join(sorted(unknown))}"]
    return []
