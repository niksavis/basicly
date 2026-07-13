"""Tests for locating the bundled core catalog."""

from __future__ import annotations

from pathlib import Path

from basicly import catalog
from basicly.catalog import bundled_catalog_root

CATALOG_SUBDIRS = ("fragments", "skills", "hooks", "targets", "templates")


def test_bundled_catalog_root_resolves_to_a_real_catalog() -> None:
    """The locator returns an existing directory holding every catalog subtree."""
    root = bundled_catalog_root()
    assert root.is_dir()
    for sub in CATALOG_SUBDIRS:
        assert (root / sub).is_dir(), f"catalog is missing '{sub}/'"


def test_source_checkout_falls_back_to_dogfooded_core() -> None:
    """Running from this source tree resolves to the authoring `.basicly/core`."""
    packaged = Path(catalog.__file__).parent / catalog.CATALOG_DIRNAME
    if packaged.is_dir():
        # Installed distribution: the packaged copy wins; nothing to assert here.
        return
    assert bundled_catalog_root().as_posix().endswith(".basicly/core")
