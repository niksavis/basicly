"""Tests for locating the bundled core catalog."""

from __future__ import annotations

from pathlib import Path

from basicly.catalog import bundled_catalog_root, iter_catalog_files

CATALOG_SUBDIRS = ("fragments", "skills", "hooks", "targets", "templates")


def test_bundled_catalog_root_resolves_to_a_real_catalog() -> None:
    """The locator returns an existing directory holding every catalog subtree."""
    root = bundled_catalog_root()
    assert root.is_dir()
    for sub in CATALOG_SUBDIRS:
        assert (root / sub).is_dir(), f"catalog is missing '{sub}/'"


def test_source_checkout_resolves_to_dogfooded_core() -> None:
    """Running from this source tree always resolves to the authoring source.

    The source checkout is found by marker walk and takes precedence, so a
    stale projected copy (e.g. a leftover src/basicly/catalog dir or a stale
    site-packages snapshot) can never shadow the live `.basicly/core`.
    """
    assert bundled_catalog_root().as_posix().endswith(".basicly/core")


def test_iter_catalog_files_skips_bytecode(tmp_path: Path) -> None:
    """The shared walker excludes __pycache__ trees and .pyc files."""
    (tmp_path / "hooks" / "__pycache__").mkdir(parents=True)
    (tmp_path / "hooks" / "__pycache__" / "x.cpython-314.pyc").write_bytes(b"")
    (tmp_path / "hooks" / "stale.pyc").write_bytes(b"")
    (tmp_path / "hooks" / "pre-commit.py").write_text("print()\n", encoding="utf-8")

    files = list(iter_catalog_files(tmp_path))
    assert files == [tmp_path / "hooks" / "pre-commit.py"]
