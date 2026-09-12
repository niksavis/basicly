from __future__ import annotations

import re
from typing import TYPE_CHECKING

from docs_claim_sources import SKILLS_DIR, ClaimError, read_text

from basicly import config, loop

if TYPE_CHECKING:
    from pathlib import Path


def _types_after(text: str, anchor: str) -> tuple[str, ...]:

    at = text.find(anchor)
    if at == -1:
        raise ClaimError(f"anchor {anchor.strip()!r} not found; the prose was reworded past it")
    span = text[at + len(anchor) :]
    if ";" in span:
        span = span[: span.index(";")]
    return tuple(sorted(re.findall(r"`([a-z]+)`", span)))


def skill_work_types(root: Path) -> list[str]:
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
