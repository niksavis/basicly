from __future__ import annotations

from pathlib import Path

import pytest

from basicly import decompose, demonstration_proof, plan_entry, plan_gate, plan_record
from tests.plan_fixtures import child_payload as _child_payload
from tests.plan_fixtures import plan_payload as _plan_payload
from tests.plan_fixtures import planned as _planned
from tests.plan_fixtures import recorded_body as _recorded_body

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "demonstration",
    [
        "run `basicly decompose feat --plan plan.toml` and read the group table",
        "POST to `/v1/plans` and read the refusal in the response body",
        "`uv run pytest tests/test_plan_demonstration.py -k demonstration`",
    ],
)
def test_a_demonstration_naming_a_command_a_request_or_a_test_is_admitted(
    demonstration: str,
) -> None:

    unit = _planned("a", demonstration=demonstration)
    verdict = plan_gate.gate_plan((unit,))

    assert not verdict.refused
    assert plan_gate.demonstration_fault(unit) == ""
    assert not demonstration_proof.collects_nothing(REPO_ROOT, demonstration)


def test_a_child_naming_no_demonstration_is_refused_naming_the_child() -> None:

    verdict = plan_gate.gate_plan((
        _planned("keeps its demonstration", "src/a.py"),
        _planned("add the model", "src/b.py", demonstration=None),
    ))

    assert verdict.refused
    assert "'add the model'" in verdict.reason
    assert plan_gate.DEMONSTRATION_FIELD in verdict.reason
    assert "'keeps its demonstration'" not in verdict.reason


def test_a_demonstration_naming_nothing_runnable_is_refused() -> None:

    verdict = plan_gate.gate_plan((
        _planned("a", demonstration="the loop will exercise the new service end to end"),
    ))

    assert verdict.refused
    assert "nothing runnable" in verdict.reason


def test_a_demonstration_spanning_lines_is_refused_because_it_records_as_one() -> None:

    verdict = plan_gate.gate_plan((_planned("a", demonstration="run `basicly check`\nthen read"),))

    assert verdict.refused
    assert "one line" in verdict.reason


def test_a_plan_document_whose_child_names_no_demonstration_is_refused() -> None:

    payload = _child_payload("a")
    del payload["demonstration"]

    with pytest.raises(plan_gate.PlanGateError) as caught:
        decompose.parse_children(_plan_payload(payload))

    assert plan_gate.DEMONSTRATION_FIELD in str(caught.value)


def test_an_empty_demonstration_is_refused_where_it_is_read() -> None:
    with pytest.raises(ValueError, match="demonstration"):
        decompose.parse_children(_plan_payload(_child_payload("a", demonstration="  ")))


def test_the_build_entry_predicate_does_not_require_a_demonstration() -> None:

    body = _recorded_body()

    assert plan_record.parse_plan_section(body).demonstration is None
    assert plan_entry.entry_verdict_for("feat.1", body).admitted
    assert not plan_entry.entry_verdict_for("feat.1", _recorded_body(integrity=None)).admitted
