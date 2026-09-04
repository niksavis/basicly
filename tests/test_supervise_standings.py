"""Tests for the *lifetime* of a lane standing, not its shape (basicly-ncday7).

Split from `test_supervise_board.py` because that module already stands 4618 of 4000 tokens
on a `module_size` waiver, and `check_test_naming` permits the `test_<module>_<aspect>.py`
form for exactly this.

**Every other standing test publishes by calling `supervise.note_standing` directly**, so the
suite observed one write and never a *sequence* of them. That is why four retirement gaps
passed a green suite: a state was published on the way in and nothing dropped it on the way
out, so a lane kept reading `landing` after its landing returned, and a refusal that never
published left every lane reading `queued` for a pass that would start nothing. These tests
drive the real call paths and assert on what the registry holds *between* writes.
"""

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
    """No test inherits another's standings: the registry is process-wide by design."""
    supervise.clear_standings()
    yield
    supervise.clear_standings()


def _session() -> supervise.SessionState:
    """A session the patched routing never reads, so its graph can be empty."""
    return supervise.SessionState("basicly-root", "open", (), ())


def _carried(issue_id: str) -> supervise.LaneOutcome:
    """A lane whose work is already committed, which `_is_green` admits without a result."""
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
    """AC2: landings are serial, so exactly one lane may read `landing` at a time.

    The gap this covers: `_land_in_order` published `landing` per lane and retired it only
    when `route_outcomes`' frame returned, so a route that does not stop the queue
    (`bounced`, `re-dispatch`) left the routed lane published as landing while the next
    lane published its own. Three landable lanes then read three simultaneous landings,
    each with a growing duration, for the 3-8 minutes per landing still ahead.
    """
    ordered = tuple(_carried(one) for one in ("basicly-aaa", "basicly-bbb", "basicly-ccc"))
    landing_now: list[set[str]] = []

    def _bounced(
        _repo: Path,
        _session: supervise.SessionState,
        outcome: supervise.LaneOutcome,
        _landed: list[tuple[str, tuple[str, ...]]],
        _collisions: list[tuple[str, tuple[str, ...]]],
    ) -> supervise.RoutedOutcome:
        # Read inside the route, which is the only moment the claim is about.
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
    """A route that did not land says so, instead of leaving the landing claim standing."""
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


def test_the_pass_spend_refusal_reaches_a_lane_carrying_no_forecast(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC3: the ceiling that stopped the pass reaches every lane the pass was to start.

    The gap this covers: the refusal published for `counted` alone, which holds only the
    lanes with a real forecast. When the forecast walk fails it is suppressed and `counted`
    is empty, so nothing was published at all and every lane kept the `queued` standing
    `_admit_wip` gave it - "admitted, waiting for a runner slot" for a pass starting nothing.
    """
    admission = supervise.PassSpendAdmission(
        forecast_tokens=90,
        remaining_tokens=10,
        counted=(),
        unforecast=(),
        violation="the pass would spend 90 against 10 remaining",
        assumed=("basicly-aaa", "basicly-bbb"),
    )
    monkeypatch.setattr(supervise.decisions, "enqueue", lambda *_a, **_k: None)

    supervise.record_pass_refusal(work_repo, "basicly-root", admission)

    assert _states() == {"basicly-aaa": "refused", "basicly-bbb": "refused"}


def test_a_lane_whose_worktree_record_is_gone_publishes_the_refusal(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal before `live_lane` must pop the `queued` standing `_admit_wip` wrote.

    `live_lane` is the only site that retires `queued`, and every refusal inside
    `_dispatch_lane` returns before it. So a lane the engine refused rendered as one
    waiting for a runner slot - the inversion the record names, where a lane that ran
    published nothing and a lane that never started published `queued`.
    """
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
