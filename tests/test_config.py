"""Tests for project path configuration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from basicly import permissions
from basicly.config import (
    CONFIG_FILE,
    DEFAULT_CONFIG_TOML,
    LOCAL_CONFIG_FILE,
    PolicyConfig,
    WorktreeConfig,
    load_policy_config,
    load_project_paths,
    load_runner_config,
    load_technology_selection,
    load_verify_config,
    load_worktree_config,
    record_technology_selection,
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


def test_technology_selection_absent_means_everything(tmp_path: Path) -> None:
    """No file, no [catalog] section, or the scaffold all mean: no selection."""
    assert load_technology_selection(tmp_path) is None
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    assert load_technology_selection(tmp_path) is None


def test_record_technology_selection_round_trips(tmp_path: Path) -> None:
    """Recording appends a [catalog] section and the loader reads it back."""
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML + "\n# user note\n", encoding="utf-8")
    record_technology_selection(tmp_path, ["python", "zsh"])
    assert load_technology_selection(tmp_path) == frozenset({"python", "zsh"})
    # The user-owned parts of the file survive the append.
    assert "# user note" in (tmp_path / CONFIG_FILE).read_text(encoding="utf-8")

    # Re-recording rewrites the selection in place instead of duplicating it.
    record_technology_selection(tmp_path, ["go"])
    assert load_technology_selection(tmp_path) == frozenset({"go"})
    assert (tmp_path / CONFIG_FILE).read_text(encoding="utf-8").count("\n[catalog]") == 1


def test_record_technology_selection_scaffolds_missing_config(tmp_path: Path) -> None:
    """Recording into a repo without basicly.toml scaffolds it first."""
    record_technology_selection(tmp_path, ["python"])
    assert load_technology_selection(tmp_path) == frozenset({"python"})
    assert load_project_paths(tmp_path) == load_project_paths(tmp_path / "elsewhere")


def test_technology_selection_rejects_unknown_value(tmp_path: Path) -> None:
    """A typo in the selection fails loudly instead of silently dropping content."""
    (tmp_path / CONFIG_FILE).write_text('[catalog]\ntechnologies = ["pyton"]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="pyton"):
        load_technology_selection(tmp_path)


def test_record_technology_selection_reuses_bare_catalog_section(tmp_path: Path) -> None:
    """A [catalog] section without the key gains the line — never a second table."""
    (tmp_path / CONFIG_FILE).write_text(
        "[catalog]\n# future keys\n\n[worktree]\nconcurrency = 2\n", encoding="utf-8"
    )
    record_technology_selection(tmp_path, ["python"])
    assert load_technology_selection(tmp_path) == frozenset({"python"})
    text = (tmp_path / CONFIG_FILE).read_text(encoding="utf-8")
    assert text.count("[catalog]") == 1 and "# future keys" in text


def test_record_technology_selection_repairs_invalid_value(tmp_path: Path) -> None:
    """Re-recording over a typo'd selection rewrites it (the natural repair path)."""
    (tmp_path / CONFIG_FILE).write_text('[catalog]\ntechnologies = ["pyton"]\n', encoding="utf-8")
    record_technology_selection(tmp_path, ["python"])
    assert load_technology_selection(tmp_path) == frozenset({"python"})


@pytest.mark.parametrize(
    "layout",
    [
        '[catalog]\ntechnologies = [\n  "python",\n]\n',  # multiline array
        'catalog.technologies = ["python"]\n',  # dotted key, no [catalog] header
    ],
)
def test_record_technology_selection_refuses_unsupported_layouts(
    tmp_path: Path, layout: str
) -> None:
    """A layout the line splice cannot rewrite errors out with the file untouched."""
    (tmp_path / CONFIG_FILE).write_text(layout, encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        record_technology_selection(tmp_path, ["zsh"])
    assert (tmp_path / CONFIG_FILE).read_text(encoding="utf-8") == layout


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


def _builtins_with_copilot_deny_stripped(config) -> tuple:
    """config.specs with the copilot deny-list cleared, to compare against BUILTIN_RUNNERS.

    load_runner_config folds the baseline deny-list onto the copilot spec
    (basicly-lqz5), so the resolved specs no longer equal the raw built-ins;
    normalizing that one field back lets the rest be compared for drift.
    """
    return tuple(replace(s, deny_tools=()) if s.name == "copilot" else s for s in config.specs)


def _expected_copilot_deny() -> tuple[str, ...]:
    return tuple(permissions.copilot_deny_specs(permissions.load_deny_rules()))


def test_runner_config_defaults_without_file(tmp_path: Path) -> None:
    """With no basicly.toml the config is the built-ins (copilot carries the deny-list)."""
    config = load_runner_config(tmp_path)
    by_name = {spec.name: spec for spec in config.specs}
    assert by_name["copilot"].deny_tools == _expected_copilot_deny()
    assert _builtins_with_copilot_deny_stripped(config) == BUILTIN_RUNNERS
    assert config.default == "auto"


def test_default_config_toml_runner_matches_defaults(tmp_path: Path) -> None:
    """The scaffolded [runner] section resolves to the built-in adapters and 'auto'."""
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    config = load_runner_config(tmp_path)
    assert _builtins_with_copilot_deny_stripped(config) == BUILTIN_RUNNERS
    assert config.default == "auto"


def test_runner_config_injects_copilot_deny_tools(tmp_path: Path) -> None:
    """The copilot adapter carries the baseline deny-list as --deny-tool specs; others do not."""
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["copilot"].deny_tools == _expected_copilot_deny()
    assert by_name["copilot"].deny_tools  # non-empty from the shipped manifest
    assert by_name["claude"].deny_tools == ()
    assert by_name["codex"].deny_tools == ()


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


def test_runner_config_parses_optional_model(tmp_path: Path) -> None:
    """An [[runner.agents]] entry may pin a model; it lands on the RunnerSpec."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "claude"\n'
        'command = ["claude", "-p", "{prompt}"]\nmodel = "opus"\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["claude"].model == "opus"


def test_runner_config_model_defaults_none(tmp_path: Path) -> None:
    """An agent entry without a model leaves the spec's model unset."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\n', encoding="utf-8"
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["x"].model is None


def test_runner_config_rejects_blank_model(tmp_path: Path) -> None:
    """A present-but-empty model is a config error, not a silent None."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\nmodel = "  "\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-empty string"):
        load_runner_config(tmp_path)


def test_runner_config_codex_defaults_sandbox_and_approval(tmp_path: Path) -> None:
    """The shipped codex adapter carries the guardrail defaults; others leave them unset."""
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["codex"].sandbox == "workspace-write"
    assert by_name["codex"].approval == "on-failure"
    assert by_name["claude"].sandbox is None
    assert by_name["claude"].approval is None


def test_runner_config_parses_sandbox_and_approval_override(tmp_path: Path) -> None:
    """An [[runner.agents]] entry may set sandbox/approval; they land on the RunnerSpec."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "codex"\n'
        'command = ["codex", "exec", "{prompt}"]\n'
        'sandbox = "read-only"\napproval = "untrusted"\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["codex"].sandbox == "read-only"
    assert by_name["codex"].approval == "untrusted"


def test_runner_config_sandbox_approval_default_none_for_override(tmp_path: Path) -> None:
    """An override that omits the keys is not silently re-defaulted to codex's values."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "codex"\ncommand = ["codex", "exec", "{prompt}"]\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["codex"].sandbox is None
    assert by_name["codex"].approval is None


def test_runner_config_rejects_blank_sandbox(tmp_path: Path) -> None:
    """A present-but-empty sandbox is a config error, not a silent None."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\nsandbox = "  "\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-empty string"):
        load_runner_config(tmp_path)


def test_runner_config_overrides_builtin_command(tmp_path: Path) -> None:
    """An agent entry matching a built-in name overrides its command template."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "claude"\ncommand = ["claude", "--print", "{prompt}"]\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["claude"].command == ("claude", "--print", "{prompt}")


def test_runner_config_parses_bot_git_identity(tmp_path: Path) -> None:
    """Both git_name and git_email land on the spec as the opt-in bot identity (basicly-smzg)."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "bot"\ncommand = ["bot", "{prompt}"]\n'
        'git_name = "basicly-bot"\ngit_email = "bot@example.com"\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["bot"].git_name == "basicly-bot"
    assert by_name["bot"].git_email == "bot@example.com"


def test_runner_config_bot_identity_defaults_none(tmp_path: Path) -> None:
    """An agent entry without a bot identity leaves both fields unset."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\n', encoding="utf-8"
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["x"].git_name is None
    assert by_name["x"].git_email is None


def test_runner_config_rejects_lone_git_identity_half(tmp_path: Path) -> None:
    """A git_name without git_email (or vice versa) is a config error, not a half identity."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\ngit_name = "bot"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="both 'git_name' and 'git_email'"):
        load_runner_config(tmp_path)

    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\ngit_email = "b@example.com"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="both 'git_name' and 'git_email'"):
        load_runner_config(tmp_path)


def test_runner_config_rejects_blank_git_identity(tmp_path: Path) -> None:
    """A present-but-empty git identity field is a config error."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\n'
        'git_name = "  "\ngit_email = "b@example.com"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-empty string"):
        load_runner_config(tmp_path)


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


def test_local_config_overrides_harness_sections(tmp_path: Path) -> None:
    """basicly.local.toml keys win over basicly.toml, key by key, per section."""
    (tmp_path / CONFIG_FILE).write_text(
        '[worktree]\nbase_branch = "develop"\nconcurrency = 8\n'
        "[policy]\nmax_rework = 3\n"
        '[runner]\ndefault = "claude"\n',
        encoding="utf-8",
    )
    (tmp_path / LOCAL_CONFIG_FILE).write_text(
        '[worktree]\nconcurrency = 2\n[policy]\nmax_rework = 1\n[runner]\ndefault = "manual"\n',
        encoding="utf-8",
    )

    worktree = load_worktree_config(tmp_path)
    assert worktree.concurrency == 2
    assert worktree.base_branch == "develop"  # untouched base keys survive the merge
    assert load_policy_config(tmp_path).max_rework == 1
    assert load_runner_config(tmp_path).default == "manual"


def test_local_config_alone_configures_harness(tmp_path: Path) -> None:
    """The overlay works without a basicly.toml at all."""
    (tmp_path / LOCAL_CONFIG_FILE).write_text(
        '[runner]\ndefault = "manual"\n[worktree]\nconcurrency = 1\n',
        encoding="utf-8",
    )
    assert load_runner_config(tmp_path).default == "manual"
    assert load_worktree_config(tmp_path).concurrency == 1


def test_local_config_replaces_verify_checks_wholesale(tmp_path: Path) -> None:
    """A local checks list replaces the shared one; it is not concatenated."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[verify.checks]]\nname = "pytest"\ncommand = ["pytest", "-q"]\nmodes = ["full"]\n',
        encoding="utf-8",
    )
    (tmp_path / LOCAL_CONFIG_FILE).write_text(
        '[[verify.checks]]\nname = "ruff"\ncommand = ["ruff", "check"]\nmodes = ["fast"]\n',
        encoding="utf-8",
    )
    checks = load_verify_config(tmp_path).checks
    assert [check.name for check in checks] == ["ruff"]


def test_local_config_never_affects_projection_config(tmp_path: Path) -> None:
    """[paths] and [catalog] are repo-level: the overlay must not shift them."""
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    (tmp_path / LOCAL_CONFIG_FILE).write_text(
        '[paths]\ncore_fragments = "elsewhere/fragments"\n[catalog]\ntechnologies = ["python"]\n',
        encoding="utf-8",
    )
    assert load_project_paths(tmp_path).core_fragments_dir == Path(".basicly/core/fragments")
    assert load_technology_selection(tmp_path) is None
