"""Tests for reading a tree's declared config schema out of its own source."""

from __future__ import annotations

from pathlib import Path

from basicly import tree_schema
from basicly.config import CONFIG_SCHEMA

REPO_ROOT = Path(__file__).resolve().parents[1]


def _engine_tree(root: Path, source: str) -> Path:
    """Write *source* into *root* as the basicly engine source a checkout ships."""
    engine = root / "src" / "basicly" / "config.py"
    engine.parent.mkdir(parents=True, exist_ok=True)
    engine.write_text(source, encoding="utf-8")
    return engine


def test_the_reader_reproduces_this_repos_own_schema() -> None:
    """The static reader and the imported module must agree on this very file.

    The anti-drift half of basicly-69az. A landing is judged by whatever this reader
    makes of the tree's `config.py`, so a construct it silently mis-reads would move
    the gate rather than relocate it. Equality against the live `CONFIG_SCHEMA` is
    the only assertion that stays true as the schema grows.
    """
    assert tree_schema.read(REPO_ROOT) == CONFIG_SCHEMA


def test_a_schema_declared_in_a_way_the_reader_cannot_model_reads_as_none(
    tmp_path: Path,
) -> None:
    """Fail closed: an unmodelled construct is None, which restores the caller's own schema."""
    _engine_tree(tmp_path, "CONFIG_SCHEMA = build_schema()\n")

    assert tree_schema.read(tmp_path) is None


def test_a_repo_that_only_uses_basicly_ships_no_engine_source(tmp_path: Path) -> None:
    """The two answers `ships_engine_source` separates: a checkout, and a consumer repo.

    It is what tells the two None cases apart — a tree with no schema of its own has no
    ordering to get right, so its refusal must not carry the ordering rule.
    """
    assert not tree_schema.ships_engine_source(tmp_path)

    _engine_tree(tmp_path, "CONFIG_SCHEMA = build_schema()\n")

    assert tree_schema.ships_engine_source(tmp_path)
