"""Tests for the client-attach surfaces of ``basicly loop`` (basicly-kjc5.8).

``loop session``, ``loop watch``, ``loop decisions`` and ``loop answer`` are what a
*second* session sees of a run it is not driving, so every test here asserts on the
rendered observation rather than on supervisor state: a client that reads the wrong
lane count or misses a pending decision is wrong even when the supervisor is right.

Split out of ``test_cli_loop`` when the module-size ratchet caught that file growing.
The boundary is *attach* against *drive*: nothing here advances a node or runs a
ceremony, which is what the tests left behind do.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from basicly import cli, supervise
from basicly.decisions import DecisionItem

if TYPE_CHECKING:
    import pytest


# --- session (client attach, basicly-kjc5.8) ---------------------------------


def _observation(**overrides: object) -> supervise.Observation:
    defaults: dict[str, object] = {
        "root_issue": "basicly-epic",
        "root_status": "open",
        "children_total": 3,
        "children_open": 2,
        "done": False,
        "lanes": (
            supervise.LaneView(
                issue_id="basicly-epic.1",
                status="in_progress",
                worktree="basicly-epic-1",
                branch="harness/basicly-epic-1",
                live=True,
                last_agent="claude",
                last_outcome="executed",
                last_run_at="2026-07-25T10:00:00+00:00",
                last_tokens=1200,
            ),
        ),
        "pending_decisions": (
            DecisionItem(
                decision_id="basicly-epic.1#abc123",
                issue_id="basicly-epic.1",
                kind="validate",
                question="ship without the migration?",
            ),
        ),
        "holder": supervise.LockInfo(
            pid=4242, session_id="basicly-epic:live", root_issue="basicly-epic", age_s=3.0
        ),
        "holder_stale": False,
        "holder_on_this_root": True,
        "grant_level": "L2",
        "token_budget": 5000,
        "spent_tokens": 1200,
        "human_wait_s": 5_400,
        "delegated_wait_s": 45,
        "dispatch_s": 92.5,
    }
    defaults.update(overrides)
    return supervise.Observation(**defaults)  # type: ignore[arg-type]


def test_loop_session_prints_the_attach_surface(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Attaching renders the holder, each lane's last run, the queue, and grant spend."""
    monkeypatch.setattr(supervise, "observe", lambda *_a, **_k: _observation())

    assert cli.main(["loop", "session", "basicly-epic"]) == 0
    out = capsys.readouterr().out
    assert "root:       basicly-epic (open)" in out
    assert "basicly-epic:live (pid 4242) - heartbeat 3s old" in out
    assert "children:   3 total, 2 open" in out
    assert "basicly-epic.1 (in_progress) -> basicly-epic-1 on harness/basicly-epic-1 [live]" in out
    assert "last run: claude executed at 2026-07-25T10:00:00+00:00, 1200 tokens" in out
    assert "decisions:  1 pending" in out
    assert "ship without the migration?" in out
    assert "grant:      L2, 1200/5000 tokens spent" in out


def test_loop_session_reports_human_wait_apart_from_dispatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Wall clock is dominated by waiting on a human, so the rollup says so (kjc5.51).

    Reported beside dispatch and never folded into it: one is the compute the
    session bought, the other is the bottleneck a delivery forecast has to predict.
    """
    monkeypatch.setattr(supervise, "observe", lambda *_a, **_k: _observation())

    assert cli.main(["loop", "session", "basicly-epic"]) == 0
    assert "wait:       1.5h human, 45s delegated (dispatch 2m)" in capsys.readouterr().out


def test_loop_session_observes_the_labelled_cut_it_was_given(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A client has to be able to attach to the session that is actually running.

    A root can be supervised over its decomposition or over a labelled cut, and those
    are different lane sets — so a client that could not name the selector would report
    a running label pass as childless (basicly-1lpo).
    """
    seen: list[str | None] = []
    monkeypatch.setattr(
        supervise,
        "observe",
        lambda *_a, lane_label=None, **_k: (
            seen.append(lane_label) or _observation(lane_label=lane_label)
        ),
    )

    assert cli.main(["loop", "session", "basicly-epic", "--label", "release-v0.7.0"]) == 0
    assert seen == ["release-v0.7.0"]
    assert "select:     label 'release-v0.7.0'" in capsys.readouterr().out


def test_loop_session_names_an_unsupervised_root(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No supervisor is a reportable state and still exits 0 — the read succeeded."""
    monkeypatch.setattr(
        supervise,
        "observe",
        lambda *_a, **_k: _observation(
            holder=None,
            holder_on_this_root=False,
            lanes=(),
            pending_decisions=(),
            grant_level=None,
            token_budget=None,
            spent_tokens=0,
        ),
    )

    assert cli.main(["loop", "session", "basicly-epic"]) == 0
    out = capsys.readouterr().out
    assert "supervisor: (none running" in out
    assert "lane:       (no in-flight lanes)" in out
    assert "decisions:  none pending" in out
    assert "grant:      (none) - 0 tokens spent this session" in out


def test_loop_session_warns_that_a_stale_holder_may_be_taken_over(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A crashed holder must read as crashed, or a client waits on a dead session."""
    monkeypatch.setattr(
        supervise,
        "observe",
        lambda *_a, **_k: _observation(
            holder=supervise.LockInfo(
                pid=7, session_id="basicly-epic:crashed", root_issue="basicly-epic", age_s=312.0
            ),
            holder_stale=True,
        ),
    )

    assert cli.main(["loop", "session", "basicly-epic"]) == 0
    assert "heartbeat 312s old - STALE" in capsys.readouterr().out


def test_loop_session_names_a_holder_on_another_root(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The lock is a repo singleton, so say whose session the holder actually runs."""
    monkeypatch.setattr(
        supervise,
        "observe",
        lambda *_a, **_k: _observation(
            holder=supervise.LockInfo(
                pid=9, session_id="other:live", root_issue="basicly-other", age_s=2.0
            ),
            holder_on_this_root=False,
        ),
    )

    assert cli.main(["loop", "session", "basicly-epic"]) == 0
    out = capsys.readouterr().out
    assert "supervising basicly-other, not this session; heartbeat 2s old" in out


def test_loop_session_json_emits_the_whole_observation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--json is the client's machine surface: every field, nested lanes and queue."""
    monkeypatch.setattr(supervise, "observe", lambda *_a, **_k: _observation())

    assert cli.main(["loop", "session", "basicly-epic", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["root_issue"] == "basicly-epic"
    assert payload["holder"]["session_id"] == "basicly-epic:live"
    assert payload["lanes"][0]["branch"] == "harness/basicly-epic-1"
    assert payload["lanes"][0]["last_tokens"] == 1200
    assert payload["pending_decisions"][0]["kind"] == "validate"
    assert (payload["grant_level"], payload["token_budget"], payload["spent_tokens"]) == (
        "L2",
        5000,
        1200,
    )
    assert (payload["human_wait_s"], payload["delegated_wait_s"], payload["dispatch_s"]) == (
        5_400,
        45,
        92.5,
    )
    # A derived property asdict would drop, and the one flag a machine client acts on.
    assert payload["supervised"] is True
