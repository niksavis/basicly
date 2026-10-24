from __future__ import annotations

from pathlib import Path, PurePath

from . import projection


class OutputRefusedError(OSError):
    pass


def destination_chain(path: PurePath, cwd: PurePath) -> tuple[PurePath, ...]:
    if path.drive and not path.root:
        raise OutputRefusedError(f"Drive-relative output {path} is ambiguous; use an absolute path")
    absolute = path if path.is_absolute() else cwd / path
    return tuple(reversed((absolute, *absolute.parents)))


def write_bytes(path: Path, content: bytes) -> None:
    for component in destination_chain(path, Path.cwd()):
        checked = Path(component)
        if checked.is_symlink() or checked.is_junction():
            raise OutputRefusedError(
                f"Output component {checked} is linked; choose a destination without links"
            )
    projection.atomic_write_bytes(path, content)
