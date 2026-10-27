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


def test_an_uncreated_seed_mints_the_session_under_the_id_the_harness_chose() -> None:
    argv = format_command(
        CLAUDE, "go", seed=SessionSeed("11111111-1111-4111-8111-111111111111", exists=False)
    )

    assert argv[:3] == ["claude", "--session-id", "11111111-1111-4111-8111-111111111111"]
    assert "--fork-session" not in argv


def test_a_created_seed_continues_the_same_conversation() -> None:
    argv = format_command(
        CLAUDE, "go", seed=SessionSeed("11111111-1111-4111-8111-111111111111", exists=True)
    )

    assert argv[:3] == ["claude", "--resume", "11111111-1111-4111-8111-111111111111"]
    assert "--fork-session" not in argv


def test_no_seed_leaves_the_argv_exactly_as_it_was_before_inheritance_existed() -> None:
    assert format_command(CLAUDE, "go") == format_command(CLAUDE, "go", seed=None)


def test_codex_resumes_the_explicit_task_session() -> None:
    codex = next(spec for spec in runner.BUILTIN_RUNNERS if spec.name == "codex")

    argv = format_command(
        codex, "go", seed=SessionSeed("11111111-1111-4111-8111-111111111111", exists=True)
    )

    assert argv == ["codex", "exec", "resume", "11111111-1111-4111-8111-111111111111", "go"]


def test_an_unknown_resume_style_raises_rather_than_dispatching_unseeded() -> None:
    spec = RunnerSpec("odd", HEADLESS, ("odd", PROMPT_PLACEHOLDER), resume_style="telepathy")

    with pytest.raises(ValueError, match="resume_style"):
        format_command(
            spec, "go", seed=SessionSeed("11111111-1111-4111-8111-111111111111", exists=True)
        )


def test_the_role_flag_still_reads_first_on_a_seeded_argv() -> None:
    argv = format_command(
        CLAUDE,
        "go",
        role="implementer",
        seed=SessionSeed("11111111-1111-4111-8111-111111111111", exists=True),
    )

    assert argv[:2] == ["claude", "--agent"]
    assert argv.index("--resume") > argv.index("implementer")


def test_the_seed_rides_back_on_the_result_so_a_caller_can_record_it() -> None:
    result = runner.run(
        CLAUDE,
        "go",
        Path.cwd(),
        dry_run=True,
        seed=SessionSeed("11111111-1111-4111-8111-111111111111", False),
    )

    assert result.seed == SessionSeed("11111111-1111-4111-8111-111111111111", exists=False)


def test_only_a_lost_seed_is_retried() -> None:
    lost = runner.RunResult("claude", (), executed=True, returncode=1, stderr="No conversation")
    real = runner.RunResult(
        "claude", (), executed=True, returncode=1, stderr=f"{runner.LOST_SESSION_MARKER}: s-1"
    )
    forked = SessionSeed("11111111-1111-4111-8111-111111111111", exists=True)

    assert runner._seed_was_lost(forked, real) is True
    assert runner._seed_was_lost(None, real) is False
    assert (
        runner._seed_was_lost(
            SessionSeed("11111111-1111-4111-8111-111111111111", exists=False), real
        )
        is False
    )
    assert runner._seed_was_lost(forked, lost) is False
    assert (
        runner._seed_was_lost(
            forked, runner.RunResult("claude", (), executed=True, returncode=0, stderr="")
        )
        is False
    )


def _stub_cli(directory: Path, name: str, body: str) -> None:

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

    bin_dir = tmp_path / "bin"
    _stub_cli(bin_dir, "stubcli", _REFUSES_A_RESUME)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    spec = RunnerSpec(
        "stub", HEADLESS, ("stubcli", PROMPT_PLACEHOLDER), resume_style=RESUME_FORK_FLAGS
    )

    result = runner.run(
        spec, "go", tmp_path, seed=SessionSeed("22222222-2222-4222-8222-222222222222", exists=True)
    )

    first, second = json.loads((bin_dir / "calls.json").read_text())
    assert first[:3] == ["--resume", "22222222-2222-4222-8222-222222222222", "--fork-session"]
    assert second[0] == "--session-id"
    assert second[1] != "22222222-2222-4222-8222-222222222222"
    assert result.returncode == 0
    assert result.seed == SessionSeed(second[1], exists=False)
