from __future__ import annotations

from pathlib import Path, PurePath
from typing import TYPE_CHECKING

from . import board_assets, projection

if TYPE_CHECKING:
    from collections.abc import Callable


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


def write_assets_beside(
    templates_root: Path,
    out_dir: Path,
    *,
    on_refusal: Callable[[Path, OSError], None] | None = None,
) -> list[Path]:
    target = out_dir / board_assets.DIRNAME
    written = []
    for name in board_assets.ASSETS:
        landing = target / name
        try:
            write_bytes(landing, board_assets.source(templates_root, name).read_bytes())
        except OSError as error:
            if on_refusal is None:
                raise
            on_refusal(landing, error)
            continue
        written.append(landing)
    return written
