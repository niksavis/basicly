"""The tree, read as evidence for a documentation claim.

:class:`ClaimError` is why the reads live in one place. A claim that *drifted* is
repairable by ``--fix``; a claim that could not be *evaluated* is not, and the two must
not report the same way — so a missing file or unparseable source raises rather than
returning a default that would make an unevaluable claim look current.

Split out of ``docs_claims`` when the module-size ratchet caught that script growing.
The boundary is *evidence* against *judgement*: nothing here knows what any claim
asserts, and nothing here writes, so this module needs no import back.

``.scripts`` is deliberately not a package, so importing this requires the caller's own
directory on ``sys.path``; ``docs_claims.py`` puts it there and says why.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


class ClaimError(Exception):
    """A claim cannot be evaluated at all — a missing marker, file, or source field.

    Distinct from drift: drift is repairable by ``--fix``, this is not, so both
    modes report it and exit non-zero rather than writing a placeholder.
    """


def read_text(path: Path) -> str:
    """Text of *path*, with newlines normalized by universal-newline decoding."""
    if not path.exists():
        raise ClaimError(f"{path} does not exist")
    return path.read_text(encoding="utf-8")


def load_yaml(path: Path) -> dict[str, Any]:
    """Parse a YAML mapping source, failing loudly on anything else."""
    data = yaml.safe_load(read_text(path))
    if not isinstance(data, dict):
        raise ClaimError(f"{path}: expected a YAML mapping")
    return data


def subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    """The parser's subcommand action, or ``None`` for a leaf command.

    The parser is evidence like any file here: it is what the CLI actually ships,
    read rather than restated. Shared because two claim modules now walk it.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None
