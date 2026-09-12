from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from basicly import cli, run_record, runner, session
from basicly.config import load_policy_config, load_runner_config

CONFIG = """\
[runner]
default = "manual"

[policy]
autonomy = "L0"
max_rework = 2
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "basicly.toml").write_text(CONFIG, encoding="utf-8")
    return tmp_path


def test_no_override_is_the_default_state() -> None:
    assert session.override_pairs() == ()
    assert session.overrides_for("runner") == {}


def test_override_pairs_are_sorted_for_diffability() -> None:
    session.set_override("runner", "default", "claude")
    session.set_override("policy", "autonomy", "L1")
    assert session.override_pairs() == ("policy.autonomy=L1", "runner.default=claude")


def test_a_later_override_of_one_key_replaces_it() -> None:
    session.set_override("runner", "default", "codex")
    session.set_override("runner", "default", "claude")
    assert session.override_pairs() == ("runner.default=claude",)


def test_clear_overrides_restores_the_empty_state() -> None:
    session.set_override("policy", "autonomy", "L3")
    session.clear_overrides()
    assert session.override_pairs() == ()


def test_an_override_wins_over_committed_config(repo: Path) -> None:
    assert load_runner_config(repo).default == "manual"
    assert load_policy_config(repo).autonomy == "L0"

    session.set_override("runner", "default", "claude")
    session.set_override("policy", "autonomy", "L1")

    assert load_runner_config(repo).default == "claude"
    assert load_policy_config(repo).autonomy == "L1"


def test_no_committed_file_is_written(repo: Path) -> None:
    before = (repo / "basicly.toml").read_bytes()
    session.set_override("runner", "default", "claude")
    load_runner_config(repo)
    assert (repo / "basicly.toml").read_bytes() == before
    assert not (repo / "basicly.local.toml").exists()


def test_clearing_restores_the_committed_value(repo: Path) -> None:
    session.set_override("runner", "default", "claude")
    session.clear_overrides()
    assert load_runner_config(repo).default == "manual"


def test_an_override_leaves_sibling_keys_alone(repo: Path) -> None:
    session.set_override("policy", "autonomy", "L2")
    config = load_policy_config(repo)
    assert config.autonomy == "L2"
    assert config.max_rework == 2


def _record() -> run_record.RunRecord:
    return run_record.build_record(
        agent="claude", handoff=False, returncode=0, duration_s=1.0, command=("claude",)
    )


def test_a_run_record_carries_the_overrides_it_ran_under() -> None:
    session.set_override("runner", "default", "claude")
    session.set_override("policy", "autonomy", "L1")
    assert _record().config_overrides == ("policy.autonomy=L1", "runner.default=claude")


def test_a_run_record_is_empty_when_config_came_from_files() -> None:
    assert _record().config_overrides == ()


def _args(runner: str | None = None, autonomy: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(runner=runner, autonomy=autonomy)


def test_the_cli_applies_both_flags(repo: Path) -> None:
    applied = cli._apply_session_overrides(repo, _args(runner="claude", autonomy="L1"))
    assert applied == ("policy.autonomy=L1", "runner.default=claude")
    assert load_runner_config(repo).default == "claude"


def test_the_cli_applies_nothing_without_flags(repo: Path) -> None:
    assert cli._apply_session_overrides(repo, _args()) == ()
    assert load_runner_config(repo).default == "manual"


def test_an_unknown_runner_is_refused_with_the_configured_names(repo: Path) -> None:
    with pytest.raises(ValueError, match="unknown runner 'nope'"):
        cli._apply_session_overrides(repo, _args(runner="nope"))
    assert session.override_pairs() == ()


def test_an_unknown_autonomy_level_is_refused(repo: Path) -> None:
    with pytest.raises(ValueError, match="unknown autonomy level"):
        cli._apply_session_overrides(repo, _args(autonomy="L9"))


def test_a_valid_runner_is_not_left_applied_by_an_invalid_autonomy(repo: Path) -> None:

    with pytest.raises(ValueError, match="unknown autonomy level"):
        cli._apply_session_overrides(repo, _args(runner="claude", autonomy="L9"))

    assert session.override_pairs() == ()
    assert load_runner_config(repo).default == "manual"


def test_auto_is_an_accepted_runner_name(repo: Path) -> None:
    assert cli._apply_session_overrides(repo, _args(runner="auto")) == ("runner.default=auto",)


def test_the_supervise_parser_exposes_both_flags() -> None:
    parser = cli._build_parser()
    args = parser.parse_args([
        "loop",
        "supervise",
        "basicly-x",
        "--runner",
        "claude",
        "--autonomy",
        "L1",
    ])
    assert args.runner == "claude"
    assert args.autonomy == "L1"


def test_the_supervise_parser_rejects_an_unknown_level() -> None:
    parser = cli._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["loop", "supervise", "basicly-x", "--autonomy", "L9"])


def _grant_args(**kwargs) -> argparse.Namespace:
    defaults = {
        "issue": "basicly-root",
        "level": None,
        "token_budget": None,
        "revoke": False,
        "autonomy": None,
        "confirm": None,
    }
    return argparse.Namespace(**{**defaults, **kwargs})


def test_the_grant_ceiling_can_be_pinned_for_one_issuance(repo: Path) -> None:
    assert load_policy_config(repo).autonomy == "L0"
    cli._apply_session_overrides(repo, _grant_args(autonomy="L1"))
    assert load_policy_config(repo).autonomy == "L1"


def test_the_grant_reads_the_committed_ceiling_without_the_flag(repo: Path) -> None:
    cli._apply_session_overrides(repo, _grant_args(level="L1"))
    assert load_policy_config(repo).autonomy == "L0"


def test_the_grant_parser_exposes_the_autonomy_flag() -> None:
    args = cli._build_parser().parse_args([
        "policy",
        "grant",
        "basicly-x",
        "--level",
        "L1",
        "--token-budget",
        "1000",
        "--autonomy",
        "L1",
    ])
    assert args.autonomy == "L1" and args.level == "L1" and args.token_budget == 1000


def test_the_grant_challenge_reprints_the_autonomy_override(
    monkeypatch: pytest.MonkeyPatch, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    monkeypatch.chdir(repo)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.policy, "_new_code", lambda: "cafe1234")

    assert (
        cli.main([
            "policy",
            "grant",
            "basicly-root",
            "--level",
            "L1",
            "--token-budget",
            "1000000",
            "--autonomy",
            "L1",
        ])
        == 1
    )

    rerun = capsys.readouterr().err
    assert "--autonomy L1" in rerun
    assert "--confirm cafe1234" in rerun
    assert "--token-budget 1000000" in rerun


@pytest.mark.parametrize("command", ["advance", "run", "supervise"])
def test_every_dispatching_subcommand_accepts_the_session_overrides(command: str) -> None:

    parser = cli._build_parser()

    args = parser.parse_args(["loop", command, "i-1", "--runner", "manual", "--autonomy", "L1"])

    assert (args.runner, args.autonomy) == ("manual", "L1")


def test_the_runner_override_restores_the_handoff_over_a_committed_agent(
    tmp_path: Path,
) -> None:
    (tmp_path / "basicly.toml").write_text('[runner]\ndefault = "claude"\n', encoding="utf-8")
    assert load_runner_config(tmp_path).default == "claude"

    applied = cli._apply_session_overrides(
        tmp_path, argparse.Namespace(runner="manual", autonomy=None)
    )

    assert applied == ("runner.default=manual",)
    resolved = load_runner_config(tmp_path)
    spec = next(s for s in resolved.specs if s.name == resolved.default)
    assert spec.kind == "handoff", "an interactive build must be able to stay a handoff"


def test_an_unknown_runner_is_refused_rather_than_silently_ignored(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown runner"):
        cli._apply_session_overrides(
            tmp_path, argparse.Namespace(runner="nosuchagent", autonomy=None)
        )


def test_a_named_tier_reaches_the_runner_config(repo: Path) -> None:
    args = argparse.Namespace(runner=None, autonomy=None, tier="maximum")
    assert cli._apply_session_overrides(repo, args) == ("runner.default_tier=maximum",)
    assert load_runner_config(repo).specs[0].tier == "maximum"
    assert (repo / "basicly.toml").read_text(encoding="utf-8") == CONFIG


def test_an_unknown_tier_is_refused_before_anything_is_overridden(repo: Path) -> None:
    args = argparse.Namespace(runner="manual", autonomy=None, tier="titanium")
    with pytest.raises(ValueError, match="unknown model tier"):
        cli._apply_session_overrides(repo, args)
    assert session.override_pairs() == ()


def test_no_tier_flag_leaves_the_committed_default_alone(repo: Path) -> None:
    args = argparse.Namespace(runner=None, autonomy=None, tier=None)
    assert cli._apply_session_overrides(repo, args) == ()
    assert "default_tier" not in str(session.override_pairs())


def test_a_tier_override_is_recorded_on_the_run_record(repo: Path) -> None:
    cli._apply_session_overrides(repo, argparse.Namespace(runner=None, autonomy=None, tier="low"))
    assert _record().config_overrides == ("runner.default_tier=low",)


def test_no_test_inherits_the_process_globals_left_by_another_first_half() -> None:

    assert session.override_pairs() == ()
    budget = runner.configure_process_budget(97, 7)
    assert (budget.total, budget.lane_slots) == (97, 7)

    session.set_override("runner", "default", "claude")


def test_no_test_inherits_the_process_globals_left_by_another_second_half() -> None:
    assert session.override_pairs() == ()
    budget = runner.configure_process_budget(11, 3)
    assert (budget.total, budget.lane_slots) == (11, 3)

    session.set_override("policy", "autonomy", "L3")
