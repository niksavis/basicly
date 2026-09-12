from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import merge, verify
from tests.test_merge import _HAS_WORK, _FakeGit, _patch_git, _Proc, _session

_BEAD = "basicly-tjhjmk"


def _ticker(*ticks: float):
    remaining = list(ticks)

    def clock() -> float:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return clock


def _landed(monkeypatch: pytest.MonkeyPatch) -> _FakeGit:
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

    clock = _ticker(100.0, 101.5, 104.0, 136.0, 140.0)
    landing = merge._Landing(Path("/nowhere"), _BEAD, clock=clock)

    landing.mark("preflight")
    landing.mark("rebase")
    landing.mark("verify")
    total = round(clock() - landing.started, 3)

    assert landing.stages == [("preflight", 1.5), ("rebase", 2.5), ("verify", 32.0)]
    assert sum(seconds for _, seconds in landing.stages) == 36.0
    assert total == 40.0


def test_a_landing_records_every_stage_it_ran_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    for status in ("merged", "rebase-conflicts"):
        merge._Landing(tmp_path, _BEAD).close(merge.MergeResult("feat", status, "d"))

    assert [entry["status"] for entry in _recorded(tmp_path)] == ["merged", "rebase-conflicts"]


def test_an_unreadable_log_costs_the_measurement_and_never_the_landing(tmp_path: Path) -> None:
    path = tmp_path / merge.LANDING_TIMINGS_FILE
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")

    result = merge._Landing(tmp_path, _BEAD).close(merge.MergeResult("feat", "merged", "d"))

    assert result.status == "merged"
    assert [entry["status"] for entry in _recorded(tmp_path)] == ["merged"]
