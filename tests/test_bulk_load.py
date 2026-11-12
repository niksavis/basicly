from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / ".scripts" / "bulk_load.py"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bulk = _load(SCRIPT, "bulk_load")


def _passing() -> dict[str, object]:
    return {
        "p95_s": 200.0,
        "written": 3,
        "seen": 3,
        "sessions_without_events": [],
        "queued_after_stop": {"m0": 0, "m1": 0},
        "errors": 0,
    }


@pytest.mark.parametrize(
    ("share", "expected"),
    [(0.5, 5.0), (0.95, 10.0), (1.0, 10.0), (0.1, 1.0)],
)
def test_percentile_takes_the_nearest_rank(share: float, expected: float) -> None:
    values = [10.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]

    assert bulk.percentile(values, share) == expected


def test_percentile_of_no_sample_is_unknown() -> None:
    assert bulk.percentile([], 0.95) is None


def test_a_passing_report_has_no_failures() -> None:
    assert bulk.failures(_passing()) == []


@pytest.mark.parametrize(
    ("change", "named"),
    [
        ({"p95_s": 240.1}, "above 240.0 s"),
        ({"p95_s": None}, "p95 visibility is unknown"),
        ({"seen": 2}, "3 events written but 2 seen"),
        ({"sessions_without_events": ["s3"]}, "sessions wrote no event"),
        ({"queued_after_stop": {"m0": 0, "m1": 2}}, "a queue is not empty after stop"),
        ({"queued_after_stop": {"m0": -1}}, "a queue is not empty after stop"),
        ({"errors": 1}, "1 kit commands failed"),
    ],
)
def test_the_gate_names_each_failure(change: dict[str, object], named: str) -> None:
    found = bulk.failures({**_passing(), **change})

    assert len(found) == 1
    assert named in found[0]


def test_p95_at_the_limit_passes() -> None:
    assert bulk.failures({**_passing(), "p95_s": bulk.P95_LIMIT_S}) == []


def test_the_child_environment_drops_ci_and_session_settings() -> None:
    base = {
        "CI": "true",
        "BASICLY_TRACKER_EPHEMERAL": "1",
        "BASICLY_TRACKER_SESSION": "s9",
        "PATH": "/bin",
    }

    assert bulk.child_env(base) == {"PATH": "/bin"}


@pytest.mark.parametrize(
    ("answer", "kind"),
    [
        (None, "failed"),
        ({"hook": "stop", "skipped": "lock", "queued": 2}, "skipped"),
        ({"hook": "stop", "shared": {"deferred": "another share"}}, "deferred"),
        ({"hook": "stop", "shared": {"shared": 3, "pending": 0}}, "shared"),
    ],
)
def test_a_stop_answer_is_classified(answer: dict[str, object] | None, kind: str) -> None:
    assert bulk.stop_answer(answer) == kind


def test_the_schedule_writes_ten_events_per_session_hour_and_prompts_every_two_minutes() -> None:
    plan = bulk.schedule(bulk.Load(sessions=2, machines=1, minutes=60.0, seed=3, scale=1.0))
    kinds = [(a.kind, a.session) for a in plan]

    assert kinds.count(("write", 0)) == kinds.count(("write", 1)) == 10
    assert kinds.count(("prompt", 0)) == kinds.count(("prompt", 1)) == 30
    assert kinds.count(("read", -1)) == 120
    assert [a.at for a in plan] == sorted(a.at for a in plan)


def test_the_schedule_divides_every_interval_by_the_scale() -> None:
    plan = bulk.schedule(bulk.Load(sessions=1, machines=1, minutes=60.0, seed=1, scale=4.0))

    assert max(a.at for a in plan) < 900.0
    assert sum(a.kind == "write" for a in plan) == 10


def test_proof_t_bulk_small_load_sees_every_event_and_empties_every_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CI", "true")
    load = bulk.Load(sessions=4, machines=2, minutes=6.0, seed=1, scale=8.0)

    report = bulk.measure(load)

    assert report["errors"] == 0, report["first_errors"]
    assert report["sessions_without_events"] == []
    per_session = report["events_per_session"]
    assert isinstance(per_session, dict)
    assert sum(per_session.values()) == report["written"]
    assert report["written"] >= 4
    assert report["seen"] == report["written"]
    assert report["seen_after_stop"] < report["written"]
    assert report["queued_after_stop"] == {"m0": 0, "m1": 0}
    assert report["note"] == bulk.UNSCALED
    assert bulk.failures(report) == []
