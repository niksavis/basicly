"""The files a board page references beside itself, vendored and served from the same root.

A `--out` run writes 310 record pages (measured 2026-09-08), so bootstrap's 232 KB is one
file under :data:`DIRNAME` rather than inlined 310 times, named by every page through the
relative path :func:`href` spells (basicly-lywzp71). ``vendor/PROVENANCE.txt`` says how to
refresh it.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

DIRNAME = "vendor"
STYLESHEET = "bootstrap.min.css"
# The exact dev dependency in `package.json`; `tests/test_board_assets.py` holds them together.
BOOTSTRAP_VERSION = "5.3.8"

# Every file a page may reference, with its content type; the licence is not served.
ASSETS: Mapping[str, str] = {STYLESHEET: "text/css; charset=utf-8"}

ROUTE = f"/{DIRNAME}/"


def href(name: str, *, nested: bool = False) -> str:
    """The relative path a page writes for asset *name*; *nested* is a record page."""
    return f"../{DIRNAME}/{name}" if nested else f"{DIRNAME}/{name}"


def source(templates_root: Path, name: str) -> Path:
    """Where asset *name* lives in the bundled catalog."""
    return templates_root / DIRNAME / name


def read(templates_root: Path, name: str) -> tuple[bytes, str] | None:
    """The bytes and content type of asset *name*, or ``None`` for a name the table lacks."""
    content_type = ASSETS.get(name)
    if content_type is None:
        return None
    return source(templates_root, name).read_bytes(), content_type


def write_beside(templates_root: Path, out_dir: Path) -> list[Path]:
    """Copy every asset under *out_dir*, where the pages a `--out` run wrote will find them."""
    target = out_dir / DIRNAME
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name in ASSETS:
        landing = target / name
        shutil.copyfile(source(templates_root, name), landing)
        written.append(landing)
    return written
