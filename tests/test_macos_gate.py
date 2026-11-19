from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import macos_gate

MATRIX = "        os: [ubuntu-latest, windows-latest, macos-latest]\n"


def _repo(tmp_path: Path, text: str = MATRIX) -> Path:
    path = tmp_path / macos_gate.WORKFLOW
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return tmp_path


def _query(runs: dict[str, list[dict]], jobs: dict[int, list[dict]]) -> macos_gate.Query:
    def answer(argv: list[str]) -> str | None:
        if argv[:2] == ["run", "list"]:
            return json.dumps(runs[argv[argv.index("--workflow") + 1]])
        return json.dumps({"jobs": jobs[int(argv[2])]})

    return answer


def _run(number: int, event: str, at: str) -> dict:
    return {"databaseId": number, "event": event, "createdAt": at, "url": f"run/{number}"}


def _mac(conclusion: str, status: str = "completed") -> list[dict]:
    return [
        {"name": "Consumer hooks on ubuntu-latest", "status": "completed", "conclusion": "success"},
        {"name": "Consumer hooks on macos-latest", "status": status, "conclusion": conclusion},
    ]


def test_a_red_latest_macos_run_refuses_the_release_and_names_it(tmp_path: Path) -> None:
    query = _query(
        {
            "compatibility.yml": [_run(2, "schedule", "2026-11-19T03:17:00Z")],
            "release.yml": [_run(1, "push", "2026-11-18T10:00:00Z")],
        },
        {2: _mac("failure"), 1: _mac("success")},
    )
    reason = macos_gate.refusal(_repo(tmp_path), query)
    assert reason is not None
    assert "failure" in reason
    assert "run/2" in reason
    assert "gh workflow run compatibility.yml" in reason


def test_a_green_latest_macos_run_allows_the_release(tmp_path: Path) -> None:
    query = _query(
        {
            "compatibility.yml": [_run(2, "schedule", "2026-11-18T03:17:00Z")],
            "release.yml": [_run(3, "push", "2026-11-19T10:00:00Z")],
        },
        {2: _mac("failure"), 3: _mac("success")},
    )
    assert macos_gate.refusal(_repo(tmp_path), query) is None


def test_push_runs_without_macos_and_unfinished_jobs_are_passed_over(tmp_path: Path) -> None:
    query = _query(
        {
            "compatibility.yml": [
                _run(5, "push", "2026-11-19T11:00:00Z"),
                _run(4, "workflow_dispatch", "2026-11-19T10:00:00Z"),
                _run(2, "schedule", "2026-11-19T03:17:00Z"),
            ],
            "release.yml": [],
        },
        {4: _mac("", status="in_progress"), 2: _mac("cancelled")},
    )
    reason = macos_gate.refusal(_repo(tmp_path), query)
    assert reason is not None
    assert "cancelled" in reason
    assert "run/2" in reason


@pytest.mark.parametrize(
    ("runs", "expected"),
    [(None, "cannot read"), ({"compatibility.yml": [], "release.yml": []}, "no completed")],
)
def test_an_unreadable_or_empty_history_refuses_by_name(
    tmp_path: Path, runs: dict | None, expected: str
) -> None:
    def query(argv: list[str]) -> str | None:
        if runs is None:
            return None
        return json.dumps(runs[argv[argv.index("--workflow") + 1]])

    reason = macos_gate.refusal(_repo(tmp_path), query)
    assert reason is not None
    assert expected in reason


def test_a_repository_without_a_macos_job_is_not_asked(tmp_path: Path) -> None:
    def query(argv: list[str]) -> str | None:
        raise AssertionError(argv)

    assert macos_gate.refusal(tmp_path, query) is None
    assert macos_gate.refusal(_repo(tmp_path, "        os: [ubuntu-latest]\n"), query) is None
