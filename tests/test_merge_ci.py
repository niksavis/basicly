from __future__ import annotations

import argparse
import contextlib
from pathlib import Path

import pytest

from basicly import ci_landing, delivery_pr, merge, verify
from basicly import cli as basicly_cli
from tests.test_merge import _HAS_WORK, _FakeGit, _patch_git, _Proc, _session


@pytest.fixture
def lane(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(merge, "load_session", lambda _n, _r: _session())
    monkeypatch.setattr(merge, "current_branch", lambda _r: "main")
    _patch_git(
        monkeypatch,
        _FakeGit({
            **_HAS_WORK,
            "status": _Proc(0, ""),
            "rebase": _Proc(0),
            "merge-tree": _Proc(0, "merged-tree\n"),
            "merge": _Proc(0),
            "rev-parse": _Proc(0, "def456"),
            "merge-base": _Proc(0),
        }),
    )
    modes: list[str] = []

    def run_verify(_root: Path, mode: str, *_a: object, **_k: object) -> verify.VerifyReport:
        modes.append(mode)
        return verify.VerifyReport(mode, ())

    monkeypatch.setattr(verify, "run_verify", run_verify)
    monkeypatch.setattr(ci_landing, "local_checks", lambda _w: verify.VerifyReport("full", ()))
    return modes


def test_a_ci_landing_merges_on_the_proof_without_a_local_full_verify(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lane: list[str]
) -> None:
    proved: list[ci_landing.Candidate] = []
    monkeypatch.setattr(
        ci_landing, "prove", lambda _root, candidate: proved.append(candidate) or {}
    )

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5", verify_mode="ci")

    assert result.merged
    assert lane == []
    assert proved == [ci_landing.Candidate("feat", "basicly-onb.5", "abc123", "def456")]


@pytest.mark.usefixtures("lane")
def test_a_refused_proof_lands_nothing_and_names_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refuse(_root: Path, _candidate: ci_landing.Candidate) -> dict[str, str]:
        raise ci_landing.CiLandingError("quality-gates.yml run R ended failure in shard 3/12")

    monkeypatch.setattr(ci_landing, "prove", refuse)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5", verify_mode="ci")

    assert not result.merged
    assert result.status == "verify-failed"
    assert "ended failure in shard 3/12" in result.detail


@pytest.mark.usefixtures("lane")
def test_failing_local_only_checks_stop_a_ci_landing_before_any_push(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        ci_landing,
        "local_checks",
        lambda _w: verify.VerifyReport("full", (verify.CheckResult("ledger-fsck", "fail", 1),)),
    )
    monkeypatch.setattr(ci_landing, "prove", lambda *_a: pytest.fail("pushed after a failure"))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5", verify_mode="ci")

    assert result.status == "verify-failed"
    assert "local-only checks failed" in result.detail


@pytest.mark.usefixtures("lane")
def test_a_failed_local_only_check_shows_its_output_and_names_an_infrastructure_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failed = (
        verify.CheckResult(
            "ledger-fsck", "fail", 1, "2 broken events", output="line one\nbroken: ev-1\n"
        ),
        verify.CheckResult(
            "pytest-live-ledger",
            "fail",
            126,
            "cannot run pytest (Permission denied)",
            infrastructure_error=True,
        ),
    )
    monkeypatch.setattr(ci_landing, "local_checks", lambda _w: verify.VerifyReport("full", failed))
    monkeypatch.setattr(ci_landing, "prove", lambda *_a: pytest.fail("pushed after a failure"))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5", verify_mode="ci")

    assert "ledger-fsck failed: 2 broken events" in result.detail
    assert "broken: ev-1" in result.detail
    assert "verify-fail-feat-ledger-fsck.log" in result.detail
    assert (
        "pytest-live-ledger could not start (infrastructure error, exit 126): "
        "cannot run pytest (Permission denied)"
    ) in result.detail


@pytest.mark.usefixtures("lane")
def test_a_failed_local_only_check_prints_no_secret_or_home_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token = "ghp_" + "a" * 36
    failed = (
        verify.CheckResult(
            "ledger-fsck",
            "fail",
            1,
            "broken under /home/someone/repo",
            output=f"token {token}\nread /home/someone/repo/x.py\n",
        ),
    )
    monkeypatch.setattr(ci_landing, "local_checks", lambda _w: verify.VerifyReport("full", failed))
    monkeypatch.setattr(ci_landing, "prove", lambda *_a: pytest.fail("pushed after a failure"))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5", verify_mode="ci")

    assert token not in result.detail
    assert "/home/someone" not in result.detail
    assert "ledger-fsck failed" in result.detail


def test_a_pr_delivery_lands_through_the_adapter_and_never_dispatches_a_land_ref(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lane: list[str]
) -> None:
    delivered: list[tuple[ci_landing.Candidate, str]] = []

    def deliver(_root: Path, candidate: ci_landing.Candidate, base: str) -> delivery_pr.Landed:
        delivered.append((candidate, base))
        return delivery_pr.Landed("f" * 40, "https://example.invalid/pull/5", ("run",), ())

    monkeypatch.setattr(delivery_pr, "deliver", deliver)
    monkeypatch.setattr(ci_landing, "prove", lambda *_a: pytest.fail("dispatched a land ref"))

    result = merge.merge_worktree(
        tmp_path, "feat", bead="basicly-onb.5", verify_mode="ci", delivery="pr"
    )

    assert result.merged
    assert result.landed_head == "f" * 40
    assert lane == []
    assert delivered == [
        (ci_landing.Candidate("feat", "basicly-onb.5", "abc123", "def456"), "main")
    ]


@pytest.mark.usefixtures("lane")
def test_a_refused_pr_delivery_lands_nothing_and_names_the_cause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refuse(*_a: object) -> delivery_pr.Landed:
        raise delivery_pr.PrDeliveryError("quality-gates.yml run R ended failure")

    monkeypatch.setattr(delivery_pr, "deliver", refuse)

    result = merge.merge_worktree(
        tmp_path, "feat", bead="basicly-onb.5", verify_mode="ci", delivery="pr"
    )

    assert result.status == "verify-failed"
    assert "ended failure" in result.detail


def test_the_pr_command_never_pushes_the_base_and_cleans_up_the_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    landed = merge.MergeResult("feat", "merged", "pull/5 merged", landed_head="f" * 40)

    def merge_worktree(*_a: object, **kw: object) -> merge.MergeResult:
        calls.append(kw)
        return landed

    monkeypatch.setattr(basicly_cli, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        basicly_cli.landing_lock, "hold", lambda *_a, **_k: contextlib.nullcontext()
    )
    monkeypatch.setattr(basicly_cli.merge, "merge_worktree", merge_worktree)
    monkeypatch.setattr(ci_landing, "finish", lambda *_a: pytest.fail("pushed the base"))
    monkeypatch.setattr(ci_landing, "drop_refs", lambda *_a: pytest.fail("dropped land refs"))
    monkeypatch.setattr(
        basicly_cli, "_clean_landed_lane", lambda _root, name: calls.append({name: 1}) or []
    )
    args = argparse.Namespace(name="feat", bead="basicly-x1", mode="full", wait=0, delivery="pr")

    assert basicly_cli._cmd_worktree_merge(args) == 0
    assert calls == [{"bead": "basicly-x1", "verify_mode": "ci", "delivery": "pr"}, {"feat": 1}]
