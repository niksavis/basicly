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
    load_runner_config,
    load_verify_config,
    load_worktree_config,
)
from basicly.runner import BUILTIN_RUNNERS


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
    """The scaffold enables no checks (consumer stacks vary) but keeps examples.

    A scaffolded consumer must never be blocked by tooling it lacks
    (basicly-zrj.13.2): an empty verify config passes vacuously, and the
    commented-out examples document how to declare stack-appropriate checks.
    """
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    config = load_verify_config(tmp_path)

    assert config.checks == ()
    assert "# [[verify.checks]]" in DEFAULT_CONFIG_TOML  # examples stay documented


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


def test_runner_config_defaults_without_file(tmp_path: Path) -> None:
    """With no basicly.toml the runner config is the built-in adapters, default 'auto'."""
    config = load_runner_config(tmp_path)
    assert config.specs == BUILTIN_RUNNERS
    assert config.default == "auto"


def test_default_config_toml_runner_matches_defaults(tmp_path: Path) -> None:
    """The scaffolded [runner] section resolves to the built-in adapters and 'auto'."""
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    config = load_runner_config(tmp_path)
    assert config.specs == BUILTIN_RUNNERS
    assert config.default == "auto"


def test_runner_config_adds_custom_agent(tmp_path: Path) -> None:
    """An [[runner.agents]] entry adds a new adapter alongside the built-ins."""
    (tmp_path / CONFIG_FILE).write_text(
        '[runner]\ndefault = "opencode"\n'
        '[[runner.agents]]\nname = "opencode"\n'
        'command = ["opencode", "run", "{prompt}"]\nprompt_via = "stdin"\n',
        encoding="utf-8",
    )
    config = load_runner_config(tmp_path)
    assert config.default == "opencode"
    by_name = {spec.name: spec for spec in config.specs}
    assert by_name["opencode"].command == ("opencode", "run", "{prompt}")
    assert by_name["opencode"].prompt_via == "stdin"
    assert "claude" in by_name  # built-ins are preserved


def test_runner_config_overrides_builtin_command(tmp_path: Path) -> None:
    """An agent entry matching a built-in name overrides its command template."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "claude"\ncommand = ["claude", "--print", "{prompt}"]\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["claude"].command == ("claude", "--print", "{prompt}")


def test_runner_config_rejects_malformed_agent(tmp_path: Path) -> None:
    """A malformed agent entry raises rather than silently dropping the adapter."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = []\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="non-empty 'command'"):
        load_runner_config(tmp_path)

    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x"]\nprompt_via = "telepathy"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown prompt_via"):
        load_runner_config(tmp_path)
