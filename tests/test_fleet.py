"""Tests for the cross-repo fleet rollup (basicly-h0f0).

The rollup is read-only and resilient: it discovers basicly repos under a
workspace root, summarizes each one's run-records, and folds an injected
per-repo status snapshot in — a single bad repo becomes an error entry, never a
failed rollup.
"""

from __future__ import annotations

import json
from pathlib import Path

from basicly import fleet, run_record


def _make_repo(root: Path, name: str, *, basicly: bool = True) -> Path:
    repo = root / name
    repo.mkdir()
    if basicly:
        (repo / ".basicly").mkdir()
    return repo


def _write_records(repo: Path, data: dict) -> None:
    records = repo / run_record.RUN_RECORDS_FILE
    records.parent.mkdir(parents=True, exist_ok=True)
    records.write_text(json.dumps(data), encoding="utf-8")


def test_discover_repos_finds_basicly_dirs_sorted(tmp_path: Path) -> None:
    """Only immediate, non-hidden subdirs with a .basicly/ dir count, name-sorted."""
    _make_repo(tmp_path, "zebra")
    _make_repo(tmp_path, "alpha")
    _make_repo(tmp_path, "plain", basicly=False)
    _make_repo(tmp_path, ".hidden")
    (tmp_path / "afile").write_text("x", encoding="utf-8")
    found = fleet.discover_repos(tmp_path)
    assert [p.name for p in found] == ["alpha", "zebra"]


def test_discover_repos_tolerates_missing_root(tmp_path: Path) -> None:
    """A non-existent or non-directory root yields an empty list, never raises."""
    assert fleet.discover_repos(tmp_path / "nope") == []
    afile = tmp_path / "afile"
    afile.write_text("x", encoding="utf-8")
    assert fleet.discover_repos(afile) == []


def test_run_record_summary_empty_when_no_records(tmp_path: Path) -> None:
    """A repo with no run-records file reports zeroes, not None."""
    summary = fleet.run_record_summary(tmp_path)
    assert summary == {
        "total_runs": 0,
        "by_outcome": {},
        "agents": [],
        "models": [],
        "beads_with_runs": 0,
    }


def test_run_record_summary_aggregates_outcomes_agents_models(tmp_path: Path) -> None:
    """Totals, per-outcome counts, and distinct agents/models roll up across beads."""
    _write_records(
        tmp_path,
        {
            "b1": [
                {"outcome": "executed", "agent": "claude", "model": "opus"},
                {"outcome": "failed", "agent": "claude", "model": None},
            ],
            "b2": [{"outcome": "executed", "agent": "codex", "model": "gpt"}],
            "b3": [],  # a bead with no runs does not count
        },
    )
    summary = fleet.run_record_summary(tmp_path)
    assert summary["total_runs"] == 3
    assert summary["by_outcome"] == {"executed": 2, "failed": 1}
    assert summary["agents"] == ["claude", "codex"]
    assert summary["models"] == ["gpt", "opus"]
    assert summary["beads_with_runs"] == 2


def test_run_record_summary_tolerates_corrupt_file(tmp_path: Path) -> None:
    """A corrupt records file degrades to an empty summary, never raises."""
    records = tmp_path / run_record.RUN_RECORDS_FILE
    records.parent.mkdir(parents=True, exist_ok=True)
    records.write_text("{not json", encoding="utf-8")
    assert fleet.run_record_summary(tmp_path)["total_runs"] == 0


def test_fleet_report_rolls_up_repos_and_totals(tmp_path: Path) -> None:
    """The rollup carries schema, root, per-repo entries, and aggregated totals."""
    a = _make_repo(tmp_path, "alpha")
    _make_repo(tmp_path, "beta")
    _write_records(a, {"b1": [{"outcome": "executed", "agent": "claude"}]})

    def _status(repo: Path) -> dict:
        return {"repo_kind": "consumer", "name": repo.name}

    report = fleet.fleet_report(tmp_path, _status)
    assert report["schema_version"] == fleet.FLEET_SCHEMA_VERSION
    assert report["workspace_root"] == str(tmp_path)
    assert [r["name"] for r in report["repos"]] == ["alpha", "beta"]
    assert report["repos"][0]["status"] == {"repo_kind": "consumer", "name": "alpha"}
    assert report["totals"] == {
        "repos": 2,
        "total_runs": 1,
        "by_outcome": {"executed": 1},
    }


def test_fleet_report_captures_a_failing_repo_as_error(tmp_path: Path) -> None:
    """A repo whose status snapshot raises becomes an error entry; the rollup survives."""
    _make_repo(tmp_path, "good")
    _make_repo(tmp_path, "bad")

    def _status(repo: Path) -> dict:
        if repo.name == "bad":
            raise ValueError("broken install")
        return {"ok": True}

    report = fleet.fleet_report(tmp_path, _status)
    entries = {r["name"]: r for r in report["repos"]}
    assert entries["good"]["status"] == {"ok": True}
    assert entries["bad"]["status"]["error"] == "ValueError: broken install"
    # The failing repo still contributes its (empty) run summary and the totals stand.
    assert report["totals"]["repos"] == 2
