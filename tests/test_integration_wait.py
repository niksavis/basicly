"""The human-wait meter against a real tracker (basicly-kjc5.51).

The meter measures an interval between two comment markers, so it rests on a property
no stub can vouch for: that the store stamps every comment and reports the stamp back.
A fake agrees with whatever it was written to believe, so a change to the stamp would
leave every unit test green while the shipped meter recorded an interval hours wrong.

Also pinned: the marker reaches the *committed* log. That is why the evidence is a
comment marker rather than a run-record field (D11) — the log travels, and
``.basicly/usage/`` never leaves the machine that wrote it.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from basicly import decisions, policy, tracker, tracker_paths
from tests import flipped_tracker

# Not a real name: the ledger redacts the running user's own on every write, so a fixture
# using it would read back as a placeholder and assert about the redactor instead.
ANSWERER = "an-operator"

# How far ahead of the tracker's stamp the answer is taken to land. Pinned rather
# than slept out: the interval under test must not depend on how long the test ran.
_WAITED_S = 600

# Tolerance for the tracker's own clock resolution: ``created_at`` has whole-second
# resolution, so the meter may read a second or two over. It covers *only* that. The
# real br round-trips between the ask and the answer also land in the interval, but
# their cost is measured per run rather than budgeted here — see
# :func:`_assert_interval`.
_SLACK_S = 5

# One whole second, because that is the resolution of the *start* of the interval.
# ``record_wait`` subtracts the tracker's second-resolution ``created_at`` from the
# local clock and then truncates with ``int()``, so the reported figure carries one
# second of quantisation error in whichever direction br's sub-second handling takes
# it. Applied to both ends deliberately: which direction that is belongs to the store, not
# to us, and a test that only tolerates one of them is asserting an undocumented
# property of a third-party tool (basicly-5h0g).
_STAMP_RESOLUTION_S = 1


def _assert_interval(waited_s: int, elapsed_s: float) -> None:
    """The injected offset must show up, allowing for the time the calls really took.

    Both bounds carry slack, and for the same reason: the interval is measured
    between two clocks of different resolutions and then truncated.

    The upper bound used to be a flat ``_WAITED_S + _SLACK_S``, which quietly
    asserted that two real tracker writes complete within five seconds. On a loaded
    machine they do not: a full-suite run measured ``609 <= 605`` and failed a push
    for a defect that was not there (basicly-o7z5). Taking the overhead from a
    monotonic clock keeps the property under test without also testing the host.

    The lower bound was left bare by that fix, which made it assert that br's stamp
    can never land ahead of the local clock reading. Under four-worker load it did:
    a full-suite run measured ``600 <= 599`` and failed the verify gate on a correct
    meter (basicly-5h0g). One second of quantisation is not a defect — a broken
    meter reports nothing like the pinned offset — so the floor is
    ``_WAITED_S - _STAMP_RESOLUTION_S`` rather than a wider tolerance that would
    stop discriminating.
    """
    assert _WAITED_S - _STAMP_RESOLUTION_S <= waited_s <= _WAITED_S + elapsed_s + _SLACK_S


@pytest.fixture
def probe_repo(tmp_path: Path) -> Path:
    """A throwaway ledger, so no probe record touches the real tracker."""
    repo = flipped_tracker.flipped_repo(tmp_path / "tracker")
    (repo / "basicly.toml").write_text(
        '[tracker]\nmode = "owned"\nprefix = "probe"\n', encoding="utf-8"
    )
    return repo


@pytest.fixture
def issue_id(probe_repo: Path) -> str:
    """One open record to hang markers on, minted by the store itself."""
    return tracker.create_record(probe_repo, ["create", "probe wait", "-t", "task", "--json"])


@pytest.fixture
def answered_late(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the harness clock :data:`_WAITED_S` past the tracker's own."""
    monkeypatch.setattr(policy, "_now", lambda: time.time() + _WAITED_S)


def test_the_tracker_stamps_and_reports_a_parseable_created_at(
    probe_repo: Path, issue_id: str
) -> None:
    """The one property the whole meter rests on, asserted against the real store."""
    tracker.add_comment(probe_repo, issue_id, "probe")

    (comment,) = tracker.read_comments(probe_repo, issue_id)
    stamp = policy._parse_ts(str(comment.get("created_at", "")))

    assert stamp is not None, f"the ledger recorded no parseable stamp: {comment!r}"
    assert abs(stamp.timestamp() - time.time()) < 60  # the same instant, same zone


@pytest.mark.usefixtures("answered_late")
def test_a_checkpoint_wait_round_trips_through_the_real_tracker(
    probe_repo: Path, issue_id: str
) -> None:
    """Challenge to approval: the interval and the answerer come back off the bead."""
    started = time.monotonic()
    challenge = policy.approve_checkpoint_guarded(probe_repo, issue_id, "ship", interactive=False)
    assert challenge.status == "challenge"

    approved = policy.approve_checkpoint_guarded(
        probe_repo, issue_id, "ship", interactive=False, confirm=challenge.code
    )
    assert approved.status == "approved"
    elapsed = time.monotonic() - started

    (event,) = policy.wait_events(probe_repo, issue_id)
    assert (event.kind, event.subject) == ("checkpoint", "ship")
    assert (event.answered_by, event.delegated) == (policy.HUMAN_BY, False)
    _assert_interval(event.waited_s, elapsed)


@pytest.mark.usefixtures("answered_late")
def test_a_queued_decision_wait_round_trips_through_the_real_tracker(
    probe_repo: Path, issue_id: str
) -> None:
    """Enqueue to answer: the queue's own hold time, measured off its own markers."""
    started = time.monotonic()
    item = decisions.enqueue(probe_repo, issue_id, "needs-input", "which db?")

    decisions.answer(probe_repo, item.decision_id, "postgres", by=ANSWERER)
    elapsed = time.monotonic() - started

    (event,) = policy.wait_events(probe_repo, issue_id)
    assert (event.wait_id, event.kind, event.subject) == (
        item.decision_id,
        "decision",
        "needs-input",
    )
    assert (event.answered_by, event.delegated) == (ANSWERER, False)
    _assert_interval(event.waited_s, elapsed)


@pytest.mark.usefixtures("answered_late")
def test_the_wait_marker_travels_in_the_committed_ledger(probe_repo: Path, issue_id: str) -> None:
    """Why a marker and not a run-record field: a clone can read the evidence (D11).

    Read off the committed log rather than through the seam — git is the transport, so
    what a teammate has is these bytes.
    """
    item = decisions.enqueue(probe_repo, issue_id, "needs-input", "which db?")
    decisions.answer(probe_repo, item.decision_id, "postgres", by=ANSWERER)

    logs = sorted((probe_repo / tracker_paths.LEDGER_DIR_NAME).glob("events-*.jsonl"))
    committed = "".join(path.read_text(encoding="utf-8") for path in logs)

    assert f"{policy.WAIT_MARKER} id={item.decision_id} kind=decision answered" in committed
