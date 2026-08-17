"""Tests for the special-cause signal over the gate-failure ledger (basicly-xmhc).

The suppression tests matter more than the firing ones. factory-loop.md §3.2 makes
acting on common cause *tampering*, so a detector that fires on every failure is worse
than no detector: each test that asserts silence names the shape it must stay silent on.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import policy, retrospective, roles
from basicly.config import LOOP_PHASES
from basicly.loop_state import PHASES
from tests import fake_tracker

if TYPE_CHECKING:
    import pytest


def _points(*counts: int) -> tuple[retrospective.Point, ...]:
    """A ledger of *counts*, one unit each, in the order they were recorded."""
    return tuple(retrospective.Point(f"u{index}", n) for index, n in enumerate(counts))


class _Proc:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class _FakeBr:
    """One session's tracker: a root, its parent-child children, and their markers."""

    def __init__(self, comments: dict[str, list[str]]) -> None:
        self.comments = {issue: list(texts) for issue, texts in comments.items()}
        self.root = next(iter(self.comments))

    def __call__(self, _repo_root: Path, args: list[str], **_kw: object) -> _Proc:
        if args[:1] == ["show"]:
            children = [i for i in self.comments if i != args[1]] if args[1] == self.root else []
            record = {
                "id": args[1],
                "status": "open",
                "dependents": [{"id": i, "dependency_type": "parent-child"} for i in children],
            }
            return _Proc(json.dumps([record]))
        if args[:2] == ["comments", "list"]:
            return _Proc(json.dumps([{"text": t} for t in self.comments.get(args[2], [])]))
        if args[:2] == ["comments", "add"]:
            self.comments.setdefault(args[2], []).append(args[-1])
            return _Proc()
        raise AssertionError(f"unexpected br call: {args}")


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr) -> None:
    """Route the tracker seams every consumer shares at *fake* (as `test_policy` does)."""
    monkeypatch.setattr(policy, "_write", fake)
    fake_tracker.install(monkeypatch, fake)


_CHART = retrospective.Chart(centre=0.3, sigma=0.55, upper=1.94, observations=10)


# --- suppression: what must NOT fire ----------------------------------------


def test_a_single_failure_is_common_cause_even_where_the_arithmetic_signals() -> None:
    """The rule §3.2 states outright, and the one this mechanism is judged on.

    Nineteen clean units and one failure put c-bar at 0.05, where the c-chart's normal
    approximation puts the three-sigma limit *below one* — so the arithmetic alone
    would make every isolated failure a signal. The first assertion is what makes this
    test discriminating: it proves the limit was crossed and that
    ``MIN_SPECIAL_COUNT`` is what refused, rather than the ledger being quiet anyway.
    """
    points = _points(*([0] * 19), 1)

    assert retrospective.chart(points).upper < 1
    assert retrospective.evaluate(points).fires is False


def test_one_defect_on_one_landing_does_not_fire() -> None:
    """The other half of the pair below: any one of the three defects, alone."""
    signal = retrospective.evaluate(_points(0, 0, 0, 0, 0, 0, 0, 0, 0, 1))

    assert signal.fires is False
    assert "common cause" in signal.detail


def test_a_ledger_shorter_than_the_shortest_rule_never_fires() -> None:
    """Six observations cannot carry a run of seven, and a mean over them is the outlier."""
    signal = retrospective.evaluate(_points(0, 0, 0, 0, 0, 9))

    assert signal.fires is False
    assert f"below the {retrospective.MIN_OBSERVATIONS}" in signal.detail


def test_a_scattered_ledger_inside_the_limits_is_a_stable_process() -> None:
    """Alternating single failures are variation, not an assignable cause."""
    assert retrospective.evaluate(_points(0, 1, 0, 1, 0, 1, 0, 1, 0, 1)).fires is False


def test_a_falling_trend_is_not_a_signal() -> None:
    """The run and trend rules are one-sided on purpose.

    A process getting steadily cleaner is a real Shewhart signal, but the output
    contract is a control that would have refused a defect and there is no defect to
    name — firing here would spend a dispatch to explain an improvement.
    """
    assert retrospective.evaluate(_points(6, 5, 4, 3, 2, 1, 0, 0, 0, 0)).fires is False


# --- firing: the three rules -------------------------------------------------


def test_three_defects_on_one_landing_fire_and_name_the_point() -> None:
    """The shape this session actually produced, used as the detector's test case.

    `basicly-u2hl.54` made VALIDATE a phase under five acceptance criteria, and three
    defects were found downstream of it that no criterion named: `basicly-xab3`,
    `basicly-w88t` and `basicly-e2mz.4`. Three on one landing is one special cause, and
    the message says so rather than leaving a reader to count.
    """
    signal = retrospective.evaluate(_points(0, 0, 0, 0, 0, 0, 0, 0, 0, 3))

    assert signal.fires and signal.rule == retrospective.BEYOND_LIMITS
    assert signal.point == "u9"
    assert "3 failures on one unit is one special cause, not 3 common ones" in signal.detail


def test_the_beyond_limits_rule_names_the_latest_out_of_control_point() -> None:
    """Two assignable causes in one ledger: the newest is the unacted one.

    :func:`retrospective.claim` keys its once-only marker on the named point, so naming
    the earliest would suppress every later special cause behind the first one.
    """
    assert retrospective.evaluate(_points(3, 0, 0, 0, 0, 0, 0, 0, 0, 3)).point == "u9"


def test_a_run_above_the_centre_line_fires_inside_the_limits() -> None:
    """§3.2's second clause: a non-random pattern with no point beyond three sigma."""
    points = _points(0, 0, 0, 1, 1, 1, 1, 1, 1, 1)
    signal = retrospective.evaluate(points)

    assert max(point.failures for point in points) < retrospective.chart(points).upper
    assert signal.fires and signal.rule == retrospective.RUN
    assert f"{retrospective.RUN_LENGTH} consecutive units" in signal.detail


def test_a_rising_trend_fires_inside_the_limits() -> None:
    """The other non-random pattern, and the signal names which of the two it saw."""
    points = _points(5, 4, 3, 0, 1, 2, 3, 4, 5, 6)
    signal = retrospective.evaluate(points)

    assert max(point.failures for point in points) < retrospective.chart(points).upper
    assert signal.fires and signal.rule == retrospective.TREND
    assert f"{retrospective.TREND_LENGTH} consecutive units" in signal.detail


# --- the ledger --------------------------------------------------------------


def test_the_ledger_counts_gate_failures_and_not_the_families_it_must_not_blame(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A rework marker is the failure event; the three neighbours are excluded.

    `rework-allowance` is an operator forgiving an attempt, `gate-unreliable` is a
    defect in the gate, and `gate-shared-tracker` is another lane's record failing a
    tracker-wide check. Counting any of them puts a failure on a unit that did not
    produce it, which is the one thing the chart has to get right.
    """
    _install(
        monkeypatch,
        _FakeBr({
            "root": ["[harness-policy] rework gate=verify"],
            "child": [
                "[harness-policy] rework gate=verify",
                "[harness-policy] rework gate=merge",
                "[harness-policy] rework-allowance gate=verify",
                "[harness-policy] gate-unreliable gate=verify",
                "[harness-policy] gate-shared-tracker gate=verify culprits=other",
            ],
        }),
    )

    assert retrospective.read_ledger(tmp_path, "root") == (
        retrospective.Point("root", 1),
        retrospective.Point("child", 2),
    )


def test_a_seeded_ledger_carries_the_signal_and_its_inputs_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """From recorded markers to a verdict a reader can check, in one composition.

    Driven off a seeded tracker rather than hand-built points, so the marker read, the
    chart and the rule are exercised together — which is what `loop status` will report
    and what the engine's own blocked detail already reports.
    """
    failures = ["[harness-policy] rework gate=verify"] * 3
    ledger = {f"u{index}": [] for index in range(9)}
    _install(monkeypatch, _FakeBr({**ledger, "u9": failures}))

    signal = retrospective.evaluate(retrospective.read_ledger(tmp_path, "u0"))

    assert signal.fires and signal.rule == retrospective.BEYOND_LIMITS
    assert signal.point == "u9" and "u9 carries 3 gate failures" in signal.detail
    assert (signal.chart.observations, round(signal.chart.centre, 2)) == (10, 0.3)
    assert round(signal.chart.upper, 2) == 1.94


def test_a_silent_signal_still_carries_the_limit_nothing_crossed() -> None:
    """A verdict without its chart is unfalsifiable, so silence carries one too."""
    signal = retrospective.evaluate(_points(*([0] * 19), 1))

    assert signal.fires is False
    assert signal.chart.observations == 20 and round(signal.chart.upper, 2) == 0.72


# --- claiming a signal exactly once ------------------------------------------


def test_a_signal_is_claimed_once_while_a_different_one_still_claims(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One assignable cause, one retrospective — and never one retrospective ever."""
    _install(monkeypatch, _FakeBr({"root": []}))
    first = retrospective.Signal(True, _CHART, retrospective.BEYOND_LIMITS, "u9", "d")

    assert retrospective.claim(tmp_path, "root", first) is True
    assert retrospective.claim(tmp_path, "root", first) is False
    assert retrospective.claim(tmp_path, "root", replace(first, point="u4")) is True


def test_a_store_that_cannot_write_refuses_the_claim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Missing evidence suppresses, because an unrecorded claim re-dispatches forever."""
    monkeypatch.setattr(retrospective.tracker, "try_read_comments", lambda *_a: [])
    monkeypatch.setattr(retrospective.tracker, "try_add_comment", lambda *_a: False)
    signal = retrospective.Signal(True, _CHART, retrospective.RUN, "u9", "d")

    assert retrospective.claim(tmp_path, "root", signal) is False


# --- the output contract (§3.2) ----------------------------------------------


def test_the_output_is_a_named_control_a_tier_and_a_class_of_defects() -> None:
    """The three things §3.2 asks for, and nothing about a why-chain."""
    stated = retrospective.parse_outcome(
        "control: a gate refusing a landing whose criteria name no downstream consumer\n"
        "tier: control\n"
        "defect-class: acceptance criteria that close over an incomplete system\n"
    )

    assert retrospective.refusals(stated) == ()
    assert stated["tier"] in retrospective.TIERS


def test_a_reply_missing_any_of_the_three_is_refused() -> None:
    """Each missing field is named, so the refusal says what to answer next time."""
    refusals = retrospective.refusals(retrospective.parse_outcome("tier: warning\n"))

    assert "no control is named" in refusals
    assert "no class of defects is named" in refusals


def test_a_documentation_tier_is_recorded_as_a_downgrade_with_its_reason() -> None:
    """The weakest tier costs an explanation, or it is a way of answering nothing."""
    bare = {
        "control": "a note in the skill",
        "tier": "documentation",
        "defect-class": "prompt drift",
    }

    assert any("downgrade" in refusal for refusal in retrospective.refusals(bare))
    assert retrospective.refusals(bare | {"downgrade-reason": "no gate can see it"}) == ()


def test_a_causal_chain_must_carry_the_branch_it_did_not_take() -> None:
    """The chain alone is not an auditable answer.

    Card (2017): iterated why yields one causal path, chosen by the asker, and does not
    reproduce between analysts — so the branch not taken travels with it or the reader
    cannot tell an argued conclusion from an arbitrary one.
    """
    chained = {
        "control": "a gate",
        "tier": "control",
        "defect-class": "stale claims",
        "chain": "a -> b -> c",
    }

    assert "a causal chain must carry the branch not taken beside it" in (
        retrospective.refusals(chained)
    )
    assert retrospective.refusals(chained | {"branch-not-taken": "b could be d"}) == ()


def test_an_unknown_tier_is_refused_by_name() -> None:
    """The vocabulary is closed: an invented tier would make the field unreadable."""
    stated = {"control": "a gate", "tier": "critical", "defect-class": "stale claims"}

    assert any("is not one of" in refusal for refusal in retrospective.refusals(stated))


def test_settle_records_a_refusal_rather_than_discarding_the_reply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The dispatch was paid for either way, so the unusable answer is kept as evidence."""
    fake = _FakeBr({"root": []})
    _install(monkeypatch, fake)

    detail = retrospective.settle(tmp_path, "root", "control: a gate\n")

    assert detail.startswith("refused:")
    assert "control: a gate" in fake.comments["root"][0]


def test_settle_records_the_satisfied_contract_in_one_readable_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A human reads the marker, not the reply, so the marker carries the three fields."""
    fake = _FakeBr({"root": []})
    _install(monkeypatch, fake)

    detail = retrospective.settle(
        tmp_path, "root", "control: a plan gate\ntier: control\ndefect-class: unread scope\n"
    )

    assert detail == "control tier: a plan gate covers unread scope"


def test_the_prompt_states_the_signal_its_inputs_and_the_contract() -> None:
    """A retrospector briefed without the chart cannot say which point it is explaining."""
    signal = retrospective.Signal(True, _CHART, retrospective.BEYOND_LIMITS, "u9", "three on one")
    prompt = retrospective.prompt("root", signal)

    assert "rule: beyond-limits" in prompt and "point: u9" in prompt
    assert "upper limit 1.94" in prompt and "10 observations" in prompt
    assert "control, warning, documentation" in prompt
    assert "A why-chain alone is not an answer" in prompt


def test_the_phase_resolves_the_retrospector_and_is_not_a_rung_in_the_ladder() -> None:
    """It has a role and no place on the ladder, which is what §3.2 asks for.

    ``loop_state.PHASES`` and ``config.LOOP_PHASES`` differ by the terminal ``done``
    only; neither carries this, because a unit never sits in a retrospective.
    """
    assert roles.ROLE_BY_PHASE[retrospective.PHASE] == "retrospector"
    assert retrospective.PHASE not in LOOP_PHASES
    assert retrospective.PHASE not in PHASES
