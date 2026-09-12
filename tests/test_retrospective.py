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
    return tuple(retrospective.Point(f"u{index}", n) for index, n in enumerate(counts))


class _Proc:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class _FakeBr:
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
    monkeypatch.setattr(policy, "_write", fake)
    fake_tracker.install(monkeypatch, fake)


_CHART = retrospective.Chart(centre=0.3, sigma=0.55, upper=1.94, observations=10)


def test_a_single_failure_is_common_cause_even_where_the_arithmetic_signals() -> None:

    points = _points(*([0] * 19), 1)

    assert retrospective.chart(points).upper < 1
    assert retrospective.evaluate(points).fires is False


def test_one_defect_on_one_landing_does_not_fire() -> None:
    signal = retrospective.evaluate(_points(0, 0, 0, 0, 0, 0, 0, 0, 0, 1))

    assert signal.fires is False
    assert "common cause" in signal.detail


def test_a_ledger_shorter_than_the_shortest_rule_never_fires() -> None:
    signal = retrospective.evaluate(_points(0, 0, 0, 0, 0, 9))

    assert signal.fires is False
    assert f"below the {retrospective.MIN_OBSERVATIONS}" in signal.detail


def test_a_scattered_ledger_inside_the_limits_is_a_stable_process() -> None:
    assert retrospective.evaluate(_points(0, 1, 0, 1, 0, 1, 0, 1, 0, 1)).fires is False


def test_a_falling_trend_is_not_a_signal() -> None:

    assert retrospective.evaluate(_points(6, 5, 4, 3, 2, 1, 0, 0, 0, 0)).fires is False


def test_three_defects_on_one_landing_fire_and_name_the_point() -> None:

    signal = retrospective.evaluate(_points(0, 0, 0, 0, 0, 0, 0, 0, 0, 3))

    assert signal.fires and signal.rule == retrospective.BEYOND_LIMITS
    assert signal.point == "u9"
    assert "3 failures on one unit is one special cause, not 3 common ones" in signal.detail


def test_the_beyond_limits_rule_names_the_latest_out_of_control_point() -> None:

    assert retrospective.evaluate(_points(3, 0, 0, 0, 0, 0, 0, 0, 0, 3)).point == "u9"


def test_a_run_above_the_centre_line_fires_inside_the_limits() -> None:
    points = _points(0, 0, 0, 1, 1, 1, 1, 1, 1, 1)
    signal = retrospective.evaluate(points)

    assert max(point.failures for point in points) < retrospective.chart(points).upper
    assert signal.fires and signal.rule == retrospective.RUN
    assert f"{retrospective.RUN_LENGTH} consecutive units" in signal.detail


def test_a_rising_trend_fires_inside_the_limits() -> None:
    points = _points(5, 4, 3, 0, 1, 2, 3, 4, 5, 6)
    signal = retrospective.evaluate(points)

    assert max(point.failures for point in points) < retrospective.chart(points).upper
    assert signal.fires and signal.rule == retrospective.TREND
    assert f"{retrospective.TREND_LENGTH} consecutive units" in signal.detail


def test_the_ledger_counts_gate_failures_and_not_the_families_it_must_not_blame(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

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

    failures = ["[harness-policy] rework gate=verify"] * 3
    ledger = {f"u{index}": [] for index in range(9)}
    _install(monkeypatch, _FakeBr({**ledger, "u9": failures}))

    signal = retrospective.evaluate(retrospective.read_ledger(tmp_path, "u0"))

    assert signal.fires and signal.rule == retrospective.BEYOND_LIMITS
    assert signal.point == "u9" and "u9 carries 3 gate failures" in signal.detail
    assert (signal.chart.observations, round(signal.chart.centre, 2)) == (10, 0.3)
    assert round(signal.chart.upper, 2) == 1.94


def test_a_silent_signal_still_carries_the_limit_nothing_crossed() -> None:
    signal = retrospective.evaluate(_points(*([0] * 19), 1))

    assert signal.fires is False
    assert signal.chart.observations == 20 and round(signal.chart.upper, 2) == 0.72


def test_a_signal_is_claimed_once_while_a_different_one_still_claims(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, _FakeBr({"root": []}))
    first = retrospective.Signal(True, _CHART, retrospective.BEYOND_LIMITS, "u9", "d")

    assert retrospective.claim(tmp_path, "root", first) is True
    assert retrospective.claim(tmp_path, "root", first) is False
    assert retrospective.claim(tmp_path, "root", replace(first, point="u4")) is True


def test_a_store_that_cannot_write_refuses_the_claim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(retrospective.tracker, "try_read_comments", lambda *_a: [])
    monkeypatch.setattr(retrospective.tracker, "try_add_comment", lambda *_a: False)
    signal = retrospective.Signal(True, _CHART, retrospective.RUN, "u9", "d")

    assert retrospective.claim(tmp_path, "root", signal) is False


def test_the_output_is_a_named_control_a_tier_and_a_class_of_defects() -> None:
    stated = retrospective.parse_outcome(
        "control: a gate refusing a landing whose criteria name no downstream consumer\n"
        "tier: control\n"
        "defect-class: acceptance criteria that close over an incomplete system\n"
    )

    assert retrospective.refusals(stated) == ()
    assert stated["tier"] in retrospective.TIERS


def test_a_reply_missing_any_of_the_three_is_refused() -> None:
    refusals = retrospective.refusals(retrospective.parse_outcome("tier: warning\n"))

    assert "no control is named" in refusals
    assert "no class of defects is named" in refusals


def test_a_documentation_tier_is_recorded_as_a_downgrade_with_its_reason() -> None:
    bare = {
        "control": "a note in the skill",
        "tier": "documentation",
        "defect-class": "prompt drift",
    }

    assert any("downgrade" in refusal for refusal in retrospective.refusals(bare))
    assert retrospective.refusals(bare | {"downgrade-reason": "no gate can see it"}) == ()


def test_a_causal_chain_must_carry_the_branch_it_did_not_take() -> None:

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
    stated = {"control": "a gate", "tier": "critical", "defect-class": "stale claims"}

    assert any("is not one of" in refusal for refusal in retrospective.refusals(stated))


def test_settle_records_a_refusal_rather_than_discarding_the_reply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr({"root": []})
    _install(monkeypatch, fake)

    detail = retrospective.settle(tmp_path, "root", "control: a gate\n")

    assert detail.startswith("refused:")
    assert "control: a gate" in fake.comments["root"][0]


def test_settle_records_the_satisfied_contract_in_one_readable_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr({"root": []})
    _install(monkeypatch, fake)

    detail = retrospective.settle(
        tmp_path, "root", "control: a plan gate\ntier: control\ndefect-class: unread scope\n"
    )

    assert detail == "control tier: a plan gate covers unread scope"


def test_the_prompt_states_the_signal_its_inputs_and_the_contract() -> None:
    signal = retrospective.Signal(True, _CHART, retrospective.BEYOND_LIMITS, "u9", "three on one")
    prompt = retrospective.prompt("root", signal)

    assert "rule: beyond-limits" in prompt and "point: u9" in prompt
    assert "upper limit 1.94" in prompt and "10 observations" in prompt
    assert "control, warning, documentation" in prompt
    assert "A why-chain alone is not an answer" in prompt


def test_the_phase_resolves_the_retrospector_and_is_not_a_rung_in_the_ladder() -> None:

    assert roles.ROLE_BY_PHASE[retrospective.PHASE] == "retrospector"
    assert retrospective.PHASE not in LOOP_PHASES
    assert retrospective.PHASE not in PHASES
