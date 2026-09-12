from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import verify
from basicly.config import VerifyCheck

if TYPE_CHECKING:
    import pytest


def _sleeping_check(seconds: float) -> VerifyCheck:
    return VerifyCheck(
        name="slow",
        command=(sys.executable, "-c", f"import time; time.sleep({seconds})"),
        modes=frozenset({"full"}),
    )


def test_a_check_records_the_wall_clock_of_its_own_subprocess(tmp_path: Path) -> None:

    result = verify.run_check(_sleeping_check(0.05), tmp_path, "full")

    assert result.status == "pass"
    assert result.duration_s >= 0.05


def test_a_check_that_never_spawned_is_not_recorded_as_a_fast_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    staged = VerifyCheck(
        name="ruff", command=("ruff", "check"), modes=frozenset({"staged"}), staged_suffix=".py"
    )
    monkeypatch.setattr(verify, "staged_files", lambda *_a: [])

    result = verify._run(staged, ["ruff", "check"], tmp_path, "staged")

    assert result.status == "skip"
    assert result.duration_s == 0.0


def test_a_command_that_could_not_be_spawned_reports_the_failure_not_a_time(
    tmp_path: Path,
) -> None:

    missing = VerifyCheck(
        name="ghost", command=("basicly-no-such-tool",), modes=frozenset({"full"})
    )

    result = verify.run_check(missing, tmp_path, "full")

    assert result.status == "fail"
    assert result.duration_s == 0.0
