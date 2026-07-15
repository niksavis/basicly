"""Tests for the config-driven hook check runner (basicly-yp3)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_check_runner():
    """Load the check_runner module from its hook-script path."""
    script_path = (
        Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "check_runner.py"
    )
    spec = importlib.util.spec_from_file_location("check_runner_hook", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_config(repo: Path, body: str) -> None:
    (repo / "basicly.toml").write_text(body, encoding="utf-8")


def test_no_config_or_checks_passes_with_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A consumer with no basicly.toml (or no checks) is never blocked."""
    module = _load_check_runner()
    assert module.run_checks(tmp_path, "fast") == 0
    assert "nothing to gate" in capsys.readouterr().out

    _write_config(tmp_path, "[worktree]\nconcurrency = 2\n")
    assert module.run_checks(tmp_path, "fast") == 0


def test_mode_routing_selects_only_matching_checks(tmp_path: Path) -> None:
    """Checks run only in their declared modes (fast vs full)."""
    module = _load_check_runner()
    _write_config(
        tmp_path,
        '[[verify.checks]]\nname = "fast-only"\ncommand = ["true"]\nmodes = ["fast"]\n'
        '[[verify.checks]]\nname = "full-only"\ncommand = ["true"]\nmodes = ["full"]\n',
    )
    assert [name for name, _ in module.load_checks(tmp_path, "fast")] == ["fast-only"]
    assert [name for name, _ in module.load_checks(tmp_path, "full")] == ["full-only"]


def test_failing_check_fails_the_run(tmp_path: Path) -> None:
    """A non-zero check exits 1."""
    module = _load_check_runner()
    _write_config(
        tmp_path,
        '[[verify.checks]]\nname = "bad"\ncommand = ["false"]\nmodes = ["fast"]\n',
    )
    assert module.run_checks(tmp_path, "fast") == 1


def test_passing_checks_pass_the_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """All-green checks exit 0 with a summary line."""
    module = _load_check_runner()
    _write_config(
        tmp_path,
        '[[verify.checks]]\nname = "ok"\ncommand = ["true"]\nmodes = ["fast"]\n',
    )
    assert module.run_checks(tmp_path, "fast") == 0
    assert "1/1" in capsys.readouterr().out


def test_missing_tool_is_a_one_line_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A command not on PATH fails with a readable message, not a traceback."""
    module = _load_check_runner()
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    _write_config(
        tmp_path,
        '[[verify.checks]]\nname = "ghost"\ncommand = ["ghost-tool"]\nmodes = ["fast"]\n',
    )
    assert module.run_checks(tmp_path, "fast") == 1
    err = capsys.readouterr().err
    assert "command not found: ghost-tool" in err


def test_malformed_check_is_a_loud_error(tmp_path: Path) -> None:
    """A check without a command must not be silently dropped."""
    module = _load_check_runner()
    _write_config(
        tmp_path,
        '[[verify.checks]]\nname = "broken"\nmodes = ["fast"]\n',
    )
    with pytest.raises(SystemExit, match="command"):
        module.load_checks(tmp_path, "fast")
