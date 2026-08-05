"""Tests for project path configuration."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

import basicly
from basicly import permissions, run_record, runner
from basicly.config import (
    CONFIG_FILE,
    CONFIG_SCHEMA,
    DEFAULT_CONFIG_TOML,
    DEFAULT_MAX_AGENT_PROCESSES,
    DEFAULT_STALL_AFTER,
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
    load_verify_config,
    load_worktree_config,
    record_technology_selection,
    unknown_config_keys,
    untiered_metered_runners,
)
from basicly.runner import (
    ADAPTER_WINDOW,
    AGENT_TIER,
    AGENT_WINDOW,
    BUILTIN_RUNNERS,
    DECLARED_WINDOW,
    FALLBACK_WINDOW,
    FAMILY_DEFAULT_TIER,
)

# This repo's own checkout: the subject of the declaration gates below, which assert
# on the config and ledger it actually ships rather than on a fixture.
REPO_ROOT = Path(__file__).resolve().parents[1]


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
    assert load_worktree_config(tmp_path) == WorktreeConfig(
        base_branch=None, concurrency=DEFAULT_WORKTREE_CONCURRENCY
    )


def test_default_config_toml_worktree_matches_defaults(tmp_path: Path) -> None:
    """The scaffolded [worktree] section resolves to the built-in defaults."""
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    assert load_worktree_config(tmp_path) == WorktreeConfig(
        base_branch=None, concurrency=DEFAULT_WORKTREE_CONCURRENCY
    )


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
    assert load_worktree_config(tmp_path).concurrency == DEFAULT_WORKTREE_CONCURRENCY


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


def test_verify_config_reads_the_optional_fix_command(tmp_path: Path) -> None:
    """A check may declare a mechanical repair; absent means there is none."""
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
    """A fix_command that is not a list of strings is a loud error."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[verify.checks]]\nname = "x"\ncommand = ["true"]\n'
        'fix_command = "true"\nmodes = ["fast"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="'fix_command'"):
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


def test_runner_config_parses_a_model_tier_and_vendor(tmp_path: Path) -> None:
    """An entry may declare a portable tier instead of a provider model id."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "copilot"\n'
        'command = ["copilot", "-p", "{prompt}"]\ntier = "high"\nvendor = "openai"\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["copilot"].tier == "high"
    assert by_name["copilot"].vendor == "openai"
    assert by_name["copilot"].model is None  # a tier is not a pin


def test_runner_config_rejects_an_unknown_model_tier(tmp_path: Path) -> None:
    """Caught at load, not at dispatch — a typo must not surface mid-lane."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\ntier = "ludicrous"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown model tier"):
        load_runner_config(tmp_path)


def test_runner_config_rejects_an_unknown_default_tier(tmp_path: Path) -> None:
    """The family fallback is validated on the same terms as a per-agent tier."""
    (tmp_path / CONFIG_FILE).write_text(
        '[runner]\ndefault_tier = "turbo"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not a known model tier"):
        load_runner_config(tmp_path)


def test_runner_config_default_tier_is_absent_by_default(tmp_path: Path) -> None:
    """An unconfigured repo implies no tier, so nothing starts getting pinned."""
    (tmp_path / CONFIG_FILE).write_text("[runner]\n", encoding="utf-8")
    config = load_runner_config(tmp_path)
    assert config.default_tier is None
    assert all(spec.tier is None for spec in config.specs)


def test_a_default_tier_lands_on_every_spec_that_declares_none(tmp_path: Path) -> None:
    """Defaulting happens on the spec, so every dispatch site honours it for free.

    A dispatch site added later cannot forget to thread a parameter that does not
    exist, which is why this is applied at load rather than passed to run().
    """
    (tmp_path / CONFIG_FILE).write_text(
        '[runner]\ndefault_tier = "medium"\n'
        '[[runner.agents]]\nname = "pinned"\n'
        'command = ["pinned", "{prompt}"]\nmodel = "opus"\n'
        '[[runner.agents]]\nname = "tiered"\n'
        'command = ["tiered", "{prompt}"]\ntier = "maximum"\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}

    # A built-in that declares nothing inherits the default, labelled as such.
    assert by_name["claude"].tier == "medium"
    assert by_name["claude"].tier_source == FAMILY_DEFAULT_TIER
    # Its own tier wins, and keeps its own provenance.
    assert by_name["tiered"].tier == "maximum"
    assert by_name["tiered"].tier_source == AGENT_TIER
    # An explicit model pin is left alone: the pin wins, so a tier here would only
    # misreport where the model came from.
    assert by_name["pinned"].tier is None


# --- The tier declaration this repo has to keep (basicly-tcmy.35) -------------

# The model this repo's lanes were observed running before anything pinned one, read
# off `observed_models` on the committed ledger. Held here as the value the declared
# tier has to keep resolving to, so a tier change that quietly moves the work onto
# another model fails instead of being re-recorded as the new normal.
_OBSERVED_LANE_MODEL = "claude-opus-5"


def _repo_runner_specs() -> dict[str, runner.RunnerSpec]:
    return {spec.name: spec for spec in load_runner_config(REPO_ROOT).specs}


def test_every_metered_runner_in_this_repo_resolves_a_model_it_can_name() -> None:
    """The live gate, asserted on this repo's own config rather than a fixture.

    basicly-tcmy.35 was not a defect in the resolver: it refuses an unresolvable tier
    before spawning anything, and it is correct. The defect was the *input*. Nothing
    here declared a tier, the refusal fires only when one was declared, and so 48
    metered dispatches recorded an exact 313.30 USD of spend against no model at all
    — with every existing gate green, because an absent tier reads exactly like a
    deliberate no-tier choice. This asserts the input is present and resolves.
    """
    assert untiered_metered_runners(load_runner_config(REPO_ROOT), repo_root=REPO_ROOT) == []


def test_the_gate_names_the_key_when_nothing_declares_a_tier(tmp_path: Path) -> None:
    """The known-bad control: the config this repo had until basicly-tcmy.35.

    Without it the gate above cannot be told apart from one that has no way to fail.
    Every headless built-in is reported and each names `[runner] default_tier` — the
    single line that fixes all of them — while the handoff runner is not swept in
    with them: it spawns nothing, spends nothing, and has no argv to pin a model
    onto, so flagging it would train the reader to ignore the check.
    """
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
    """The declaration records what already happens; it does not choose something new.

    `high` resolves through the committed map to the model every dispatch that
    reported one was observed running, so the work stays on that model and what
    changes is only that the record can name it: the tier, where the tier came from,
    and that it was honoured.
    """
    resolution = runner.resolve_model(_repo_runner_specs()["claude"], repo_root=REPO_ROOT)

    assert resolution.tier == "high"
    assert resolution.source == FAMILY_DEFAULT_TIER
    assert resolution.honoured
    assert resolution.model == _OBSERVED_LANE_MODEL


def test_the_ledger_holds_the_metered_dispatches_that_named_no_model() -> None:
    """The positive control on the population that filed basicly-tcmy.35.

    Read off the committed tracker, so this is the evidence a fresh clone has (D11)
    rather than a local usage file. Two facts, both load-bearing: dispatches carrying
    a recorded cost exist at all, without which everything after the first assertion
    is a filter over an empty list and passes vacuously; and on those records the
    model that spent the money is recoverable only from what was *observed*, which is
    the fact a declared tier turns into something the record states about itself.
    """
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
    assert by_name["codex"].approval == "never"
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


def test_runner_config_parses_usage_format(tmp_path: Path) -> None:
    """An [[runner.agents]] entry may set usage_format (basicly-kjc5.1); it lands on the spec."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "myclaude"\n'
        'command = ["myclaude", "-p", "{prompt}"]\nusage_format = "claude-json"\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["myclaude"].usage_format == "claude-json"


def test_runner_config_usage_format_defaults_none_for_override(tmp_path: Path) -> None:
    """An override that omits usage_format is not re-defaulted to the builtin's value."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "claude"\ncommand = ["claude", "-p", "{prompt}"]\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["claude"].usage_format is None


def test_runner_config_copilot_session_store_defaults_none(tmp_path: Path) -> None:
    """Unset leaves the home-relative default, so no machine path is ever committed."""
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["copilot"].session_store is None


def test_runner_config_parses_copilot_session_store(tmp_path: Path) -> None:
    """[runner] copilot_session_store redirects where measured usage is read from."""
    (tmp_path / CONFIG_FILE).write_text(
        '[runner]\ncopilot_session_store = "/opt/copilot/session-state"\n', encoding="utf-8"
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["copilot"].session_store == Path("/opt/copilot/session-state")
    assert by_name["claude"].session_store is None


def test_local_config_overrides_copilot_session_store(tmp_path: Path) -> None:
    """The gitignored overlay wins — the only place a machine-specific store path belongs.

    This is why the key sits under [runner] and not [paths]: projection config
    deliberately never reads the overlay, so a [paths] key could not be set
    per-machine at all.
    """
    (tmp_path / CONFIG_FILE).write_text(
        '[runner]\ncopilot_session_store = "shared/store"\n', encoding="utf-8"
    )
    (tmp_path / LOCAL_CONFIG_FILE).write_text(
        '[runner]\ncopilot_session_store = "~/.copilot/session-state"\n', encoding="utf-8"
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    # Left unexpanded on purpose: the reader expands it, so `~` stays portable.
    assert by_name["copilot"].session_store == Path("~/.copilot/session-state")


def test_runner_config_ignores_a_blank_copilot_session_store(tmp_path: Path) -> None:
    """A blank value is not a path; it falls back to the default rather than to cwd."""
    (tmp_path / CONFIG_FILE).write_text(
        '[runner]\ncopilot_session_store = "   "\n', encoding="utf-8"
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["copilot"].session_store is None


def test_runner_config_parses_deny_style(tmp_path: Path) -> None:
    """A custom agent may declare its family's deny wire form (basicly-kjc5.16).

    This is what keeps the decider usable behind a wrapper: with no deny_style the
    confinement overlay has no flag to emit, so invoke_decider refuses to dispatch.
    """
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
    """An unknown deny_style is a config error, not a flag the binary cannot read."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\ndeny_style = "bogus"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="deny_style"):
        load_runner_config(tmp_path)


def test_runner_config_rejects_unknown_usage_format(tmp_path: Path) -> None:
    """An unknown usage_format is a config error, not a silently unmetered adapter."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x", "{prompt}"]\nusage_format = "bogus"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="usage_format"):
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


# --- [policy.sizing] (basicly-kjc5.2, D8) ------------------------------------


def test_sizing_config_defaults_when_absent(tmp_path: Path) -> None:
    """No config file (or no [policy.sizing]) resolves to the D8 defaults."""
    sizing = load_sizing_config(tmp_path)
    # Against the named defaults, not their literal values: the ceiling is derived
    # from measured lane outcomes and moves when the evidence does (basicly-3w44).
    # A literal here asserts only that someone typed the same number twice, and it
    # turns a legitimate recalibration into a test failure in an unrelated file.
    assert sizing.working_set_min == DEFAULT_WORKING_SET_MIN
    assert sizing.working_set_max == DEFAULT_WORKING_SET_MAX
    assert sizing.build_factors == {"task": 3.0, "bug": 2.0, "chore": 1.5}
    assert sizing.calibration_min_samples == 10
    assert sizing.calibration_window == 50


def test_sizing_config_parses_overrides(tmp_path: Path) -> None:
    """[policy.sizing] keys and the build_factor table override the defaults."""
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
    assert sizing.build_factors["bug"] == 2.0  # unset classes keep their seeds
    # Which entries a repo declared, so a dispatch record can say that its factor was
    # declared rather than seeded (basicly-tcmy.5). Provenance is kept here, where it is
    # known, rather than inferred later by comparing the value against the seed.
    assert sizing.configured_build_factors == frozenset({"task", "spike"})


def test_sizing_config_inverted_band_falls_back(tmp_path: Path) -> None:
    """An inverted band would refuse everything, so it falls back to defaults."""
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
    """Wrong-typed keys fall back per key (same stance as the other loaders)."""
    (tmp_path / CONFIG_FILE).write_text(
        '[policy.sizing]\nworking_set_min = "big"\ncalibration_window = -3\n'
        '[policy.sizing.build_factor]\ntask = "fast"\nbug = 4.0\n',
        encoding="utf-8",
    )
    sizing = load_sizing_config(tmp_path)
    assert sizing.working_set_min == 8_000
    assert sizing.calibration_window == 50
    assert sizing.build_factors["task"] == 3.0  # bad value keeps the seed
    assert sizing.build_factors["bug"] == 4.0
    # A rejected entry left the seed in force, so calling it configured would attribute
    # the number in use to a declaration that was never honoured.
    assert sizing.configured_build_factors == frozenset({"bug"})


# --- context_window / context_ceiling (basicly-kjc5.6, D8) --------------------


def test_runner_config_parses_context_window(tmp_path: Path) -> None:
    """An [[runner.agents]] entry may restate its window for the ceiling meter."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "claude"\n'
        'command = ["claude", "-p", "{prompt}"]\ncontext_window = 1000000\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["claude"].context_window == 1_000_000


def test_runner_config_context_window_defaults_for_override(tmp_path: Path) -> None:
    """An override omitting context_window gets the conservative default.

    Not the builtin's value — the same replaces-wholesale stance as
    usage_format.
    """
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "claude"\ncommand = ["claude", "-p", "{prompt}"]\n',
        encoding="utf-8",
    )
    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}
    assert by_name["claude"].context_window == 128_000


def test_runner_config_rejects_malformed_context_window(tmp_path: Path) -> None:
    """A non-integer or non-positive window is a config error, not a silent shrink."""
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
    """AC: a repo declares its window per agent, and the spec records that it did.

    The cheap path has to be the correct one (basicly-23ep). Declaring through
    `[[runner.agents]]` replaces the builtin wholesale, so a consumer who only wanted
    to state a window would also have to restate the command, the usage format and the
    deny style — and a restatement that silently drops one of those is a worse defect
    than the stale window it fixed. So the adapter must survive the declaration intact.
    """
    (tmp_path / CONFIG_FILE).write_text(
        "[runner.context_windows]\nclaude = 1000000\n", encoding="utf-8"
    )

    by_name = {spec.name: spec for spec in load_runner_config(tmp_path).specs}

    assert by_name["claude"].context_window == 1_000_000
    assert by_name["claude"].context_window_source == DECLARED_WINDOW
    # The rest of the builtin adapter is untouched by the declaration.
    assert by_name["claude"].usage_format == "claude-stream-json"
    assert by_name["claude"].command == ("claude", "-p", "{prompt}")
    # And an agent nobody declared still says its window was defaulted, not chosen.
    assert by_name["codex"].context_window_source == ADAPTER_WINDOW


def test_context_windows_rejects_an_agent_it_cannot_name(tmp_path: Path) -> None:
    """A typo must fail loudly — its only other symptom is the default it meant to replace.

    That silence is the defect basicly-23ep is: a window nobody had checked, applied
    because nothing said it had not been applied.
    """
    (tmp_path / CONFIG_FILE).write_text(
        "[runner.context_windows]\nclaud = 1000000\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unknown agent 'claud'"):
        load_runner_config(tmp_path)


def test_context_windows_rejects_a_window_that_is_not_a_count_of_tokens(
    tmp_path: Path,
) -> None:
    """Same stance as the per-agent key: a malformed window is an error, not a shrink."""
    for value in ('"1m"', "0", "-1", "true"):
        (tmp_path / CONFIG_FILE).write_text(
            f"[runner.context_windows]\nclaude = {value}\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="context_windows"):
            load_runner_config(tmp_path)


def test_an_agent_entry_declaring_its_own_window_records_that_it_declared_one(
    tmp_path: Path,
) -> None:
    """The two declaration paths are distinguishable from the defaulted one.

    `context_window` on an entry is a decision; its absence is the conservative
    fallback. Recording which happened is what makes a stale window findable at all —
    without it, a default and a checked figure read identically (basicly-23ep).
    """
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
    """context_ceiling defaults to 0.6 and accepts a fraction in (0, 1]."""
    assert load_sizing_config(tmp_path).context_ceiling == 0.6
    (tmp_path / CONFIG_FILE).write_text(
        "[policy.sizing]\ncontext_ceiling = 0.5\n", encoding="utf-8"
    )
    assert load_sizing_config(tmp_path).context_ceiling == 0.5


def test_sizing_config_context_ceiling_unusable_values_fall_back(tmp_path: Path) -> None:
    """Zero, negative, above-1, or bool ceilings would wedge or disable the meter."""
    for value in ("0", "-0.2", "1.5", "true"):
        (tmp_path / CONFIG_FILE).write_text(
            f"[policy.sizing]\ncontext_ceiling = {value}\n", encoding="utf-8"
        )
        assert load_sizing_config(tmp_path).context_ceiling == 0.6


# --- [policy] autonomy ceiling (basicly-kjc5.3, D3) ---------------------------


def test_policy_config_autonomy_defaults_to_l0(tmp_path: Path) -> None:
    """Factory autonomy is opt-in: the default ceiling keeps grants unissuable."""
    assert load_policy_config(tmp_path).autonomy == "L0"


def test_policy_config_autonomy_parses_valid_levels(tmp_path: Path) -> None:
    """A configured ceiling in the L0-L3 vocabulary lands on the config."""
    (tmp_path / CONFIG_FILE).write_text('[policy]\nautonomy = "L2"\n', encoding="utf-8")
    assert load_policy_config(tmp_path).autonomy == "L2"


def test_policy_config_autonomy_unknown_value_falls_back(tmp_path: Path) -> None:
    """A typo must fail closed to L0, never open autonomy by accident."""
    (tmp_path / CONFIG_FILE).write_text('[policy]\nautonomy = "L9"\n', encoding="utf-8")
    assert load_policy_config(tmp_path).autonomy == "L0"


# --- [policy] scope_collision (basicly-jr0l.44) -------------------------------


def test_policy_config_scope_collision_defaults_to_block(tmp_path: Path) -> None:
    """Deterministic checks are authoritative, so the collision case refuses by default."""
    assert load_policy_config(tmp_path).scope_collision == "block"


def test_policy_config_scope_collision_parses_warn(tmp_path: Path) -> None:
    """A repo that would rather pay the conflict than the rework cycle can opt out."""
    (tmp_path / CONFIG_FILE).write_text('[policy]\nscope_collision = "warn"\n', encoding="utf-8")
    assert load_policy_config(tmp_path).scope_collision == "warn"


def test_policy_config_scope_collision_unknown_value_falls_back(tmp_path: Path) -> None:
    """A typo lands on the default; the evidence half is recorded whatever it says."""
    (tmp_path / CONFIG_FILE).write_text('[policy]\nscope_collision = "shrug"\n', encoding="utf-8")
    assert load_policy_config(tmp_path).scope_collision == "block"


# --- decision-queue knobs (basicly-kjc5.4, design 7.1/7.3/§6) ------------------


def test_policy_config_notify_command_parses_argv_list(tmp_path: Path) -> None:
    """notify_command is an argv list; disabled by default; malformed fails closed."""
    assert load_policy_config(tmp_path).notify_command == ()
    (tmp_path / CONFIG_FILE).write_text(
        '[policy]\nnotify_command = ["notify-send", "basicly"]\n', encoding="utf-8"
    )
    assert load_policy_config(tmp_path).notify_command == ("notify-send", "basicly")
    (tmp_path / CONFIG_FILE).write_text(
        '[policy]\nnotify_command = "notify-send basicly"\n', encoding="utf-8"
    )
    assert load_policy_config(tmp_path).notify_command == ()  # a string is not an argv


def test_policy_config_decider_max_decisions(tmp_path: Path) -> None:
    """The runaway-loop guard defaults to 50 and takes positive overrides."""
    assert load_policy_config(tmp_path).decider_max_decisions == 50
    (tmp_path / CONFIG_FILE).write_text("[policy]\ndecider_max_decisions = 5\n", encoding="utf-8")
    assert load_policy_config(tmp_path).decider_max_decisions == 5


def test_policy_config_max_subtasks_per_lane(tmp_path: Path) -> None:
    """The lane sub-task bound defaults to 10; positive overrides land, junk falls back."""
    assert load_policy_config(tmp_path).max_subtasks_per_lane == 10
    (tmp_path / CONFIG_FILE).write_text("[policy]\nmax_subtasks_per_lane = 3\n", encoding="utf-8")
    assert load_policy_config(tmp_path).max_subtasks_per_lane == 3
    (tmp_path / CONFIG_FILE).write_text("[policy]\nmax_subtasks_per_lane = 0\n", encoding="utf-8")
    assert load_policy_config(tmp_path).max_subtasks_per_lane == 10


def test_runner_config_decider_selection(tmp_path: Path) -> None:
    """[runner] decider names the decider agent; absent falls back to the default."""
    assert load_runner_config(tmp_path).decider is None
    (tmp_path / CONFIG_FILE).write_text('[runner]\ndecider = "claude"\n', encoding="utf-8")
    assert load_runner_config(tmp_path).decider == "claude"


def test_runner_config_stall_after(tmp_path: Path) -> None:
    """stall_after defaults to the documented 900s; positive overrides land, junk falls back."""
    assert load_runner_config(tmp_path).stall_after == DEFAULT_STALL_AFTER
    (tmp_path / CONFIG_FILE).write_text("[runner]\nstall_after = 120\n", encoding="utf-8")
    assert load_runner_config(tmp_path).stall_after == 120.0
    (tmp_path / CONFIG_FILE).write_text("[runner]\nstall_after = 0.5\n", encoding="utf-8")
    assert load_runner_config(tmp_path).stall_after == 0.5
    (tmp_path / CONFIG_FILE).write_text("[runner]\nstall_after = -1\n", encoding="utf-8")
    assert load_runner_config(tmp_path).stall_after == DEFAULT_STALL_AFTER
    (tmp_path / CONFIG_FILE).write_text("[runner]\nstall_after = true\n", encoding="utf-8")
    assert load_runner_config(tmp_path).stall_after == DEFAULT_STALL_AFTER


def test_runner_config_max_agent_processes(tmp_path: Path) -> None:
    """The global process ceiling defaults to 8; positive overrides land, junk falls back."""
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
    """runner_timeout defaults to 3600s; positive overrides land, junk falls back."""
    assert load_runner_config(tmp_path).runner_timeout == 3600.0
    (tmp_path / CONFIG_FILE).write_text("[runner]\nrunner_timeout = 120\n", encoding="utf-8")
    assert load_runner_config(tmp_path).runner_timeout == 120.0
    (tmp_path / CONFIG_FILE).write_text("[runner]\nrunner_timeout = -5\n", encoding="utf-8")
    assert load_runner_config(tmp_path).runner_timeout == 3600.0


# --- [policy.evidence] declarations (basicly-m4zv.13) -------------------------


def test_policy_evidence_is_empty_by_default(tmp_path: Path) -> None:
    """Opt-in: with nothing declared the evidence mechanism is inert."""
    assert load_policy_config(tmp_path).evidence == {}


def test_policy_evidence_declarations_parse_per_phase(tmp_path: Path) -> None:
    """A phase -> path table lands on the config, one entry per declaring phase."""
    (tmp_path / CONFIG_FILE).write_text(
        '[policy.evidence]\nverify = ".basicly/evidence/verify.log"\nbuild = "build.log"\n',
        encoding="utf-8",
    )
    assert load_policy_config(tmp_path).evidence == {
        "verify": ".basicly/evidence/verify.log",
        "build": "build.log",
    }


def test_policy_evidence_keeps_a_value_it_cannot_make_sense_of(tmp_path: Path) -> None:
    """A nonsense value is carried through, not dropped.

    Dropping it would turn a typo into a requirement that never fires — the one
    failure mode this mechanism exists to remove. It is carried as a string so
    ``policy.evidence_status`` refuses it with a diagnostic instead.
    """
    (tmp_path / CONFIG_FILE).write_text("[policy.evidence]\nverify = 3\n", encoding="utf-8")
    assert load_policy_config(tmp_path).evidence == {"verify": "3"}


def test_policy_evidence_ignores_a_non_table_section(tmp_path: Path) -> None:
    """`evidence = "x"` is not a declaration table, so it declares nothing."""
    (tmp_path / CONFIG_FILE).write_text('[policy]\nevidence = "x"\n', encoding="utf-8")
    assert load_policy_config(tmp_path).evidence == {}


def test_policy_evidence_is_overridable_by_the_local_overlay(tmp_path: Path) -> None:
    """A machine-local overlay can retarget (or clear) the declaration wholesale."""
    (tmp_path / CONFIG_FILE).write_text(
        '[policy.evidence]\nverify = "shared.log"\n', encoding="utf-8"
    )
    (tmp_path / LOCAL_CONFIG_FILE).write_text(
        '[policy.evidence]\nverify = "local.log"\n', encoding="utf-8"
    )
    assert load_policy_config(tmp_path).evidence == {"verify": "local.log"}


# --- Strict config schema: an unknown name fails loudly (basicly-1piy) ---

_REPO_ROOT = Path(__file__).parent.parent

# Every string literal this module hands to a config table's ``.get`` or to
# ``_parse_path_value``. Both forms, because [paths] keys arrive as an argument
# rather than inside the call.
_CONFIG_KEY_READS = re.compile(r'\.get\(\s*"([a-z_]+)"|_parse_path_value\(paths,\s*"([a-z_]+)"')


def _schema_names() -> set[str]:
    """Every name CONFIG_SCHEMA accepts anywhere, flattened."""
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
    """The reported reproduction: `[loop] concurrency` written for `[worktree]`.

    It was silently ignored, and the only symptom was the committed default of 5
    continuing to apply — indistinguishable from the override having worked at the
    value it was already at. The refusal has to name where `concurrency` does live,
    or the reader learns only that they were wrong, not what to write instead.
    """
    (tmp_path / LOCAL_CONFIG_FILE).write_text("[loop]\nconcurrency = 2\n", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_worktree_config(tmp_path)

    message = str(excinfo.value)
    assert LOCAL_CONFIG_FILE in message
    assert "unknown section 'loop'" in message
    assert "'concurrency' is accepted in [worktree]" in message


def test_an_unknown_key_fails_and_names_what_its_section_accepts(tmp_path: Path) -> None:
    """A near-miss inside a real section: the fix is one of the names printed."""
    (tmp_path / CONFIG_FILE).write_text("[worktree]\nconcurency = 2\n", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_worktree_config(tmp_path)

    assert "unknown key 'concurency' in [worktree]" in str(excinfo.value)
    assert "[worktree] accepts base_branch, concurrency" in str(excinfo.value)


def test_an_unknown_name_in_a_nested_table_fails(tmp_path: Path) -> None:
    """Sub-tables are walked too — `[policy.sizing]` is where the band lives."""
    (tmp_path / CONFIG_FILE).write_text(
        "[policy.sizing]\nworking_set_ceiling = 99\n", encoding="utf-8"
    )

    with pytest.raises(ValueError) as excinfo:
        load_sizing_config(tmp_path)

    assert "unknown key 'working_set_ceiling' in [policy.sizing]" in str(excinfo.value)


def test_an_unknown_name_in_an_array_of_tables_fails(tmp_path: Path) -> None:
    """`[[runner.agents]]` entries are tables with their own vocabulary."""
    (tmp_path / CONFIG_FILE).write_text(
        '[[runner.agents]]\nname = "x"\ncommand = ["x"]\nmodel_id = "y"\n', encoding="utf-8"
    )

    with pytest.raises(ValueError) as excinfo:
        load_runner_config(tmp_path)

    assert "unknown key 'model_id' in [runner.agents]" in str(excinfo.value)


def test_the_refusal_records_the_forward_compatibility_stance(tmp_path: Path) -> None:
    """Strict in both directions, including against a *newer* config.

    The accepted cost of erroring rather than warning is that a repo pinned to an
    older basicly whose config carries a key added since fails every command. That
    is survivable only because the failure diagnoses itself: it names this engine's
    version and says upgrading is one of the two fixes. Asserted here so the stance
    cannot be quietly dropped from the message it is recorded in.
    """
    (tmp_path / CONFIG_FILE).write_text("[worktree]\nfrom_the_future = 1\n", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_worktree_config(tmp_path)

    assert basicly.__version__ in str(excinfo.value)
    assert "upgrade basicly if it comes from a newer version" in str(excinfo.value)


def test_a_malformed_overlay_fails_the_loaders_that_never_read_it(tmp_path: Path) -> None:
    """[paths] and [catalog] are repo-level, but they still refuse a bad overlay.

    `basicly check` reaches the config only through these two, so leaving them out
    would put the gitignored file — the one with no diff to review, which is the
    whole hazard — behind the one command a consumer runs to find drift.
    """
    (tmp_path / CONFIG_FILE).write_text('[catalog]\ntechnologies = ["python"]\n', encoding="utf-8")
    (tmp_path / LOCAL_CONFIG_FILE).write_text("[worktree]\ntypo = 1\n", encoding="utf-8")

    for load in (load_project_paths, load_technology_selection):
        with pytest.raises(ValueError, match="unknown key 'typo'"):
            load(tmp_path)


def test_every_declared_name_is_reported_not_just_the_first(tmp_path: Path) -> None:
    """One pass over the file, so a second typo is not a second failed run."""
    (tmp_path / CONFIG_FILE).write_text(
        "[worktree]\ntypo_one = 1\n\n[policy]\ntypo_two = 2\n", encoding="utf-8"
    )

    problems = unknown_config_keys(tmp_path)

    assert len(problems) == 2
    assert any("'typo_one'" in problem for problem in problems)
    assert any("'typo_two'" in problem for problem in problems)


def test_consumer_chosen_keys_in_an_open_table_are_accepted(tmp_path: Path) -> None:
    """Three tables key on names the consumer picks, not on engine vocabulary.

    An agent name, a loop phase and a task class. Each has its own downstream
    validator that names what it will accept, so checking them against a fixed list
    here would refuse a legitimate declaration the engine goes on to honour.
    """
    (tmp_path / CONFIG_FILE).write_text(
        "[runner.context_windows]\nclaude = 1000\n\n"
        '[policy.evidence]\nverify = "v.log"\n\n'
        "[policy.sizing.build_factor]\nchore = 1.5\n",
        encoding="utf-8",
    )

    assert unknown_config_keys(tmp_path) == []


def test_the_privacy_denylist_only_a_hook_reads_is_accepted(tmp_path: Path) -> None:
    """`[[privacy.denied]]` has no reader in config.py, and refusing it would break a gate.

    `internal-info-scan.py` reads it straight out of basicly.local.toml and nothing
    else does, precisely because the tokens must never be committed. A schema
    derived from this module's own loaders would have started failing every command
    on a machine that had configured the gate — the concrete reason this allowlist
    is authored against the config surface rather than against config.py.
    """
    (tmp_path / LOCAL_CONFIG_FILE).write_text(
        '[[privacy.denied]]\nname = "corp-domain"\ntoken = "internal.example"\n',
        encoding="utf-8",
    )

    assert unknown_config_keys(tmp_path) == []


def test_the_shipped_scaffold_declares_only_recognised_names(tmp_path: Path) -> None:
    """`basicly install` must not scaffold a file its own loader then refuses."""
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")

    assert unknown_config_keys(tmp_path) == []


def test_this_repos_own_config_declares_only_recognised_names() -> None:
    """The authoring repo is the first consumer; its config has to pass its own gate."""
    assert unknown_config_keys(_REPO_ROOT) == []


def test_every_config_key_a_loader_reads_is_in_the_schema() -> None:
    """The anti-staleness half: adding a key to a loader forces adding it here.

    An allowlist's failure mode is the mirror of the denylist's — it goes stale by
    refusing something real rather than by admitting something dead — and a schema
    that refuses a key the engine honours is a worse bug than the one it fixes. So
    the schema is checked against the literals `config.py` actually reads, and the
    only names allowed on the schema side alone are the two whose readers live
    outside this module (a hook and the pre-commit check runner).
    """
    source = (_REPO_ROOT / "src" / "basicly" / "config.py").read_text(encoding="utf-8")
    read = {name or fallback for name, fallback in _CONFIG_KEY_READS.findall(source)}
    # Section names reached through `_harness_section(repo_root, "<name>")`, which
    # is a call form the pattern above cannot see.
    read |= set(re.findall(r'_harness_section\(repo_root,\s*"([a-z_]+)"\)', source))

    assert read - _schema_names() == set()
