from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

DIRNAME = "icons"

VIEW = 16.0

VERSION = "1.13.1"

ICONS: Mapping[str, str] = {
    "calm": "check-circle-fill",
    "waiting": "hourglass-split",
    "stuck": "exclamation-triangle-fill",
    "withheld": "slash-circle-fill",
    "absent": "question-circle-fill",
    "checkpoint": "person-fill",
    "chain": "arrow-right-short",
}

DIGESTS: Mapping[str, str] = {
    "arrow-right-short": "2a621f2c9ed464adf3a8019738a8767767aa0ee488adb715a568f94101e7a4f0",
    "check-circle-fill": "c08a880c387a5ee82e81abd5038d1c13fe75c23ad146efef159d2e2687701cec",
    "exclamation-triangle-fill": "a777ce69e8a80a35a8a245f2add4e99685a92fb48dfa93c83d79ca6e4457a65a",
    "hourglass-split": "3cb733ec9d24d880c1cdee24fb5c6fc3d7a99f291ff5556278227ee108579e73",
    "person-fill": "f85064e7e508eec65e9bdd7689b6fd83207398281c9cf84907c7d72bb5b7866b",
    "question-circle-fill": "efd690d4889dca9d8aefe428617a95f271765ffb7d03301a0281abdf46c53e82",
    "slash-circle-fill": "5a6ca275d8a51f4dc66d8867d96da704aff5db60b2d5e77d0e025c3421c8da40",
}

_PATH = re.compile(r"<path\b([^>]*?)\bd=\"([^\"]+)\"")
_VIEWBOX = re.compile(r"viewBox=\"0 0 (\d+) (\d+)\"")


class IconError(Exception):
    pass


@dataclass(frozen=True)
class Mark:
    path: str
    even_odd: bool


def digest(mark: Mark) -> str:
    return hashlib.sha256(f"{mark.path}|{mark.even_odd}".encode()).hexdigest()


def read_mark(icons_dir: Path, mark: str) -> Mark:

    icon = ICONS[mark]
    path = icons_dir / f"{icon}.svg"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise IconError(f"cannot read the vendored icon {path}: {err}") from err
    box = _VIEWBOX.search(text)
    if box is None or (float(box.group(1)), float(box.group(2))) != (VIEW, VIEW):
        raise IconError(f"{path} is not drawn on a {VIEW:g}x{VIEW:g} grid")
    found = _PATH.findall(text)
    if len(found) != 1:
        raise IconError(f"{path} holds {len(found)} paths; a mark is one path")
    attrs, data = found[0]
    read = Mark(path=data, even_odd="evenodd" in attrs)
    if (got := digest(read)) != DIGESTS[icon]:
        raise IconError(
            f"{path} no longer draws what was reviewed: {got} against the recorded "
            f"{DIGESTS[icon]}. Refresh it from the pinned release, or update DIGESTS in "
            f"{__name__} in the same change."
        )
    return read


def marks(templates_dir: Path) -> Mapping[str, Mark]:

    icons_dir = templates_dir / DIRNAME
    return {mark: read_mark(icons_dir, mark) for mark in ICONS}
