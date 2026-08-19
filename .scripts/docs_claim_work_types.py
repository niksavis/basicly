"""The work-type lists a skill states, held against the engine that rejects them.

Split out of ``docs_claims`` on the pattern its siblings set: one whole claim, evidence
and judgement both, so ``docs_claims`` keeps only the registration.

The skill advertised ``docs`` and ``question`` as valid types for months
(``basicly-tcmy.9``). Both are rejected by :func:`basicly.classify`, so filing a docs bead
produced one the loop could never advance — and the tracker validates nothing, storing
whatever ``--type`` it is handed, so no tool caught it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from docs_claim_sources import SKILLS_DIR, ClaimError, read_text

# `loop._LEAF_TYPES` is private and has no public alias; this script is repo-local
# tooling reading its own tree, and the whole point is to bind to the definition the
# engine actually uses rather than to a second copy of it.
from basicly import config, loop

if TYPE_CHECKING:
    from pathlib import Path


def _types_after(text: str, anchor: str) -> tuple[str, ...]:
    """Backticked names between *anchor* and the end of its sentence.

    A missing anchor is a :class:`ClaimError`, not an empty list: prose reworded past
    the anchor must fail loudly rather than silently assert nothing.
    """
    at = text.find(anchor)
    if at == -1:
        raise ClaimError(f"anchor {anchor.strip()!r} not found; the prose was reworded past it")
    span = text[at + len(anchor) :]
    if ";" in span:
        span = span[: span.index(";")]
    return tuple(sorted(re.findall(r"`([a-z]+)`", span)))


def skill_work_types(root: Path) -> list[str]:
    """The work-type lists a skill states must be the engine's, not a copy."""
    # Found by the claim, never by a path: the stating source moved once (`tool-br` to
    # `work-tracker`) and the literal path made that rename fail this gate (vkh0.42.9).
    # A block scalar wraps a sentence, so the catalog is read as one flattened line.
    paths = sorted((root / SKILLS_DIR).glob("*/skill.yaml"))
    catalog = " ".join(" ".join(read_text(path) for path in paths).split())

    problems: list[str] = []
    for anchor, expected, origin in (
        ("harness work types are ", config.WORK_TYPES, "config.WORK_TYPES"),
        ("leaf types ", loop._LEAF_TYPES, "loop._LEAF_TYPES"),
    ):
        stated = _types_after(catalog, anchor)
        if stated != tuple(sorted(expected)):
            problems.append(
                f"after {anchor.strip()!r} the skill states {list(stated)}; "
                f"{origin} is {sorted(expected)}"
            )
    return problems
