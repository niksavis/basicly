from __future__ import annotations

import ast
import re
from dataclasses import replace
from pathlib import Path

import pytest

import basicly
from basicly import config, dropin, permissions, run_record, runner
from basicly.config import (
    CONFIG_FILE,
    CONFIG_SCHEMA,
    DEFAULT_CONFIG_TOML,
    DEFAULT_MAX_AGENT_PROCESSES,
    DEFAULT_QUIET_AFTER,
    DEFAULT_STALL_AFTER,
    DEFAULT_TYPE_SECTIONS,
    DEFAULT_WORKING_SET_MAX,
    DEFAULT_WORKING_SET_MIN,
    DEFAULT_WORKTREE_CONCURRENCY,
    LOCAL_CONFIG_FILE,
    PolicyConfig,
    WorktreeConfig,
    load_policy_config,
    load_project_paths,
    load_runner_config,
    load_sizing_config,
    load_technology_selection,
    load_type_sections,
    load_verify_config,
    load_worktree_config,
    record_technology_selection,
    unknown_config_keys,
    untiered_metered_runners,
)
from basicly.context_window import (
    AGENT_WINDOW,
    DECLARED_WINDOW,
    FALLBACK_WINDOW,
)
from basicly.runner import (
    AGENT_TIER,
    BUILTIN_RUNNERS,
    FAMILY_DEFAULT_TIER,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_default_config_toml_matches_builtin_defaults(tmp_path: Path) -> None:

    defaults = load_project_paths(tmp_path)

    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    scaffolded = load_project_paths(tmp_path)

    assert scaffolded == defaults


def test_technology_selection_absent_means_everything(tmp_path: Path) -> None:
    assert load_technology_selection(tmp_path) is None
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    assert load_technology_selection(tmp_path) is None


def test_record_technology_selection_round_trips(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML + "\n# user note\n", encoding="utf-8")
    record_technology_selection(tmp_path, ["python", "zsh"])
    assert load_technology_selection(tmp_path) == frozenset({"python", "zsh"})
    assert "# user note" in (tmp_path / CONFIG_FILE).read_text(encoding="utf-8")

    record_technology_selection(tmp_path, ["go"])
    assert load_technology_selection(tmp_path) == frozenset({"go"})
    assert (tmp_path / CONFIG_FILE).read_text(encoding="utf-8").count("\n[catalog]") == 1


def test_record_technology_selection_scaffolds_missing_config(tmp_path: Path) -> None:
    record_technology_selection(tmp_path, ["python"])
    assert load_technology_selection(tmp_path) == frozenset({"python"})
    assert load_project_paths(tmp_path) == load_project_paths(tmp_path / "elsewhere")


def test_technology_selection_rejects_unknown_value(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text('[catalog]\ntechnologies = ["pyton"]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="pyton"):
        load_technology_selection(tmp_path)


def test_record_technology_selection_reuses_bare_catalog_section(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        "[catalog]\n# future keys\n\n[worktree]\nconcurrency = 2\n", encoding="utf-8"
    )
    record_technology_selection(tmp_path, ["python"])
    assert load_technology_selection(tmp_path) == frozenset({"python"})
    text = (tmp_path / CONFIG_FILE).read_text(encoding="utf-8")
    assert text.count("[catalog]") == 1 and "# future keys" in text


def test_record_technology_selection_repairs_invalid_value(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text('[catalog]\ntechnologies = ["pyton"]\n', encoding="utf-8")
    record_technology_selection(tmp_path, ["python"])
    assert load_technology_selection(tmp_path) == frozenset({"python"})


@pytest.mark.parametrize(
    "layout",
    [
        '[catalog]\ntechnologies = [\n  "python",\n]\n',
        'catalog.technologies = ["python"]\n',
    ],
)
def test_record_technology_selection_refuses_unsupported_layouts(
    tmp_path: Path, layout: str
) -> None:
    (tmp_path / CONFIG_FILE).write_text(layout, encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        record_technology_selection(tmp_path, ["zsh"])
    assert (tmp_path / CONFIG_FILE).read_text(encoding="utf-8") == layout


def test_core_root_derives_from_fragments_dir(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[paths]\ncore_fragments = "conf/agents/fragments"\n',
        encoding="utf-8",
    )
    paths = load_project_paths(tmp_path)
    assert paths.core_root == Path("conf/agents")


def test_worktree_config_defaults_without_file(tmp_path: Path) -> None:
    assert load_worktree_config(tmp_path) == WorktreeConfig(
        base_branch=None, concurrency=DEFAULT_WORKTREE_CONCURRENCY
    )


def test_default_config_toml_worktree_matches_defaults(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    assert load_worktree_config(tmp_path) == WorktreeConfig(
        base_branch=None, concurrency=DEFAULT_WORKTREE_CONCURRENCY
    )


def test_worktree_config_custom_values(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[worktree]\nbase_branch = "develop"\nconcurrency = 8\n',
        encoding="utf-8",
    )
    assert load_worktree_config(tmp_path) == WorktreeConfig(base_branch="develop", concurrency=8)

    (tmp_path / CONFIG_FILE).write_text(
        "[worktree]\nconcurrency = 0\n",
        encoding="utf-8",
    )
    assert load_worktree_config(tmp_path).concurrency == DEFAULT_WORKTREE_CONCURRENCY


def test_worktree_config_reads_the_append_only_paths(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[worktree]\nappend_only_paths = ["CHANGELOG.md", "docs/release-notes.md"]\n',
        encoding="utf-8",
    )
    assert load_worktree_config(tmp_path).append_only_paths == (
        "CHANGELOG.md",
        "docs/release-notes.md",
    )


def test_append_only_paths_default_to_none_declared(tmp_path: Path) -> None:
    assert load_worktree_config(tmp_path).append_only_paths == ()
    (tmp_path / CONFIG_FILE).write_text("[worktree]\nconcurrency = 2\n", encoding="utf-8")
    assert load_worktree_config(tmp_path).append_only_paths == ()


def test_an_append_only_glob_is_refused_rather_than_ignored(tmp_path: Path) -> None:

    (tmp_path / CONFIG_FILE).write_text(
        '[worktree]\nappend_only_paths = ["docs/**"]\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="is a glob"):
        load_worktree_config(tmp_path)


def test_worktree_config_reads_each_generated_path_with_its_own_rebuild(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        "[worktree.regenerate_commands]\n"
        '".basicly/generated-manifest.json" = ["basicly", "build"]\n'
        '"docs/architecture/status.md" = ["docs_claims.py", "--fix"]\n',
        encoding="utf-8",
    )

    config = load_worktree_config(tmp_path)

    assert config.regenerate_commands == {
        ".basicly/generated-manifest.json": ("basicly", "build"),
        "docs/architecture/status.md": ("docs_claims.py", "--fix"),
    }


def test_generated_paths_default_to_none_declared(tmp_path: Path) -> None:
    assert load_worktree_config(tmp_path).regenerate_commands == {}
    (tmp_path / CONFIG_FILE).write_text("[worktree]\nconcurrency = 2\n", encoding="utf-8")
    assert load_worktree_config(tmp_path).regenerate_commands == {}


def test_a_generated_glob_is_refused_rather_than_ignored(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[worktree.regenerate_commands]\n".basicly/**" = ["true"]\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match=r"regenerate_commands.*is a glob"):
        load_worktree_config(tmp_path)


def test_a_generated_path_without_a_rebuild_command_is_refused(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[worktree.regenerate_commands]\n"manifest.json" = []\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="declares no command"):
        load_worktree_config(tmp_path)


def test_a_rebuild_command_that_is_not_an_argv_list_is_refused(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[worktree.regenerate_commands]\n"manifest.json" = "basicly build"\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="must be an argv list"):
        load_worktree_config(tmp_path)


def test_verify_config_empty_without_section(tmp_path: Path) -> None:
    assert load_verify_config(tmp_path).checks == ()
    (tmp_path / CONFIG_FILE).write_text("[worktree]\nconcurrency = 2\n", encoding="utf-8")
    assert load_verify_config(tmp_path).checks == ()


def test_default_config_toml_verify_checks(tmp_path: Path) -> None:

    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    config = load_verify_config(tmp_path)

    assert config.checks == ()
    assert "# [[verify.checks]]" in DEFAULT_CONFIG_TOML


def test_verify_config_rejects_malformed_check(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[verify.checks]]\nname = "ruff"\nmodes = ["fast"]\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="non-empty 'command'"):
        load_verify_config(tmp_path)


def test_verify_config_reads_the_optional_fix_command(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[verify.checks]]\nname = "ruff-format"\ncommand = ["ruff", "format", "--check"]\n'
        'fix_command = ["ruff", "format"]\nmodes = ["fast"]\n'
        '[[verify.checks]]\nname = "ruff"\ncommand = ["ruff", "check"]\nmodes = ["fast"]\n',
        encoding="utf-8",
    )
    checks = load_verify_config(tmp_path).checks
    assert checks[0].fix_command == ("ruff", "format")
    assert checks[1].fix_command is None


def test_verify_config_rejects_malformed_fix_command(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[verify.checks]]\nname = "x"\ncommand = ["true"]\n'
        'fix_command = "true"\nmodes = ["fast"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="'fix_command'"):
        load_verify_config(tmp_path)


def test_verify_config_rejects_unknown_mode(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[verify.checks]]\nname = "x"\ncommand = ["true"]\nmodes = ["quick"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown mode"):
        load_verify_config(tmp_path)


def test_policy_config_defaults_without_file(tmp_path: Path) -> None:
    assert load_policy_config(tmp_path) == PolicyConfig(required_gates=("verify",), max_rework=2)


def test_default_config_toml_policy_matches_defaults(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    assert load_policy_config(tmp_path) == PolicyConfig(required_gates=("verify",), max_rework=2)


def test_policy_config_custom_values(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[policy]\nrequired_gates = ["verify", "security"]\nmax_rework = 3\n',
        encoding="utf-8",
    )
    config = load_policy_config(tmp_path)
    assert config.required_gates == ("verify", "security")
    assert config.max_rework == 3

    (tmp_path / CONFIG_FILE).write_text("[policy]\nmax_rework = -1\n", encoding="utf-8")
    assert load_policy_config(tmp_path).max_rework == 2


def test_type_sections_come_from_configuration(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[policy.type_sections]\nbug = ["## Repro"]\nchore = []\n', encoding="utf-8"
    )
    assert load_type_sections(tmp_path) == {"bug": ("## Repro",), "chore": ()}


def test_type_sections_refuses_an_unknown_work_type(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[policy.type_sections]\ndefect = ["## Repro"]\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="defect"):
        load_type_sections(tmp_path)


def test_type_sections_refuses_a_value_that_is_not_a_heading_list(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[policy.type_sections]\nbug = "## Repro"\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="list of section headings"):
        load_type_sections(tmp_path)


def test_type_sections_absent_falls_back_to_the_builtin_set_and_says_so_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config._say_type_sections_fallback.cache_clear()
    assert load_type_sections(tmp_path) == DEFAULT_TYPE_SECTIONS
    assert "[policy.type_sections]" in capsys.readouterr().err
    assert load_type_sections(tmp_path) == DEFAULT_TYPE_SECTIONS
    assert capsys.readouterr().err == ""


def _builtins_with_copilot_deny_stripped(config) -> tuple:

    return tuple(replace(s, deny_tools=()) if s.name == "copilot" else s for s in config.specs)


def _expected_copilot_deny() -> tuple[str, ...]:
    return tuple(permissions.copilot_deny_specs(permissions.load_deny_rules()))


def test_runner_config_defaults_without_file(tmp_path: Path) -> None:
    config = load_runner_config(tmp_path)
    by_name = {spec.name: spec for spec in config.specs}
    assert by_name["copilot"].deny_tools == _expected_copilot_deny()
    assert _builtins_with_copilot_deny_stripped(config) == BUILTIN_RUNNERS
    assert config.default == "auto"


def test_default_config_toml_runner_matches_defaults(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    config = load_runner_config(tmp_path)
    assert _builtins_with_copilot_deny_stripped(config) == BUILTIN_RUNNERS
    assert config.default == "auto"


def test_runner_config_injects_copilot_deny_tools(tmp_path: Path) -> None:
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["copilot"].deny_tools == _expected_copilot_deny()
    assert by_name["copilot"].deny_tools
    assert by_name["claude"].deny_tools == ()
    assert by_name["codex"].deny_tools == ()


def test_runner_config_adds_custom_agent(tmp_path: Path) -> None:
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
    assert "claude" in by_name


def test_runner_config_parses_optional_model(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "claude"\n'
        'command = ["claude", "-p", "{prompt}"]\nmodel = "opus"\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["claude"].model == "opus"


def test_runner_config_parses_a_model_tier_and_vendor(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "copilot"\n'
        'command = ["copilot", "-p", "{prompt}"]\ntier = "high"\nvendor = "openai"\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["copilot"].tier == "high"
    assert by_name["copilot"].vendor == "openai"
    assert by_name["copilot"].model is None


def test_runner_config_rejects_an_unknown_model_tier(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\ntier = "ludicrous"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown model tier"):
        load_runner_config(tmp_path)


def test_runner_config_rejects_an_unknown_default_tier(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[runner]\ndefault_tier = "turbo"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not a known model tier"):
        load_runner_config(tmp_path)


def test_runner_config_default_tier_is_absent_by_default(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text("[runner]\n", encoding="utf-8")
    config = load_runner_config(tmp_path)
    assert config.default_tier is None
    assert all(spec.tier is None for spec in config.specs)


def test_a_default_tier_lands_on_every_spec_that_declares_none(tmp_path: Path) -> None:

    (tmp_path / CONFIG_FILE).write_text(
        '[runner]\ndefault_tier = "medium"\n'
        '[[runner.agents]]\nname = "pinned"\n'
        'command = ["pinned", "{prompt}"]\nmodel = "opus"\n'
        '[[runner.agents]]\nname = "tiered"\n'
        'command = ["tiered", "{prompt}"]\ntier = "maximum"\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}

    assert by_name["claude"].tier == "medium"
    assert by_name["claude"].tier_source == FAMILY_DEFAULT_TIER
    assert by_name["tiered"].tier == "maximum"
    assert by_name["tiered"].tier_source == AGENT_TIER
    assert by_name["pinned"].tier is None


_OBSERVED_LANE_MODEL = "claude-opus-5"


def _repo_runner_specs() -> dict[str, runner.RunnerSpec]:
    return {spec.name: spec for spec in load_runner_config(REPO_ROOT).specs}


def test_every_metered_runner_in_this_repo_resolves_a_model_it_can_name() -> None:

    assert untiered_metered_runners(load_runner_config(REPO_ROOT), repo_root=REPO_ROOT) == []


def test_the_gate_names_the_key_when_nothing_declares_a_tier(tmp_path: Path) -> None:

    (tmp_path / CONFIG_FILE).write_text("[runner]\n", encoding="utf-8")
    metered = [spec for spec in BUILTIN_RUNNERS if spec.kind == runner.HEADLESS]

    problems = untiered_metered_runners(load_runner_config(tmp_path), repo_root=REPO_ROOT)

    assert len(problems) == len(metered)
    report = "\n".join(problems)
    for spec in metered:
        assert f"runner {spec.name!r}" in report
    assert report.count("[runner] default_tier") == len(metered)
    assert "manual" not in report


def test_the_declared_tier_pins_the_model_this_repos_lanes_already_ran() -> None:

    resolution = runner.resolve_model(_repo_runner_specs()["claude"], repo_root=REPO_ROOT)

    assert resolution.tier == "high"
    assert resolution.source == FAMILY_DEFAULT_TIER
    assert resolution.honoured
    assert resolution.model == _OBSERVED_LANE_MODEL


def test_the_ledger_holds_the_metered_dispatches_that_named_no_model() -> None:

    metered = [
        entry
        for entries in run_record.dispatch_history(REPO_ROOT).values()
        for entry in entries
        if isinstance(cost := entry.get("cost"), int | float) and not isinstance(cost, bool)
    ]
    assert metered, "no dispatch carries a recorded cost — this control would be inert"

    unnamed = [e for e in metered if e.get("model") is None and e.get("model_tier") is None]

    assert unnamed
    assert any(_OBSERVED_LANE_MODEL in (e.get("observed_models") or ()) for e in unnamed)


def test_runner_config_model_defaults_none(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\n', encoding="utf-8"
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["x"].model is None


def test_runner_config_rejects_blank_model(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\nmodel = "  "\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-empty string"):
        load_runner_config(tmp_path)


def test_runner_config_codex_defaults_sandbox_and_approval(tmp_path: Path) -> None:
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["codex"].sandbox == "workspace-write"
    assert by_name["codex"].approval == "never"
    assert by_name["claude"].sandbox is None
    assert by_name["claude"].approval is None


def test_runner_config_parses_sandbox_and_approval_override(tmp_path: Path) -> None:
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
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "codex"\ncommand = ["codex", "exec", "{prompt}"]\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["codex"].sandbox is None
    assert by_name["codex"].approval is None


def test_runner_config_rejects_blank_sandbox(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\nsandbox = "  "\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-empty string"):
        load_runner_config(tmp_path)


def test_runner_config_parses_usage_format(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "myclaude"\n'
        'command = ["myclaude", "-p", "{prompt}"]\nusage_format = "claude-json"\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["myclaude"].usage_format == "claude-json"


def test_runner_config_usage_format_defaults_none_for_override(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "claude"\ncommand = ["claude", "-p", "{prompt}"]\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["claude"].usage_format is None


def test_runner_config_copilot_session_store_defaults_none(tmp_path: Path) -> None:
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["copilot"].session_store is None


def test_runner_config_parses_copilot_session_store(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[runner]\ncopilot_session_store = "/opt/copilot/session-state"\n', encoding="utf-8"
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["copilot"].session_store == Path("/opt/copilot/session-state")
    assert by_name["claude"].session_store is None


def test_local_config_overrides_copilot_session_store(tmp_path: Path) -> None:

    (tmp_path / CONFIG_FILE).write_text(
        '[runner]\ncopilot_session_store = "shared/store"\n', encoding="utf-8"
    )
    (tmp_path / LOCAL_CONFIG_FILE).write_text(
        '[runner]\ncopilot_session_store = "~/.copilot/session-state"\n', encoding="utf-8"
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["copilot"].session_store == Path("~/.copilot/session-state")


def test_runner_config_ignores_a_blank_copilot_session_store(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[runner]\ncopilot_session_store = "   "\n', encoding="utf-8"
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["copilot"].session_store is None


def test_runner_config_parses_deny_style(tmp_path: Path) -> None:

    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "myclaude"\n'
        'command = ["myclaude", "-p", "{prompt}"]\ndeny_style = "disallowed-tools"\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    spec = by_name["myclaude"]
    assert spec.deny_style == "disallowed-tools"
    confined = runner.confine_for_decider(spec)
    assert confined is not None and "Bash" in confined.deny_tools


def test_runner_config_rejects_unknown_deny_style(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\ndeny_style = "bogus"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="deny_style"):
        load_runner_config(tmp_path)


def test_runner_config_rejects_unknown_usage_format(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\nusage_format = "bogus"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="usage_format"):
        load_runner_config(tmp_path)


def test_runner_config_overrides_builtin_command(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "claude"\ncommand = ["claude", "--print", "{prompt}"]\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["claude"].command == ("claude", "--print", "{prompt}")


def test_runner_config_parses_bot_git_identity(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "bot"\ncommand = ["bot", "{prompt}"]\n'
        'git_name = "basicly-bot"\ngit_email = "bot@example.com"\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["bot"].git_name == "basicly-bot"
    assert by_name["bot"].git_email == "bot@example.com"


def test_runner_config_bot_identity_defaults_none(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\n', encoding="utf-8"
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["x"].git_name is None
    assert by_name["x"].git_email is None


def test_runner_config_rejects_lone_git_identity_half(tmp_path: Path) -> None:
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
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\n'
        'git_name = "  "\ngit_email = "b@example.com"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-empty string"):
        load_runner_config(tmp_path)


def test_runner_config_rejects_malformed_agent(tmp_path: Path) -> None:
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
    assert worktree.base_branch == "develop"
    assert load_policy_config(tmp_path).max_rework == 1
    assert load_runner_config(tmp_path).default == "manual"


def test_local_config_alone_configures_harness(tmp_path: Path) -> None:
    (tmp_path / LOCAL_CONFIG_FILE).write_text(
        '[runner]\ndefault = "manual"\n[worktree]\nconcurrency = 1\n',
        encoding="utf-8",
    )
    assert load_runner_config(tmp_path).default == "manual"
    assert load_worktree_config(tmp_path).concurrency == 1


def test_local_config_replaces_verify_checks_wholesale(tmp_path: Path) -> None:
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
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    (tmp_path / LOCAL_CONFIG_FILE).write_text(
        '[paths]\ncore_fragments = "elsewhere/fragments"\n[catalog]\ntechnologies = ["python"]\n',
        encoding="utf-8",
    )
    assert load_project_paths(tmp_path).core_fragments_dir == Path(".basicly/core/fragments")
    assert load_technology_selection(tmp_path) is None


def test_sizing_config_defaults_when_absent(tmp_path: Path) -> None:
    sizing = load_sizing_config(tmp_path)
    assert sizing.working_set_min == DEFAULT_WORKING_SET_MIN
    assert sizing.working_set_max == DEFAULT_WORKING_SET_MAX
    assert sizing.build_factors == {"task": 3.0, "bug": 2.0, "chore": 1.5}
    assert sizing.calibration_min_samples == 10
    assert sizing.calibration_window == 50


def test_sizing_config_parses_overrides(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        "[policy.sizing]\n"
        "working_set_min = 4000\nworking_set_max = 32000\n"
        "calibration_min_samples = 5\ncalibration_window = 20\n"
        "[policy.sizing.build_factor]\ntask = 2.5\nspike = 1.0\n",
        encoding="utf-8",
    )
    sizing = load_sizing_config(tmp_path)
    assert (sizing.working_set_min, sizing.working_set_max) == (4_000, 32_000)
    assert (sizing.calibration_min_samples, sizing.calibration_window) == (5, 20)
    assert sizing.build_factors["task"] == 2.5
    assert sizing.build_factors["spike"] == 1.0
    assert sizing.build_factors["bug"] == 2.0
    assert sizing.configured_build_factors == frozenset({"task", "spike"})


def test_sizing_config_inverted_band_falls_back(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        "[policy.sizing]\nworking_set_min = 64000\nworking_set_max = 8000\n",
        encoding="utf-8",
    )
    sizing = load_sizing_config(tmp_path)
    assert (sizing.working_set_min, sizing.working_set_max) == (
        DEFAULT_WORKING_SET_MIN,
        DEFAULT_WORKING_SET_MAX,
    )


def test_sizing_config_ignores_wrong_typed_values(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[policy.sizing]\nworking_set_min = "big"\ncalibration_window = -3\n'
        '[policy.sizing.build_factor]\ntask = "fast"\nbug = 4.0\n',
        encoding="utf-8",
    )
    sizing = load_sizing_config(tmp_path)
    assert sizing.working_set_min == 4_600
    assert sizing.calibration_window == 50
    assert sizing.build_factors["task"] == 3.0
    assert sizing.build_factors["bug"] == 4.0
    assert sizing.configured_build_factors == frozenset({"bug"})


def test_runner_config_parses_context_window(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "claude"\n'
        'command = ["claude", "-p", "{prompt}"]\ncontext_window = 1000000\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["claude"].context_window == 1_000_000


def test_runner_config_context_window_defaults_for_override(tmp_path: Path) -> None:

    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "claude"\ncommand = ["claude", "-p", "{prompt}"]\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["claude"].context_window == 128_000


def test_runner_config_rejects_malformed_context_window(tmp_path: Path) -> None:
    for value in ('"big"', "0", "true"):
        (tmp_path / CONFIG_FILE).write_text(
            f'[[runner.agents]]\nname = "x"\ncommand = ["x", "{{prompt}}"]\n'
            f"context_window = {value}\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="context_window"):
            load_runner_config(tmp_path)


def test_context_windows_declares_a_builtin_window_without_restating_the_adapter(
    tmp_path: Path,
) -> None:

    (tmp_path / CONFIG_FILE).write_text(
        "[runner.context_windows]\nclaude = 1000000\n", encoding="utf-8"
    )

    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}

    assert by_name["claude"].context_window == 1_000_000
    assert by_name["claude"].context_window_source == DECLARED_WINDOW
    assert by_name["claude"].usage_format == "claude-stream-json"
    assert by_name["claude"].command == ("claude", "-p", "{prompt}")
    assert by_name["codex"].context_window_source == FALLBACK_WINDOW


def test_context_windows_rejects_an_agent_it_cannot_name(tmp_path: Path) -> None:

    (tmp_path / CONFIG_FILE).write_text(
        "[runner.context_windows]\nclaud = 1000000\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unknown agent 'claud'"):
        load_runner_config(tmp_path)


def test_context_windows_rejects_a_window_that_is_not_a_count_of_tokens(
    tmp_path: Path,
) -> None:
    for value in ('"1m"', "0", "-1", "true"):
        (tmp_path / CONFIG_FILE).write_text(
            f"[runner.context_windows]\nclaude = {value}\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="context_windows"):
            load_runner_config(tmp_path)


def test_an_agent_entry_declaring_its_own_window_records_that_it_declared_one(
    tmp_path: Path,
) -> None:

    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "a"\ncommand = ["a", "{prompt}"]\ncontext_window = 300000\n'
        '[[runner.agents]]\nname = "b"\ncommand = ["b", "{prompt}"]\n',
        encoding="utf-8",
    )

    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}

    assert (by_name["a"].context_window, by_name["a"].context_window_source) == (
        300_000,
        AGENT_WINDOW,
    )
    assert (by_name["b"].context_window, by_name["b"].context_window_source) == (
        128_000,
        FALLBACK_WINDOW,
    )


def test_sizing_config_context_ceiling_defaults_and_overrides(tmp_path: Path) -> None:
    assert load_sizing_config(tmp_path).context_ceiling == 0.6
    (tmp_path / CONFIG_FILE).write_text(
        "[policy.sizing]\ncontext_ceiling = 0.5\n", encoding="utf-8"
    )
    assert load_sizing_config(tmp_path).context_ceiling == 0.5


def test_sizing_config_context_ceiling_unusable_values_fall_back(tmp_path: Path) -> None:
    for value in ("0", "-0.2", "1.5", "true"):
        (tmp_path / CONFIG_FILE).write_text(
            f"[policy.sizing]\ncontext_ceiling = {value}\n", encoding="utf-8"
        )
        assert load_sizing_config(tmp_path).context_ceiling == 0.6


def test_policy_config_autonomy_defaults_to_l0(tmp_path: Path) -> None:
    assert load_policy_config(tmp_path).autonomy == "L0"


def test_policy_config_autonomy_parses_valid_levels(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text('[policy]\nautonomy = "L2"\n', encoding="utf-8")
    assert load_policy_config(tmp_path).autonomy == "L2"


def test_policy_config_autonomy_unknown_value_falls_back(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text('[policy]\nautonomy = "L9"\n', encoding="utf-8")
    assert load_policy_config(tmp_path).autonomy == "L0"


def test_policy_config_scope_collision_defaults_to_block(tmp_path: Path) -> None:
    assert load_policy_config(tmp_path).scope_collision == "block"


def test_policy_config_scope_collision_parses_warn(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text('[policy]\nscope_collision = "warn"\n', encoding="utf-8")
    assert load_policy_config(tmp_path).scope_collision == "warn"


def test_policy_config_scope_collision_unknown_value_falls_back(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text('[policy]\nscope_collision = "shrug"\n', encoding="utf-8")
    assert load_policy_config(tmp_path).scope_collision == "block"


def test_policy_config_notify_command_parses_argv_list(tmp_path: Path) -> None:
    assert load_policy_config(tmp_path).notify_command == ()
    (tmp_path / CONFIG_FILE).write_text(
        '[policy]\nnotify_command = ["notify-send", "basicly"]\n', encoding="utf-8"
    )
    assert load_policy_config(tmp_path).notify_command == ("notify-send", "basicly")
    (tmp_path / CONFIG_FILE).write_text(
        '[policy]\nnotify_command = "notify-send basicly"\n', encoding="utf-8"
    )
    assert load_policy_config(tmp_path).notify_command == ()


def test_policy_config_decider_max_decisions(tmp_path: Path) -> None:
    assert load_policy_config(tmp_path).decider_max_decisions == 50
    (tmp_path / CONFIG_FILE).write_text("[policy]\ndecider_max_decisions = 5\n", encoding="utf-8")
    assert load_policy_config(tmp_path).decider_max_decisions == 5


def test_policy_config_max_subtasks_per_lane(tmp_path: Path) -> None:
    assert load_policy_config(tmp_path).max_subtasks_per_lane == 10
    (tmp_path / CONFIG_FILE).write_text("[policy]\nmax_subtasks_per_lane = 3\n", encoding="utf-8")
    assert load_policy_config(tmp_path).max_subtasks_per_lane == 3
    (tmp_path / CONFIG_FILE).write_text("[policy]\nmax_subtasks_per_lane = 0\n", encoding="utf-8")
    assert load_policy_config(tmp_path).max_subtasks_per_lane == 10


def test_runner_config_decider_selection(tmp_path: Path) -> None:
    assert load_runner_config(tmp_path).decider is None
    (tmp_path / CONFIG_FILE).write_text('[runner]\ndecider = "claude"\n', encoding="utf-8")
    assert load_runner_config(tmp_path).decider == "claude"


def test_runner_config_stall_after(tmp_path: Path) -> None:
    assert load_runner_config(tmp_path).stall_after == DEFAULT_STALL_AFTER
    (tmp_path / CONFIG_FILE).write_text("[runner]\nstall_after = 120\n", encoding="utf-8")
    assert load_runner_config(tmp_path).stall_after == 120.0
    (tmp_path / CONFIG_FILE).write_text("[runner]\nstall_after = 0.5\n", encoding="utf-8")
    assert load_runner_config(tmp_path).stall_after == 0.5
    (tmp_path / CONFIG_FILE).write_text("[runner]\nstall_after = -1\n", encoding="utf-8")
    assert load_runner_config(tmp_path).stall_after == DEFAULT_STALL_AFTER
    (tmp_path / CONFIG_FILE).write_text("[runner]\nstall_after = true\n", encoding="utf-8")
    assert load_runner_config(tmp_path).stall_after == DEFAULT_STALL_AFTER


def test_runner_config_quiet_after(tmp_path: Path) -> None:

    assert load_runner_config(tmp_path).quiet_after == DEFAULT_QUIET_AFTER
    (tmp_path / CONFIG_FILE).write_text("[runner]\nquiet_after = 2400\n", encoding="utf-8")
    assert load_runner_config(tmp_path).quiet_after == 2400.0
    (tmp_path / CONFIG_FILE).write_text("[runner]\nquiet_after = 0.5\n", encoding="utf-8")
    assert load_runner_config(tmp_path).quiet_after == 0.5
    (tmp_path / CONFIG_FILE).write_text("[runner]\nquiet_after = -1\n", encoding="utf-8")
    assert load_runner_config(tmp_path).quiet_after == DEFAULT_QUIET_AFTER
    (tmp_path / CONFIG_FILE).write_text("[runner]\nquiet_after = true\n", encoding="utf-8")
    assert load_runner_config(tmp_path).quiet_after == DEFAULT_QUIET_AFTER


def test_runner_config_max_agent_processes(tmp_path: Path) -> None:
    assert load_runner_config(tmp_path).max_agent_processes == DEFAULT_MAX_AGENT_PROCESSES
    (tmp_path / CONFIG_FILE).write_text("[runner]\nmax_agent_processes = 16\n", encoding="utf-8")
    assert load_runner_config(tmp_path).max_agent_processes == 16
    (tmp_path / CONFIG_FILE).write_text("[runner]\nmax_agent_processes = 0\n", encoding="utf-8")
    assert load_runner_config(tmp_path).max_agent_processes == DEFAULT_MAX_AGENT_PROCESSES
    (tmp_path / CONFIG_FILE).write_text(
        '[runner]\nmax_agent_processes = "lots"\n', encoding="utf-8"
    )
    assert load_runner_config(tmp_path).max_agent_processes == DEFAULT_MAX_AGENT_PROCESSES


def test_runner_config_runner_timeout(tmp_path: Path) -> None:
    assert load_runner_config(tmp_path).runner_timeout == 3600.0
    (tmp_path / CONFIG_FILE).write_text("[runner]\nrunner_timeout = 120\n", encoding="utf-8")
    assert load_runner_config(tmp_path).runner_timeout == 120.0
    (tmp_path / CONFIG_FILE).write_text("[runner]\nrunner_timeout = -5\n", encoding="utf-8")
    assert load_runner_config(tmp_path).runner_timeout == 3600.0


def test_policy_evidence_is_empty_by_default(tmp_path: Path) -> None:
    assert load_policy_config(tmp_path).evidence == {}


def test_policy_evidence_declarations_parse_per_phase(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[policy.evidence]\nverify = ".basicly/evidence/verify.log"\nbuild = "build.log"\n',
        encoding="utf-8",
    )
    assert load_policy_config(tmp_path).evidence == {
        "verify": ".basicly/evidence/verify.log",
        "build": "build.log",
    }


def test_policy_evidence_keeps_a_value_it_cannot_make_sense_of(tmp_path: Path) -> None:

    (tmp_path / CONFIG_FILE).write_text("[policy.evidence]\nverify = 3\n", encoding="utf-8")
    assert load_policy_config(tmp_path).evidence == {"verify": "3"}


def test_policy_evidence_ignores_a_non_table_section(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text('[policy]\nevidence = "x"\n', encoding="utf-8")
    assert load_policy_config(tmp_path).evidence == {}


def test_policy_evidence_is_overridable_by_the_local_overlay(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[policy.evidence]\nverify = "shared.log"\n', encoding="utf-8"
    )
    (tmp_path / LOCAL_CONFIG_FILE).write_text(
        '[policy.evidence]\nverify = "local.log"\n', encoding="utf-8"
    )
    assert load_policy_config(tmp_path).evidence == {"verify": "local.log"}


_REPO_ROOT = Path(__file__).parent.parent

_CONFIG_KEY_READS = re.compile(r'\.get\(\s*"([a-z_]+)"|_parse_path_value\(paths,\s*"([a-z_]+)"')


def _schema_names() -> set[str]:
    names: set[str] = set()

    def walk(table: object) -> None:
        names.update(table.keys)  # type: ignore[attr-defined]
        for child, sub in {**table.tables, **table.arrays}.items():  # type: ignore[attr-defined]
            names.add(child)
            walk(sub)

    names.update(CONFIG_SCHEMA)
    for section in CONFIG_SCHEMA.values():
        walk(section)
    return names


def test_an_unknown_section_fails_and_names_the_section_that_accepts_its_key(
    tmp_path: Path,
) -> None:

    (tmp_path / LOCAL_CONFIG_FILE).write_text("[loop]\nconcurrency = 2\n", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_worktree_config(tmp_path)

    message = str(excinfo.value)
    assert LOCAL_CONFIG_FILE in message
    assert "unknown section 'loop'" in message
    assert "'concurrency' is accepted in [worktree]" in message


def test_an_unknown_key_fails_and_names_what_its_section_accepts(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text("[worktree]\nconcurency = 2\n", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_worktree_config(tmp_path)

    assert "unknown key 'concurency' in [worktree]" in str(excinfo.value)
    assert "[worktree] accepts append_only_paths, base_branch, concurrency" in str(excinfo.value)


def test_an_unknown_name_in_a_nested_table_fails(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        "[policy.sizing]\nworking_set_ceiling = 99\n", encoding="utf-8"
    )

    with pytest.raises(ValueError) as excinfo:
        load_sizing_config(tmp_path)

    assert "unknown key 'working_set_ceiling' in [policy.sizing]" in str(excinfo.value)


def test_an_unknown_name_in_an_array_of_tables_fails(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x"]\nmodel_id = "y"\n', encoding="utf-8"
    )

    with pytest.raises(ValueError) as excinfo:
        load_runner_config(tmp_path)

    assert "unknown key 'model_id' in [runner.agents]" in str(excinfo.value)


def test_the_refusal_records_the_forward_compatibility_stance(tmp_path: Path) -> None:

    (tmp_path / CONFIG_FILE).write_text("[worktree]\nfrom_the_future = 1\n", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_worktree_config(tmp_path)

    assert basicly.__version__ in str(excinfo.value)
    assert "upgrade basicly if it comes from a newer version" in str(excinfo.value)


def test_a_malformed_overlay_fails_the_loaders_that_never_read_it(tmp_path: Path) -> None:

    (tmp_path / CONFIG_FILE).write_text('[catalog]\ntechnologies = ["python"]\n', encoding="utf-8")
    (tmp_path / LOCAL_CONFIG_FILE).write_text("[worktree]\ntypo = 1\n", encoding="utf-8")

    for load in (load_project_paths, load_technology_selection):
        with pytest.raises(ValueError, match="unknown key 'typo'"):
            load(tmp_path)


def test_every_declared_name_is_reported_not_just_the_first(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        "[worktree]\ntypo_one = 1\n\n[policy]\ntypo_two = 2\n", encoding="utf-8"
    )

    problems = unknown_config_keys(tmp_path)

    assert len(problems) == 2
    assert any("'typo_one'" in problem for problem in problems)
    assert any("'typo_two'" in problem for problem in problems)


def test_consumer_chosen_keys_in_an_open_table_are_accepted(tmp_path: Path) -> None:

    (tmp_path / CONFIG_FILE).write_text(
        "[runner.context_windows]\nclaude = 1000\n\n"
        '[policy.evidence]\nverify = "v.log"\n\n'
        "[policy.sizing.build_factor]\nchore = 1.5\n",
        encoding="utf-8",
    )

    assert unknown_config_keys(tmp_path) == []


def test_the_privacy_denylist_only_a_hook_reads_is_accepted(tmp_path: Path) -> None:

    (tmp_path / LOCAL_CONFIG_FILE).write_text(
        '[[privacy.denied]]\nname = "corp-domain"\ntoken = "internal.example"\n',
        encoding="utf-8",
    )

    assert unknown_config_keys(tmp_path) == []


def test_the_shipped_scaffold_declares_only_recognised_names(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")

    assert unknown_config_keys(tmp_path) == []


def test_this_repos_own_config_declares_only_recognised_names() -> None:
    assert unknown_config_keys(_REPO_ROOT) == []


def test_every_config_key_a_loader_reads_is_in_the_schema() -> None:

    source = (_REPO_ROOT / "src" / "basicly" / "config.py").read_text(encoding="utf-8")
    read = {name or fallback for name, fallback in _CONFIG_KEY_READS.findall(source)}
    read |= set(re.findall(r'_harness_section\(repo_root,\s*"([a-z_]+)"\)', source))

    assert read - _schema_names() == set()


_RATCHET_GATE_ROOTS = (Path(".scripts"), Path("src") / "basicly")


def _module_string_constants(module: ast.Module) -> dict[str, str]:
    bound: dict[str, str] = {}
    for statement in module.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        match statement.targets[0], statement.value:
            case ast.Name(id=name), ast.Constant(value=str() as text):
                bound[name] = text
    return bound


def _gate_argument(call: ast.Call, bound: dict[str, str]) -> str | None:
    match call.args[1] if len(call.args) > 1 else None:
        case ast.Constant(value=str() as gate):
            return gate
        case ast.Name(id=name):
            return bound.get(name)
        case _:
            return None


def _composed_ratchet_gates() -> dict[str, str]:

    found: dict[str, str] = {}
    for root in _RATCHET_GATE_ROOTS:
        for path in sorted((_REPO_ROOT / root).glob("*.py")):
            module = ast.parse(path.read_text(encoding="utf-8"))
            bound = _module_string_constants(module)
            for node in ast.walk(module):
                if not isinstance(node, ast.Call):
                    continue
                match node.func:
                    case ast.Name(id="compose_ratchet") | ast.Attribute(attr="compose_ratchet"):
                        gate = _gate_argument(node, bound)
                    case _:
                        continue
                if gate is not None:
                    found.setdefault(gate, (root / path.name).as_posix())
    return found


def test_ratchet_sections_register_every_gate_that_composes_one() -> None:

    composed = _composed_ratchet_gates()
    assert {"module_size", "noqa_debt"} <= set(composed), composed

    accepted = set(CONFIG_SCHEMA[dropin.RATCHET_SECTION].tables)
    missing = {gate: where for gate, where in sorted(composed.items()) if gate not in accepted}

    assert not missing, "\n".join(
        f"{where} composes [{dropin.RATCHET_SECTION}.{gate}], so src/basicly/config.py must "
        f"declare {gate!r} in CONFIG_SCHEMA[{dropin.RATCHET_SECTION!r}].tables"
        for gate, where in missing.items()
    )


_ENGINE_SOURCE = Path("src") / "basicly" / "config.py"

_SCHEMA_ANCHOR = "CONFIG_SCHEMA: dict[str, Table] = {"


def _engine_tree(root: Path, source: str) -> Path:
    engine = root / _ENGINE_SOURCE
    engine.parent.mkdir(parents=True, exist_ok=True)
    engine.write_text(source, encoding="utf-8")
    return engine


def _lane_source(added: str) -> str:
    source = (REPO_ROOT / _ENGINE_SOURCE).read_text(encoding="utf-8")
    assert _SCHEMA_ANCHOR in source, "CONFIG_SCHEMA is no longer declared as a dict literal"
    grafted = source.replace(
        _SCHEMA_ANCHOR,
        f'{_SCHEMA_ANCHOR}\n    "lane": Table(keys=frozenset({{"{added}"}})),',
        1,
    )
    assert grafted != source
    return grafted


def test_a_lane_adding_a_schema_entry_may_declare_it_in_the_same_commit(tmp_path: Path) -> None:

    _engine_tree(tmp_path, _lane_source("added_by_the_lane"))
    (tmp_path / CONFIG_FILE).write_text("[lane]\nadded_by_the_lane = 1\n", encoding="utf-8")
    documents = config._config_documents(tmp_path)

    assert config._problems(documents, config._ROOT_TABLE), "fixture no longer reproduces the bug"
    assert unknown_config_keys(tmp_path) == []


def test_the_landing_can_load_a_lane_config_the_pre_merge_engine_cannot_honour(
    tmp_path: Path,
) -> None:

    _engine_tree(tmp_path, _lane_source("added_by_the_lane"))
    (tmp_path / CONFIG_FILE).write_text(
        "[lane]\nadded_by_the_lane = 1\n\n[[verify.checks]]\n"
        'name = "unit"\ncommand = ["true"]\nmodes = ["fast", "full"]\n',
        encoding="utf-8",
    )

    assert [check.name for check in load_verify_config(tmp_path).checks] == ["unit"]


def test_a_typo_is_still_refused_in_a_tree_that_ships_the_engine(tmp_path: Path) -> None:

    _engine_tree(tmp_path, (REPO_ROOT / _ENGINE_SOURCE).read_text(encoding="utf-8"))
    (tmp_path / CONFIG_FILE).write_text("[worktree]\nconcurency = 2\n", encoding="utf-8")

    problems = unknown_config_keys(tmp_path)

    assert len(problems) == 1
    assert "unknown key 'concurency' in [worktree]" in problems[0]


def test_a_lane_declaring_a_key_its_own_schema_lacks_is_refused(tmp_path: Path) -> None:

    _engine_tree(tmp_path, _lane_source("added_by_the_lane"))
    (tmp_path / CONFIG_FILE).write_text(
        "[lane]\nadded_by_the_lane = 1\nadded_by_nobody = 2\n", encoding="utf-8"
    )

    problems = unknown_config_keys(tmp_path)

    assert len(problems) == 1
    assert "unknown key 'added_by_nobody' in [lane]" in problems[0]


def test_an_unreadable_tree_schema_falls_back_and_names_the_ordering_rule(
    tmp_path: Path,
) -> None:

    _engine_tree(tmp_path, "CONFIG_SCHEMA = build_schema()\n")
    (tmp_path / CONFIG_FILE).write_text("[worktree]\nappend_only = 1\n", encoding="utf-8")

    with pytest.raises(ValueError) as raised:
        load_worktree_config(tmp_path)

    assert "land the schema change first" in str(raised.value)


def test_a_consumer_repo_refusal_does_not_mention_the_ordering_rule(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text("[worktree]\nappend_only = 1\n", encoding="utf-8")

    with pytest.raises(ValueError) as raised:
        load_worktree_config(tmp_path)

    assert "land the schema change first" not in str(raised.value)


def test_the_tree_schema_is_reread_when_the_tree_changes(tmp_path: Path) -> None:

    (tmp_path / CONFIG_FILE).write_text("[lane]\nadded_late = 1\n", encoding="utf-8")
    _engine_tree(tmp_path, (REPO_ROOT / _ENGINE_SOURCE).read_text(encoding="utf-8"))
    assert unknown_config_keys(tmp_path) != []

    _engine_tree(tmp_path, _lane_source("added_late"))

    assert unknown_config_keys(tmp_path) == []
