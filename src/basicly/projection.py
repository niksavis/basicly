"""Shared projection engine: one write-if-changed and one sync-result type.

Skills, hooks, and build all render source content to destination files, skip the
write when nothing changed, and report what was written vs. left unchanged. This
module holds the single implementation each of them routes through, so the
comparison contract lives in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SyncResult:
    """Files written vs. left unchanged by a projection run."""

    written: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)


def write_if_changed(path: Path, content: bytes) -> bool:
    """Write ``content`` to ``path`` only when it differs; return True when written.

    Comparison and write are byte-exact — no newline translation — so a file is
    rewritten only on a real content change. This keeps hook scripts CRLF-safe and
    makes every projected file deterministic across platforms.
    """
    if path.exists() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return True


def sync_file(path: Path, content: bytes, result: SyncResult) -> None:
    """Write ``content`` if changed and record ``path`` under written/unchanged."""
    (result.written if write_if_changed(path, content) else result.unchanged).append(path)
