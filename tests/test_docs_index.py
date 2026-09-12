from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONVENTIONS = REPO / "docs" / "architecture" / "conventions.md"


def test_every_document_under_docs_is_named_by_the_documentation_set() -> None:
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
