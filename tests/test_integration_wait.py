"""The human-wait meter against a real ``br`` tracker (basicly-kjc5.51).

The meter measures an interval between two comment markers, which makes it rest
entirely on two properties of the tracker that no stub can vouch for: that ``br``
stamps every comment with a machine-readable ``created_at``, and that it reports
it back on ``comments list --json``. A fake agrees with whatever the fake was
written to believe — so if a ``br`` release renamed the field, dropped the zone
suffix, or emitted local time, every unit test here would stay green while the
shipped meter silently recorded nothing (or an interval hours wrong).

Also pinned here: the marker reaches ``.beads/issues.jsonl``. That is the whole
reason the evidence is a comment marker rather than a run-record field (D11) —
comments are exported, so a teammate's clone can read the wait; ``.basicly/usage/``
never leaves the machine that wrote it.

Only ``br`` is real; there is no git history or loop to drive, so the fixture is a
bare ``br init`` workspace like ``tests/test_integration_dor_scaffold.py``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from basicly import br, decisions, policy

needs_br = pytest.mark.skipif(
    br.which() is None, reason="the beads tracker (br) is not installed on this machine"
)

pytestmark = needs_br

# How far ahead of the tracker's stamp the answer is taken to land. Pinned rather
# than slept out: the interval under test must not depend on how long the test ran.
_WAITED_S = 600

# Tolerance for the tracker's own clock resolution: ``created_at`` has whole-second
# resolution and truncates, so the meter may read a second or two over. It covers
# *only* that. The real br round-trips between the ask and the answer also land in
# the interval, but their cost is measured per run rather than budgeted here — see
# :func:`_assert_interval`.
_SLACK_S = 5


def _assert_interval(waited_s: int, elapsed_s: float) -> None:
    """The injected offset must show up, allowing for the time the calls really took.

    The upper bound used to be a flat ``_WAITED_S + _SLACK_S``, which quietly
    asserted that two real br invocations complete within five seconds. On a
    loaded machine they do not: a full-suite run measured ``609 <= 605`` and
    failed a push for a defect that was not there (basicly-o7z5). Taking the
    overhead from a monotonic clock keeps the property under test — the pinned
    offset is what the meter reports — without also testing how fast the host is,
    per ``.claude/rules/platform-hermetic-tests.md``.
    """
    assert _WAITED_S <= waited_s <= _WAITED_S + elapsed_s + _SLACK_S


@pytest.fixture
def tracker(tmp_path: Path) -> Path:
    """A throwaway ``br`` workspace, so no probe bead touches the real tracker."""
    workspace = tmp_path / "tracker"
    workspace.mkdir()
    br.run_br(workspace, ["init", "--prefix", "probe"])
    return workspace


@pytest.fixture
def issue_id(tracker: Path) -> str:
    """One open bead to hang markers on."""
    out = br.run_br(tracker, ["create", "probe wait", "-t", "task", "--json"]).stdout
    return str(json.loads(out)["id"])


@pytest.fixture
def answered_late(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the harness clock :data:`_WAITED_S` past the tracker's own."""
    monkeypatch.setattr(policy, "_now", lambda: time.time() + _WAITED_S)


def test_the_tracker_stamps_and_reports_a_parseable_created_at(
    tracker: Path, issue_id: str
) -> None:
    """The one property the whole meter rests on, asserted against the real tool."""
    br.run_br(tracker, ["comments", "add", issue_id, "probe"])

    (comment,) = json.loads(br.run_br(tracker, ["comments", "list", issue_id, "--json"]).stdout)
    stamp = policy._parse_ts(str(comment.get("created_at", "")))

    assert stamp is not None, f"br reported no parseable created_at: {comment!r}"
    assert abs(stamp.timestamp() - time.time()) < 60  # the same instant, same zone


@pytest.mark.usefixtures("answered_late")
def test_a_checkpoint_wait_round_trips_through_the_real_tracker(
    tracker: Path, issue_id: str
) -> None:
    """Challenge to approval: the interval and the answerer come back off the bead."""
    started = time.monotonic()
    challenge = policy.approve_checkpoint_guarded(tracker, issue_id, "ship", interactive=False)
    assert challenge.status == "challenge"

    approved = policy.approve_checkpoint_guarded(
        tracker, issue_id, "ship", interactive=False, confirm=challenge.code
    )
    assert approved.status == "approved"
    elapsed = time.monotonic() - started

    (event,) = policy.wait_events(tracker, issue_id)
    assert (event.kind, event.subject) == ("checkpoint", "ship")
    assert (event.answered_by, event.delegated) == (policy.HUMAN_BY, False)
    _assert_interval(event.waited_s, elapsed)


@pytest.mark.usefixtures("answered_late")
def test_a_queued_decision_wait_round_trips_through_the_real_tracker(
    tracker: Path, issue_id: str
) -> None:
    """Enqueue to answer: the queue's own hold time, measured off its own markers."""
    started = time.monotonic()
    item = decisions.enqueue(tracker, issue_id, "needs-input", "which db?")

    decisions.answer(tracker, item.decision_id, "postgres", by="niksa")
    elapsed = time.monotonic() - started

    (event,) = policy.wait_events(tracker, issue_id)
    assert (event.wait_id, event.kind, event.subject) == (
        item.decision_id,
        "decision",
        "needs-input",
    )
    assert (event.answered_by, event.delegated) == ("niksa", False)
    _assert_interval(event.waited_s, elapsed)


@pytest.mark.usefixtures("answered_late")
def test_the_wait_marker_travels_in_the_tracker_export(tracker: Path, issue_id: str) -> None:
    """Why a marker and not a run-record field: a clone can read the evidence (D11)."""
    item = decisions.enqueue(tracker, issue_id, "needs-input", "which db?")
    decisions.answer(tracker, item.decision_id, "postgres", by="niksa")

    br.run_br(tracker, ["sync", "--flush-only"])
    export = (tracker / ".beads" / "issues.jsonl").read_text(encoding="utf-8")

    assert f"{policy.WAIT_MARKER} id={item.decision_id} kind=decision answered" in export
