from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from basicly import supervise

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _no_standings() -> Iterator[None]:
    supervise.clear_standings()
    yield
    supervise.clear_standings()


def _session() -> supervise.SessionState:
    return supervise.SessionState("basicly-root", "open", (), ())


def _carried(issue_id: str) -> supervise.LaneOutcome:
    return supervise.LaneOutcome(
        issue_id=issue_id,
        runner_name="manual",
        result=None,
        needs_fact=None,
        occupancy=None,
        overrun=False,
        detail="carried with its work committed",
        dispatched=False,
    )


def _states() -> dict[str, str]:
    return {issue_id: hold.state for issue_id, hold in supervise.lane_standings().items()}


def test_only_the_lane_being_landed_now_reads_landing(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    ordered = tuple(_carried(one) for one in ("basicly-aaa", "basicly-bbb", "basicly-ccc"))
    landing_now: list[set[str]] = []

    def _bounced(
        _repo: Path,
        _session: supervise.SessionState,
        outcome: supervise.LaneOutcome,
        _landed: list[tuple[str, tuple[str, ...]]],
        _collisions: list[tuple[str, tuple[str, ...]]],
    ) -> supervise.RoutedOutcome:
        landing_now.append({one for one, state in _states().items() if state == "landing"})
        return supervise.RoutedOutcome(outcome.issue_id, "bounced", "re-dispatch next pass")

    monkeypatch.setattr(supervise, "_route_one", _bounced)
    monkeypatch.setattr(supervise.merge, "head_sha", lambda _root: "0" * 40)
    monkeypatch.setattr(
        supervise, "_attribute_pass_couplings", lambda _root, routed, _c, _l: routed
    )

    supervise._note_landing_queue(ordered)
    supervise._land_in_order(work_repo, _session(), ordered, None)

    assert landing_now == [{"basicly-aaa"}, {"basicly-bbb"}, {"basicly-ccc"}]


def test_a_bounced_landing_leaves_the_lane_refused_rather_than_landing(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ordered = (_carried("basicly-aaa"),)

    monkeypatch.setattr(
        supervise,
        "_route_one",
        lambda _r, _s, outcome, _l, _c: supervise.RoutedOutcome(
            outcome.issue_id, "bounced", "the rebase conflicted"
        ),
    )
    monkeypatch.setattr(supervise.merge, "head_sha", lambda _root: "0" * 40)
    monkeypatch.setattr(
        supervise, "_attribute_pass_couplings", lambda _root, routed, _c, _l: routed
    )

    supervise._note_landing_queue(ordered)
    supervise._land_in_order(work_repo, _session(), ordered, None)

    hold = supervise.lane_standings()["basicly-aaa"]
    assert hold.state == supervise.LANE_REFUSED
    assert hold.detail == "the rebase conflicted"


def test_a_lane_whose_worktree_record_is_gone_publishes_the_refusal(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    lane = supervise.AdoptedLane(
        issue_id="basicly-aaa",
        status="open",
        binding=cast("Any", SimpleNamespace(name="basicly-aaa")),
        live=True,
    )
    monkeypatch.setattr(supervise.worktree, "load_session", lambda _name, _root: None)
    supervise.note_standing(
        supervise.LANE_QUEUED, "admitted, waiting for a runner slot", lane.issue_id
    )

    outcome = supervise._dispatch_lane(
        work_repo,
        _session(),
        lane,
        cast("Any", SimpleNamespace(name="manual")),
        cast("Any", None),
    )

    assert "has no session record" in outcome.detail
    assert _states() == {"basicly-aaa": "refused"}
