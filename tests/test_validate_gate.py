from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from basicly import integrity, loop, loop_state, policy, validate_gate, wip
from basicly.config import ENGINE_GATE_PROVIDERS, LOOP_PHASES, VERIFY_GATE_PROVIDER, PolicyConfig
from basicly.loop_state import WorktreeBinding
from basicly.policy import GateStatus
from tests import fake_tracker

CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)
GATE = validate_gate.VALIDATE_GATE

L3_MARKER = "[harness-classification] level=L3 rule=cli-surface gates=full tier=maximum"
L2_MARKER = "[harness-classification] level=L2 rule=engine gates=full tier=high"


class _Proc:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class _FakeBr:
    def __init__(
        self,
        *,
        gates: list[dict] | None = None,
        comments: list[str] | None = None,
        **record: object,
    ) -> None:
        self.record: dict = {
            "id": "i",
            "status": "in_progress",
            "issue_type": "task",
            "external_ref": None,
            "dependents": [],
        }
        self.record.update(record)
        self.gates = gates or []
        self.comments = comments or []

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["show"]:
            return _Proc(json.dumps([self.record]))
        if args[:2] == ["gate", "list"]:
            return _Proc(json.dumps({"results": self.gates}))
        if args[:2] == ["comments", "list"]:
            return _Proc(json.dumps([{"text": t} for t in self.comments]))
        raise AssertionError(f"unexpected br call: {args}")


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr) -> None:
    monkeypatch.setattr(policy, "_write", fake)
    fake_tracker.install(monkeypatch, fake)


def _engine_gate(gate: str, passed: bool) -> dict:
    return {"gate": gate, "provider": VERIFY_GATE_PROVIDER, "passed": passed}


def _gates(*, verify: bool = True, validate: bool | None = None) -> GateStatus:
    passed = ("verify",) if verify else ()
    missing = () if verify else ("verify",)
    failed: tuple[str, ...] = ()
    if validate is True:
        passed = (*passed, GATE)
    elif validate is False:
        failed = (GATE,)
    else:
        missing = (*missing, GATE)
    return GateStatus(not failed and not missing, passed, failed, missing, ())


def test_validate_sits_between_verify_and_ship_in_both_tuples() -> None:
    for phases in (LOOP_PHASES, loop_state.PHASES):
        assert phases.index("verify") < phases.index("validate") < phases.index("ship")
    assert set(loop_state.PHASES) - set(LOOP_PHASES) == {"done"}


def test_every_loop_phase_has_exactly_one_handler() -> None:
    assert set(loop._HANDLERS) == set(LOOP_PHASES)


def test_validate_is_an_admissible_evidence_phase() -> None:
    config = PolicyConfig(required_gates=("verify",), max_rework=2, evidence={"validate": "r.log"})
    assert policy.unknown_evidence_phases(config) == ()


def test_a_unit_resting_in_validate_counts_as_downstream_wip() -> None:
    assert "validate" in wip.DOWNSTREAM_PHASES


@pytest.mark.parametrize(
    ("comments", "expected"),
    [
        ([L3_MARKER], "L3"),
        ([L2_MARKER], "L2"),
        ([], None),
        (["some unrelated comment"], None),
        ([L2_MARKER, L3_MARKER], "L3"),
        ([L3_MARKER, L2_MARKER], "L2"),
        (["[harness-classification] level=L9 rule=x"], None),
        (["[harness-classification] rule=x"], None),
    ],
)
def test_the_recorded_level_is_read_from_the_standing_marker(
    comments: list[str], expected: str | None
) -> None:

    assert validate_gate.level_in(comments) == expected


def test_l3_promotes_the_consumer_gate_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _install(monkeypatch, _FakeBr(comments=[L3_MARKER]))
    required = validate_gate.required_config(tmp_path, "i", CONFIG).required_gates
    assert required == ("verify", GATE)
    assert "evidence-binding" in integrity.selection_for("L3").gates
    assert "evidence-binding" not in required
    assert "full" not in required


@pytest.mark.parametrize("comments", [[L2_MARKER], []])
def test_a_unit_below_l3_owes_no_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, comments: list[str]
) -> None:
    _install(monkeypatch, _FakeBr(comments=comments))
    assert validate_gate.required_config(tmp_path, "i", CONFIG) is CONFIG


def test_a_repo_already_requiring_the_gate_is_not_given_it_twice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, _FakeBr(comments=[L3_MARKER]))
    declared = PolicyConfig(required_gates=("verify", GATE), max_rework=2)
    assert validate_gate.required_config(tmp_path, "i", declared) is declared


def test_a_second_required_gate_does_not_drop_a_landed_unit_back_to_build() -> None:

    gates = _gates(verify=True, validate=None)
    assert gates.can_advance is False
    phase = loop_state.derive_phase("in_progress", (), WorktreeBinding("n", "b"), gates, False)
    assert phase == "validate"


def test_an_l3_unit_rests_in_validate_rather_than_shipping() -> None:
    gates = _gates(verify=True, validate=None)
    assert loop_state.derive_phase("in_progress", ("ship",), None, gates, False) == "validate"
    bound = WorktreeBinding("n", "b")
    assert loop_state.derive_phase("in_progress", ("ship",), bound, gates, False) == "validate"


def test_a_failed_validation_holds_the_unit_in_validate() -> None:
    gates = _gates(verify=True, validate=False)
    assert loop_state.derive_phase("in_progress", ("ship",), None, gates, False) == "validate"


def test_a_green_validation_lets_the_unit_ship() -> None:
    gates = _gates(verify=True, validate=True)
    assert loop_state.derive_phase("in_progress", ("ship",), None, gates, False) == "ship"


def test_validation_outstanding_on_an_unbuilt_unit_does_not_reach_validate() -> None:
    gates = _gates(verify=False, validate=None)
    assert loop_state.derive_phase("in_progress", ("classify",), None, gates, False) == "classify"


@pytest.mark.parametrize("checkpoints", [(), ("ship",)])
def test_a_unit_below_l3_derives_the_phase_it_always_did(checkpoints: tuple[str, ...]) -> None:
    gates = GateStatus(True, ("verify",), (), (), ())
    expected = "ship" if checkpoints else "verify"
    bound = WorktreeBinding("n", "b")
    assert loop_state.derive_phase("in_progress", checkpoints, bound, gates, False) == expected


def test_an_l3_issue_reconstructs_into_the_validate_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(
        monkeypatch,
        _FakeBr(
            comments=[L3_MARKER],
            gates=[_engine_gate("verify", True)],
            external_ref=loop_state.format_worktree_ref("feat", "harness/feat"),
        ),
    )
    state = loop_state.read_node_state(tmp_path, "i", CONFIG)
    assert state.phase == "validate"
    assert GATE in state.gates.required_missing
    assert GATE in state.rework


def test_an_l2_issue_reconstructs_into_verify_as_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(
        monkeypatch,
        _FakeBr(
            comments=[L2_MARKER],
            gates=[_engine_gate("verify", True)],
            external_ref=loop_state.format_worktree_ref("feat", "harness/feat"),
        ),
    )
    state = loop_state.read_node_state(tmp_path, "i", CONFIG)
    assert state.phase == "verify"
    assert state.gates.required_missing == ()


def test_a_foreign_validation_result_does_not_satisfy_the_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(
        monkeypatch,
        _FakeBr(
            comments=[L3_MARKER],
            gates=[
                _engine_gate("verify", True),
                {"gate": GATE, "provider": "some-agent", "passed": True},
            ],
            external_ref=loop_state.format_worktree_ref("feat", "harness/feat"),
        ),
    )
    state = loop_state.read_node_state(tmp_path, "i", CONFIG)
    assert state.phase == "validate"
    assert [v.gate for v in state.gates.disregarded] == [GATE]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("ran the cli, it printed the table\nVALIDATION: PASS", True),
        ("could not launch it\nVALIDATION: FAIL", False),
        ("`VALIDATION: PASS`", True),
        ("validation: pass", True),
        ("VALIDATION: PASS\nactually no\nVALIDATION: FAIL", False),
        ("I ran the tests and they passed", None),
        ("", None),
        ("VALIDATION: probably fine", None),
        ("**I exercised the cli and it worked**", None),
        ("- PASS: the cli printed the table", None),
    ],
)
def test_the_verdict_is_read_from_the_reply(reply: str, expected: bool | None) -> None:

    assert validate_gate.verdict_from_reply(reply) is expected


@pytest.mark.parametrize(
    "reply",
    [
        "**VALIDATION: PASS**",
        "**VALIDATION:** PASS",
        "**VALIDATION**: PASS",
        "VALIDATION: **PASS**",
        "## VALIDATION: PASS",
        "- **VALIDATION:** PASS",
        "*VALIDATION: PASS*",
        "__VALIDATION: PASS__",
        "**`VALIDATION: PASS`**",
    ],
)
def test_a_verdict_wrapped_in_markdown_emphasis_is_read(reply: str) -> None:
    assert validate_gate.verdict_from_reply(reply) is True


def test_the_engine_records_the_verdict_under_its_own_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    calls: list[list[str]] = []
    monkeypatch.setattr(validate_gate, "_write", lambda _r, args: calls.append(args))

    validate_gate.record_verdict(tmp_path, "i", passed=True)

    assert calls[0][:3] == ["gate", "report", "i"]
    assert "--provider" in calls[0]
    provider = calls[0][calls[0].index("--provider") + 1]
    assert provider in ENGINE_GATE_PROVIDERS
    assert calls[0][calls[0].index("--status") + 1] == "pass"


def _modules_spelling_the_gate() -> dict[str, int]:

    found: dict[str, int] = {}
    for path in sorted(Path(integrity.__file__).parent.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        spellings = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == GATE
        )
        if spellings:
            found[path.name] = spellings
    return found


def test_the_gate_name_is_spelled_once_in_the_engine() -> None:

    assert _modules_spelling_the_gate() == {"integrity.py": 1}
    assert validate_gate.VALIDATE_GATE == integrity.VALIDATE_GATE


def test_the_gate_taxonomy_classifies_the_validate_gate() -> None:

    assert policy.GATE_TYPE_BY_GATE[GATE] == policy.REVISION
