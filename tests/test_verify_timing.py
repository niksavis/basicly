"""Tests for the per-check wall clock a verify run records (basicly-tjhjmk).

A landing's verify stage is most of its 3-8 minutes, and a stage with no split inside it
names nothing a cut could target: measured on this tree 2026-08-27, `pytest` alone is 84.4s
of a 132.4s full run. `CheckResult.duration_s` is what carries that split out to the
landing record.

Its own file rather than an addition to `test_verify.py`, which sits 18 tokens under its
size baseline and may only shrink.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import verify
from basicly.config import VerifyCheck

if TYPE_CHECKING:
    import pytest


def _sleeping_check(seconds: float) -> VerifyCheck:
    """A declared check that does nothing but occupy the clock for *seconds*."""
    return VerifyCheck(
        name="slow",
        command=(sys.executable, "-c", f"import time; time.sleep({seconds})"),
        modes=frozenset({"full"}),
    )


def test_a_check_records_the_wall_clock_of_its_own_subprocess(tmp_path: Path) -> None:
    """The lower bound is the assertion: no upper one, because a loaded runner is not a defect.

    Asserted against a process that provably took at least this long rather than against a
    window, so the test cannot fail on whichever CI runner is busiest that day.
    """
    result = verify.run_check(_sleeping_check(0.05), tmp_path, "full")

    assert result.status == "pass"
    assert result.duration_s >= 0.05


def test_a_check_that_never_spawned_is_not_recorded_as_a_fast_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A skip and a 0.0s check must not read alike, so the skip records no duration at all.

    A `staged` skip returns before the clock starts; crediting it a measured 0.0s would put
    it top of a "fastest checks" reading as if it had run and been instant.
    """
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
    """A failed spawn measures nothing, so it records nothing.

    Neither exit code is asserted: whether an absent tool surfaces as 127 or as 126 depends
    on what sits on PATH — this machine answers `Permission denied` for a name nothing
    provides — and that is the environment, not the contract.
    """
    missing = VerifyCheck(
        name="ghost", command=("basicly-no-such-tool",), modes=frozenset({"full"})
    )

    result = verify.run_check(missing, tmp_path, "full")

    assert result.status == "fail"
    assert result.duration_s == 0.0
