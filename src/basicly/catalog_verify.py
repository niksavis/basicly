from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schema import Fragment

NEAR_DUPLICATE_RATIO = 0.95

CONTRADICTION_PAIRS: tuple[tuple[str, str], ...] = (
    ("tabs", "spaces"),
    ("os.path", "pathlib"),
)

AMBIGUOUS_PHRASES: tuple[str, ...] = (
    "as appropriate",
    "as needed",
    "as necessary",
    "where possible",
    "where appropriate",
    "and so on",
    "if applicable",
)


def _normalize(body: str) -> str:
    return " ".join(body.split()).lower()


def _duplicate_bodies(fragments: list[Fragment]) -> list[str]:
    violations: list[str] = []
    items = [(f, _normalize(f.body)) for f in fragments if f.body.strip()]
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (frag_a, norm_a), (frag_b, norm_b) = items[i], items[j]
            first, second = sorted((frag_a.id, frag_b.id))
            if norm_a == norm_b:
                violations.append(f"fragments '{first}' and '{second}' have identical bodies")
                continue
            ratio = SequenceMatcher(None, norm_a, norm_b).ratio()
            if ratio >= NEAR_DUPLICATE_RATIO:
                violations.append(
                    f"fragments '{first}' and '{second}' have near-duplicate bodies "
                    f"({ratio:.0%} similar)"
                )
    return sorted(violations)


def _mentions(term: str, body: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", body, re.IGNORECASE) is not None


def _contradictions(fragments: list[Fragment]) -> list[str]:
    violations: list[str] = []
    for term_a, term_b in CONTRADICTION_PAIRS:
        a_only = sorted(
            f.id for f in fragments if _mentions(term_a, f.body) and not _mentions(term_b, f.body)
        )
        b_only = sorted(
            f.id for f in fragments if _mentions(term_b, f.body) and not _mentions(term_a, f.body)
        )
        if a_only and b_only:
            violations.append(
                f"possible contradiction: '{term_a}' (in {', '.join(a_only)}) "
                f"vs '{term_b}' (in {', '.join(b_only)})"
            )
    return violations


def _ambiguous_phrases(fragments: list[Fragment]) -> list[str]:
    violations: list[str] = []
    for fragment in fragments:
        body = fragment.body.lower()
        violations.extend(
            f"fragment '{fragment.id}' contains vague phrase '{phrase}'"
            for phrase in AMBIGUOUS_PHRASES
            if phrase in body
        )
    return sorted(violations)


def _scope_overlaps(fragments: list[Fragment]) -> list[str]:
    violations: list[str] = []
    scoped = [f for f in fragments if f.is_scoped]
    for i in range(len(scoped)):
        for j in range(i + 1, len(scoped)):
            frag_a, frag_b = scoped[i], scoped[j]
            same_targets = frozenset(frag_a.applies_to) == frozenset(frag_b.applies_to)
            same_paths = frozenset(frag_a.scope_paths) == frozenset(frag_b.scope_paths)
            if same_targets and same_paths:
                first, second = sorted((frag_a.id, frag_b.id))
                violations.append(
                    f"fragments '{first}' and '{second}' share the same scope "
                    f"({frag_a.scope_summary}) and targets"
                )
    return sorted(violations)


def verify_catalog(fragments: list[Fragment]) -> list[str]:
    return (
        _duplicate_bodies(fragments)
        + _contradictions(fragments)
        + _ambiguous_phrases(fragments)
        + _scope_overlaps(fragments)
    )
