"""Tests for project path configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly.config import (
    CONFIG_FILE,
    DEFAULT_CONFIG_TOML,
    PolicyConfig,
    WorktreeConfig,
    load_policy_config,
    load_project_paths,
    load_verify_config,
    load_worktree_config,
)


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


def test_worktree_config_defaults_without_file(tmp_path: Path) -> None:
    """With no basicly.toml the worktree config is (current branch, cap 4)."""
    assert load_worktree_config(tmp_path) == WorktreeConfig(base_branch=None, concurrency=4)


def test_default_config_toml_worktree_matches_defaults(tmp_path: Path) -> None:
    """The scaffolded [worktree] section resolves to the built-in defaults."""
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    assert load_worktree_config(tmp_path) == WorktreeConfig(base_branch=None, concurrency=4)


def test_worktree_config_custom_values(tmp_path: Path) -> None:
    """Custom base_branch and concurrency are parsed; a bad cap falls back."""
    (tmp_path / CONFIG_FILE).write_text(
        '[worktree]\nbase_branch = "develop"\nconcurrency = 8\n',
        encoding="utf-8",
    )
    assert load_worktree_config(tmp_path) == WorktreeConfig(base_branch="develop", concurrency=8)

    (tmp_path / CONFIG_FILE).write_text(
        "[worktree]\nconcurrency = 0\n",
        encoding="utf-8",
    )
    assert load_worktree_config(tmp_path).concurrency == 4


def test_verify_config_empty_without_section(tmp_path: Path) -> None:
    """No file or no [verify] section yields no checks."""
    assert load_verify_config(tmp_path).checks == ()
    (tmp_path / CONFIG_FILE).write_text("[worktree]\nconcurrency = 2\n", encoding="utf-8")
    assert load_verify_config(tmp_path).checks == ()


def test_default_config_toml_verify_checks(tmp_path: Path) -> None:
    """The scaffolded [verify] section parses into the expected mode routing."""
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    config = load_verify_config(tmp_path)

    assert [c.name for c in config.checks] == ["ruff", "ruff-format", "pyright", "pytest"]
    assert [c.name for c in config.for_mode("full")] == ["ruff", "ruff-format", "pyright", "pytest"]
    assert [c.name for c in config.for_mode("fast")] == ["ruff", "ruff-format", "pyright"]
    assert [(c.name, c.staged_suffix) for c in config.for_mode("staged")] == [
        ("ruff", ".py"),
        ("ruff-format", ".py"),
        ("pyright", ".py"),
    ]
    assert config.checks[0].command == ("ruff", "check")


def test_verify_config_rejects_malformed_check(tmp_path: Path) -> None:
    """A check missing its command is a loud error, not a silently dropped gate."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[verify.checks]]\nname = "ruff"\nmodes = ["fast"]\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="non-empty 'command'"):
        load_verify_config(tmp_path)


def test_verify_config_rejects_unknown_mode(tmp_path: Path) -> None:
    """An unknown mode is rejected so a typo never quietly disables a check."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[verify.checks]]\nname = "x"\ncommand = ["true"]\nmodes = ["quick"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown mode"):
        load_verify_config(tmp_path)


def test_policy_config_defaults_without_file(tmp_path: Path) -> None:
    """With no basicly.toml the policy is (required verify, cap 2)."""
    assert load_policy_config(tmp_path) == PolicyConfig(required_gates=("verify",), max_rework=2)


def test_default_config_toml_policy_matches_defaults(tmp_path: Path) -> None:
    """The scaffolded [policy] section resolves to the built-in defaults."""
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    assert load_policy_config(tmp_path) == PolicyConfig(required_gates=("verify",), max_rework=2)


def test_policy_config_custom_values(tmp_path: Path) -> None:
    """Custom required_gates and max_rework parse; a negative cap falls back."""
    (tmp_path / CONFIG_FILE).write_text(
        '[policy]\nrequired_gates = ["verify", "security"]\nmax_rework = 3\n',
        encoding="utf-8",
    )
    config = load_policy_config(tmp_path)
    assert config.required_gates == ("verify", "security")
    assert config.max_rework == 3

    (tmp_path / CONFIG_FILE).write_text("[policy]\nmax_rework = -1\n", encoding="utf-8")
    assert load_policy_config(tmp_path).max_rework == 2
