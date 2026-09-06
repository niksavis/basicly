"""The marks the board draws, read out of the vendored bootstrap-icons release as SVG paths.

The boundary is a mark's *shape* against :mod:`basicly.board_wall`, which holds the states
and their glyph, border and colour channels.

A path and never a codepoint, because a font that lacks the character draws tofu. Inline and
never a reference: ``tests/test_board_render.py`` refuses ``<script``, ``<link`` and ``src=``,
so an icon font, a stylesheet and a sprite URL are all out.

``icons/PROVENANCE.txt`` states the version, the licence and how to refresh the files.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

# Beside the templates, so the wheel's `force-include` carries them with the page.
DIRNAME = "icons"

# bootstrap-icons draws every icon on this grid. Asserted against each file, never trusted.
VIEW = 16.0

# The pinned release, matching the dev dependency in `package.json`.
VERSION = "1.13.1"

# The first five keys are the five states `board_regions.band` can hold, so the watch band
# never asks for a key this roster lacks.
ICONS: Mapping[str, str] = {
    "calm": "check-circle-fill",
    "waiting": "hourglass-split",
    # The same wait past `board_regions.BAND_ALARM_AFTER_S`.
    "stuck": "exclamation-triangle-fill",
    # The producer holds this section and refused it, which is not the same fact as never
    # having measured it - hence two icons rather than one "no value" mark.
    "withheld": "slash-circle-fill",
    "absent": "question-circle-fill",
    # Replaces a diamond, which named no actor: the reader's question at a checkpoint is
    # *who* must act, and a rotated square cannot answer it.
    "checkpoint": "person-fill",
    # Replaces `content: "\\2192"` - a codepoint spelled as a CSS escape, so the one tofu
    # site a search of the rendered bytes could not find.
    "chain": "arrow-right-short",
}

# What each icon drew when it was reviewed: `sha256("<path>|<even_odd>")`. Reading the
# vendored file is not a pin on its own, because every check then reads that one file and
# they agree trivially - measured, mutating one byte of `person-fill` left 24 of 24 tests
# green. Over the drawn content, so reflowed markup is not a false alarm.
DIGESTS: Mapping[str, str] = {
    "arrow-right-short": "2a621f2c9ed464adf3a8019738a8767767aa0ee488adb715a568f94101e7a4f0",
    "check-circle-fill": "c08a880c387a5ee82e81abd5038d1c13fe75c23ad146efef159d2e2687701cec",
    "exclamation-triangle-fill": "a777ce69e8a80a35a8a245f2add4e99685a92fb48dfa93c83d79ca6e4457a65a",
    "hourglass-split": "3cb733ec9d24d880c1cdee24fb5c6fc3d7a99f291ff5556278227ee108579e73",
    "person-fill": "f85064e7e508eec65e9bdd7689b6fd83207398281c9cf84907c7d72bb5b7866b",
    "question-circle-fill": "efd690d4889dca9d8aefe428617a95f271765ffb7d03301a0281abdf46c53e82",
    "slash-circle-fill": "5a6ca275d8a51f4dc66d8867d96da704aff5db60b2d5e77d0e025c3421c8da40",
}

# A regex, not an XML parser: the input is 300 committed bytes, so `defusedxml` would be a
# dependency with one reader.
_PATH = re.compile(r"<path\b([^>]*?)\bd=\"([^\"]+)\"")
_VIEWBOX = re.compile(r"viewBox=\"0 0 (\d+) (\d+)\"")


class IconError(Exception):
    """A vendored icon cannot be drawn as it was reviewed."""


@dataclass(frozen=True)
class Mark:
    """One drawn mark: its path data, and whether it winds even-odd.

    ``even_odd`` is carried because dropping it does not fail - it fills the icon's hole and
    draws a blob that still reads as a mark.
    """

    path: str
    even_odd: bool


def digest(mark: Mark) -> str:
    """*mark*'s entry in :data:`DIGESTS`, derived from what it draws."""
    return hashlib.sha256(f"{mark.path}|{mark.even_odd}".encode()).hexdigest()


def read_mark(icons_dir: Path, mark: str) -> Mark:
    """*mark*'s icon, read from the vendored file :data:`ICONS` names for it.

    Shape before digest, so a malformed file says what is wrong rather than naming a hash.

    Raises:
        IconError: The file is absent, is not one ``<path>`` on a :data:`VIEW` square grid,
            or no longer draws what :data:`DIGESTS` recorded.
        KeyError: *mark* is not on the roster.
    """
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
    """Every mark on the roster, read from ``<templates_dir>/icons``.

    Not cached: seven sub-kilobyte reads cost less than a cache going stale under a test
    that edits a vendored file.

    Raises:
        IconError: Any roster entry could not be read; see :func:`read_mark`.
    """
    icons_dir = templates_dir / DIRNAME
    return {mark: read_mark(icons_dir, mark) for mark in ICONS}
