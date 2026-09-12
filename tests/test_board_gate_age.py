from __future__ import annotations

from basicly import board_footer, board_wall
from tests.test_board_footer import _age
from tests.test_board_regions import _reads


def _stamped(recorded: str, *, passed: bool = False) -> dict:
    return {
        "mode": "full",
        "recorded_at": recorded,
        "passed": passed,
        "checks": [{"name": "pytest", "status": "fail" if not passed else "pass"}],
    }


def test_the_verdict_says_how_far_behind_the_document_it_was_taken() -> None:

    _cell, caption = board_footer.gates(
        _reads("wall-v1.json", gates=_stamped("2026-08-21T15:56:12Z")), _age()
    )
    assert "taken 46m 40s before this snapshot" in caption


def test_a_verdict_further_behind_than_the_window_leaves_the_failing_vocabulary() -> None:
    behind = board_footer.VERDICT_STALE_AFTER_S
    fresh = board_footer.gates(
        _reads("wall-v1.json", gates=_stamped("2026-08-21T16:42:00Z")), _age()
    )[0]
    assert fresh.state is not None and fresh.state.key == board_wall.FAIL

    stale = board_footer.gates(
        _reads("wall-v1.json", gates=_stamped("2026-08-21T14:00:00Z")), _age()
    )[0]
    assert stale.state is not None and stale.state.key == board_wall.STALE
    assert stale.value == "1 FAILING: pytest", "the failure itself must still be named"
    assert behind < 2 * 3600, "the window is wider than the case this test drives"


def test_a_stale_green_verdict_is_also_marked_rather_than_trusted() -> None:
    cell = board_footer.gates(
        _reads("wall-v1.json", gates=_stamped("2026-08-21T13:00:00Z", passed=True)), _age()
    )[0]
    assert cell.value == "GREEN"
    assert cell.state is not None and cell.state.key == board_wall.STALE


def test_a_verdict_with_no_readable_stamp_reports_no_age_rather_than_a_guess() -> None:
    for unreadable in ("", "not a stamp"):
        _cell, caption = board_footer.gates(
            _reads("wall-v1.json", gates=_stamped(unreadable)), _age()
        )
        assert "before this snapshot" not in caption
    assert (
        "before this snapshot"
        in board_footer.gates(
            _reads("wall-v1.json", gates=_stamped("2026-08-21T16:00:00Z")), _age()
        )[1]
    )


def test_a_verdict_stamped_ahead_of_the_document_reads_as_zero_not_a_future() -> None:
    _cell, caption = board_footer.gates(
        _reads("wall-v1.json", gates=_stamped("2026-08-21T18:00:00Z")), _age()
    )
    assert "taken 0s before this snapshot" in caption
    assert "-" not in caption.split("taken ")[1], "a negative age reached the page"
