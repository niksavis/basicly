"""Per-role session inheritance: minting a seed, forking it, and losing it (basicly-2kh170).

Every flag asserted here was probed against the installed claude CLI (2.1.247) on
2026-08-27 before any of it was written: ``--session-id`` mints a named session and is
refused on an id already in use, ``--resume <id> --fork-session`` returns the seed's
context under a fresh id and leaves the seed forkable again, and a missing seed comes back
on stderr as :data:`~basicly.runner.LOST_SESSION_MARKER`. These pin the argv and the store
that turn those facts into a policy; they do not re-probe the CLI.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from basicly import runner
from basicly.runner import (
    HEADLESS,
    PROMPT_PLACEHOLDER,
    RESUME_FORK_FLAGS,
    RunnerSpec,
    SessionSeed,
    format_command,
)

CLAUDE = next(spec for spec in runner.BUILTIN_RUNNERS if spec.name == "claude")


def _seeds(repo_root: Path) -> dict:
    return json.loads((repo_root / runner.SESSION_SEEDS_FILE).read_text(encoding="utf-8"))


# --- The store: one seed per feature per family ---------------------------------


def test_a_feature_with_no_seed_mints_one_and_reports_it_as_not_yet_created(
    tmp_path: Path,
) -> None:
    """The first dispatch creates the seed, so the store hands it an id and no promise."""
    seed = runner.session_seed(tmp_path, "basicly-2kh170", "claude")

    assert seed.exists is False
    assert len(seed.session_id) == 36


def test_a_recorded_seed_comes_back_for_every_later_dispatch_on_that_feature(
    tmp_path: Path,
) -> None:
    """Recording is what closes the loop: the next ask forks instead of minting again."""
    minted = runner.session_seed(tmp_path, "basicly-2kh170", "claude")

    runner.record_session_seed(tmp_path, "basicly-2kh170", "claude", minted.session_id)

    again = runner.session_seed(tmp_path, "basicly-2kh170", "claude")
    assert again == SessionSeed(minted.session_id, exists=True)


def test_one_familys_seed_is_never_offered_to_another(tmp_path: Path) -> None:
    """A seed is one agent's conversation, and the other agent's CLI would refuse the id."""
    runner.record_session_seed(tmp_path, "basicly-2kh170", "claude", "a-claude-session")

    assert runner.session_seed(tmp_path, "basicly-2kh170", "copilot").exists is False
    assert runner.session_seed(tmp_path, "basicly-2kh170", "claude").exists is True


def test_two_features_never_share_a_seed(tmp_path: Path) -> None:
    """A corpus belongs to one feature; sharing would fork the wrong repo reading."""
    runner.record_session_seed(tmp_path, "basicly-aaa", "claude", "session-a")
    runner.record_session_seed(tmp_path, "basicly-bbb", "claude", "session-b")

    assert _seeds(tmp_path) == {
        "basicly-aaa": {"claude": "session-a"},
        "basicly-bbb": {"claude": "session-b"},
    }


def test_an_unreadable_store_mints_rather_than_raising(tmp_path: Path) -> None:
    """Telemetry-grade state: a corrupt file costs a cold dispatch, never a lane."""
    path = tmp_path / runner.SESSION_SEEDS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert runner.session_seed(tmp_path, "basicly-2kh170", "claude").exists is False


# --- The argv: mint once, fork after --------------------------------------------


def test_an_uncreated_seed_mints_the_session_under_the_id_the_harness_chose() -> None:
    """Minting our own id is what makes the seed findable without scraping stdout."""
    argv = format_command(CLAUDE, "go", seed=SessionSeed("s-1", exists=False))

    assert argv[:3] == ["claude", "--session-id", "s-1"]
    assert "--fork-session" not in argv


def test_a_created_seed_is_forked_rather_than_re_entered() -> None:
    """Forking is what lets two lanes share one seed: re-entry would interleave them."""
    argv = format_command(CLAUDE, "go", seed=SessionSeed("s-1", exists=True))

    assert argv[:4] == ["claude", "--resume", "s-1", "--fork-session"]


def test_no_seed_leaves_the_argv_exactly_as_it_was_before_inheritance_existed() -> None:
    """Every cold call site must be byte-identical to what it dispatched before."""
    assert format_command(CLAUDE, "go") == format_command(CLAUDE, "go", seed=None)


def test_a_family_that_cannot_fork_drops_the_seed_instead_of_emitting_a_flag() -> None:
    """Codex spells resume as a subcommand, so a flag here would be argv it cannot parse."""
    codex = next(spec for spec in runner.BUILTIN_RUNNERS if spec.name == "codex")

    argv = format_command(codex, "go", seed=SessionSeed("s-1", exists=True))

    assert "--resume" not in argv


def test_an_unknown_resume_style_raises_rather_than_dispatching_unseeded() -> None:
    """Unlike a dropped seed, a style nobody implements is a config error, not a cold start."""
    spec = RunnerSpec("odd", HEADLESS, ("odd", PROMPT_PLACEHOLDER), resume_style="telepathy")

    with pytest.raises(ValueError, match="resume_style"):
        format_command(spec, "go", seed=SessionSeed("s-1", exists=True))


def test_the_role_flag_still_reads_first_on_a_seeded_argv() -> None:
    """An operator tells a specialised dispatch from a default one by the head of the line."""
    argv = format_command(CLAUDE, "go", role="implementer", seed=SessionSeed("s-1", exists=True))

    assert argv[:2] == ["claude", "--agent"]
    assert argv.index("--resume") > argv.index("implementer")


def test_the_seed_rides_back_on_the_result_so_a_caller_can_record_it() -> None:
    """Only a returned dispatch proves the session exists, so the id has to survive the run."""
    result = runner.run(CLAUDE, "go", Path.cwd(), dry_run=True, seed=SessionSeed("s-1", False))

    assert result.seed == SessionSeed("s-1", exists=False)


# --- A seed the agent no longer holds -------------------------------------------


def test_only_a_lost_seed_is_retried() -> None:
    """The three near misses that must not re-dispatch: cold, clean, and killed."""
    lost = runner.RunResult("claude", (), executed=True, returncode=1, stderr="No conversation")
    real = runner.RunResult(
        "claude", (), executed=True, returncode=1, stderr=f"{runner.LOST_SESSION_MARKER}: s-1"
    )
    forked = SessionSeed("s-1", exists=True)

    assert runner._seed_was_lost(forked, real) is True
    assert runner._seed_was_lost(None, real) is False
    assert runner._seed_was_lost(SessionSeed("s-1", exists=False), real) is False
    assert runner._seed_was_lost(forked, lost) is False
    assert (
        runner._seed_was_lost(
            forked, runner.RunResult("claude", (), executed=True, returncode=0, stderr="")
        )
        is False
    )


def _stub_cli(directory: Path, name: str, body: str) -> None:
    """A command on PATH that resolves on every platform, implemented once.

    Both shims delegate to this interpreter running one python file, so the behaviour
    under test has a single spelling — a `sh` version and a `.cmd` version of the same
    logic would be two things free to disagree. POSIX resolves the bare file by its
    executable bit and Windows resolves the `.cmd` twin through PATHEXT.
    """
    directory.mkdir(parents=True, exist_ok=True)
    impl = directory / f"{name}_impl.py"
    impl.write_text(body, encoding="utf-8")
    posix = directory / name
    posix.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{impl}" "$@"\n', encoding="utf-8")
    posix.chmod(0o755)
    (directory / f"{name}.cmd").write_text(
        f'@echo off\r\n"{sys.executable}" "{impl}" %*\r\n', encoding="utf-8"
    )


_REFUSES_A_RESUME = """
import json, pathlib, sys

log = pathlib.Path(__file__).with_name("calls.json")
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(sys.argv[1:])
log.write_text(json.dumps(calls))
if "--resume" in sys.argv:
    sys.stderr.write("No conversation found with session ID: gone\\n")
    raise SystemExit(1)
print("ok")
"""


def test_a_pruned_seed_costs_a_re_read_and_not_the_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude prunes sessions on its own schedule, so a recorded seed can die under a feature.

    Re-seeded rather than retried cold: a cold retry would leave the dead id recorded and
    every later dispatch on the feature would pay the same failed fork.
    """
    bin_dir = tmp_path / "bin"
    _stub_cli(bin_dir, "stubcli", _REFUSES_A_RESUME)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    spec = RunnerSpec(
        "stub", HEADLESS, ("stubcli", PROMPT_PLACEHOLDER), resume_style=RESUME_FORK_FLAGS
    )

    result = runner.run(spec, "go", tmp_path, seed=SessionSeed("gone", exists=True))

    first, second = json.loads((bin_dir / "calls.json").read_text())
    assert first[:3] == ["--resume", "gone", "--fork-session"]
    assert second[0] == "--session-id"
    assert second[1] != "gone"
    assert result.returncode == 0
    assert result.seed == SessionSeed(second[1], exists=False)


def test_the_store_ignores_itself_on_creation(tmp_path: Path) -> None:
    """A committed seed would name a session that exists on exactly one machine."""
    runner.record_session_seed(tmp_path, "basicly-2kh170", "claude", "s-1")

    assert (tmp_path / runner.SESSION_SEEDS_FILE).parent.joinpath(".gitignore").read_text() == "*\n"
