from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
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


def write_beside(templates_root: Path, out_dir: Path) -> list[Path]:
    target = out_dir / DIRNAME
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name in ASSETS:
        landing = target / name
        shutil.copyfile(source(templates_root, name), landing)
        written.append(landing)
    return written
