"""Tests for per-session harness config overrides (basicly-jr0l.8).

The registry is process-global by design (D1: one supervisor process per
session), so every test here clears it — a leaked override would silently
reconfigure whatever ran next.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from basicly import cli, run_record, session
from basicly.config import load_policy_config, load_runner_config

CONFIG = """\
[runner]
default = "manual"

[policy]
autonomy = "L0"
max_rework = 2
"""


@pytest.fixture(autouse=True)
def _no_leaked_overrides():
    """Process-global state has to be reset around every test, both ways."""
    session.clear_overrides()
    yield
    session.clear_overrides()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo whose committed config pins manual/L0, as this repo's does."""
    (tmp_path / "basicly.toml").write_text(CONFIG, encoding="utf-8")
    return tmp_path


# --- The registry itself -----------------------------------------------------


def test_no_override_is_the_default_state() -> None:
    """An unconfigured process must look exactly like one with no overrides."""
    assert session.override_pairs() == ()
    assert session.overrides_for("runner") == {}


def test_override_pairs_are_sorted_for_diffability() -> None:
    """Two records of the same pair of overrides must render identically."""
    session.set_override("runner", "default", "claude")
    session.set_override("policy", "autonomy", "L1")
    assert session.override_pairs() == ("policy.autonomy=L1", "runner.default=claude")


def test_a_later_override_of_one_key_replaces_it() -> None:
    """Last write wins per key, so a caller cannot accumulate two values for one."""
    session.set_override("runner", "default", "codex")
    session.set_override("runner", "default", "claude")
    assert session.override_pairs() == ("runner.default=claude",)


def test_clear_overrides_restores_the_empty_state() -> None:
    """A session ending must not leave its choices behind for the next one."""
    session.set_override("policy", "autonomy", "L3")
    session.clear_overrides()
    assert session.override_pairs() == ()


# --- Reaching the config readers --------------------------------------------


def test_an_override_wins_over_committed_config(repo: Path) -> None:
    """The point of the bead: one command reconfigures the run, no file edited."""
    assert load_runner_config(repo).default == "manual"
    assert load_policy_config(repo).autonomy == "L0"

    session.set_override("runner", "default", "claude")
    session.set_override("policy", "autonomy", "L1")

    assert load_runner_config(repo).default == "claude"
    assert load_policy_config(repo).autonomy == "L1"


def test_no_committed_file_is_written(repo: Path) -> None:
    """An override must never mutate config every consumer shares."""
    before = (repo / "basicly.toml").read_bytes()
    session.set_override("runner", "default", "claude")
    load_runner_config(repo)
    assert (repo / "basicly.toml").read_bytes() == before
    assert not (repo / "basicly.local.toml").exists()


def test_clearing_restores_the_committed_value(repo: Path) -> None:
    """No revert step for an operator to remember — that was the D10 complaint."""
    session.set_override("runner", "default", "claude")
    session.clear_overrides()
    assert load_runner_config(repo).default == "manual"


def test_an_override_leaves_sibling_keys_alone(repo: Path) -> None:
    """Key-level merge: overriding autonomy must not drop max_rework."""
    session.set_override("policy", "autonomy", "L2")
    config = load_policy_config(repo)
    assert config.autonomy == "L2"
    assert config.max_rework == 2


# --- Provenance in the run record (D9) --------------------------------------


def _record() -> run_record.RunRecord:
    return run_record.build_record(
        agent="claude", handoff=False, returncode=0, duration_s=1.0, command=("claude",)
    )


def test_a_run_record_carries_the_overrides_it_ran_under() -> None:
    """Unrecorded, two different dispatches would be indistinguishable (D9)."""
    session.set_override("runner", "default", "claude")
    session.set_override("policy", "autonomy", "L1")
    assert _record().config_overrides == ("policy.autonomy=L1", "runner.default=claude")


def test_a_run_record_is_empty_when_config_came_from_files() -> None:
    """The ordinary case must stay clean, so a non-empty field means something."""
    assert _record().config_overrides == ()


# --- The CLI surface ---------------------------------------------------------


def _args(runner: str | None = None, autonomy: str | None = None) -> argparse.Namespace:
    """The parsed flags as the handler receives them."""
    return argparse.Namespace(runner=runner, autonomy=autonomy)


def test_the_cli_applies_both_flags(repo: Path) -> None:
    """`loop supervise --runner X --autonomy Y` sets both for the session."""
    applied = cli._apply_session_overrides(repo, _args(runner="claude", autonomy="L1"))
    assert applied == ("policy.autonomy=L1", "runner.default=claude")
    assert load_runner_config(repo).default == "claude"


def test_the_cli_applies_nothing_without_flags(repo: Path) -> None:
    """An ordinary invocation must behave exactly as it did before this existed."""
    assert cli._apply_session_overrides(repo, _args()) == ()
    assert load_runner_config(repo).default == "manual"


def test_an_unknown_runner_is_refused_with_the_configured_names(repo: Path) -> None:
    """Validated up front: a typo would otherwise surface as an adapter miss mid-run."""
    with pytest.raises(ValueError, match="unknown runner 'nope'"):
        cli._apply_session_overrides(repo, _args(runner="nope"))
    assert session.override_pairs() == ()  # nothing partially applied


def test_an_unknown_autonomy_level_is_refused(repo: Path) -> None:
    """Silently reading as the default is the quiet direction on a permission control."""
    with pytest.raises(ValueError, match="unknown autonomy level"):
        cli._apply_session_overrides(repo, _args(autonomy="L9"))


def test_auto_is_an_accepted_runner_name(repo: Path) -> None:
    """`auto` is not a configured adapter but is a legal default, so it must pass."""
    assert cli._apply_session_overrides(repo, _args(runner="auto")) == ("runner.default=auto",)


def test_the_supervise_parser_exposes_both_flags() -> None:
    """The wiring, so the flags cannot exist in the handler but not the parser."""
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
    """The parser's own choices catch the level before the handler runs."""
    parser = cli._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["loop", "supervise", "basicly-x", "--autonomy", "L9"])


# --- The same pin on policy grant (basicly-jr0l.15) --------------------------


def _grant_args(**kwargs) -> argparse.Namespace:
    """`policy grant` flags as the handler receives them."""
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
    """A grant is issued by a separate process, so the session pin cannot reach it."""
    assert load_policy_config(repo).autonomy == "L0"
    cli._apply_session_overrides(repo, _grant_args(autonomy="L1"))
    assert load_policy_config(repo).autonomy == "L1"


def test_the_grant_reads_the_committed_ceiling_without_the_flag(repo: Path) -> None:
    """Unchanged behaviour when the flag is absent — this widens nothing by default."""
    cli._apply_session_overrides(repo, _grant_args(level="L1"))
    assert load_policy_config(repo).autonomy == "L0"


def test_the_grant_parser_exposes_the_autonomy_flag() -> None:
    """The wiring, so the flag cannot exist in the handler but not the parser."""
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
    """Regression: the relayed command must carry --autonomy or it dead-ends.

    The override is process-local, so a re-run without it is refused at the
    committed ceiling again — the challenge would hand back a command that cannot
    work. Found by exercising the command, not by a test.
    """
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
