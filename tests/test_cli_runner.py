from __future__ import annotations

import pytest

from basicly import cli, runner


@pytest.fixture(autouse=True)
def _no_config(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _no_help_probe(monkeypatch: pytest.MonkeyPatch):

    monkeypatch.setattr(runner, "_run_help", lambda _binary: None)


CODEX_APPROVAL_HELP = """\
Options:
  -a, --ask-for-approval <APPROVAL_POLICY>
          Configure when the model requires human approval

          Possible values:
          - untrusted:  Only run "trusted" commands
          - on-request: The model decides when to ask
          - never:      Never ask for user approval
"""


def test_runner_dry_run_prints_exact_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["runner", "dry-run", "--runner", "claude", "--prompt", "do it"]) == 0
    out = capsys.readouterr().out
    assert "claude -p" in out
    assert "do it" in out


def test_runner_dry_run_handoff_when_none_available(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(runner.shutil, "which", lambda _b: None)
    assert cli.main(["runner", "dry-run", "--runner", "auto", "--prompt", "do it"]) == 0
    out = capsys.readouterr().out
    assert "handoff" in out
    assert "manual" in out


def test_runner_list_shows_availability(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        runner.shutil, "which", lambda b: "/usr/bin/codex" if b == "codex" else None
    )
    monkeypatch.setattr(runner, "_run_help", lambda _b: "codex exec [options]")
    assert cli.main(["runner", "list"]) == 0
    out = capsys.readouterr().out
    assert "codex" in out and "available" in out
    assert "not on PATH" in out
    assert "selected (auto): codex" in out


def test_runner_list_surfaces_capability(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        runner.shutil, "which", lambda b: "/usr/bin/codex" if b == "codex" else None
    )
    monkeypatch.setattr(runner, "_run_help", lambda _b: "codex exec [options]")
    assert cli.main(["runner", "list"]) == 0
    assert "capable" in capsys.readouterr().out


def test_runner_list_flags_a_dropped_headless_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        runner.shutil, "which", lambda b: "/usr/bin/codex" if b == "codex" else None
    )
    monkeypatch.setattr(runner, "_run_help", lambda _b: "codex chat [options]")
    assert cli.main(["runner", "list"]) == 0
    out = capsys.readouterr().out
    assert "flag unconfirmed" in out
    assert "selected (auto): manual" in out


def test_runner_dry_run_surfaces_pinned_model(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "basicly.toml").write_text(
        '[[runner.agents]]\nname = "claude"\n'
        'command = ["claude", "-p", "{prompt}"]\nmodel = "opus"\n',
        encoding="utf-8",
    )
    assert cli.main(["runner", "dry-run", "--runner", "claude", "--prompt", "do it"]) == 0
    out = capsys.readouterr().out
    assert "model: opus" in out
    assert "claude --model opus -p" in out


def test_runner_dry_run_surfaces_codex_sandbox_and_approval(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["runner", "dry-run", "--runner", "codex", "--prompt", "do it"]) == 0
    out = capsys.readouterr().out
    assert "sandbox: workspace-write" in out
    assert "approval: never" in out
    assert "codex --sandbox workspace-write -a never exec" in out


def test_runner_dry_run_accepts_the_shipped_codex_guardrails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "_run_help", lambda _binary: CODEX_APPROVAL_HELP)
    assert cli.main(["runner", "dry-run", "--runner", "codex", "--prompt", "do it"]) == 0


def test_runner_dry_run_rejects_an_approval_the_cli_does_not_accept(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:

    (tmp_path / "basicly.toml").write_text(
        '[[runner.agents]]\nname = "codex"\ncommand = ["codex", "exec", "{prompt}"]\n'
        'sandbox = "workspace-write"\napproval = "on-failure"\n'
    )
    monkeypatch.setattr(runner, "_run_help", lambda _binary: CODEX_APPROVAL_HELP)
    assert cli.main(["runner", "dry-run", "--runner", "codex", "--prompt", "do it"]) == 1
    captured = capsys.readouterr()
    assert "on-failure" in captured.err
    assert "untrusted, on-request, never" in captured.err


def test_runner_list_surfaces_pinned_model(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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
    assert cli.main(["runner", "dry-run", "--runner", "nope", "--prompt", "x"]) == 1
    assert "unknown runner" in capsys.readouterr().err
