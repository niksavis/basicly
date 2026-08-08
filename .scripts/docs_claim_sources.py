"""The tree, read as evidence for a documentation claim.

One responsibility, and it is reaching the file. Every claim in ``docs_claims.py`` —
generated block and assertion alike — starts by reading something the repo already
answers: a target's YAML, ``basicly.toml``, a skill source, a hook script. This module
is where that read happens and where its failure gets a name.

:class:`ClaimError` is the whole point of it being one place. A claim that *drifted* is
repairable by ``--fix``; a claim that could not be *evaluated* is not, and the two must
not report the same way — so a missing file or unparseable source raises rather than
returning a default that would make an unevaluable claim look current.

Split out of ``docs_claims`` when the module-size ratchet caught that script growing.
The boundary is *evidence* against *judgement*: nothing here knows what any claim
asserts, and nothing here writes — the ``--fix`` repair stays beside the block registry
it repairs — so this module needs no import back into the script that reads through it.

``.scripts`` is deliberately not a package, so importing this requires the caller's own
directory on ``sys.path``; ``docs_claims.py`` puts it there and says why.
"""

from __future__ import annotations

import tomllib
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


def load_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file, failing loudly on anything but a mapping."""
    try:
        return tomllib.loads(read_text(path))
    except tomllib.TOMLDecodeError as exc:
        raise ClaimError(f"{path}: {exc}") from exc


def load_yaml(path: Path) -> dict[str, Any]:
    """Parse a YAML mapping source, failing loudly on anything else."""
    data = yaml.safe_load(read_text(path))
    if not isinstance(data, dict):
        raise ClaimError(f"{path}: expected a YAML mapping")
    return data
