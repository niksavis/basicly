from __future__ import annotations

import contextlib
import inspect
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from basicly import decision_marker, decisions, policy, run_record, runner, tracker
from basicly.config import PolicyConfig, RunnerConfig
from tests import fake_tracker, flipped_tracker


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


_EPOCH = "2026-01-01T00:00:00Z"


class _FakeBr:
    def __init__(self, records: dict[str, dict] | None = None) -> None:
        self.records = records or {}
        self.comments: dict[str, list[str]] = {}
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
    monkeypatch.setattr(policy, "_write", fake)
    fake_tracker.install(monkeypatch, fake)


def _no_notify(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []
    monkeypatch.setattr(decisions, "_notify", lambda _r, item: calls.append(item))
    return calls


def test_enqueue_is_idempotent_per_content(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    notified = _no_notify(monkeypatch)

    first = decisions.enqueue(tmp_path, "b-epic.1", "needs-input", "which db?", "docs conflict")
    again = decisions.enqueue(tmp_path, "b-epic.1", "needs-input", "which db?", "docs conflict")

    assert first.decision_id == again.decision_id
    assert first.decision_id.startswith("b-epic.1#")
    assert len(fake.comments["b-epic.1"]) == 1
    assert len(notified) == 1


def test_enqueue_rejects_unknown_kind(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(monkeypatch, _FakeBr())
    with pytest.raises(ValueError, match="unknown decision kind"):
        decisions.enqueue(tmp_path, "b-epic.1", "vibe", "q")


def test_answer_round_trips_with_attribution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    item = decisions.enqueue(tmp_path, "b-epic.1", "needs-input", "which db?")

    answered = decisions.answer(tmp_path, item.decision_id, "postgres", by="human")

    assert answered.answer == "postgres"
    assert answered.answered_by == "human"
    stored = decisions.get(tmp_path, item.decision_id)
    assert stored is not None and not stored.pending
    assert stored.answer == "postgres"


def test_answer_refuses_missing_and_double_answers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    with pytest.raises(ValueError, match="no decision"):
        decisions.answer(tmp_path, "epic.1#abcdef", "x", by="human")
    item = decisions.enqueue(tmp_path, "b-epic.1", "needs-input", "which db?")
    decisions.answer(tmp_path, item.decision_id, "postgres", by="human")
    with pytest.raises(ValueError, match="already answered"):
        decisions.answer(tmp_path, item.decision_id, "mysql", by="human")


def test_pending_scans_the_session_tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    child = {"id": "b-epic.1", "dependency_type": "parent-child"}
    fake = _FakeBr(records={"b-epic": {"status": "open", "dependents": [child]}})
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    kept = decisions.enqueue(tmp_path, "b-epic.1", "needs-input", "which db?")
    answered = decisions.enqueue(tmp_path, "b-epic", "escalation", "rework cap on verify")
    decisions.answer(tmp_path, answered.decision_id, "park it", by="human")

    items = decisions.pending(tmp_path, "b-epic")

    assert [i.decision_id for i in items] == [kept.decision_id]


def _gating_track() -> _FakeBr:

    return _FakeBr(
        records={
            "b-epic": {
                "status": "open",
                "dependents": [{"id": "b-epic.1", "dependency_type": "parent-child"}],
                "dependencies": [{"id": "gated", "dependency_type": "blocks"}],
            },
            "b-epic.1": {"status": "open", "dependents": [], "dependencies": []},
            "gated": {"status": "open", "dependents": [], "dependencies": []},
        }
    )


def test_a_delegated_answer_on_a_gated_bead_counts_against_the_runaway_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _install(monkeypatch, _gating_track())
    _no_notify(monkeypatch)
    item = decisions.enqueue(tmp_path, "gated", "needs-input", "which db?")
    decisions.answer(
        tmp_path, item.decision_id, "postgres", by=f"{decisions.DECIDER_BY_PREFIX}claude"
    )

    assert decisions.decider_answers_count(tmp_path, "b-epic") == 1


def test_an_escalation_on_a_gated_bead_is_reported_as_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _install(monkeypatch, _gating_track())
    _no_notify(monkeypatch)
    item = decisions.enqueue(tmp_path, "gated", "escalation", "rework cap on verify")

    assert [i.decision_id for i in decisions.pending(tmp_path, "b-epic")] == [item.decision_id]


def _write_export(
    repo_root: Path, statuses: dict[str, str], parents: dict[str, str] | None = None
) -> None:

    repo = flipped_tracker.flipped_repo(repo_root)
    kit = tracker.kit(repo)
    drafts = [
        kit.events.Draft(record, kit.events.KIND_STATUS, {"status": status})
        for record, status in statuses.items()
    ]
    drafts += [
        kit.events.Draft(
            child,
            kit.migrate.KIND_EDGE,
            {
                kit.migrate.EDGE_FROM: child,
                kit.migrate.EDGE_TO: parent,
                kit.migrate.EDGE_TYPE: "parent-child",
            },
        )
        for child, parent in (parents or {}).items()
    ]
    kit.events.append(tracker.ledger_dir(repo), drafts)


def test_pending_drops_items_on_closed_beads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    child = {"id": "b-epic.1", "dependency_type": "parent-child"}
    fake = _FakeBr(records={"b-epic": {"status": "open", "dependents": [child]}})
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    stale = decisions.enqueue(tmp_path, "b-epic.1", "checkpoint", "approve the ship checkpoint")
    live = decisions.enqueue(tmp_path, "b-epic", "escalation", "widen the band?")

    _write_export(tmp_path, {"b-epic": "open", "b-epic.1": "open"}, {"b-epic.1": "b-epic"})
    assert {i.decision_id for i in decisions.pending(tmp_path, "b-epic")} == {
        stale.decision_id,
        live.decision_id,
    }, "control: while the bead is open its item is outstanding"

    _write_export(tmp_path, {"b-epic": "open", "b-epic.1": "closed"}, {"b-epic.1": "b-epic"})
    assert [i.decision_id for i in decisions.pending(tmp_path, "b-epic")] == [live.decision_id]


def test_pending_reports_everything_when_the_export_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr(records={"b-epic": {"status": "open", "dependents": []}})
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    item = decisions.enqueue(tmp_path, "b-epic", "escalation", "widen the band?")

    assert decisions.closed_ids(tmp_path) == frozenset()
    assert [i.decision_id for i in decisions.pending(tmp_path, "b-epic")] == [item.decision_id]


def test_settle_checkpoint_answers_only_the_named_checkpoints_asks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr()
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    ship = decisions.enqueue(
        tmp_path, "b-epic", "checkpoint", "approve the ship checkpoint for epic"
    )
    classify = decisions.enqueue(
        tmp_path, "b-epic", "checkpoint", "approve the classify checkpoint"
    )
    other = decisions.enqueue(tmp_path, "b-epic", "escalation", "ship it or not?")

    settled = decisions.settle_checkpoint(tmp_path, "b-epic", "ship", by="human")

    assert [i.decision_id for i in settled] == [ship.decision_id]
    for untouched in (classify, other):
        item = decisions.get(tmp_path, untouched.decision_id)
        assert item is not None and item.pending


def _pin_clocks(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr, *, waited_s: int) -> None:
    fake.now = _QUEUED_AT.isoformat().replace("+00:00", "Z")
    monkeypatch.setattr(policy, "_now", lambda: _QUEUED_AT.timestamp() + waited_s)


_QUEUED_AT = datetime(2026, 7, 26, 9, 0, tzinfo=UTC)


def test_answering_records_how_long_the_queue_held_the_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr()
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    _pin_clocks(monkeypatch, fake, waited_s=3_600)
    item = decisions.enqueue(tmp_path, "b-epic.1", "needs-input", "which db?")

    decisions.answer(tmp_path, item.decision_id, "postgres", by="niksa")

    (event,) = policy.wait_events(tmp_path, "b-epic.1")
    assert (event.wait_id, event.kind, event.subject) == (
        item.decision_id,
        "decision",
        "needs-input",
    )
    assert (event.waited_s, event.answered_by, event.delegated) == (3_600, "niksa", False)


def test_a_delegated_answer_is_recorded_as_the_wait_it_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    _pin_clocks(monkeypatch, fake, waited_s=45)
    item = decisions.enqueue(tmp_path, "b-epic", "needs-input", "which db?")

    decisions.answer(tmp_path, item.decision_id, "postgres", by=f"{decisions.DECIDER_BY_PREFIX}c")

    summary = policy.session_wait_summary(tmp_path, "b-epic")
    assert (summary.human_wait_s, summary.delegated_wait_s) == (0, 45)
    assert [(e.answered_by, e.delegated) for e in summary.events] == [("decider:c", True)]


def test_an_unusable_enqueue_stamp_records_no_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    fake.now = "whenever"
    item = decisions.enqueue(tmp_path, "b-epic.1", "needs-input", "which db?")

    answered = decisions.answer(tmp_path, item.decision_id, "postgres", by="human")

    assert answered.answer == "postgres"
    assert policy.wait_events(tmp_path, "b-epic.1") == ()


def test_notify_fires_only_for_human_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    config = PolicyConfig(
        required_gates=("verify",), max_rework=2, notify_command=("notify-send", "basicly")
    )
    monkeypatch.setattr(decisions, "load_policy_config", lambda _r: config)
    calls: list[list[str]] = []
    monkeypatch.setattr(decisions.subprocess, "run", lambda argv, **_k: calls.append(list(argv)))

    item = decisions.enqueue(tmp_path, "b-epic.1", "needs-input", "which db?")
    decisions.enqueue(tmp_path, "b-epic.1", "escalation", "cap hit", human_required=False)

    assert calls == [["notify-send", "basicly", item.decision_id, "which db?"]]


def test_notify_disabled_and_failing_are_tolerated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    decisions.enqueue(tmp_path, "b-epic.1", "needs-input", "no config, no crash")

    config = PolicyConfig(
        required_gates=("verify",), max_rework=2, notify_command=("does-not-exist",)
    )
    monkeypatch.setattr(decisions, "load_policy_config", lambda _r: config)

    def boom(*_a, **_k):
        raise OSError("command not found")

    monkeypatch.setattr(decisions.subprocess, "run", boom)
    item = decisions.enqueue(tmp_path, "b-epic.1", "needs-input", "still enqueued")
    assert decisions.get(tmp_path, item.decision_id) is not None


def _decider_setup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stdout: str,
    *,
    usage_format: str | None = None,
) -> tuple[_FakeBr, decisions.DecisionItem]:
    fake = _FakeBr(records={"b-epic": {"status": "open", "description": "db is postgres"}})
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    item = decisions.enqueue(tmp_path, "b-epic", "needs-input", "which db?")
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
    verdict = json.dumps({
        "decision": "postgres",
        "rationale": "corpus",
        "confidence": 0.9,
        "abstain": False,
    })
    _fake, item = _decider_setup(monkeypatch, tmp_path, verdict)

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "b-epic")

    assert isinstance(outcome, decisions.DecisionItem)
    assert outcome.answer == "postgres"
    assert outcome.answered_by == "decider:fake"


def test_decider_dispatch_is_bounded_and_metered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

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
    decisions.invoke_decider(tmp_path, item.decision_id, "b-epic")

    assert seen["timeout"] == 3600.0
    assert seen["capture_usage"] is True
    assert recorded == [item.issue_id]
    assert phases == ["decide"]


def test_the_decider_is_invoked_with_usage_capture_on() -> None:
    mentions = [line.strip() for line in inspect.getsource(decisions.invoke_decider).splitlines()]

    assert [line for line in mentions if "capture_usage=True" in line], (
        "the decider is metered through a call that does not capture usage"
    )


_VERDICT = json.dumps({"decision": "postgres", "rationale": "corpus", "abstain": False})


def _claude_like_decider(
    monkeypatch: pytest.MonkeyPatch, *, honour_flag: bool = True, noise: str = ""
) -> None:

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

    fake, item = _decider_setup(monkeypatch, tmp_path, "", usage_format=runner.CLAUDE_JSON)
    _claude_like_decider(monkeypatch)
    fake.comments.setdefault("b-epic", []).append("[harness-policy] grant level=L3 budget=8000000")

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "b-epic")

    assert isinstance(outcome, decisions.DecisionItem)
    assert outcome.answer == "postgres"
    meter = policy.session_spend(tmp_path, "b-epic")
    assert meter.unmetered_dispatches == 0
    assert meter.measured_tokens == 18
    status = policy.spend_status(tmp_path, "b-epic")
    assert status.halted is False, status.detail


def test_a_decision_survives_a_line_the_cli_printed_before_its_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake, item = _decider_setup(monkeypatch, tmp_path, "", usage_format=runner.CLAUDE_JSON)
    _claude_like_decider(
        monkeypatch, noise="Warning: no stdin data received in 3s, proceeding without it.\n"
    )
    fake.comments.setdefault("b-epic", []).append("[harness-policy] grant level=L3 budget=8000000")

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "b-epic")

    assert isinstance(outcome, decisions.DecisionItem)
    assert outcome.answer == "postgres"
    assert policy.session_spend(tmp_path, "b-epic").unmetered_dispatches == 0
    assert policy.spend_status(tmp_path, "b-epic").halted is False


def test_an_unmetered_decider_dispatch_is_what_halted_the_grant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake, item = _decider_setup(monkeypatch, tmp_path, "", usage_format=runner.CLAUDE_JSON)
    _claude_like_decider(monkeypatch, honour_flag=False)
    fake.comments.setdefault("b-epic", []).append("[harness-policy] grant level=L3 budget=8000000")

    delegated = decisions.invoke_decider(tmp_path, item.decision_id, "b-epic")
    assert isinstance(delegated, decisions.DecisionItem)

    status = policy.spend_status(tmp_path, "b-epic")
    assert status.halted is True
    assert status.unmetered_dispatches == 1
    assert "cannot be metered" in status.detail


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
    _fake, item = _decider_setup(monkeypatch, tmp_path, stdout, usage_format=usage_format)
    monkeypatch.setattr(decisions.runner, "record_dispatch", lambda *_a, **_k: None)

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "b-epic")

    assert isinstance(outcome, decisions.DecisionItem)
    assert outcome.answer == "postgres"


def test_a_raw_envelope_abstains() -> None:

    envelope = json.dumps({"type": "result", "result": _VERDICT, "usage": {}})
    raw = decisions.parse_verdict(envelope)
    assert raw.abstain is True
    assert not raw.decision


def test_decider_timeout_abstains(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake, item = _decider_setup(monkeypatch, tmp_path, "")
    monkeypatch.setattr(
        decisions.runner,
        "run",
        lambda *_a, **_k: runner.RunResult(
            "fake", ("fake",), executed=True, returncode=1, timed_out=True
        ),
    )
    monkeypatch.setattr(decisions.runner, "record_dispatch", lambda *_a, **_k: None)

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "b-epic")

    assert isinstance(outcome, decisions.DeciderVerdict)
    assert outcome.abstain is True and "runner_timeout" in outcome.rationale
    stored = decisions.get(tmp_path, item.decision_id)
    assert stored is not None and stored.pending


def test_decider_dispatches_a_confined_spec_not_the_selected_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

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

    decisions.invoke_decider(tmp_path, item.decision_id, "b-epic")

    assert len(dispatched) == 1
    assert dispatched[0].deny_tools, "the decider was dispatched unconfined"


def test_the_decider_still_decides_when_the_grant_budget_is_spent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake, item = _decider_setup(monkeypatch, tmp_path, '{"decision": "postgres", "abstain": false}')
    fake.comments.setdefault("b-epic", []).append("[harness-policy] grant level=L2 budget=100")
    run_record.record(
        tmp_path,
        "b-epic",
        run_record.build_record(
            agent="t", handoff=False, returncode=0, duration_s=1.0, command=("t",), tokens=100
        ),
    )
    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "b-epic")

    assert isinstance(outcome, decisions.DecisionItem), (
        "a spent budget abstained and sent the decision back to a human"
    )
    assert outcome.answer == "postgres", "the decider's own answer is what comes back"


def test_decider_runs_while_the_grant_is_inside_its_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake, item = _decider_setup(monkeypatch, tmp_path, '{"decision": "postgres", "abstain": false}')
    fake.comments.setdefault("b-epic", []).append("[harness-policy] grant level=L2 budget=100")
    run_record.record(
        tmp_path,
        "b-epic",
        run_record.build_record(
            agent="t", handoff=False, returncode=0, duration_s=1.0, command=("t",), tokens=99
        ),
    )

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "b-epic")

    assert isinstance(outcome, decisions.DecisionItem)
    assert outcome.answer == "postgres"


def test_decider_abstains_when_the_runner_cannot_be_confined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _fake, item = _decider_setup(monkeypatch, tmp_path, "")
    bare = runner.RunnerSpec("mystery", runner.HEADLESS, ("mystery", runner.PROMPT_PLACEHOLDER))
    monkeypatch.setattr(decisions.runner, "select_runner", lambda *_a, **_k: bare)
    monkeypatch.setattr(
        decisions.runner,
        "run",
        lambda *_a, **_k: pytest.fail("an unconfinable decider must never be dispatched"),
    )

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "b-epic")

    assert isinstance(outcome, decisions.DeciderVerdict)
    assert outcome.abstain is True and "confinement" in outcome.rationale
    stored = decisions.get(tmp_path, item.decision_id)
    assert stored is not None and stored.pending


def test_decider_abstention_leaves_the_item_with_the_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    verdict = json.dumps({
        "decision": "",
        "rationale": "not in corpus",
        "confidence": 0.2,
        "abstain": True,
    })
    _fake, item = _decider_setup(monkeypatch, tmp_path, verdict)

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "b-epic")

    assert isinstance(outcome, decisions.DeciderVerdict)
    assert outcome.abstain is True
    stored = decisions.get(tmp_path, item.decision_id)
    assert stored is not None and stored.pending


def test_decider_cap_makes_remaining_decisions_human_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    verdict = json.dumps({
        "decision": "postgres",
        "rationale": "corpus",
        "confidence": 0.9,
        "abstain": False,
    })
    _fake, item = _decider_setup(monkeypatch, tmp_path, verdict)
    config = PolicyConfig(required_gates=("verify",), max_rework=2, decider_max_decisions=0)

    outcome = decisions.invoke_decider(tmp_path, item.decision_id, "b-epic", config=config)

    assert isinstance(outcome, decisions.DeciderVerdict)
    assert outcome.abstain is True
    assert "decider_max_decisions" in outcome.rationale


def test_answer_rejects_attribution_that_is_not_a_single_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    item = decisions.enqueue(tmp_path, "b-epic.1", "needs-input", "which db?")
    other = decisions.enqueue(tmp_path, "b-epic.1", "needs-input", "which cache?")

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
    fake = _FakeBr()
    _install(monkeypatch, fake)
    notified = _no_notify(monkeypatch)
    first = decisions.enqueue(tmp_path, "b-epic.1", "needs-input", "which db?")
    decisions.answer(tmp_path, first.decision_id, "postgres", by="human")

    reopened = decisions.enqueue(tmp_path, "b-epic.1", "needs-input", "which db?")

    assert reopened.decision_id != first.decision_id
    assert reopened.decision_id.endswith("-2")
    assert reopened.pending
    assert len(notified) == 2
    pending_ids = [i.decision_id for i in decisions.pending(tmp_path, "b-epic.1")]
    assert pending_ids == [reopened.decision_id]


def test_decider_answer_persists_the_audit_trail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    verdict = json.dumps({
        "decision": "postgres",
        "rationale": "corpus says so",
        "confidence": 0.9,
        "abstain": False,
    })
    fake, item = _decider_setup(monkeypatch, tmp_path, verdict)

    decisions.invoke_decider(tmp_path, item.decision_id, "b-epic")

    answer_marker = next(
        text for text in fake.comments["b-epic"] if f"id={item.decision_id} answered" in text
    )
    assert "corpus says so" in answer_marker
    assert "0.9" in answer_marker


def test_concurrent_enqueue_of_one_fact_queues_and_notifies_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr()
    _install(monkeypatch, fake)
    notified: list[str] = []
    monkeypatch.setattr(decisions, "_notify", lambda _r, item: notified.append(item.decision_id))

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

    fake = _FakeBr(records={"b-epic": {"status": "open", "description": "db is postgres"}})
    _install(monkeypatch, fake)
    _no_notify(monkeypatch)
    verdict = json.dumps({
        "decision": "postgres",
        "rationale": "corpus",
        "confidence": 0.9,
        "abstain": False,
    })
    item = decisions.enqueue(tmp_path, "b-epic", "needs-input", "which db?")
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

    decisions.invoke_decider(tmp_path, item.decision_id, "b-epic")

    guarded = events[events.index("acquire") : events.index("release")]
    assert guarded == ["acquire", "count", "answer"], events
    assert decisions.decider_answers_count(tmp_path, "b-epic") == 1
