from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from basicly import landing_guard, merge, verify
from tests.test_merge import (
    _HAS_WORK,
    _add_source,
    _FakeGit,
    _git_here,
    _lane_worktree,
    _patch_git,
    _Proc,
    _seed_repo,
    _session,
)


@pytest.fixture(autouse=True)
def _base_ready(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(merge, "load_session", lambda _n, _r: _session())
    monkeypatch.setattr(merge, "current_branch", lambda _r: "main")


def _landing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rerun: verify.VerifyReport):
    _patch_git(monkeypatch, _FakeGit({**_HAS_WORK, "status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: rerun)
    return merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")


def test_a_first_failure_carries_the_original_check_command_and_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    reproduced = verify.VerifyReport(
        "full",
        (
            verify.CheckResult(
                "pyright", "fail", 1, output="src/a.py:1: error", command=("pyright", "--strict")
            ),
            verify.CheckResult("ruff", "pass", 0, command=("ruff", "check")),
        ),
    )

    result = _landing(monkeypatch, tmp_path, reproduced)

    assert result.status == "verify-failed"
    assert [(c.name, c.output, c.command) for c in result.checks] == [
        ("pyright", "src/a.py:1: error", ("pyright", "--strict"))
    ]


@pytest.mark.parametrize(
    "error", [FileNotFoundError(2, "missing tool"), PermissionError(13, "Permission denied")]
)
def test_a_tool_launch_failure_retains_its_cause_without_source_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: OSError
) -> None:
    def unavailable(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(verify, "_spawn", unavailable)
    check = verify.run_check(
        verify.VerifyCheck("ruff", ("ruff", "check"), frozenset({"full"})), tmp_path, "full"
    )
    result = _landing(monkeypatch, tmp_path, verify.VerifyReport("full", (check,)))
    assert result.status == "verify-unavailable"
    assert check.detail in result.detail
    assert result.checks == (check,)


def test_a_started_check_with_exit_126_remains_a_source_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    check = verify.CheckResult("ruff", "fail", 126, "source check failed", command=("ruff",))
    result = _landing(monkeypatch, tmp_path, verify.VerifyReport("full", (check,)))
    assert result.status == "verify-failed"


def test_an_unstable_engine_is_refused_before_running_candidate_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def unavailable(*_args, **_kwargs):
        raise landing_guard.GuardUnavailableError("candidate engine required")

    monkeypatch.setattr(landing_guard, "require_stable_engine", unavailable, raising=False)
    called = []
    _patch_git(monkeypatch, _FakeGit({**_HAS_WORK, "status": _Proc(0, ""), "rebase": _Proc(0)}))

    def checked(*_args, **_kwargs):
        called.append(True)
        return verify.VerifyReport("full", ())

    monkeypatch.setattr(verify, "run_verify", checked)
    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")
    assert result.status == "not-ready"
    assert "candidate engine required" in result.detail
    assert "uv run --project" in result.detail
    assert "--directory" in result.detail
    assert "basicly worktree merge feat --bead basicly-onb.5 --mode full" in result.detail
    assert called == []


def test_a_known_dependency_defect_carries_no_lane_repair_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = verify.VerifyReport(
        "full",
        (
            verify.CheckResult(
                "pytest", "fail", 1, output="LockUnavailableError: another writer holds the ledger"
            ),
        ),
    )
    result = _landing(monkeypatch, tmp_path, report)

    assert result.unreliable is True
    assert result.checks == ()


def test_a_failed_landing_does_not_execute_a_diagnostic_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    command = (
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; p=Path('executions'); "
        "p.write_text(p.read_text()+'x' if p.exists() else 'x'); "
        "print('original failure'); sys.exit(7)",
    )
    config = verify.VerifyConfig((verify.VerifyCheck("failure", command, frozenset({"full"})),))
    monkeypatch.setattr(verify, "load_verify_config", lambda *_a: config)
    monkeypatch.setattr(
        merge, "load_session", lambda *_a: replace(_session(), worktree_path=str(tmp_path))
    )
    _patch_git(monkeypatch, _FakeGit({**_HAS_WORK, "status": _Proc(0, ""), "rebase": _Proc(0)}))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert (tmp_path / "executions").read_text() == "x"
    assert result.status == "verify-failed"
    assert result.checks[0].command == command
    assert result.checks[0].output == "original failure\n"
    assert result.checks[0].returncode == 7


@pytest.mark.parametrize("change", ["candidate", "destination", "filesystem", "runtime", "policy"])
def test_real_landing_refuses_movement_during_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    repo = _seed_repo(tmp_path)
    session = _lane_worktree(tmp_path, repo)
    _add_source(session.path, "b")
    monkeypatch.setattr(merge, "load_session", lambda *_a: session)
    destination = _git_here(repo, "rev-parse", "HEAD")

    def verify_then_move(cwd, *_args, **_kwargs):
        assert cwd == session.path
        if change == "candidate":
            _git_here(session.path, "commit", "--allow-empty", "-m", "candidate moved")
        elif change == "destination":
            _git_here(repo, "commit", "--allow-empty", "-m", "destination moved")
        elif change == "filesystem":
            (session.path / "sources/a.txt").write_text("user correction")
        elif change == "runtime":
            runtime = session.path / ".venv/Lib/site-packages"
            runtime.mkdir(parents=True)
            (runtime / "runtime.py").write_text("changed runtime")
        else:
            with (session.path / "basicly.toml").open("a") as stream:
                stream.write("\n[verify]\nfail_fast=false\n")
        return verify.VerifyReport("full", (verify.CheckResult("check", "pass", 0),))

    monkeypatch.setattr(verify, "run_verify", verify_then_move)
    result = merge.merge_worktree(repo, "feat", bead="basicly-fixture")
    assert result.status == merge.STALE_BRANCH
    assert not result.merged
    if change != "destination":
        assert _git_here(repo, "rev-parse", "HEAD") == destination
    if change == "filesystem":
        assert (session.path / "sources/a.txt").read_text() == "user correction"


def test_real_unchanged_landing_proves_the_captured_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _seed_repo(tmp_path)
    session = _lane_worktree(tmp_path, repo)
    _add_source(session.path, "b")
    candidate = _git_here(session.path, "rev-parse", "HEAD")
    monkeypatch.setattr(merge, "load_session", lambda *_a: session)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    result = merge.merge_worktree(repo, "feat", bead="basicly-fixture")
    assert result.merged, result.detail
    assert result.landed_head == candidate
    assert _git_here(repo, "rev-parse", "HEAD^2") == candidate
    assert not _git_here(repo, "status", "--porcelain")
