from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SyncResult:
    written: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)


def atomic_write_bytes(path: Path, content: bytes) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".basicly-tmp")
    tmp.write_bytes(content)
    tmp.replace(path)


def atomic_write_text(path: Path, content: str) -> None:
    atomic_write_bytes(path, content.encode("utf-8"))


def write_if_changed(path: Path, content: bytes) -> bool:

    if path.exists() and path.read_bytes() == content:
        return False
    atomic_write_bytes(path, content)
    return True


def sync_file(path: Path, content: bytes, result: SyncResult) -> None:
    (result.written if write_if_changed(path, content) else result.unchanged).append(path)


BACKUP_SUFFIX = ".basicly-bak"


def back_up_unrecognised(path: Path, content: bytes, *, tracked: bool) -> Path | None:

    if tracked or not path.exists():
        return None
    previous = path.read_bytes()
    if previous == content:
        return None
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    backup.write_bytes(previous)
    return backup
