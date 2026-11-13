from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tests.swarm_fixtures import _finding, _reply
from tests.test_swarm_climb import LENSES, _climb, _decision, _graded, _round


def _usage(directory: Path, agents: list[dict[str, Any]]) -> Path:
    reports = {
        path.stem: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.glob("*.md")
    }
    path = directory / "usage.json"
    path.write_text(json.dumps({"reports_sha256": reports, "agents": agents}), encoding="utf-8")
    return path


def _agent(lens_name: str, **changes: Any) -> dict[str, Any]:
    return {
        "id": f"run-{lens_name}",
        "lens": lens_name,
        "role": "reviewer",
        "tokens": 100,
        "cost_usd": 0.1,
        "duration_s": 2.0,
        "cost_kind": "cli-estimate",
        "source": f"fixture host metadata {lens_name}",
        **changes,
    }


def _metrics(state: Path) -> dict[str, Any]:
    return json.loads(state.read_text())["rounds"][-1]["metrics"]


def test_a_round_records_missing_usage_as_unknown(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    directory = tmp_path / "r1"
    _graded(directory, LENSES[0], {"x": 5, "y": 5})
    assert _round(state, directory, LENSES[0]) == 0
    usage = _metrics(state)["usage"]
    assert usage["missing_reviewers"] == ["a", "b", "c"]
    assert usage["tokens"] is None
    assert usage["cost_usd"] is None
    assert usage["known_tokens"] is None
    assert usage["scope"] == "reported agents only"


def test_a_round_records_report_bound_usage_for_reviewers_and_verifiers(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    directory = tmp_path / "r1"
    _graded(directory, LENSES[0], {"x": 5, "y": 5})
    agents = [_agent(lens) for lens in ["a", "b", "c"]]
    agents.append(_agent("a", id="verify-a", role="verifier", tokens=50, cost_usd=0.05))
    path = _usage(directory, agents)
    assert _round(state, directory, LENSES[0], "--usage", str(path)) == 0
    metrics = _metrics(state)
    assert metrics["usage"]["tokens"] == 350
    assert metrics["usage"]["cost_usd"] == pytest.approx(0.35)
    assert metrics["usage"]["agent_seconds"] == 8
    assert metrics["usage"]["cost_kinds"] == ["cli-estimate"]
    assert metrics["usage_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_partial_usage_keeps_known_sums_but_no_complete_total(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    directory = tmp_path / "r1"
    _graded(directory, LENSES[0], {"x": 5, "y": 5})
    path = _usage(directory, [_agent("a", tokens=0, cost_usd=0, duration_s=0)])
    assert _round(state, directory, LENSES[0], "--usage", str(path)) == 0
    usage = _metrics(state)["usage"]
    assert usage["missing_reviewers"] == ["b", "c"]
    assert usage["tokens"] is None
    assert usage["cost_usd"] is None
    assert usage["known_tokens"] == 0
    assert usage["known_cost_usd"] == 0


def test_finding_yield_counts_exact_cited_identities_across_lenses_and_rounds(
    tmp_path: Path,
) -> None:
    state = _climb(tmp_path)
    for number, lenses in enumerate(LENSES[:2], 1):
        directory = tmp_path / f"r{number}"
        for lens in lenses.split(","):
            _reply(
                directory,
                lens,
                {
                    "grades": {"x": number + 4, "y": number + 4, "w": number + 4},
                    "findings": [
                        _finding("important", "plan.md:10", "same defect"),
                        _finding("important", "intuition", "uncited defect"),
                    ],
                },
            )
        assert _round(state, directory, lenses) == 0
        findings = _metrics(state)["findings"]
        assert findings["reported_important"] == 6
        assert findings["uncited_important"] == 3
        assert findings["distinct_cited_important"] == 1
        assert findings["repeated_cited_important"] == number - 1
    assert _decision(state).startswith("continue")


@pytest.mark.parametrize(
    "change",
    [
        {"tokens": True},
        {"tokens": -1},
        {"tokens": 1.5},
        {"cost_usd": float("nan")},
        {"duration_s": -1},
        {"cost_kind": None},
        {"source": ""},
        {"role": "owner"},
        {"role": []},
        {"cost_kind": []},
        {"lens": "typo"},
    ],
)
def test_invalid_usage_refuses_the_round_without_changing_state(
    tmp_path: Path, change: dict[str, Any]
) -> None:
    state = _climb(tmp_path)
    directory = tmp_path / "r1"
    _graded(directory, LENSES[0], {"x": 5, "y": 5})
    path = _usage(directory, [_agent("a", **change)])
    before = state.read_bytes()
    with pytest.raises(SystemExit, match="usage"):
        _round(state, directory, LENSES[0], "--usage", str(path))
    assert state.read_bytes() == before


def test_usage_for_different_replies_is_refused(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    directory = tmp_path / "r1"
    _graded(directory, LENSES[0], {"x": 5, "y": 5})
    path = _usage(directory, [_agent("a")])
    _reply(directory, "a", {"grades": {"x": 6, "y": 6, "w": 6}, "findings": []})
    before = state.read_bytes()
    with pytest.raises(SystemExit, match=r"usage.*replies"):
        _round(state, directory, LENSES[0], "--usage", str(path))
    assert state.read_bytes() == before


def test_reused_dispatch_ids_cannot_double_count_usage(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    for number, lenses in enumerate(LENSES[:2], 1):
        directory = tmp_path / f"r{number}"
        _graded(directory, lenses, {"x": number + 4, "y": number + 4})
        path = _usage(directory, [_agent(lenses[0], id="same-dispatch")])
        if number == 1:
            assert _round(state, directory, lenses, "--usage", str(path)) == 0
        else:
            before = state.read_bytes()
            with pytest.raises(SystemExit, match=r"usage.*dispatch"):
                _round(state, directory, lenses, "--usage", str(path))
            assert state.read_bytes() == before


def test_retries_and_partial_agent_metrics_remain_visible(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    directory = tmp_path / "r1"
    _graded(directory, LENSES[0], {"x": 5, "y": 5})
    agents = [_agent(lens) for lens in ["a", "b", "c"]]
    agents.append(_agent("a", id="retry-a", tokens=None, cost_kind="reported"))
    path = _usage(directory, agents)
    assert _round(state, directory, LENSES[0], "--usage", str(path)) == 0
    usage = _metrics(state)["usage"]
    assert usage["tokens"] is None
    assert usage["known_tokens"] == 300
    assert usage["cost_usd"] == pytest.approx(0.4)
    assert usage["cost_kinds"] == ["cli-estimate", "reported"]
    assert len(usage["agents"]) == 4


def test_duplicate_dispatch_in_one_round_is_refused(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    directory = tmp_path / "r1"
    _graded(directory, LENSES[0], {"x": 5, "y": 5})
    path = _usage(directory, [_agent("a"), _agent("a")])
    before = state.read_bytes()
    with pytest.raises(SystemExit, match="already recorded"):
        _round(state, directory, LENSES[0], "--usage", str(path))
    assert state.read_bytes() == before


@pytest.mark.parametrize("raw", ['{"agents":[],"agents":[]}', "[]", '{"agents":', "null"])
def test_malformed_manifests_do_not_change_state(tmp_path: Path, raw: str) -> None:
    state = _climb(tmp_path)
    directory = tmp_path / "r1"
    _graded(directory, LENSES[0], {"x": 5, "y": 5})
    path = directory / "usage.json"
    path.write_text(raw, encoding="utf-8")
    before = state.read_bytes()
    with pytest.raises(SystemExit, match="usage"):
        _round(state, directory, LENSES[0], "--usage", str(path))
    assert state.read_bytes() == before


def test_environment_filename_is_refused_before_open(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    directory = tmp_path / "r1"
    _graded(directory, LENSES[0], {"x": 5, "y": 5})
    with pytest.raises(SystemExit, match="environment file"):
        _round(state, directory, LENSES[0], "--usage", str(directory / ".env-never-created"))


def test_legacy_rounds_print_unknown_usage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _climb(tmp_path)
    for number, lenses in enumerate(LENSES[:2], 1):
        directory = tmp_path / f"r{number}"
        _graded(directory, lenses, {"x": number + 4, "y": number + 4})
        assert _round(state, directory, lenses) == 0
        if number == 1:
            data = json.loads(state.read_text())
            del data["rounds"][0]["metrics"]
            state.write_text(json.dumps(data), encoding="utf-8")
    output = capsys.readouterr().out
    assert "| 1 | tokens | unknown |" in output
    assert "| 1 | distinct_cited_important | unknown |" in output
