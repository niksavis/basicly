from __future__ import annotations

from pathlib import Path

from basicly import tree_schema
from basicly.config import CONFIG_SCHEMA

REPO_ROOT = Path(__file__).resolve().parents[1]


def _engine_tree(root: Path, source: str) -> Path:
    engine = root / "src" / "basicly" / "config.py"
    engine.parent.mkdir(parents=True, exist_ok=True)
    engine.write_text(source, encoding="utf-8")
    return engine


def test_the_reader_reproduces_this_repos_own_schema() -> None:

    assert tree_schema.read(REPO_ROOT) == CONFIG_SCHEMA


def test_a_schema_declared_in_a_way_the_reader_cannot_model_reads_as_none(
    tmp_path: Path,
) -> None:
    _engine_tree(tmp_path, "CONFIG_SCHEMA = build_schema()\n")

    assert tree_schema.read(tmp_path) is None


def test_a_repo_that_only_uses_basicly_ships_no_engine_source(tmp_path: Path) -> None:

    assert not tree_schema.ships_engine_source(tmp_path)

    _engine_tree(tmp_path, "CONFIG_SCHEMA = build_schema()\n")

    assert tree_schema.ships_engine_source(tmp_path)
