"""Structured needs-input / I-don't-know as a first-class loop outcome (basicly-o774).

A dispatched headless agent has no reliable way to say "I spent my iteration
budget and cannot resolve a required fact" — models default to answering, and the
stop-instead-of-guess policy is soft prose in the ``decision-protocol`` /
``knowledge-priming`` fragments (spike basicly-zv48, dimension 6). This module is
the enforced seam: the agent writes a small sentinel file into its worktree, and
the loop reads it after a clean dispatch, blocks on the missing fact, and consumes
the sentinel so a re-dispatch starts clean.

The sentinel lives under the already self-ignored ``.basicly/usage/`` directory
(the same convention as run-records, tool-usage, and checkpoint-confirms), so it
never enters a commit. A file — not a stdout marker — carries the signal: it
survives output redaction and truncation, and every agent CLI can write one
without a cross-agent output convention.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path

# Sentinel the dispatched agent writes, relative to its worktree root. Lives in
# the self-ignored usage dir so it never enters a commit.
SENTINEL_FILE = Path(".basicly/usage/needs-input.json")


@dataclass(frozen=True)
class NeedsInput:
    """An agent's structured "I cannot proceed without this fact" signal."""

    fact: str
    detail: str = ""


def take(cwd: Path) -> NeedsInput | None:
    """Read and consume the needs-input sentinel from *cwd*, if the agent wrote one.

    Returns the parsed :class:`NeedsInput` when a well-formed sentinel with a
    non-empty ``fact`` is present, else ``None`` (no file, or malformed/empty —
    tolerated, never raised). The sentinel is always removed on presence, valid
    or not, so a stale or garbled file never re-triggers on the next dispatch.
    """
    path = cwd / SENTINEL_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    # Consume unconditionally: a malformed sentinel must not linger and re-fire.
    with contextlib.suppress(OSError):
        path.unlink()
    try:
        data = json.loads(raw)
    except ValueError, TypeError:
        return None
    if not isinstance(data, dict):
        return None
    fact = data.get("fact")
    if not isinstance(fact, str) or not fact.strip():
        return None
    detail = data.get("detail")
    return NeedsInput(fact.strip(), detail.strip() if isinstance(detail, str) else "")
