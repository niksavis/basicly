"""Tests for the ``basicly runner`` CLI wiring (onb.7).

The CLI resolves a runner from --runner / the configured default, prints the
exact command for a dry run, and streams captured output for a live run. These
tests fake PATH detection and the runner.run call and assert only that wiring.
"""

from __future__ import annotations

import pytest

from basicly import cli, runner


@pytest.fixture(autouse=True)
def _no_config(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Run in an empty dir so load_runner_config yields the built-in adapters."""
    monkeypatch.chdir(tmp_path)


def test_runner_dry_run_prints_exact_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """dry-run prints the exact argv the named runner would execute; exits 0."""
    assert cli.main(["runner", "dry-run", "--runner", "claude", "--prompt", "do it"]) == 0
    out = capsys.readouterr().out
    assert "claude -p" in out
    assert "do it" in out


def test_runner_dry_run_handoff_when_none_available(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no big-3 CLI on PATH, auto resolves to the manual handoff — no command."""
    monkeypatch.setattr(runner.shutil, "which", lambda _b: None)
    assert cli.main(["runner", "dry-run", "--runner", "auto", "--prompt", "do it"]) == 0
    out = capsys.readouterr().out
    assert "handoff" in out
    assert "manual" in out


def test_runner_list_shows_availability(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """List marks which adapters are on PATH and which runner auto would select."""
    monkeypatch.setattr(
        runner.shutil, "which", lambda b: "/usr/bin/codex" if b == "codex" else None
    )
    monkeypatch.setattr(runner, "_run_help", lambda _b: "codex exec [options]")  # capable
    assert cli.main(["runner", "list"]) == 0
    out = capsys.readouterr().out
    assert "codex" in out and "available" in out
    assert "not on PATH" in out  # claude/copilot absent
    assert "selected (auto): codex" in out


def test_runner_list_surfaces_capability(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An on-PATH runner whose --help confirms its flag is marked capable (basicly-bveo)."""
    monkeypatch.setattr(
        runner.shutil, "which", lambda b: "/usr/bin/codex" if b == "codex" else None
    )
    monkeypatch.setattr(runner, "_run_help", lambda _b: "codex exec [options]")
    assert cli.main(["runner", "list"]) == 0
    assert "capable" in capsys.readouterr().out


def test_runner_list_flags_a_dropped_headless_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An on-PATH runner whose --help no longer mentions its flag is flagged unconfirmed."""
    monkeypatch.setattr(
        runner.shutil, "which", lambda b: "/usr/bin/codex" if b == "codex" else None
    )
    monkeypatch.setattr(runner, "_run_help", lambda _b: "codex chat [options]")  # no 'exec'
    assert cli.main(["runner", "list"]) == 0
    out = capsys.readouterr().out
    assert "flag unconfirmed" in out
    assert "selected (auto): manual" in out  # incapable codex is skipped


def test_runner_dry_run_surfaces_pinned_model(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    """A pinned model shows on the dry-run header and is injected into the argv."""
    (tmp_path / "basicly.toml").write_text(
        '[[runner.agents]]\nname = "claude"\n'
        'command = ["claude", "-p", "{prompt}"]\nmodel = "opus"\n',
        encoding="utf-8",
    )
    assert cli.main(["runner", "dry-run", "--runner", "claude", "--prompt", "do it"]) == 0
    out = capsys.readouterr().out
    assert "model: opus" in out
    assert "claude --model opus -p" in out


def test_runner_list_surfaces_pinned_model(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """List annotates an adapter that pins a model."""
    (tmp_path / "basicly.toml").write_text(
        '[[runner.agents]]\nname = "claude"\n'
        'command = ["claude", "-p", "{prompt}"]\nmodel = "opus"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(runner.shutil, "which", lambda _b: "/usr/bin/claude")
    assert cli.main(["runner", "list"]) == 0
    assert "(model: opus)" in capsys.readouterr().out


def test_runner_run_streams_output_and_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Run passes through the runner's captured stdout and exit code."""

    def fake_run(spec, prompt, _cwd, *, _dry_run=False):
        return runner.RunResult(
            spec.name,
            ("claude", "-p", prompt),
            executed=True,
            returncode=3,
            stdout="agent output\n",
        )

    monkeypatch.setattr(runner, "run", fake_run)
    code = cli.main(["runner", "run", "--runner", "claude", "--prompt", "x"])
    assert code == 3
    assert "agent output" in capsys.readouterr().out


def test_runner_unknown_name_errors(capsys: pytest.CaptureFixture[str]) -> None:
    """An unknown runner name is a clean error, exit 1."""
    assert cli.main(["runner", "dry-run", "--runner", "nope", "--prompt", "x"]) == 1
    assert "unknown runner" in capsys.readouterr().err
