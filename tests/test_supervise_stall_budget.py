"""Regression: a lane queued for a process-budget slot is not a stall (basicly-7cdeyd).

`_dispatch_lane`'s watchdog used to start counting before its own process-budget slot
was granted, so a lane held in the queue behind a full budget — waiting by design —
read as a silent, wedged dispatch. Kept in its own module because
`tests/test_supervise.py` is already frozen at its module-size cap and cannot grow
(basicly-u2hl.5).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import decisions, runner, supervise
from basicly.config import RunnerConfig
from tests.test_supervise import (
    _MANUAL_SPEC,
    _dispatch_sizing,
    _FakeBr,
    _install_br,
    _issue,
    _lane,
    _lookup,
    _session,
    _sizing,
)

if TYPE_CHECKING:
    import pytest


def test_dispatch_lane_queued_for_a_process_slot_is_not_flagged_stalled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lane waiting on a full process budget is queued by design, not stuck."""
    fake = _FakeBr({"epic.1": _issue("epic.1")})
    _install_br(monkeypatch, fake)
    monkeypatch.setattr(decisions, "_notify", lambda *_a, **_k: None)
    monkeypatch.setattr(
        supervise,
        "load_runner_config",
        lambda _r: RunnerConfig(
            specs=(_MANUAL_SPEC,), default="manual", stall_after=0.05, runner_timeout=3600.0
        ),
    )
    monkeypatch.setattr(
        supervise, "build_bundle", lambda *_a, **_k: supervise.DispatchBundle("epic.1", "p", ())
    )

    class _WtSession:
        worktree_path = str(tmp_path)

    monkeypatch.setattr(supervise.worktree, "load_session", lambda *_a, **_k: _WtSession())
    monkeypatch.setattr(supervise.loop, "record_run", lambda *_a, **_k: None)
    monkeypatch.setattr(
        supervise.decompose,
        "resolve_dispatch_sizing",
        lambda *_a: _lookup(_dispatch_sizing(20_000)),
    )
    monkeypatch.setattr(supervise, "lane_activity", lambda _cwd: "frozen")
    monkeypatch.setattr(
        supervise.runner,
        "run",
        lambda *_a, **_k: runner.RunResult(
            runner="manual", command=(), executed=True, returncode=0, stdout="done"
        ),
    )

    runner.reset_process_budget()
    outcome_box: dict = {}
    try:
        # One lane slot total, held here for longer than stall_after: epic.1 must
        # queue behind it rather than get one of its own.
        runner.configure_process_budget(runner.DECIDER_SLOTS + 1, 1)
        with runner.process_budget().slot(runner.LANE):
            thread = threading.Thread(
                target=lambda: outcome_box.update(
                    outcome=supervise._dispatch_lane(
                        tmp_path,
                        _session(_lane("epic.1")),
                        _lane("epic.1"),
                        _MANUAL_SPEC,
                        _sizing(),
                    )
                )
            )
            thread.start()
            time.sleep(0.2)  # well past stall_after while epic.1 still waits for the slot
            stalls_while_queued = [
                i for i in decisions.items_on(tmp_path, "epic.1") if i.kind == "stall"
            ]
            assert stalls_while_queued == []
        thread.join(5)
    finally:
        runner.reset_process_budget()

    assert outcome_box["outcome"].result is not None
    assert outcome_box["outcome"].result.returncode == 0
    # Never flagged, not merely resolved by dispatch end: the dispatch itself ran
    # too briefly for the watchdog it started with to ever cross stall_after.
    assert [i for i in decisions.items_on(tmp_path, "epic.1") if i.kind == "stall"] == []
