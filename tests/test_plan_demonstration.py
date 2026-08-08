"""The end-to-end demonstration a planned child must name (D18, basicly-u2hl.20).

Its own module rather than a group in ``test_plan_gate.py``: the field is the one thing
the plan gate requires that the build entry predicate deliberately does not, so the
population each half binds on is the subject, and the last assertion here is the pair.
That is also what the size ratchet asked for when the group pushed the gate suite past
the cap — a split along a nameable responsibility, not into halves.

Control pairs throughout, as in ``test_plan_gate.py``: the refusals below mean nothing
without the admitted case above them.
"""

from __future__ import annotations

import pytest

from basicly import decompose, plan_entry, plan_gate, plan_record
from tests.plan_fixtures import child_payload as _child_payload
from tests.plan_fixtures import plan_payload as _plan_payload
from tests.plan_fixtures import planned as _planned
from tests.plan_fixtures import recorded_body as _recorded_body


@pytest.mark.parametrize(
    "demonstration",
    [
        "run `basicly decompose feat --plan plan.toml` and read the group table",
        "POST to `/v1/plans` and read the refusal in the response body",
        "`uv run pytest tests/test_plan_gate.py -k demonstration`",
    ],
)
def test_a_demonstration_naming_a_command_a_request_or_a_test_is_admitted(
    demonstration: str,
) -> None:
    """The positive control, one case per form D18 names as sufficient.

    All three are the same property — the author could point at the thing that
    exercises the child through the consumer surface — and the gate must not have an
    opinion about which of the three it is.
    """
    verdict = plan_gate.gate_plan((_planned("a", demonstration=demonstration),))

    assert not verdict.refused
    assert plan_gate.demonstration_fault(_planned("a", demonstration=demonstration)) == ""


def test_a_child_naming_no_demonstration_is_refused_naming_the_child() -> None:
    """The horizontal slice: no consumer-visible behaviour, so no check to derive.

    Refused at plan time, where the remedy — cut the plan the other way — is still
    cheap. The child has to be named, or an author with six children cannot act on it.
    """
    verdict = plan_gate.gate_plan((
        _planned("keeps its demonstration", "src/a.py"),
        _planned("add the model", "src/b.py", demonstration=None),
    ))

    assert verdict.refused
    assert "'add the model'" in verdict.reason
    assert plan_gate.DEMONSTRATION_FIELD in verdict.reason
    assert "'keeps its demonstration'" not in verdict.reason


def test_a_demonstration_naming_nothing_runnable_is_refused() -> None:
    """Prose is not a demonstration, on the same rule that prose is not a scope glob.

    The negative control for the positive one above: a sentence about the child, with
    nothing in it anybody could run, would otherwise satisfy the field by existing.
    """
    verdict = plan_gate.gate_plan((
        _planned("a", demonstration="the loop will exercise the new service end to end"),
    ))

    assert verdict.refused
    assert "nothing runnable" in verdict.reason


def test_a_demonstration_spanning_lines_is_refused_because_it_records_as_one() -> None:
    """A value the recorded form would truncate must not be accepted whole.

    `render_plan_section` writes one line per field and `parse_plan_section` reads one,
    so the second line would be silently dropped and the bead would read back as
    something its author did not write.
    """
    verdict = plan_gate.gate_plan((_planned("a", demonstration="run `basicly check`\nthen read"),))

    assert verdict.refused
    assert "one line" in verdict.reason


def test_a_plan_document_whose_child_names_no_demonstration_is_refused() -> None:
    """The refusal reaches the surface a plan actually arrives on.

    `decompose --plan`, `--children` and the loop's proposer all land in
    `parse_children`; a gate that bound only on hand-built specs would bind on nothing
    a plan author ever touches.
    """
    payload = _child_payload("a")
    del payload["demonstration"]

    with pytest.raises(plan_gate.PlanGateError) as caught:
        decompose.parse_children(_plan_payload(payload))

    assert plan_gate.DEMONSTRATION_FIELD in str(caught.value)


def test_an_empty_demonstration_is_refused_where_it_is_read() -> None:
    """A declared-but-blank field is a shape error, and stays the parser's to raise."""
    with pytest.raises(ValueError, match="demonstration"):
        decompose.parse_children(_plan_payload(_child_payload("a", demonstration="  ")))


def test_the_build_entry_predicate_does_not_require_a_demonstration() -> None:
    """The ratchet: on a recorded bead, absence cannot be told from predating the rule.

    Every child recorded under a `## Plan` heading before D18 carries no demonstration
    line. Binding the entry predicate on it would refuse that whole population — a
    stopped harness, not a bound one — so the requirement lives only where the plan is
    authored fresh. The control is the field the entry predicate *does* still bind on.
    """
    body = _recorded_body()

    assert plan_record.parse_plan_section(body).demonstration is None
    assert plan_entry.entry_verdict_for("feat.1", body).admitted
    assert not plan_entry.entry_verdict_for("feat.1", _recorded_body(integrity=None)).admitted
