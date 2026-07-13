"""Tests for project path configuration."""

from __future__ import annotations

from pathlib import Path

from basicly.config import CONFIG_FILE, DEFAULT_CONFIG_TOML, load_project_paths


def test_default_config_toml_matches_builtin_defaults(tmp_path: Path) -> None:
    """The scaffolded basicly.toml must resolve to exactly the built-in defaults.

    Guards against the init scaffold and load_project_paths defaults drifting
    apart, which would pin freshly-inited repos to a stale layout.
    """
    defaults = load_project_paths(tmp_path)

    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    scaffolded = load_project_paths(tmp_path)

    assert scaffolded == defaults


def test_core_root_derives_from_fragments_dir(tmp_path: Path) -> None:
    """core_root relocates with a customized core_fragments path."""
    (tmp_path / CONFIG_FILE).write_text(
        '[paths]\ncore_fragments = "conf/agents/fragments"\n',
        encoding="utf-8",
    )
    paths = load_project_paths(tmp_path)
    assert paths.core_root == Path("conf/agents")
