from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

CATALOG_DIRNAME = "catalog"


def bundled_catalog_root() -> Path:

    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "src" / "basicly" / "catalog.py").is_file():
            source = parent / ".basicly" / "core"
            if source.is_dir():
                return source

    packaged = here.parent / CATALOG_DIRNAME
    if packaged.is_dir():
        return packaged

    raise FileNotFoundError(
        f"bundled catalog not found: no basicly source checkout above {here} "
        f"and no packaged copy at '{packaged}'"
    )


def iter_catalog_files(src: Path) -> Iterator[Path]:

    for path in sorted(src.rglob("*")):
        if path.is_dir():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        yield path
