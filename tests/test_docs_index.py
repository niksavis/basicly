"""Every document under `docs/` is named by the documentation set in `conventions.md` §8.

Owner rule 2026-08-02: a document not indexed should not exist. The index was the
implementation plan until basicly-jebd22 deleted it; the documentation-set table in
`docs/architecture/conventions.md` is the index now, by directory for the tutorial and
how-to layers and by file for the companions beside the architecture document. An unlisted
document is either an index defect or a deletion nobody performed, and both need a human -
hence a test rather than prose.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONVENTIONS = REPO / "docs" / "architecture" / "conventions.md"


def test_every_document_under_docs_is_named_by_the_documentation_set() -> None:
    """By directory for the two consumer layers, by file for the companions."""
    index = CONVENTIONS.read_text(encoding="utf-8")
    unlisted = sorted(
        path.relative_to(REPO / "docs").as_posix()
        for path in (REPO / "docs").rglob("*.md")
        if path != CONVENTIONS
        and path.name not in index
        and f"docs/{path.relative_to(REPO / 'docs').parts[0]}/" not in index
    )

    assert unlisted == [], (
        f"documents conventions.md §8 does not name - index or delete them: {unlisted}"
    )
