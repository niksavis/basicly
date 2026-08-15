"""The consumer surfaces, checked for commands the CLI does not ship (basicly-a4q3.4).

Split out of ``docs_claims`` for the reason its sibling ``docs_claim_sources`` was:
the module-size ratchet caught that script growing, and a module already over the
cap may only shrink. The boundary here is one whole claim rather than one layer —
this owns the evidence *and* the judgement for consumer-surface command claims, so
``docs_claims`` keeps only the registration.

The claim itself: ``curator`` binds a claim to its evidence at SHIP, and the
architecture document is checked in the other direction, that every shipped command
is documented. Neither catches a README advertising a command that does not exist.
A false claim in code is caught by a gate; on a README it is caught by a consumer.

``.scripts`` is deliberately not a package, so importing this requires the caller's
own path set-up, exactly as ``docs_claim_sources`` does.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from docs_claim_sources import ClaimError, read_text, subparsers

from basicly import cli

if TYPE_CHECKING:
    from pathlib import Path

# The surfaces a consumer meets before they run anything, and so the ones where a
# command that does not exist is read as a promise rather than caught by a gate.
CONSUMER_SURFACES = ("README.md", "site/index.html")

_MD_CODE = re.compile(r"```.*?```|`[^`]+`", re.DOTALL)
_HTML_CODE = re.compile(r"<code[^>]*>.*?</code>|<pre[^>]*>.*?</pre>", re.DOTALL)
_INVOCATION = re.compile(r"\bbasicly ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?")


def code_spans(text: str, path: str) -> list[str]:
    """The code-formatted regions of *path*, which are the only ones that claim.

    Code formatting is the discriminator. These same surfaces say "basicly requires
    python 3.14" and "basicly also owns" in prose and carry an `alt="basicly logo"`
    attribute; read as interface claims those report three commands nobody
    advertised, and a gate that cries wolf on authored prose gets its surface
    excluded rather than its claim fixed.
    """
    pattern = _HTML_CODE if path.endswith(".html") else _MD_CODE
    return pattern.findall(text)


def cli_command_paths() -> set[tuple[str, ...]]:
    """Every command and command-subcommand pair the CLI actually ships."""
    top = subparsers(cli._build_parser())
    if top is None:  # pragma: no cover - the CLI is a subcommand parser by construction
        raise ClaimError("the CLI parser declares no subcommands")

    paths: set[tuple[str, ...]] = set()
    for name, parser in top.choices.items():
        paths.add((name,))
        group = subparsers(parser)
        if group is not None:
            paths.update((name, sub) for sub in group.choices)
    return paths


def consumer_commands_exist(root: Path, surface: str) -> list[str]:
    """A `basicly <command>` shown in code on *surface* must be one the CLI ships.

    Only the first token after the name is resolved, plus a second when the first
    names a command group. A third would be an argument, not a claim.
    """
    shipped = cli_command_paths()
    groups = {path[0] for path in shipped if len(path) > 1}
    unknown: set[str] = set()
    for span in code_spans(read_text(root / surface), surface):
        for command, sub in _INVOCATION.findall(span):
            if (command,) not in shipped:
                unknown.add(f"basicly {command}")
            elif sub and command in groups and (command, sub) not in shipped:
                unknown.add(f"basicly {command} {sub}")
    if unknown:
        return [f"names commands the CLI does not ship: {', '.join(sorted(unknown))}"]
    return []
