"""What a landing verdict carries out of the gate that refused it (basicly-3oxf0d).

The landing re-runs exactly the checks that failed, with capture on, for its
unreliable-gate test — and kept only a one-line `detail`, so the transcript existed in
memory and was dropped. Measured on `basicly-6ajmrc`: the repair brief written from that
verdict carried `"output": ""` and the next session re-ran `pyright` by hand to learn the
two errors the landing had already read.

Its own file rather than an addition to `test_merge.py`: the subject is one step of a
landing, as `test_merge_landing.py` is, and that module is frozen at exactly its own size.

The second test is the bound on the first. An unreliable verdict is no evidence against
the lane's work, so it must carry none — a landing that attached the re-run's output to it
would brief a repair against a gate that just passed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import merge, verify
from tests.test_merge import _FAILED, _GREEN, _HAS_WORK, _FakeGit, _patch_git, _Proc, _session


@pytest.fixture(autouse=True)
def _base_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve a clean base checkout on 'main', as every landing test needs.

    Declared here rather than imported: `test_merge.py`'s copy is a fixture, and a fixture
    reaches only the module that defines it.
    """
    monkeypatch.setattr(merge, "load_session", lambda _n, _r: _session())
    monkeypatch.setattr(merge, "current_branch", lambda _r: "main")


def _landing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rerun: verify.VerifyReport):
    """A landing whose gate fails and whose re-run answers *rerun*."""
    _patch_git(monkeypatch, _FakeGit({**_HAS_WORK, "status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: _FAILED)
    monkeypatch.setattr(verify, "rerun_failures", lambda *_a, **_k: rerun)
    return merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")


def test_a_reproduced_failure_carries_the_checks_the_re_run_captured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each failing check leaves with its argv and its output, and only the failing ones.

    A check that passed on the re-run is not what a repair has to fix, so it is not
    evidence; the argv rides along because the per-check command lives in a config the
    brief cannot read.
    """
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


def test_an_unreliable_verdict_carries_no_checks_at_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A gate that passed unchanged on re-run is not evidence, so it briefs nothing."""
    result = _landing(monkeypatch, tmp_path, _GREEN)

    assert result.unreliable is True
    assert result.checks == ()
