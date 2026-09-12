from __future__ import annotations

from typing import TYPE_CHECKING

from . import tracker

if TYPE_CHECKING:
    from pathlib import Path


def bodies(repo_root: Path) -> dict[str, str]:
    return {
        ident: text
        for record in tracker.all_records(repo_root)
        if (ident := str(record.get("id") or "")) and (text := str(record.get("description") or ""))
    }
