from __future__ import annotations

import pytest

from tests.kit_modules import KIT, load

evidence = load(KIT / "evidence_checks.py", "basicly_tracker_kit_evidence_checks")
KEYS = set(evidence.COMPLETED_CHECK_KEYS)


def _check(result: str) -> dict[str, object]:
    return {
        "criterion": "the release run is green",
        "command": ["gh", "run", "list", "--limit", "1"],
        "result": result,
        "exit_code": 0,
    }


@pytest.mark.parametrize(
    "result",
    ["pending the cut; verified after the tag push", "not run yet", "will run after landing"],
)
def test_a_confirm_result_that_says_the_check_has_not_run_is_refused(result: str) -> None:
    fault = evidence.entry_fault(_check(result), KEYS, True)

    assert fault is not None and "has not run" in fault


@pytest.mark.parametrize("result", ["81 passed, 2 skipped in 4.25s", "tutorial walk: 0 failure(s)"])
def test_a_confirm_result_with_real_output_is_accepted(result: str) -> None:
    assert evidence.entry_fault(_check(result), KEYS, True) is None
