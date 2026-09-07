"""The record bodies a board page fetches beside its snapshot.

Not a snapshot section: 304 bodies is a document nobody can serve, and a page is one record.
Its own module because `board_snapshot` crossed the size cap (basicly-lc2bd3v.2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import tracker

if TYPE_CHECKING:
    from pathlib import Path


def bodies(repo_root: Path) -> dict[str, str]:
    """Each record's description, keyed by id; one fold serves all 307 pages."""
    return {
        ident: text
        for record in tracker.all_records(repo_root)
        if (ident := str(record.get("id") or "")) and (text := str(record.get("description") or ""))
    }
