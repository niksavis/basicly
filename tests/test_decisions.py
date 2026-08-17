"""Tests for the decision queue engine (basicly-kjc5.4, design 7.1/7.3).

The queue is durable markers over ``br`` with no side-state: ids are
content-derived (idempotent enqueue), answers are recorded in place with
attribution, the notify hook fires once per new human-required item, and the
decider's authority is corpus-bounded — abstentions, unparseable output, and
the per-session decision cap all leave the item with the human.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from basicly import br, decision_marker, decisions, policy, run_record, runner
from basicly.config import PolicyConfig, RunnerConfig


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


# The tracker stamp a comment a test seeded directly (never written through the
# fake's `comments add`) reads as.
_EPOCH = "2026-01-01T00:00:00Z"


class _FakeBr:
    """br stand-in: per-issue comments plus `show` records for the session walk."""

    def __init__(self, records: dict[str, dict] | None = None) -> None:
        self.records = records or {}
        self.comments: dict[str, list[str]] = {}
        # br stamps every comment with a created_at, and the wait meter
        # (basicly-kjc5.51) measures from it. Stamps are keyed by position so a
        # test that seeds self.comments directly still gets the default, and `now`
        # is the tracker's clock a test advances between writes.
        self.now = _EPOCH
        self.stamps: dict[tuple[str, int], str] = {}

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:2] == ["comments", "list"]:
            texts = self.comments.get(args[2], [])
            listing = [
                {"text": text, "created_at": self.stamps.get((args[2], i), _EPOCH)}
                for i, text in enumerate(texts)
            ]
            return _Proc(json.dumps(listing))
        if args[:2] == ["comments", "add"]:
            texts = self.comments.setdefault(args[2], [])
            texts.append(args[3])
            self.stamps[(args[2], len(texts) - 1)] = self.now
            return _Proc("")
        if args[:1] == ["show"]:
            record = self.records.get(args[1], {"status": "open", "dependents": []})
            return _Proc(json.dumps([record]))
        raise AssertionError(f"unexpected br call: {args}")


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr) -> None:
    # invoke_decider consults D3's spend ceiling (basicly-kjc5.23), and policy still
    # reads br through its own alias for the subcommands it spawns directly.
    monkeypatch.setattr(policy, "_write", fake)
    # Neither the record read nor the marker traffic is one of those: they go through
    # `br.read_record` and `br.add_comment`/`br.read_comments`, the seams every consumer
    # in the package shares (basicly-tcmy.14, basicly-s5li). `decisions` has no alias of
    # its own left — every call it makes is a marker.
    monkeypatch.setattr(br, "run_br", fake)
    monkeypatch.setattr(br, "try_run_br", fake)


def _no_notify(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []
    monkeypatch.setattr(decisions, "_notify", lambda _r, item: calls.append(item))
    return calls


# --- Enqueue / answer / pending -----------------------------------------------


def test_enqueue_is_idempotent_per_content(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Re-enqueueing the same blocked fact returns the item without a new marker."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    notified = _no_notify(monkeypatch)

    first = decisions.enqueue(tmp_path, "epic.1", "needs-input", "which db?", "docs conflict")
    again = decisions.enqueue(tmp_path, "epic.1", "needs-input", "which db?", "docs conflict")

    assert first.decision_id == again.decision_id
    assert first.decision_id.startswith("epic.1#")
    assert len(fake.comments["epic.1"]) == 1
    assert len(notified) == 1  # no duplicate notification either


def test_enqueue_rejects_unknown_kind(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The kind vocabulary is closed; a typo must not create an unroutable item."""
    _install(monkeypatch, _FakeBr())
    with pytest.raises(ValueError, match="unknown decision kind"):
        decisions.enqueue(tmp_path, "epic.1", "vibe", "q")


def test_answer_round_trips_with_attribution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An answer lands in place on the same bead and folds into the item read."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    item = decisions.enqueue(tmp_path, "epic.1", "needs-input", "which db?")

    answered = decisions.answer(tmp_path, item.decision_id, "postgres", by="human")

    assert answered.answer == "postgres"
    assert answered.answered_by == "human"
    stored = decisions.get(tmp_path, item.decision_id)
    assert stored is not None and not stored.pending
    assert stored.answer == "postgres"


def test_answer_refuses_missing_and_double_answers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The first answer wins; a second answerer must read it, not overwrite it."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    with pytest.raises(ValueError, match="no decision"):
        decisions.answer(tmp_path, "epic.1#abcdef", "x", by="human")
    item = decisions.enqueue(tmp_path, "epic.1", "needs-input", "which db?")
    decisions.answer(tmp_path, item.decision_id, "postgres", by="human")
    with pytest.raises(ValueError, match="already answered"):
        decisions.answer(tmp_path, item.decision_id, "mysql", by="human")


def test_pending_scans_the_session_tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`loop decisions` is a pure read over the root's transitive child tree."""
    child = {"id": "epic.1", "dependency_type": "parent-child"}
    fake = _FakeBr(records={"epic": {"status": "open", "dependents": [child]}})
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    kept = decisions.enqueue(tmp_path, "epic.1", "needs-input", "which db?")
    answered = decisions.enqueue(tmp_path, "epic", "escalation", "rework cap on verify")
    decisions.answer(tmp_path, answered.decision_id, "park it", by="human")

    items = decisions.pending(tmp_path, "epic")

    assert [i.decision_id for i in items] == [kept.decision_id]


# --- The session is the track, not the descent (basicly-tcmy.28) ------------


def _gating_track() -> _FakeBr:
    """A root that gates work it did not parent — the basicly-jr0l.40 topology.

    ``gated`` reaches the session only through the root's ``blocks`` dependency. A
    bead's parent is its epic of origin and nothing is re-parented, so a release
    root holds most of its track this way; on the live tracker it was 14 of the 69
    beads under ``basicly-kjc5``.
    """
    return _FakeBr(
        records={
            "epic": {
                "status": "open",
                "dependents": [{"id": "epic.1", "dependency_type": "parent-child"}],
                "dependencies": [{"id": "gated", "dependency_type": "blocks"}],
            },
            "epic.1": {"status": "open", "dependents": [], "dependencies": []},
            "gated": {"status": "open", "dependents": [], "dependencies": []},
        }
    )


def test_a_delegated_answer_on_a_gated_bead_counts_against_the_runaway_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The meter guarding ``decider_max_decisions`` has to see the whole session.

    This module read a parent-child-only walk while the grant it is metered against
    read a wider one, so answers recorded on gated beads were free: the decider
    could run past its cap by however many beads the two walks disagreed on
    (basicly-tcmy.30). Undercounting is the dangerous direction — the cap exists to
    stop a runaway loop, and a cap that cannot be reached is not a cap.
    """
    _install(monkeypatch, _gating_track())
    _no_notify(monkeypatch)
    item = decisions.enqueue(tmp_path, "gated", "needs-input", "which db?")
    decisions.answer(
        tmp_path, item.decision_id, "postgres", by=f"{decisions.DECIDER_BY_PREFIX}claude"
    )

    assert decisions.decider_answers_count(tmp_path, "epic") == 1


def test_an_escalation_on_a_gated_bead_is_reported_as_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A question a human must answer cannot be invisible because of the edge type.

    ``pending`` feeds the ``blocked: N decision(s)`` line and ``has_pending`` holds
    a lane, so an item the walk cannot reach is one nobody is told to answer and
    nothing waits for — on a bead squarely inside the grant.
    """
    _install(monkeypatch, _gating_track())
    _no_notify(monkeypatch)
    item = decisions.enqueue(tmp_path, "gated", "escalation", "rework cap on verify")

    assert [i.decision_id for i in decisions.pending(tmp_path, "epic")] == [item.decision_id]


def _write_export(repo_root: Path, statuses: dict[str, str]) -> None:
    """The committed JSONL export `closed_ids` reads, with one record per id."""
    beads = repo_root / ".beads"
    beads.mkdir(parents=True, exist_ok=True)
    (beads / "issues.jsonl").write_text(
        "\n".join(json.dumps({"id": i, "status": s}) for i, s in statuses.items()) + "\n",
        encoding="utf-8",
    )


def test_pending_drops_items_on_closed_beads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A question about finished work is not outstanding human work.

    Four shipped-and-closed beads still reported a pending ship ask after the
    2026-08-01 proof run. It was not cosmetic: `supervise.delegate_decisions` hands
    every pending item to the decider, so the queue spent tokens deciding closed beads
    (basicly-jr0l.24).
    """
    child = {"id": "epic.1", "dependency_type": "parent-child"}
    fake = _FakeBr(records={"epic": {"status": "open", "dependents": [child]}})
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    stale = decisions.enqueue(tmp_path, "epic.1", "checkpoint", "approve the ship checkpoint")
    live = decisions.enqueue(tmp_path, "epic", "escalation", "widen the band?")

    _write_export(tmp_path, {"epic": "open", "epic.1": "open"})
    assert {i.decision_id for i in decisions.pending(tmp_path, "epic")} == {
        stale.decision_id,
        live.decision_id,
    }, "control: while the bead is open its item is outstanding"

    _write_export(tmp_path, {"epic": "open", "epic.1": "closed"})
    assert [i.decision_id for i in decisions.pending(tmp_path, "epic")] == [live.decision_id]


def test_pending_reports_everything_when_the_export_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No export means no status to filter on, so the queue must not hide itself.

    Degrading to the pre-fix behaviour is the safe direction: showing a settled question
    wastes a glance, hiding a live one loses a decision.
    """
    fake = _FakeBr(records={"epic": {"status": "open", "dependents": []}})
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    item = decisions.enqueue(tmp_path, "epic", "escalation", "widen the band?")

    assert decisions.closed_ids(tmp_path) == frozenset()
    assert [i.decision_id for i in decisions.pending(tmp_path, "epic")] == [item.decision_id]


def test_settle_checkpoint_answers_only_the_named_checkpoints_asks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keyed on the checkpoint name in the question, not on kind alone.

    Matching kind alone would clear a classify ask when ship was approved; matching a
    reconstructed question string would stop clearing the moment the ask is reworded,
    which is the defect reintroduced one refactor later.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    ship = decisions.enqueue(tmp_path, "epic", "checkpoint", "approve the ship checkpoint for epic")
    classify = decisions.enqueue(tmp_path, "epic", "checkpoint", "approve the classify checkpoint")
    other = decisions.enqueue(tmp_path, "epic", "escalation", "ship it or not?")

    settled = decisions.settle_checkpoint(tmp_path, "epic", "ship", by="human")

    assert [i.decision_id for i in settled] == [ship.decision_id]
    for untouched in (classify, other):
        item = decisions.get(tmp_path, untouched.decision_id)
        assert item is not None and item.pending


# --- How long the queue held the item (basicly-kjc5.51, D11) -------------------


def _pin_clocks(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr, *, waited_s: int) -> None:
    """Enqueue at :data:`_QUEUED_AT` on the tracker's clock, answer *waited_s* later."""
    fake.now = _QUEUED_AT.isoformat().replace("+00:00", "Z")
    monkeypatch.setattr(policy, "_now", lambda: _QUEUED_AT.timestamp() + waited_s)


_QUEUED_AT = datetime(2026, 7, 26, 9, 0, tzinfo=UTC)


def test_answering_records_how_long_the_queue_held_the_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The interval from enqueue to answer is evidence on the bead, with who ended it.

    Derived from the two markers' own tracker stamps — a blocked lane's cost is
    already recorded, it was only never measured.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    _pin_clocks(monkeypatch, fake, waited_s=3_600)
    item = decisions.enqueue(tmp_path, "epic.1", "needs-input", "which db?")

    decisions.answer(tmp_path, item.decision_id, "postgres", by="niksa")

    (event,) = policy.wait_events(tmp_path, "epic.1")
    assert (event.wait_id, event.kind, event.subject) == (
        item.decision_id,
        "decision",
        "needs-input",
    )
    assert (event.waited_s, event.answered_by, event.delegated) == (3_600, "niksa", False)


def test_a_delegated_answer_is_recorded_as_the_wait_it_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Human and decider waits are measured apart — that split prices the autonomy."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    _pin_clocks(monkeypatch, fake, waited_s=45)
    item = decisions.enqueue(tmp_path, "epic", "needs-input", "which db?")

    decisions.answer(tmp_path, item.decision_id, "postgres", by=f"{decisions.DECIDER_BY_PREFIX}c")

    summary = policy.session_wait_summary(tmp_path, "epic")
    assert (summary.human_wait_s, summary.delegated_wait_s) == (0, 45)
    assert [(e.answered_by, e.delegated) for e in summary.events] == [("decider:c", True)]


def test_an_unusable_enqueue_stamp_records_no_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No measurable start means no event: the meter under-reports before it invents."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    fake.now = "whenever"  # a tracker stamp nothing can measure from
    item = decisions.enqueue(tmp_path, "epic.1", "needs-input", "which db?")

    answered = decisions.answer(tmp_path, item.decision_id, "postgres", by="human")

    assert answered.answer == "postgres"  # the answer still lands
    assert policy.wait_events(tmp_path, "epic.1") == ()


# --- Notify hook (design 7.3) --------------------------------------------------


def test_notify_fires_only_for_human_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The consumer command gets id+question appended; delegable items stay quiet."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    config = PolicyConfig(
        required_gates=("verify",), max_rework=2, notify_command=("notify-send", "basicly")
    )
    monkeypatch.setattr(decisions, "load_policy_config", lambda _r: config)
    calls: list[list[str]] = []
    monkeypatch.setattr(decisions.subprocess, "run", lambda argv, **_k: calls.append(list(argv)))

    item = decisions.enqueue(tmp_path, "epic.1", "needs-input", "which db?")
    decisions.enqueue(tmp_path, "epic.1", "escalation", "cap hit", human_required=False)

    assert calls == [["notify-send", "basicly", item.decision_id, "which db?"]]


def test_notify_disabled_and_failing_are_tolerated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No notify_command means silence; a broken one must never fail the enqueue."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    decisions.enqueue(tmp_path, "epic.1", "needs-input", "no config, no crash")

    config = PolicyConfig(
        required_gates=("verify",), max_rework=2, notify_command=("does-not-exist",)
    )
    monkeypatch.setattr(decisions, "load_policy_config", lambda _r: config)

    def boom(*_a, **_k):
        raise OSError("command not found")

    monkeypatch.setattr(decisions.subprocess, "run", boom)
    item = decisions.enqueue(tmp_path, "epic.1", "needs-input", "still enqueued")
    assert decisions.get(tmp_path, item.decision_id) is not None


# --- Decider (design 7.1): corpus-bounded authority -----------------------------


def _decider_setup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stdout: str,
    *,
    usage_format: str | None = None,
) -> tuple[_FakeBr, decisions.DecisionItem]:
    fake = _FakeBr(records={"epic": {"status": "open", "description": "db is postgres"}})
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    item = decisions.enqueue(tmp_path, "epic", "needs-input", "which db?")
    # deny_style is what makes the fake confinable; without one, invoke_decider
    # refuses to dispatch it at all (basicly-kjc5.16) - which every decider path
    # here assumes it got past. The unconfinable case has its own test.
    #
    # No usage_format by default, so *stdout* is the reply verbatim: the plain-text
    # arm of the fix (basicly-gczc), which is also every store-measured adapter.
    # A test that needs the wrapped arm names the format.
    spec = runner.RunnerSpec(
        "fake",
        runner.HEADLESS,
        ("fake", runner.PROMPT_PLACEHOLDER),
        deny_style=runner.DENY_TOOL_FLAG,
        usage_format=usage_format,
    )
    monkeypatch.setattr(
        decisions,
        "load_runner_config",
        lambda _r: RunnerConfig(specs=(spec,), default="fake", decider="fake"),
    )
    monkeypatch.setattr(decisions.runner, "select_runner", lambda *_a, **_k: spec)
    monkeypatch.setattr(
        decisions.runner,
        "run",
        lambda _spec, _prompt, _cwd, **_k: runner.RunResult(
            "fake", ("fake",), executed=True, returncode=0, stdout=stdout
        ),
    )
    return fake, item


def test_decider_records_a_derivable_answer_with_attribution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-abstaining verdict is recorded as the answer, attributed decider:<agent>."""
    verdict = json.dumps({
        "decision": "postgres",
        "rationale": "corpus",
        "confidence": 0.9,
        "abstain": False,
    })
    _fake, item = _decider_setup(monkeypatch, tmp_path, verdict)

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "epic")

    assert isinstance(outcome, decisions.DecisionItem)
    assert outcome.answer == "postgres"
    assert outcome.answered_by == "decider:fake"


def test_decider_dispatch_is_bounded_and_metered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The decider obeys runner_timeout and writes a run-record (basicly-kjc5.31).

    ``capture_usage`` is the other half of "metered" (basicly-gczc): the record
    alone carried a chars/4 estimate, which ``policy.session_spend`` counts as an
    unmeterable dispatch, and one of those zeroes the grant's remaining budget.
    """
    verdict = json.dumps({
        "decision": "postgres",
        "rationale": "corpus",
        "confidence": 0.9,
        "abstain": False,
    })
    _fake, item = _decider_setup(monkeypatch, tmp_path, verdict)
    seen: dict[str, object] = {}
    recorded: list[str] = []

    def _run(_spec, _prompt, _cwd, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        seen["capture_usage"] = kwargs.get("capture_usage", False)
        return runner.RunResult("fake", ("fake",), executed=True, returncode=0, stdout=verdict)

    monkeypatch.setattr(decisions.runner, "run", _run)
    phases: list[object] = []

    def _record(_repo, issue, _spec, _result, **inputs):
        recorded.append(issue)
        phases.append(inputs.get("phase"))

    monkeypatch.setattr(decisions.runner, "record_dispatch", _record)
    decisions.invoke_decider(tmp_path, item.decision_id, "epic")

    assert seen["timeout"] == 3600.0  # the [runner] runner_timeout default
    assert seen["capture_usage"] is True
    assert recorded == [item.issue_id]
    assert phases == ["decide"]


# --- A delegated decision must not halt the grant (basicly-gczc) --------------


def test_the_decider_call_site_comment_matches_the_flag_it_describes() -> None:
    """The prose at the dispatch claims metering; the flag has to be there too.

    This is the basicly-ipx2 defect class — a claim committed beside the thing it
    is wrong about. The comment said the decider was "metered like every other
    dispatch" through a call that never passed ``capture_usage``, so a reader
    checking whether the decider was metered found a comment saying yes. Dropping
    either side fails here.
    """
    mentions = [line.strip() for line in inspect.getsource(decisions.invoke_decider).splitlines()]
    mentions = [line for line in mentions if "capture_usage" in line]
    assert any("capture_usage=True" in line for line in mentions), (
        "the prose claims the decider is metered through a call that does not capture usage"
    )
    assert any("capture_usage=True" not in line for line in mentions), (
        "the flag is passed with no prose saying what metering means here"
    )


_VERDICT = json.dumps({"decision": "postgres", "rationale": "corpus", "abstain": False})


def _claude_like_decider(
    monkeypatch: pytest.MonkeyPatch, *, honour_flag: bool = True, noise: str = ""
) -> None:
    """Replace the decider's runner with one that behaves the way claude does.

    The whole defect lives in the *coupling* the other stubs in this module elide:
    the flag that makes usage reportable is the same flag that wraps the reply. So
    this stand-in answers the way the probed CLI answers — a result object with a
    usage block under ``capture_usage``, the bare reply without it — and a test can
    then assert on the meter rather than on what the call site was seen to pass.

    *honour_flag* False ignores the flag and always replies in plain text: the
    pre-fix call site, kept as the control that these assertions discriminate.

    *noise* prefixes the envelope with a line the CLI printed around it, which the
    probed stream arm does emit ("no stdin data received in 3s"). The reader has to
    locate the object rather than assume it is all of stdout, or one such line puts
    the answer *and* the metering back where they were.
    """

    def _run(_spec, _prompt, _cwd, **kwargs):
        wrapped = bool(kwargs.get("capture_usage")) and honour_flag
        stdout = (
            noise
            + json.dumps({
                "type": "result",
                "result": _VERDICT,
                "total_cost_usd": 0.01,
                "usage": {"input_tokens": 11, "output_tokens": 7},
            })
            if wrapped
            else _VERDICT
        )
        return runner.RunResult("fake", ("fake",), executed=True, returncode=0, stdout=stdout)

    monkeypatch.setattr(decisions.runner, "run", _run)


def test_a_delegated_decision_does_not_halt_the_grant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One delegated decision leaves the grant funded, through the real recorder.

    The bug this closes: the decider's record carried ``estimated: true``, so
    ``spend_status`` refused every following dispatch and delegated decision for
    the rest of the session — the 2026-08-02 halt. Nothing is stubbed between the
    dispatch and the meter, because a passing parser is not evidence about a
    fail-open gate: the assertion is on ``spend_status`` itself, over records the
    real ``record_dispatch`` wrote.
    """
    fake, item = _decider_setup(monkeypatch, tmp_path, "", usage_format=runner.CLAUDE_JSON)
    _claude_like_decider(monkeypatch)
    fake.comments.setdefault("epic", []).append("[harness-policy] grant level=L3 budget=8000000")

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "epic")

    assert isinstance(outcome, decisions.DecisionItem)
    assert outcome.answer == "postgres"
    meter = policy.session_spend(tmp_path, "epic")
    assert meter.unmetered_dispatches == 0
    assert meter.measured_tokens == 18  # the adapter's own numbers, not a chars/4 floor
    status = policy.spend_status(tmp_path, "epic")
    assert status.halted is False, status.detail


def test_a_decision_survives_a_line_the_cli_printed_before_its_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The same delegated decision, with the CLI's stdin warning ahead of the object.

    End-to-end companion to the reader-level test in ``tests/test_runner.py``: the
    concern is not that a parser handles noise, it is that noise costs an answer
    *and* zeroes the grant, and only ``spend_status`` over real records can say
    that it does not.
    """
    fake, item = _decider_setup(monkeypatch, tmp_path, "", usage_format=runner.CLAUDE_JSON)
    _claude_like_decider(
        monkeypatch, noise="Warning: no stdin data received in 3s, proceeding without it.\n"
    )
    fake.comments.setdefault("epic", []).append("[harness-policy] grant level=L3 budget=8000000")

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "epic")

    assert isinstance(outcome, decisions.DecisionItem)
    assert outcome.answer == "postgres"
    assert policy.session_spend(tmp_path, "epic").unmetered_dispatches == 0
    assert policy.spend_status(tmp_path, "epic").halted is False


def test_an_unmetered_decider_dispatch_is_what_halted_the_grant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The control: the pre-fix dispatch halts the grant on one decision.

    Same adapter, same grant, same single delegated decision — only the flag
    differs. Without this the test above would pass just as well against a meter
    that counts nothing, which is how the defect survived being "metered like every
    other dispatch" in a comment.
    """
    fake, item = _decider_setup(monkeypatch, tmp_path, "", usage_format=runner.CLAUDE_JSON)
    _claude_like_decider(monkeypatch, honour_flag=False)
    fake.comments.setdefault("epic", []).append("[harness-policy] grant level=L3 budget=8000000")

    delegated = decisions.invoke_decider(tmp_path, item.decision_id, "epic")
    assert isinstance(delegated, decisions.DecisionItem)

    status = policy.spend_status(tmp_path, "epic")
    assert status.halted is True
    assert status.unmetered_dispatches == 1
    assert "cannot be metered" in status.detail


# The verdict as each adapter's stdout carries it under `capture_usage`. The last
# case is the plain-text arm: a store-measured adapter's stdout was never wrapped,
# and neither was an adapter with no usage format at all.
@pytest.mark.parametrize(
    ("usage_format", "stdout"),
    [
        (runner.CLAUDE_JSON, json.dumps({"type": "result", "result": _VERDICT, "usage": {}})),
        (
            runner.CLAUDE_STREAM_JSON,
            "\n".join([
                '{"type":"system","subtype":"init"}',
                json.dumps({"type": "result", "result": _VERDICT}),
            ]),
        ),
        (
            runner.CODEX_JSONL,
            json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": _VERDICT},
            }),
        ),
        (None, _VERDICT),
    ],
)
def test_the_decider_verdict_survives_its_usage_envelope(
    usage_format: str | None,
    stdout: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A metered adapter's reply is unwrapped before parsing, per envelope shape."""
    _fake, item = _decider_setup(monkeypatch, tmp_path, stdout, usage_format=usage_format)
    monkeypatch.setattr(decisions.runner, "record_dispatch", lambda *_a, **_k: None)

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "epic")

    assert isinstance(outcome, decisions.DecisionItem)
    assert outcome.answer == "postgres"


def test_a_raw_envelope_abstains() -> None:
    """The control for the test above: unwrapped, the envelope itself abstains.

    ``parse_verdict`` takes first-``{`` to last-``}``, so handed a raw claude
    envelope it parses the *envelope*, finds no ``decision`` key, and fails closed —
    which is what the naive one-line fix ships: every delegated decision silently
    stops being delegated while the meter looks fixed.
    """
    envelope = json.dumps({"type": "result", "result": _VERDICT, "usage": {}})
    raw = decisions.parse_verdict(envelope)
    assert raw.abstain is True
    assert not raw.decision


def test_decider_timeout_abstains(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A hung decider is killed and abstains, leaving the item with the human."""
    _fake, item = _decider_setup(monkeypatch, tmp_path, "")
    monkeypatch.setattr(
        decisions.runner,
        "run",
        lambda *_a, **_k: runner.RunResult(
            "fake", ("fake",), executed=True, returncode=1, timed_out=True
        ),
    )
    monkeypatch.setattr(decisions.runner, "record_dispatch", lambda *_a, **_k: None)

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "epic")

    assert isinstance(outcome, decisions.DeciderVerdict)
    assert outcome.abstain is True and "runner_timeout" in outcome.rationale
    stored = decisions.get(tmp_path, item.decision_id)
    assert stored is not None and stored.pending


def test_decider_dispatches_a_confined_spec_not_the_selected_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The spec that reaches runner.run carries the confinement overlay (basicly-kjc5.16).

    Selecting the runner and dispatching it are two different specs on purpose: a
    decider holding a shell tool could record its own answer with `br comments
    add`, straight past decider_max_decisions and the abstain contract.
    """
    verdict = json.dumps({
        "decision": "postgres",
        "rationale": "corpus",
        "confidence": 0.9,
        "abstain": False,
    })
    _fake, item = _decider_setup(monkeypatch, tmp_path, verdict)
    dispatched: list[runner.RunnerSpec] = []

    def capturing_run(spec, _prompt, _cwd, **_k):
        dispatched.append(spec)
        return runner.RunResult("fake", ("fake",), executed=True, returncode=0, stdout=verdict)

    monkeypatch.setattr(decisions.runner, "run", capturing_run)
    monkeypatch.setattr(decisions.runner, "record_dispatch", lambda *_a, **_k: None)

    decisions.invoke_decider(tmp_path, item.decision_id, "epic")

    assert len(dispatched) == 1
    assert dispatched[0].deny_tools, "the decider was dispatched unconfined"


def test_decider_abstains_when_the_grant_budget_is_spent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D3 halts delegated decisions on the same ceiling as dispatch (basicly-kjc5.23).

    The halt lives at this entry point rather than at each caller, so a human's
    ``loop decide`` and the supervisor's autonomous pass are bound by one rule.
    """
    fake, item = _decider_setup(monkeypatch, tmp_path, '{"decision": "postgres", "abstain": false}')
    fake.comments.setdefault("epic", []).append("[harness-policy] grant level=L2 budget=100")
    run_record.record(
        tmp_path,
        "epic",
        run_record.build_record(
            agent="t", handoff=False, returncode=0, duration_s=1.0, command=("t",), tokens=100
        ),
    )
    monkeypatch.setattr(
        decisions.runner,
        "run",
        lambda *_a, **_k: pytest.fail("must not delegate past the spend ceiling"),
    )

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "epic")

    assert isinstance(outcome, decisions.DeciderVerdict)
    assert outcome.abstain is True
    assert "token_budget spent" in outcome.rationale
    stored = decisions.get(tmp_path, item.decision_id)
    assert stored is not None and stored.pending  # still the human's to answer


def test_decider_runs_while_the_grant_is_inside_its_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A funded grant still delegates - the ceiling must not disable the decider."""
    fake, item = _decider_setup(monkeypatch, tmp_path, '{"decision": "postgres", "abstain": false}')
    fake.comments.setdefault("epic", []).append("[harness-policy] grant level=L2 budget=100")
    run_record.record(
        tmp_path,
        "epic",
        run_record.build_record(
            agent="t", handoff=False, returncode=0, duration_s=1.0, command=("t",), tokens=99
        ),
    )

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "epic")

    assert isinstance(outcome, decisions.DecisionItem)
    assert outcome.answer == "postgres"


def test_decider_abstains_when_the_runner_cannot_be_confined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An agent family with no confinement overlay is not dispatched at all.

    D3's drop-to-human stance: the corpus bound is the decider's whole authority,
    so running one that cannot be bounded is worse than waiting for a human.
    """
    _fake, item = _decider_setup(monkeypatch, tmp_path, "")
    bare = runner.RunnerSpec("mystery", runner.HEADLESS, ("mystery", runner.PROMPT_PLACEHOLDER))
    monkeypatch.setattr(decisions.runner, "select_runner", lambda *_a, **_k: bare)
    monkeypatch.setattr(
        decisions.runner,
        "run",
        lambda *_a, **_k: pytest.fail("an unconfinable decider must never be dispatched"),
    )

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "epic")

    assert isinstance(outcome, decisions.DeciderVerdict)
    assert outcome.abstain is True and "confinement" in outcome.rationale
    stored = decisions.get(tmp_path, item.decision_id)
    assert stored is not None and stored.pending


def test_decider_abstention_leaves_the_item_with_the_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fact not derivable from the corpus stays pending — block-don't-guess."""
    verdict = json.dumps({
        "decision": "",
        "rationale": "not in corpus",
        "confidence": 0.2,
        "abstain": True,
    })
    _fake, item = _decider_setup(monkeypatch, tmp_path, verdict)

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "epic")

    assert isinstance(outcome, decisions.DeciderVerdict)
    assert outcome.abstain is True
    stored = decisions.get(tmp_path, item.decision_id)
    assert stored is not None and stored.pending


def test_decider_cap_makes_remaining_decisions_human_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """decider_max_decisions is the runaway-loop guard (design section 6)."""
    verdict = json.dumps({
        "decision": "postgres",
        "rationale": "corpus",
        "confidence": 0.9,
        "abstain": False,
    })
    _fake, item = _decider_setup(monkeypatch, tmp_path, verdict)
    config = PolicyConfig(required_gates=("verify",), max_rework=2, decider_max_decisions=0)

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "epic", config=config)

    assert isinstance(outcome, decisions.DeciderVerdict)
    assert outcome.abstain is True
    assert "decider_max_decisions" in outcome.rationale


# --- Review hardening (kjc5.4 code review) --------------------------------------


def test_answer_rejects_attribution_that_is_not_a_single_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A crafted --by could inject header fields (id=) or corrupt the marker."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    item = decisions.enqueue(tmp_path, "epic.1", "needs-input", "which db?")
    other = decisions.enqueue(tmp_path, "epic.1", "needs-input", "which cache?")

    for by in (f"evil id={other.decision_id}", "human\nextra", "two words", "a=b"):
        with pytest.raises(ValueError, match="attribution"):
            decisions.answer(tmp_path, item.decision_id, "x", by=by)

    stored_item = decisions.get(tmp_path, item.decision_id)
    stored_other = decisions.get(tmp_path, other.decision_id)
    assert stored_item is not None and stored_item.pending
    assert stored_other is not None and stored_other.pending


def test_reenqueue_after_answer_reopens_a_new_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fact that blocks again after an answer must resurface, not vanish."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    notified = _no_notify(monkeypatch)
    first = decisions.enqueue(tmp_path, "epic.1", "needs-input", "which db?")
    decisions.answer(tmp_path, first.decision_id, "postgres", by="human")

    reopened = decisions.enqueue(tmp_path, "epic.1", "needs-input", "which db?")

    assert reopened.decision_id != first.decision_id
    assert reopened.decision_id.endswith("-2")
    assert reopened.pending
    assert len(notified) == 2  # the re-opened item notifies again
    pending_ids = [i.decision_id for i in decisions.pending(tmp_path, "epic.1")]
    assert pending_ids == [reopened.decision_id]


def test_decider_answer_persists_the_audit_trail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rationale and confidence land in the answer payload for decision review."""
    verdict = json.dumps({
        "decision": "postgres",
        "rationale": "corpus says so",
        "confidence": 0.9,
        "abstain": False,
    })
    fake, item = _decider_setup(monkeypatch, tmp_path, verdict)

    decisions.invoke_decider(tmp_path, item.decision_id, "epic")

    # Not simply the last comment: recording an answer also closes the item's
    # wait interval (basicly-kjc5.51), which lands after it.
    answer_marker = next(
        text for text in fake.comments["epic"] if f"id={item.decision_id} answered" in text
    )
    assert "corpus says so" in answer_marker
    assert "0.9" in answer_marker


# --- concurrency (basicly-kjc5.17) ------------------------------------------


def test_concurrent_enqueue_of_one_fact_queues_and_notifies_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Concurrent lanes hitting the same fact produce one item and one notification.

    Without the module lock both threads read "not queued" and both write, so the
    queue grows a duplicate marker and the human is notified twice for one
    decision. A barrier makes the interleaving deterministic rather than hoping
    the threads collide.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    notified: list[str] = []
    monkeypatch.setattr(decisions, "_notify", lambda _r, item: notified.append(item.decision_id))

    # Each reader waits for a peer at a *timed* barrier after reading. Unlocked,
    # both threads reach it, both proceed with the same stale "not queued" read,
    # and both write. Locked, the second thread cannot enter the critical section
    # at all, so the first times out (BrokenBarrierError, suppressed) and writes
    # alone; the second then reads and finds the item. The timeout is what makes
    # this work in both worlds — a plain barrier inside a critical section can
    # never be reached by both threads and would deadlock the test.
    barrier = threading.Barrier(2)
    real_items_on = decisions.items_by_id

    def _slow_items_on(repo_root: Path, issue_id: str):
        result = real_items_on(repo_root, issue_id)
        with contextlib.suppress(threading.BrokenBarrierError):
            barrier.wait(timeout=0.3)
        return result

    monkeypatch.setattr(decisions, "items_by_id", _slow_items_on)

    results: list[decisions.DecisionItem] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(
                decisions.enqueue(tmp_path, "lane", "needs-input", "which db?")
            )
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len({item.decision_id for item in results}) == 1
    markers = [text for text in fake.comments.get("lane", []) if decision_marker.MARKER in text]
    assert len(markers) == 1
    assert len(notified) == 1


def test_decider_counts_and_records_under_one_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The cap re-check and the answer are one atomic section (basicly-kjc5.17).

    A race test cannot prove this: forcing an interleaving *inside* the critical
    section is precisely what the lock prevents, and a barrier there deadlocks.
    So assert the contract instead — the count and the write happen while the
    module lock is held. Without it, N judges each pass a check taken before a
    dispatch that takes minutes, and the session overshoots the cap.
    """
    fake = _FakeBr(records={"epic": {"status": "open", "description": "db is postgres"}})
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    verdict = json.dumps({
        "decision": "postgres",
        "rationale": "corpus",
        "confidence": 0.9,
        "abstain": False,
    })
    item = decisions.enqueue(tmp_path, "epic", "needs-input", "which db?")
    # deny_style is what makes the fake confinable; without one, invoke_decider
    # refuses to dispatch it at all (basicly-kjc5.16) - which every decider path
    # here assumes it got past. The unconfinable case has its own test.
    spec = runner.RunnerSpec(
        "fake",
        runner.HEADLESS,
        ("fake", runner.PROMPT_PLACEHOLDER),
        deny_style=runner.DENY_TOOL_FLAG,
    )
    monkeypatch.setattr(
        decisions,
        "load_runner_config",
        lambda _r: RunnerConfig(specs=(spec,), default="fake", decider="fake"),
    )
    monkeypatch.setattr(decisions.runner, "select_runner", lambda *_a, **_k: spec)
    monkeypatch.setattr(decisions.runner, "record_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(
        decisions.runner,
        "run",
        lambda *_a, **_k: runner.RunResult(
            "fake", ("fake",), executed=True, returncode=0, stdout=verdict
        ),
    )

    events: list[str] = []

    class _SpyLock:
        def __enter__(self):
            events.append("acquire")
            return self

        def __exit__(self, *_exc):
            events.append("release")
            return False

    monkeypatch.setattr(decisions, "_QUEUE_LOCK", _SpyLock())
    real_count = decisions.decider_answers_count
    monkeypatch.setattr(
        decisions,
        "decider_answers_count",
        lambda *a, **k: (events.append("count"), real_count(*a, **k))[1],
    )
    real_answer = decisions.answer
    monkeypatch.setattr(
        decisions,
        "answer",
        lambda *a, **k: (events.append("answer"), real_answer(*a, **k))[1],
    )

    decisions.invoke_decider(tmp_path, item.decision_id, "epic")

    # The pre-dispatch count is outside the lock (a cheap early exit); the
    # re-check and the write must sit inside one acquire/release pair.
    guarded = events[events.index("acquire") : events.index("release")]
    assert guarded == ["acquire", "count", "answer"], events
    assert decisions.decider_answers_count(tmp_path, "epic") == 1
