"""Tests for the landing's own stage breakdown (basicly-tjhjmk).

Six landings on 2026-08-23 took 3-8 minutes each and which slice dominated was unmeasured,
so every candidate cut was a guess. These pin the instrument that ends that: a per-stage
wall clock recorded per landing, with the residual between the stages and the total named
rather than defined away.

Its own file rather than an addition to `test_merge.py`, which is at its size baseline and
may only shrink.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import merge, verify
from tests.test_merge import _HAS_WORK, _FakeGit, _patch_git, _Proc, _session

_BEAD = "basicly-tjhjmk"


def _ticker(*ticks: float):
    """A clock returning each of *ticks* in turn, then holding the last one."""
    remaining = list(ticks)

    def clock() -> float:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return clock


def _landed(monkeypatch: pytest.MonkeyPatch) -> _FakeGit:
    """Stub git, the session and verify so `merge_worktree` runs its whole sequence."""
    monkeypatch.setattr(merge, "load_session", lambda _n, _r: _session())
    monkeypatch.setattr(merge, "current_branch", lambda _r: "main")
    fake = _patch_git(
        monkeypatch,
        _FakeGit({
            **_HAS_WORK,
            "status": _Proc(0, ""),
            "rebase": _Proc(0),
            "merge-tree": _Proc(0),
            "merge": _Proc(0),
            "rev-parse": _Proc(0, "def456"),
            "merge-base": _Proc(0),
        }),
    )
    report = verify.VerifyReport(
        "full",
        (
            verify.CheckResult("pytest", "pass", 0, duration_s=84.35),
            verify.CheckResult("ruff", "pass", 0, duration_s=0.04),
            verify.CheckResult("pyright-linux", "pass", 0, duration_s=10.93),
        ),
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: report)
    return fake


def _recorded(repo: Path) -> list[dict]:
    payload = json.loads((repo / merge.LANDING_TIMINGS_FILE).read_text(encoding="utf-8"))
    return payload[_BEAD]


def test_the_stages_are_measured_apart_from_the_total_they_are_checked_against() -> None:
    """The demonstration, on exact numbers: the residual is measured, never assumed.

    Deriving `total_s` by summing the spans would make the breakdown add up by
    construction and prove nothing about the landing, so the two clocks are read
    independently and their difference is recorded as unattributed time.
    """
    clock = _ticker(100.0, 101.5, 104.0, 136.0, 140.0)
    landing = merge._Landing(Path("/nowhere"), _BEAD, clock=clock)

    landing.mark("preflight")
    landing.mark("rebase")
    landing.mark("verify")
    total = round(clock() - landing.started, 3)

    assert landing.stages == [("preflight", 1.5), ("rebase", 2.5), ("verify", 32.0)]
    assert sum(seconds for _, seconds in landing.stages) == 36.0
    # 4.0s of the 40s wall clock is in no stage: that gap is the finding the record
    # exists to surface, and a self-summing breakdown could never show it.
    assert total == 40.0


def test_a_landing_records_every_stage_it_ran_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The sequence `merge_worktree`'s comments number, as data rather than as prose."""
    _landed(monkeypatch)

    result = merge.merge_worktree(tmp_path, "feat", bead=_BEAD)

    assert result.merged is True
    (entry,) = _recorded(tmp_path)
    assert [stage for stage, _ in entry["stages"]] == [
        "preflight",
        "tracker-commit",
        "rebase",
        "regenerate",
        "verify",
        "probe",
        "merge",
    ]
    assert entry["status"] == "merged"


def test_the_recorded_breakdown_accounts_for_the_landings_wall_clock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Attributed plus unattributed is the total, and no stage is negative."""
    _landed(monkeypatch)

    merge.merge_worktree(tmp_path, "feat", bead=_BEAD)

    (entry,) = _recorded(tmp_path)
    assert entry["attributed_s"] + entry["unattributed_s"] == pytest.approx(
        entry["total_s"], abs=0.002
    )
    assert entry["attributed_s"] <= entry["total_s"]
    assert all(seconds >= 0 for _, seconds in entry["stages"])


def test_the_verify_stage_carries_the_checks_that_made_it_slow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stage that is 90% of a landing is useless without naming what inside it was.

    Slowest first and capped, so the record names the checks a cut would target rather
    than weighting the thirty that are collectively noise the same as the one that is not.
    """
    _landed(monkeypatch)

    merge.merge_worktree(tmp_path, "feat", bead=_BEAD)

    (entry,) = _recorded(tmp_path)
    assert entry["slowest_checks"] == [
        ["pytest", 84.35],
        ["pyright-linux", 10.93],
        ["ruff", 0.04],
    ]


def test_a_landing_that_stopped_early_records_only_the_stages_it_reached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bounced landing still spent the time; the record must not lose that sample."""
    _landed(monkeypatch)
    _patch_git(
        monkeypatch,
        _FakeGit({
            **_HAS_WORK,
            "status": _Proc(0, ""),
            "rebase": _Proc(1, "CONFLICT"),
            "diff": _Proc(0, ""),
        }),
    )

    result = merge.merge_worktree(tmp_path, "feat", bead=_BEAD)

    assert result.merged is False
    (entry,) = _recorded(tmp_path)
    assert [stage for stage, _ in entry["stages"]] == ["preflight", "tracker-commit", "rebase"]
    assert entry["status"] == result.status


def test_only_a_merged_detail_carries_the_headline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed detail is a remedy whose tail is load-bearing (basicly-fi1i7z).

    The negative half is the one that matters: telemetry appended to a refusal pushed the
    `basicly.d/<bead-id>.toml` a reader needs out of a 400-character frame once already.
    """
    _landed(monkeypatch)

    merged = merge.merge_worktree(tmp_path, "feat", bead=_BEAD)

    assert "landing " in merged.detail

    monkeypatch.setattr(
        merge,
        "_verify_for_landing",
        lambda *_a, **_k: merge.MergeResult("feat", "verify-failed", "verify full failed: ruff"),
    )
    refused = merge.merge_worktree(tmp_path, "feat", bead=_BEAD)

    assert refused.detail == "verify full failed: ruff"


def test_a_second_landing_appends_beside_the_first(tmp_path: Path) -> None:
    """One landing is a sample; the dominant slice is a question about a population."""
    for status in ("merged", "rebase-conflicts"):
        merge._Landing(tmp_path, _BEAD).close(merge.MergeResult("feat", status, "d"))

    assert [entry["status"] for entry in _recorded(tmp_path)] == ["merged", "rebase-conflicts"]


def test_an_unreadable_log_costs_the_measurement_and_never_the_landing(tmp_path: Path) -> None:
    """The landing is what the caller asked for; a corrupt telemetry file is not its problem."""
    path = tmp_path / merge.LANDING_TIMINGS_FILE
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")

    result = merge._Landing(tmp_path, _BEAD).close(merge.MergeResult("feat", "merged", "d"))

    assert result.status == "merged"
    assert [entry["status"] for entry in _recorded(tmp_path)] == ["merged"]
