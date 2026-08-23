"""Tests for the lane-fact half of the board producer's caller-side derivations.

Split from ``test_board_facts`` along the responsibility line: everything here reads
``board_facts._lane_fact`` — what a lane card may claim about spend, liveness and run
history. The rule under test is the same as the parent module's: an unfillable fact is
an **absence**, never a zero.
"""

from __future__ import annotations

from basicly import board_facts, board_sections, supervise


def _view(issue_id: str, *, live: bool) -> supervise.LaneView:
    """A lane binding as the tracker holds it, with no run history of its own."""
    return supervise.LaneView(
        issue_id=issue_id,
        status="open",
        worktree=issue_id,
        branch=f"harness/{issue_id}",
        live=live,
        last_agent="claude",
        last_tokens=11,
    )


_RUN = {
    "agent": "claude",
    "model": "claude-opus-5",
    "cost": 12.5,
    "duration_s": 900.0,
    "context_tokens": 180_000,
    "context_window": 1_000_000,
}


def test_a_running_lane_carries_what_it_is_spending_and_saying_now() -> None:
    """The live stream's figures reach the card, and they beat the last run's."""
    fact = board_facts._lane_fact(
        _view("a", live=True), {"a": "build"}, {"a": 5_000_000}, {"a": "reading the gate"}, [_RUN]
    )
    assert fact.tokens == 5_000_000
    assert fact.note == "reading the gate"
    assert fact.model == "claude-opus-5"


def test_a_running_lane_does_not_inherit_the_last_dispatch_cost_or_occupancy() -> None:
    """Per-dispatch figures are omitted while a lane runs, rather than carried forward.

    The failure this refuses is quiet: last run's cost printed under a heading that says the
    lane is running now reads as this run's, and nothing on the card would say otherwise.
    `spending` carries the key with `0`: registered and not yet metered is still running.
    """
    fact = board_facts._lane_fact(_view("a", live=True), {"a": "build"}, {"a": 0}, {}, [_RUN])
    assert fact.live is True
    assert fact.cost_usd is None
    assert fact.elapsed_s is None
    assert fact.context_used is None
    assert fact.context_window is None


def test_a_provisioned_lane_with_no_live_stream_is_not_reported_as_running() -> None:
    """basicly-ze0po3: a worktree on disk outlives the agent that made it (rn0o.6 v. fi1i7z).

    `view.live` only says the tracker's worktree binding still exists, which a schema
    consumer reads as "still running" and stays true for hours after the process that made
    it has exited. `spending` names exactly the lanes with a live stream registered right
    now, so a lane absent from it is idle rather than running, and its last known figures
    speak instead of a blank "running" card carrying no note and no tokens. The worktree
    fact itself does not disappear - it travels as `provisioned`.
    """
    fact = board_facts._lane_fact(_view("a", live=True), {"a": "build"}, {}, {}, [_RUN])
    assert fact.live is False
    assert fact.provisioned is True
    assert fact.tokens == 11
    assert (fact.cost_usd, fact.elapsed_s) == (12.5, 900.0)
    row = board_sections.lanes([fact])[0]
    assert row["live"] is False
    assert row["provisioned"] is True


def test_a_finished_lane_carries_every_figure_its_run_record_holds() -> None:
    """The control for the case above: the same record, read off a lane that is not live."""
    fact = board_facts._lane_fact(_view("a", live=False), {"a": "build"}, {}, {}, [_RUN])
    assert (fact.cost_usd, fact.elapsed_s) == (12.5, 900.0)
    assert (fact.context_used, fact.context_window) == (180_000, 1_000_000)


def test_a_lane_with_no_run_record_states_no_figure_it_was_not_given() -> None:
    """An unfillable fact stays absent, which is this module's whole rule."""
    fact = board_facts._lane_fact(_view("a", live=False), {"a": "build"}, {}, {}, [])
    assert fact.model == ""
    assert fact.note == ""
    assert (fact.cost_usd, fact.context_used) == (None, None)


def test_a_boolean_is_not_read_as_a_measurement() -> None:
    """`True` is an `int` in Python, so a truthy field would otherwise price a lane at 1."""
    fact = board_facts._lane_fact(
        _view("a", live=False), {"a": "build"}, {}, {}, [{"cost": True, "context_tokens": False}]
    )
    assert fact.cost_usd is None
    assert fact.context_used is None


def test_a_lane_that_has_reported_zero_tokens_states_no_spend_at_all() -> None:
    """A live meter registered but not yet reporting omits `tokens` rather than stating 0.

    The stream is published the instant a dispatch starts, so `inflight_spend` carries a real
    `0` for every lane between its registration and its first metered turn - the exact window
    the defect was reported in. `0 tok` on a card reads as a measured figure and a free lane.
    """
    view = supervise.LaneView(
        issue_id="a", status="open", worktree="a", branch="harness/a", live=True
    )
    fact = board_facts._lane_fact(view, {"a": "build"}, {"a": 0}, {}, [])
    assert fact.tokens is None
    assert "tokens" not in board_sections.lanes([fact])[0]


def test_the_zero_window_does_not_fall_through_to_a_previous_run() -> None:
    """The zero window with something to fall back to, which is where it actually bit.

    `test_a_lane_that_has_reported_zero_tokens_states_no_spend_at_all` pins the same window
    on a lane with no run history, where a falsy test resolves to `None` by luck rather than
    by rule. Give the lane a previous dispatch and the two stop agreeing: a falsy test hands
    the window that dispatch's total, and the card reads as a lane that spent ten million
    tokens in its first second.
    """
    fact = board_facts._lane_fact(_view("a", live=True), {"a": "build"}, {"a": 0}, {}, [])
    assert _view("a", live=True).last_tokens == 11
    assert fact.tokens is None


def test_a_provisioned_lane_falls_back_exactly_like_a_finished_one() -> None:
    """basicly-ze0po3: the worktree still existing must not change what a card falls back to.

    That distinction is `provisioned` now, and `live` answers only whether the supervisor
    has a stream registered for the lane.
    """
    provisioned = board_facts._lane_fact(_view("a", live=True), {"a": "build"}, {}, {}, [])
    finished = board_facts._lane_fact(_view("a", live=False), {"a": "build"}, {}, {}, [])
    assert (provisioned.live, provisioned.provisioned) == (False, True)
    assert (finished.live, finished.provisioned) == (False, False)
    assert provisioned.tokens == finished.tokens == 11


def test_a_finished_lane_does_fall_back_to_its_last_recorded_run() -> None:
    """The control: the fallback is not removed, it is confined to lanes that are not live."""
    fact = board_facts._lane_fact(_view("a", live=False), {"a": "build"}, {}, {}, [])
    assert fact.tokens == 11
