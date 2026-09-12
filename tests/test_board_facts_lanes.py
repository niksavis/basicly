from __future__ import annotations

from basicly import board_facts, board_sections, supervise


def _view(issue_id: str, *, live: bool) -> supervise.LaneView:
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
    fact = board_facts._lane_fact(
        _view("a", live=True), {"a": "build"}, {"a": 5_000_000}, {"a": "reading the gate"}, [_RUN]
    )
    assert fact.tokens == 5_000_000
    assert fact.note == "reading the gate"
    assert fact.model == "claude-opus-5"


def test_a_running_lane_does_not_inherit_the_last_dispatch_cost_or_occupancy() -> None:

    fact = board_facts._lane_fact(_view("a", live=True), {"a": "build"}, {"a": 0}, {}, [_RUN])
    assert fact.live is True
    assert fact.cost_usd is None
    assert fact.elapsed_s is None
    assert fact.context_used is None
    assert fact.context_window is None


def test_a_provisioned_lane_with_no_live_stream_is_not_reported_as_running() -> None:

    fact = board_facts._lane_fact(_view("a", live=True), {"a": "build"}, {}, {}, [_RUN])
    assert fact.live is False
    assert fact.provisioned is True
    assert fact.tokens == 11
    assert (fact.cost_usd, fact.elapsed_s) == (12.5, 900.0)
    row = board_sections.lanes([fact])[0]
    assert row["live"] is False
    assert row["provisioned"] is True


def test_a_finished_lane_carries_every_figure_its_run_record_holds() -> None:
    fact = board_facts._lane_fact(_view("a", live=False), {"a": "build"}, {}, {}, [_RUN])
    assert (fact.cost_usd, fact.elapsed_s) == (12.5, 900.0)
    assert (fact.context_used, fact.context_window) == (180_000, 1_000_000)


def test_a_lane_with_no_run_record_states_no_figure_it_was_not_given() -> None:
    fact = board_facts._lane_fact(_view("a", live=False), {"a": "build"}, {}, {}, [])
    assert fact.model == ""
    assert fact.note == ""
    assert (fact.cost_usd, fact.context_used) == (None, None)


def test_a_boolean_is_not_read_as_a_measurement() -> None:
    fact = board_facts._lane_fact(
        _view("a", live=False), {"a": "build"}, {}, {}, [{"cost": True, "context_tokens": False}]
    )
    assert fact.cost_usd is None
    assert fact.context_used is None


def test_a_lane_that_has_reported_zero_tokens_states_no_spend_at_all() -> None:

    view = supervise.LaneView(
        issue_id="a", status="open", worktree="a", branch="harness/a", live=True
    )
    fact = board_facts._lane_fact(view, {"a": "build"}, {"a": 0}, {}, [])
    assert fact.tokens is None
    assert "tokens" not in board_sections.lanes([fact])[0]


def test_the_zero_window_does_not_fall_through_to_a_previous_run() -> None:

    fact = board_facts._lane_fact(_view("a", live=True), {"a": "build"}, {"a": 0}, {}, [])
    assert _view("a", live=True).last_tokens == 11
    assert fact.tokens is None


def test_a_provisioned_lane_falls_back_exactly_like_a_finished_one() -> None:

    provisioned = board_facts._lane_fact(_view("a", live=True), {"a": "build"}, {}, {}, [])
    finished = board_facts._lane_fact(_view("a", live=False), {"a": "build"}, {}, {}, [])
    assert (provisioned.live, provisioned.provisioned) == (False, True)
    assert (finished.live, finished.provisioned) == (False, False)
    assert provisioned.tokens == finished.tokens == 11


def test_a_finished_lane_does_fall_back_to_its_last_recorded_run() -> None:
    fact = board_facts._lane_fact(_view("a", live=False), {"a": "build"}, {}, {}, [])
    assert fact.tokens == 11
