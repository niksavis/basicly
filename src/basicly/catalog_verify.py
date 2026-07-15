"""Catalog content verification — the deterministic gate over resolved fragments.

Where ``catalog_lint`` guards the raw YAML *source contract* (schema, extensions,
no discoverable-name sources), this module runs the deterministic *content* checks
of the verification pipeline (architecture §6) against the merged, override-resolved
fragment set that ``loader.load_fragments`` produces:

1. Duplicate / near-duplicate bodies — two fragments saying the same thing.
2. Contradiction (static dictionary) — opposing preferences across fragments.
3. Ambiguity (vague-phrase deny-list) — filler a linter can flag but a model can't act on.
4. Scope overlap — two scoped fragments that both apply to the same files.

These are conservative, high-precision heuristics: each is tuned to stay silent on a
clean catalog and only fire on a genuine problem. Semantic review that needs a capable
reader (§6, advisory) is out of scope here — that is ``basicly review``.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .schema import Fragment

# Two normalized bodies at or above this similarity ratio are near-duplicates.
NEAR_DUPLICATE_RATIO = 0.95

# Curated pairs of mutually-exclusive terms. A contradiction is reported only when
# one fragment asserts one side and a *different* fragment asserts the other, and
# neither mentions both (a fragment naming both sides is stating a resolved
# preference like "prefer pathlib over os.path", not a contradiction).
CONTRADICTION_PAIRS: tuple[tuple[str, str], ...] = (
    ("tabs", "spaces"),
    ("os.path", "pathlib"),
)

# Vague filler phrases: they parse fine but give a model nothing actionable.
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
    """Collapse whitespace and lowercase a body for similarity comparison."""
    return " ".join(body.split()).lower()


def _duplicate_bodies(fragments: list[Fragment]) -> list[str]:
    """Report fragment pairs with identical or near-identical bodies."""
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
    """Return True if the body mentions the term as a whole token."""
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", body, re.IGNORECASE) is not None


def _contradictions(fragments: list[Fragment]) -> list[str]:
    """Report opposing preferences from the static contradiction dictionary."""
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
    """Report bodies containing a vague phrase from the deny-list."""
    violations: list[str] = []
    for fragment in fragments:
        body = fragment.body.lower()
        for phrase in AMBIGUOUS_PHRASES:
            if phrase in body:
                violations.append(f"fragment '{fragment.id}' contains vague phrase '{phrase}'")
    return sorted(violations)


def _scope_overlaps(fragments: list[Fragment]) -> list[str]:
    """Report scoped fragment pairs that apply to the same targets and paths."""
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
    """Return content-check violations for the resolved fragment set (empty when clean)."""
    return (
        _duplicate_bodies(fragments)
        + _contradictions(fragments)
        + _ambiguous_phrases(fragments)
        + _scope_overlaps(fragments)
    )
