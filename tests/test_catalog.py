from __future__ import annotations

from pathlib import Path

from basicly.catalog import bundled_catalog_root, iter_catalog_files

CATALOG_SUBDIRS = ("fragments", "skills", "hooks", "targets", "templates")


def test_bundled_catalog_root_resolves_to_a_real_catalog() -> None:
    root = bundled_catalog_root()
    assert root.is_dir()
    for sub in CATALOG_SUBDIRS:
        assert (root / sub).is_dir(), f"catalog is missing '{sub}/'"


def test_source_checkout_resolves_to_dogfooded_core() -> None:

    assert bundled_catalog_root().as_posix().endswith(".basicly/core")


def test_iter_catalog_files_skips_bytecode(tmp_path: Path) -> None:
    (tmp_path / "hooks" / "__pycache__").mkdir(parents=True)
    (tmp_path / "hooks" / "__pycache__" / "x.cpython-314.pyc").write_bytes(b"")
    (tmp_path / "hooks" / "stale.pyc").write_bytes(b"")
    (tmp_path / "hooks" / "pre-commit.py").write_text("print()\n", encoding="utf-8")

    files = list(iter_catalog_files(tmp_path))
    assert files == [tmp_path / "hooks" / "pre-commit.py"]
