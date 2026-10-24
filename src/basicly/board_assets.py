from __future__ import annotations

from typing import TYPE_CHECKING

from . import board_output

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

DIRNAME = "vendor"
STYLESHEET = "bootstrap.min.css"
BOOTSTRAP_VERSION = "5.3.8"

ASSETS: Mapping[str, str] = {STYLESHEET: "text/css; charset=utf-8"}

ROUTE = f"/{DIRNAME}/"


def href(name: str, *, nested: bool = False) -> str:
    return f"../{DIRNAME}/{name}" if nested else f"{DIRNAME}/{name}"


def source(templates_root: Path, name: str) -> Path:
    return templates_root / DIRNAME / name


def read(templates_root: Path, name: str) -> tuple[bytes, str] | None:
    content_type = ASSETS.get(name)
    if content_type is None:
        return None
    return source(templates_root, name).read_bytes(), content_type


def write_beside(
    templates_root: Path,
    out_dir: Path,
    *,
    on_refusal: Callable[[Path, OSError], None] | None = None,
) -> list[Path]:
    target = out_dir / DIRNAME
    written = []
    for name in ASSETS:
        landing = target / name
        try:
            board_output.write_bytes(landing, source(templates_root, name).read_bytes())
        except OSError as error:
            if on_refusal is None:
                raise
            on_refusal(landing, error)
            continue
        written.append(landing)
    return written
