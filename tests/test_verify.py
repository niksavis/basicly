"""Tests for the config-driven verify runner (onb.2)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from basicly import cli, verify
from basicly.config import VerifyCheck, VerifyConfig


class _Proc:
    """Minimal stand-in for a CompletedProcess with a chosen return code."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _check(name: str, modes: tuple[str, ...], staged_suffix: str | None = None) -> VerifyCheck:
    return VerifyCheck(
        name=name, command=(name,), modes=frozenset(modes), staged_suffix=staged_suffix
    )


def test_run_check_maps_returncode_to_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A zero exit is pass; non-zero is fail; the command runs as configured."""
    seen: list[list[str]] = []

    def fake_run(command, **_kw):
        seen.append(command)
        return _Proc(0 if command == ["ok"] else 1)

    monkeypatch.setattr(verify.subprocess, "run", fake_run)

    assert verify.run_check(_check("ok", ("full",)), tmp_path, "full").status == "pass"
    assert verify.run_check(_check("bad", ("full",)), tmp_path, "full").status == "fail"
    assert seen == [["ok"], ["bad"]]


def test_run_check_fails_cleanly_on_missing_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command not on PATH is a failed check with a one-line message.

    Regression (basicly-zrj.13.2): the FileNotFoundError used to escape as a
    traceback from the loop verify gate on consumers without the tool.
    """
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))  # deterministic: nothing resolvable
    check = VerifyCheck(name="ghost", command=("ghost-tool",), modes=frozenset({"full"}))

    result = verify.run_check(check, tmp_path, "full")

    assert result.status == "fail"
    assert result.returncode == 127
    assert "command not found: ghost-tool" in result.detail
    assert "\n" not in result.detail  # readable one-liner, not a traceback


def test_run_check_fails_cleanly_on_unrunnable_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PATH candidate that exists but cannot be executed also fails cleanly.

    On WSL, Windows mounts on PATH surface a missing tool as PermissionError
    rather than FileNotFoundError; both must yield a one-line failure.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "ghost-tool"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    tool.chmod(0o644)  # present but not executable
    monkeypatch.setenv("PATH", str(bin_dir))
    check = VerifyCheck(name="ghost", command=("ghost-tool",), modes=frozenset({"full"}))

    result = verify.run_check(check, tmp_path, "full")

    assert result.status == "fail"
    assert result.returncode in (126, 127)  # PermissionError vs FileNotFoundError by OS
    # Linux surfaces PermissionError ("cannot run"); Windows treats the
    # non-executable as not found. Either way the detail names the tool.
    assert "ghost-tool" in result.detail
    assert "\n" not in result.detail


def test_run_check_staged_fails_when_git_itself_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed git call fails the check instead of silently skipping it."""
    monkeypatch.setattr(verify, "staged_files", lambda _root, _suffix: None)
    result = verify.run_check(_check("ruff", ("staged",), ".py"), tmp_path, "staged")
    assert result.status == "fail"
    assert "git diff" in (result.detail or "")


def test_staged_files_returns_none_outside_a_repo(tmp_path: Path) -> None:
    """staged_files distinguishes git failure (None) from nothing staged ([])."""
    assert verify.staged_files(tmp_path, ".py") is None


def test_run_check_staged_skips_when_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A staged check with no matching staged files is skipped, not run."""
    monkeypatch.setattr(verify, "staged_files", lambda _root, _suffix: [])
    ran = False

    def fake_run(_command, **_kw):
        nonlocal ran
        ran = True
        return _Proc(0)

    monkeypatch.setattr(verify.subprocess, "run", fake_run)

    result = verify.run_check(_check("ruff", ("staged",), ".py"), tmp_path, "staged")
    assert result.status == "skip"
    assert ran is False


def test_run_check_staged_appends_matching_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In staged mode the matching staged files are appended to the command."""
    monkeypatch.setattr(verify, "staged_files", lambda _root, _suffix: ["a.py", "b.py"])
    captured: list[str] = []

    def fake_run(command, **_kw):
        captured.extend(command)
        return _Proc(0)

    monkeypatch.setattr(verify.subprocess, "run", fake_run)

    verify.run_check(_check("ruff", ("staged",), ".py"), tmp_path, "staged")
    assert captured == ["ruff", "a.py", "b.py"]


def test_run_verify_filters_by_mode_and_aggregates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only mode-matching checks run; the report reflects each verdict."""
    monkeypatch.setattr(
        verify.subprocess, "run", lambda command, **_kw: _Proc(0 if command == ["a"] else 1)
    )
    config = VerifyConfig((_check("a", ("full",)), _check("b", ("full",)), _check("c", ("fast",))))

    report = verify.run_verify(tmp_path, "full", config)
    assert [(r.name, r.status) for r in report.results] == [("a", "pass"), ("b", "fail")]
    assert report.passed is False
    assert report.failures == ("b",)


def test_report_gate_without_br(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When br is absent, reporting degrades gracefully instead of raising."""
    monkeypatch.setattr(verify.br, "try_run_br", lambda *_a, **_kw: None)
    report = verify.VerifyReport(mode="full", results=())
    ok, message = verify.report_gate(tmp_path, "basicly-x", report)
    assert ok is False
    assert "br not on PATH" in message


def test_report_gate_builds_expected_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A passing report records a pass gate with the aggregate note."""
    captured: dict[str, list[str]] = {}

    def fake_run(_root, args):
        captured["cmd"] = args
        return _Proc(0)

    monkeypatch.setattr(verify.br, "try_run_br", fake_run)
    report = verify.VerifyReport(mode="full", results=(verify.CheckResult("ruff", "pass", 0),))

    ok, _message = verify.report_gate(tmp_path, "basicly-x", report, gate="verify")
    cmd = captured["cmd"]
    assert ok is True
    assert cmd[:2] == ["gate", "report"]
    assert "--status" in cmd and cmd[cmd.index("--status") + 1] == "pass"
    assert cmd[-1] == "basicly-x"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)  # nosec B603 B607


@pytest.fixture
def linked_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """A real git repo plus a linked worktree of it, as ``(repo, worktree)``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init", "--no-verify")
    linked = tmp_path / "repo.worktrees" / "wt"
    _git(repo, "worktree", "add", "-b", "harness/wt", str(linked))
    return repo, linked


def test_linked_worktree_guard_states(linked_worktree: tuple[Path, Path], tmp_path: Path) -> None:
    """The guard trips only in a linked worktree — not in the main checkout or outside git."""
    repo, linked = linked_worktree
    assert verify.linked_worktree_guard(repo) is None
    reason = verify.linked_worktree_guard(linked)
    assert reason is not None and "linked worktree" in reason
    outside = tmp_path / "plain"
    outside.mkdir()
    assert verify.linked_worktree_guard(outside) is None


def test_cli_verify_refuses_to_record_gate_from_linked_worktree(
    linked_worktree: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`verify --issue` from a linked worktree fails fast instead of losing the gate."""
    _repo, linked = linked_worktree
    monkeypatch.chdir(linked)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: pytest.fail("checks must not run"))
    assert cli.main(["verify", "--mode", "full", "--issue", "basicly-x"]) == 1
    err = capsys.readouterr().err
    assert "refusing to record gate" in err and "base checkout" in err


def test_cli_verify_returns_nonzero_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI exits 1 when a check fails and 0 when all pass."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "basicly.toml").write_text(
        '[[verify.checks]]\nname = "x"\ncommand = ["x"]\nmodes = ["full"]\n', encoding="utf-8"
    )

    monkeypatch.setattr(
        verify,
        "run_verify",
        lambda *_a, **_k: verify.VerifyReport("full", (verify.CheckResult("x", "fail", 1),)),
    )
    assert cli.main(["verify", "--mode", "full"]) == 1

    monkeypatch.setattr(
        verify,
        "run_verify",
        lambda *_a, **_k: verify.VerifyReport("full", (verify.CheckResult("x", "pass", 0),)),
    )
    assert cli.main(["verify", "--mode", "full"]) == 0
