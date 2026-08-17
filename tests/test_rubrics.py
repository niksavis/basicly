"""Tests for the behavioral-rubric framework (basicly-0122).

Covers the source model + loader + work-type selection (basicly-0122.1) and the
evaluation + advisory-gate layer (basicly-0122.2): deterministic checks via the
verify runner, judged checks via the agent-agnostic runner, and gate status.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from basicly import config, review, rubrics, runner
from basicly.rubrics import DETERMINISTIC, JUDGED, NO, UNKNOWN, YES, Rubric, RubricCheck

VALID = """\
id: sample
description: A sample rubric.
applies_to:
  - bug
  - feature
checks:
  - id: has-test
    question: Was a test added?
    kind: judged
  - id: builds
    question: Does it build?
    kind: deterministic
    command: make build
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    d = tmp_path / "rubrics"
    d.mkdir(exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")
    return d


def test_load_rubrics_parses_source(tmp_path: Path) -> None:
    """A well-formed rubric loads into a Rubric with typed checks."""
    rubric_dir = _write(tmp_path, "s.rubric.yaml", VALID)
    (rubric,) = rubrics.load_rubrics(rubric_dir)
    assert rubric.id == "sample"
    assert rubric.applies_to == ("bug", "feature")
    assert [(c.id, c.kind) for c in rubric.checks] == [
        ("has-test", JUDGED),
        ("builds", DETERMINISTIC),
    ]
    assert rubric.checks[1].command == "make build"


def test_load_rubrics_missing_dir_is_empty(tmp_path: Path) -> None:
    """No rubrics directory yields no rubrics (not an error)."""
    assert rubrics.load_rubrics(tmp_path / "nope") == []


def test_select_rubrics_by_work_type(tmp_path: Path) -> None:
    """Selection keeps only rubrics whose applies_to includes the work type."""
    rubric_dir = _write(tmp_path, "s.rubric.yaml", VALID)
    loaded = rubrics.load_rubrics(rubric_dir)
    assert [r.id for r in rubrics.select_rubrics(loaded, "bug")] == ["sample"]
    assert rubrics.select_rubrics(loaded, "chore") == []


_ONE_CHECK = "checks:\n  - {id: a, question: q, kind: judged}\n"


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (f"id: s\napplies_to:\n  - bug\n{_ONE_CHECK}", "missing a non-empty 'description'"),
        (
            f"id: s\ndescription: d\napplies_to: []\n{_ONE_CHECK}",
            "'applies_to' must be a non-empty list",
        ),
        (
            "id: s\ndescription: d\napplies_to:\n  - bug\nchecks: []\n",
            "'checks' must be a non-empty list",
        ),
    ],
)
def test_load_rubrics_rejects_malformed_top_level(tmp_path: Path, text: str, match: str) -> None:
    """Missing/empty top-level fields are hard errors."""
    rubric_dir = _write(tmp_path, "s.rubric.yaml", text)
    with pytest.raises(ValueError, match=match):
        rubrics.load_rubrics(rubric_dir)


@pytest.mark.parametrize(
    ("check", "match"),
    [
        ("{id: a, question: q, kind: bogus}", "unknown kind"),
        ("{id: a, question: q, kind: deterministic}", "exactly one of 'command'"),
        (
            "{id: a, question: q, kind: deterministic, command: x, verify_mode: full}",
            "exactly one of 'command'",
        ),
        (
            "{id: a, question: q, kind: deterministic, verify_mode: bogus}",
            "unknown verify_mode",
        ),
        ("{id: a, question: q, kind: judged, command: x}", "must not carry a 'command'"),
        ("{id: a, question: q, kind: judged, verify_mode: full}", "must not carry a 'command'"),
        ("{id: a, kind: judged}", "missing a non-empty 'question'"),
    ],
)
def test_load_rubrics_rejects_malformed_check(tmp_path: Path, check: str, match: str) -> None:
    """Each check-level invariant is enforced."""
    text = f"id: s\ndescription: d\napplies_to:\n  - bug\nchecks:\n  - {check}\n"
    rubric_dir = _write(tmp_path, "s.rubric.yaml", text)
    with pytest.raises(ValueError, match=match):
        rubrics.load_rubrics(rubric_dir)


def test_load_rubrics_rejects_a_judged_only_rubric(tmp_path: Path) -> None:
    """A rubric with no deterministic check could never fail its gate."""
    text = f"id: s\ndescription: d\napplies_to:\n  - bug\n{_ONE_CHECK}"
    rubric_dir = _write(tmp_path, "s.rubric.yaml", text)
    with pytest.raises(ValueError, match="no deterministic check"):
        rubrics.load_rubrics(rubric_dir)


def test_bundled_sample_rubrics_load() -> None:
    """The shipped sample rubrics load and cover both check kinds."""
    loaded = rubrics.load_rubrics()
    by_id = {r.id: r for r in loaded}
    assert "bug-behaviors" in by_id
    assert "feature-behaviors" in by_id
    kinds = {c.kind for c in by_id["bug-behaviors"].checks}
    assert kinds == {JUDGED, DETERMINISTIC}  # bug rubric exercises both paths


def test_every_leaf_work_type_has_a_rubric_with_teeth() -> None:
    """Each work type the loop can build selects a rubric that can actually fail.

    A lane's validate gate is required (D4), and gate_status is
    deterministic-first, so a work type with no rubric — or one carrying only
    judged checks — passes having evaluated nothing (basicly-kjc5.19).
    """
    loaded = rubrics.load_rubrics()
    for work_type in ("bug", "task", "chore", "feature"):
        selected = rubrics.select_rubrics(loaded, work_type)
        assert selected, f"no rubric selected for work type {work_type!r}"
        for rubric in selected:
            assert any(c.kind == DETERMINISTIC for c in rubric.checks), (
                f"rubric {rubric.id!r} has no deterministic check"
            )


def test_shipped_deterministic_checks_are_toolchain_portable() -> None:
    """A shipped rubric delegates to the repo's verify config, not a fixed command.

    Rubrics ship in the core catalog to every consumer repo, so a hardcoded
    `uv run pytest` would answer "no" in any repo that is not this one.
    """
    for rubric in rubrics.load_rubrics():
        for check in rubric.checks:
            if check.kind == DETERMINISTIC:
                assert check.verify_mode and not check.command, (
                    f"{rubric.id}/{check.id} hardcodes a command instead of a verify_mode"
                )


# --- evaluation (basicly-0122.2) --------------------------------------------


def _det(command: str) -> RubricCheck:
    return RubricCheck(id="det", question="q", kind=DETERMINISTIC, command=command)


def _judged_rubric() -> Rubric:
    return Rubric(
        id="r",
        description="d",
        applies_to=("bug",),
        checks=(
            RubricCheck(id="q1", question="Q1?", kind=JUDGED),
            RubricCheck(id="q2", question="Q2?", kind=JUDGED),
        ),
    )


def test_evaluate_deterministic_maps_exit_code(tmp_path: Path) -> None:
    """A deterministic check is yes on exit 0, no on a non-zero exit."""
    # evaluate_deterministic splits the command with posix shlex, which treats
    # backslashes as escapes; embed the interpreter with forward slashes so a
    # Windows sys.executable path survives the split (Windows accepts them too).
    python = Path(sys.executable).as_posix()
    ok = rubrics.evaluate_deterministic(_det(f"{python} -c pass"), tmp_path)
    assert ok.answer == YES and ok.kind == DETERMINISTIC
    fail_cmd = _det(f'{python} -c "import sys;sys.exit(1)"')
    assert rubrics.evaluate_deterministic(fail_cmd, tmp_path).answer == NO


def test_evaluate_deterministic_verify_mode_delegates_to_the_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verify_mode check answers from the repo's own configured verify run."""
    check = RubricCheck(id="gates", question="q", kind=DETERMINISTIC, verify_mode="full")
    seen: dict[str, object] = {}

    def _run_verify(repo_root: Path, mode: str):
        seen["repo_root"], seen["mode"] = repo_root, mode
        return SimpleNamespace(passed=False, failures=["ruff", "pytest"])

    monkeypatch.setattr(rubrics.verify, "run_verify", _run_verify)
    verdict = rubrics.evaluate_deterministic(check, tmp_path)

    assert (seen["repo_root"], seen["mode"]) == (tmp_path, "full")
    assert verdict.answer == NO
    assert "ruff" in verdict.evidence and "pytest" in verdict.evidence

    monkeypatch.setattr(
        rubrics.verify, "run_verify", lambda *_a: SimpleNamespace(passed=True, failures=[])
    )
    assert rubrics.evaluate_deterministic(check, tmp_path).answer == YES


def test_parse_judged_reads_yes_no_and_defaults_unknown() -> None:
    """Parsed answers map to verdicts; an unanswered check is UNKNOWN."""
    checks = _judged_rubric().checks
    verdicts = rubrics.parse_judged("q1: yes - has a test\nq2: maybe\n", list(checks))
    by_id = {v.check_id: v for v in verdicts}
    assert by_id["q1"].answer == YES and by_id["q1"].evidence == "has a test"
    assert by_id["q2"].answer == UNKNOWN  # "maybe" is not yes/no


# --- Severity is a required field on judged output (basicly-m4zv.4, §5.4) -----


def test_parse_judged_reads_the_severity_off_a_finding() -> None:
    """A judged 'no' carries its severity onto the verdict, evidence intact."""
    verdicts = rubrics.parse_judged(
        "q1: yes - has a test\nq2: no - BLOCKER - the criterion is unmet\n",
        list(_judged_rubric().checks),
    )
    by_id = {v.check_id: v for v in verdicts}
    assert (by_id["q2"].answer, by_id["q2"].severity) == (NO, rubrics.BLOCKER)
    assert by_id["q2"].evidence == "the criterion is unmet"
    # A yes reports no finding, so there is nothing to classify.
    assert by_id["q1"].severity == ""


@pytest.mark.parametrize("severity", ["BLOCKER", "IMPORTANT", "MINOR", "minor"])
def test_parse_judged_accepts_the_whole_vocabulary_case_insensitively(severity: str) -> None:
    """Every named class parses, normalised to the upper-case contract spelling."""
    (verdict,) = rubrics.parse_judged(
        f"q2: no - {severity} - unmet\n", [_judged_rubric().checks[1]]
    )
    assert verdict.severity == severity.upper()


def test_parse_judged_rejects_a_finding_with_no_severity() -> None:
    """The AC: a severity-less judged verdict is a schema violation, not a complaint.

    The distinction is the whole point. Before this, the reply parsed cleanly into
    a NO the engine acted on — it enqueued a decision nobody could triage, because
    "the criterion is unmet" alone does not say whether that blocks the goal or is
    a note. The reply is now refused entire, the way unparseable JSON would be.
    """
    with pytest.raises(rubrics.JudgedSchemaError) as raised:
        rubrics.parse_judged("q1: yes - fine\nq2: no - missing\n", list(_judged_rubric().checks))
    assert raised.value.violations == (
        "q2: answered 'no' with no severity (BLOCKER/IMPORTANT/MINOR)",
    )


def test_parse_judged_reports_every_violation_at_once() -> None:
    """One re-request has to be able to fix the whole reply, so name all of it."""
    with pytest.raises(rubrics.JudgedSchemaError) as raised:
        rubrics.parse_judged("q1: no - one\nq2: no - two\n", list(_judged_rubric().checks))
    assert [v.split(":")[0] for v in raised.value.violations] == ["q1", "q2"]


def test_an_unanswered_check_is_unknown_not_a_violation() -> None:
    """Silence is an absence of judgment; only an answered 'no' owes a severity.

    Conflating them would turn every handoff and every truncated reply into a
    schema rejection, which spends a second dispatch to learn what the UNKNOWN
    already said.
    """
    verdicts = rubrics.parse_judged("q1: yes - fine\n", list(_judged_rubric().checks))
    assert {v.check_id: v.answer for v in verdicts} == {"q1": YES, "q2": UNKNOWN}


def test_the_severity_field_is_only_recognised_with_its_separator() -> None:
    """Prose opening with a severity word is evidence, not the field.

    Without the separator rule, "no - MINOR issue in the helper" would classify
    itself MINOR and quietly lose the first word of its own evidence — a parse
    that satisfies the contract by inventing the field it was checking for. Held
    to the rule, the same line is what it actually is: a finding with no severity,
    rejected.
    """
    (verdict,) = rubrics.parse_judged(
        "q1: yes - MINOR issue in the helper, but the test is there\n",
        [_judged_rubric().checks[0]],
    )
    assert verdict.severity == ""
    assert verdict.evidence == "MINOR issue in the helper, but the test is there"

    with pytest.raises(rubrics.JudgedSchemaError):
        rubrics.parse_judged("q2: no - MINOR issue in the helper\n", [_judged_rubric().checks[1]])


def test_a_severity_less_finding_cannot_be_constructed_or_recorded() -> None:
    """The invariant is on the record, so no call site can route around the parser.

    ``report_gate`` never has to re-check it: a judged NO that reaches a gate
    report necessarily has a severity, because one without it does not exist.
    """
    with pytest.raises(rubrics.JudgedSchemaError):
        rubrics.CheckVerdict("j", JUDGED, NO, "unmet")
    with pytest.raises(rubrics.JudgedSchemaError):
        rubrics.CheckVerdict("j", JUDGED, NO, "unmet", "CRITICAL")  # not in the vocabulary
    # The three shapes that report no finding must not carry one.
    with pytest.raises(rubrics.JudgedSchemaError):
        rubrics.CheckVerdict("j", JUDGED, YES, "fine", rubrics.MINOR)
    assert rubrics.CheckVerdict("j", JUDGED, NO, "unmet", rubrics.MINOR).severity == rubrics.MINOR


def test_the_gate_record_carries_the_severity(monkeypatch: pytest.MonkeyPatch) -> None:
    """A note reading only 'j=no' cannot tell a MINOR from a BLOCKER."""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        rubrics.br, "run_br", lambda _r, args: (calls.append(args), _proc("", 0))[1]
    )

    rubrics.report_gate(
        Path(), "i", [rubrics.CheckVerdict("j", JUDGED, NO, "unmet", rubrics.BLOCKER)]
    )

    judged_note = calls[1][calls[1].index("--note") + 1]
    assert "j=no (BLOCKER)" in judged_note


def test_evaluate_judged_parses_runner_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Judged checks dispatch one runner call and parse its structured answers."""
    monkeypatch.setattr(
        runner,
        "run",
        lambda *_a, **_k: runner.RunResult(
            "x", (), executed=True, returncode=0, stdout=_JUDGE_ANSWERS
        ),
    )
    verdicts = rubrics.evaluate("i", _judged_rubric(), tmp_path)
    assert {v.check_id: v.answer for v in verdicts} == {"q1": YES, "q2": NO}
    assert {v.check_id: v.severity for v in verdicts} == {"q1": "", "q2": rubrics.IMPORTANT}


def test_evaluate_judged_is_bounded_and_metered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The judge obeys runner_timeout and writes a run-record (basicly-kjc5.31).

    Unmetered, a judged dispatch spends tokens that never reach the session's D3
    ceiling; unbounded, a hung judge hangs the whole pass.

    ``capture_usage`` is what makes the record's numbers the adapter's own
    (basicly-gczc). Unflagged it carried a chars/4 estimate, which
    ``policy.session_spend`` counts as an unmeterable dispatch — and one of those
    zeroes the grant's remaining budget, so an unmetered judge did not merely
    under-count the session, it halted it.
    """
    seen: dict[str, object] = {}

    def _run(_spec, _prompt, _cwd, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        seen["capture_usage"] = kwargs.get("capture_usage", False)
        return runner.RunResult("x", (), executed=True, returncode=0, stdout="q1: yes - ok\n")

    recorded: list[tuple[str, object, str]] = []

    def _record(_repo, issue, _spec, _result, **inputs):
        recorded.append((issue, inputs.get("phase"), str(inputs.get("prompt"))))

    monkeypatch.setattr(runner, "run", _run)
    monkeypatch.setattr(runner, "record_dispatch", _record)
    rubrics.evaluate("i", _judged_rubric(), tmp_path)

    assert seen["timeout"] == 3600.0  # the [runner] runner_timeout default
    # The flag flips some adapters' stdout to a usage envelope; the answer is read
    # back out of it through runner.result_text, so both survive.
    assert seen["capture_usage"] is True
    # The recorded inputs identify the dispatch (D9): the phase it ran in and the
    # exact prompt, which the record keeps only as a digest.
    assert len(recorded) == 1
    issue, phase, prompt = recorded[0]
    assert (issue, phase) == ("i", "validate")
    assert prompt and "q1" in prompt


def test_the_judge_call_site_comment_matches_the_flag_it_describes() -> None:
    """The prose at the judged dispatch claims metering; the flag has to be there too.

    The basicly-ipx2 defect class, and this docstring's own predecessor is the
    example: it explained at length why ``capture_usage`` was deliberately *not*
    set, which was true prose about a real constraint — and the constraint's cost,
    a halted grant on every validated lane, was nowhere in it. Dropping either side
    fails here.
    """
    mentions = [line.strip() for line in inspect.getsource(rubrics._dispatch_judge).splitlines()]
    mentions = [line for line in mentions if "capture_usage" in line]
    assert any("capture_usage=True" in line for line in mentions), (
        "the prose claims the judge is metered through a call that does not capture usage"
    )
    assert any("capture_usage=True" not in line for line in mentions), (
        "the flag is passed with no prose saying what metering means here"
    )


# What a judge replies with, and the same reply inside each envelope a metered
# dispatch wraps it in. The last case is the plain-text arm: a store-measured
# adapter's stdout was never wrapped, and neither was an adapter with no usage
# format at all.
_JUDGE_ANSWERS = "q1: yes - ok\nq2: no - IMPORTANT - missing\n"


@pytest.mark.parametrize(
    ("usage_format", "stdout"),
    [
        (runner.CLAUDE_JSON, json.dumps({"type": "result", "result": _JUDGE_ANSWERS, "usage": {}})),
        (runner.CLAUDE_STREAM_JSON, json.dumps({"type": "result", "result": _JUDGE_ANSWERS})),
        (
            runner.CODEX_JSONL,
            json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": _JUDGE_ANSWERS},
            }),
        ),
        (None, _JUDGE_ANSWERS),
    ],
)
def test_judged_answers_survive_their_usage_envelope(
    usage_format: str | None,
    stdout: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A metered judge's answer lines are unwrapped before the text parser sees them.

    Every line of a usage envelope starts with ``{``, and the answer pattern is
    line-anchored, so the naive one-line fix would resolve every judged check to
    UNKNOWN — a silently unjudged rubric on a dispatch whose numbers finally looked
    right.
    """
    spec = runner.RunnerSpec("x", runner.HEADLESS, ("x",), usage_format=usage_format)
    monkeypatch.setattr(runner, "record_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "select_runner", lambda *_a, **_k: spec)
    monkeypatch.setattr(
        runner,
        "run",
        lambda *_a, **_k: runner.RunResult("x", (), executed=True, returncode=0, stdout=stdout),
    )

    verdicts = rubrics.evaluate("i", _judged_rubric(), tmp_path)

    assert {v.check_id: v.answer for v in verdicts} == {"q1": YES, "q2": NO}


def test_a_raw_envelope_judges_nothing() -> None:
    """The control for the test above: unwrapped, every judged check goes UNKNOWN.

    This is what the naive one-line fix ships — a rubric that checked nothing on a
    dispatch whose token numbers finally looked right.
    """
    envelope = json.dumps({"type": "result", "result": _JUDGE_ANSWERS, "usage": {}})
    verdicts = rubrics.parse_judged(envelope, list(_judged_rubric().checks))
    assert {v.answer for v in verdicts} == {UNKNOWN}


def test_evaluate_judged_timeout_is_unknown_not_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timed-out judge answered nothing; inventing a NO would queue a fake dispute."""
    monkeypatch.setattr(
        runner,
        "run",
        lambda *_a, **_k: runner.RunResult("x", (), executed=True, returncode=1, timed_out=True),
    )
    monkeypatch.setattr(runner, "record_dispatch", lambda *_a, **_k: None)
    verdicts = rubrics.evaluate("i", _judged_rubric(), tmp_path)
    assert {v.answer for v in verdicts} == {UNKNOWN}
    assert all("timed out" in v.evidence for v in verdicts)


def test_evaluate_judged_handoff_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the runner hands off (no agent CLI), judged checks resolve to UNKNOWN."""
    monkeypatch.setattr(
        runner,
        "run",
        lambda *_a, **_k: runner.RunResult("manual", (), executed=False, handoff=True),
    )
    verdicts = rubrics.evaluate("i", _judged_rubric(), tmp_path)
    assert all(v.answer == UNKNOWN for v in verdicts)


def _proc(output: str = "", returncode: int = 0) -> SimpleNamespace:
    """Minimal stand-in for the CompletedProcess ``br.run_br`` returns."""
    return SimpleNamespace(stdout=output, stderr=output, returncode=returncode)


def test_gate_status_is_deterministic_first() -> None:
    """Only a deterministic 'no' fails the pre-flight gate; a judged 'no' does not."""
    det_no = [rubrics.CheckVerdict("d", DETERMINISTIC, NO)]
    judged_no = [rubrics.CheckVerdict("j", JUDGED, NO, severity=rubrics.BLOCKER)]
    assert rubrics.gate_status(det_no) == "fail"
    assert rubrics.gate_status(judged_no) == "pass"
    assert rubrics.gate_status([rubrics.CheckVerdict("d", DETERMINISTIC, YES)]) == "pass"


# --- Validate is a composite of two separately-typed gates (basicly-imnu.1) ----


def test_escalation_status_fails_only_on_a_judged_no() -> None:
    """The escalation half may say fail; that is the signal a decision was enqueued.

    A deterministic no belongs to the pre-flight half, and UNKNOWN is an absence of
    judgment (a handoff or an unparseable reply) rather than a negative one.
    """
    judged_no = rubrics.CheckVerdict("j", JUDGED, NO, severity=rubrics.BLOCKER)
    assert rubrics.escalation_status([judged_no]) == "fail"
    assert rubrics.escalation_status([rubrics.CheckVerdict("j", JUDGED, YES)]) == "pass"
    assert rubrics.escalation_status([rubrics.CheckVerdict("j", JUDGED, UNKNOWN)]) == "pass"
    assert rubrics.escalation_status([rubrics.CheckVerdict("d", DETERMINISTIC, NO)]) == "pass"


def test_report_gate_records_both_halves_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    """A judged no must be visible on the record, not only in a note.

    As one gate, a judged no left the combined gate reading ``pass``, so a reader
    could not tell a satisfied acceptance criterion from a disputed one.
    """
    calls: list[list[str]] = []

    def fake(_repo_root: Path, args: list[str]) -> SimpleNamespace:
        calls.append(args)
        return _proc("", 0)

    monkeypatch.setattr(rubrics.br, "run_br", fake)

    ok, message = rubrics.report_gate(
        Path(),
        "i",
        [
            rubrics.CheckVerdict("d", DETERMINISTIC, YES),
            rubrics.CheckVerdict("j", JUDGED, NO, "criterion unmet", rubrics.BLOCKER),
        ],
    )

    assert ok, message
    reported = {args[args.index("--gate") + 1]: args[args.index("--status") + 1] for args in calls}
    assert reported == {rubrics.RUBRIC_GATE: "pass", rubrics.RUBRIC_JUDGED_GATE: "fail"}
    # The pre-flight (required) half is reported first, so a br failure midway
    # leaves the required half recorded rather than the advisory one.
    assert calls[0][calls[0].index("--gate") + 1] == rubrics.RUBRIC_GATE


def test_report_gate_records_both_halves_even_when_one_has_no_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gate that appears only when it has something to say cannot be read afterwards.

    A missing ``rubric-judged`` would be ambiguous between "no judged checks
    existed" and "the judged half never ran".
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(
        rubrics.br, "run_br", lambda _r, args: (calls.append(args), _proc("", 0))[1]
    )

    rubrics.report_gate(Path(), "i", [rubrics.CheckVerdict("d", DETERMINISTIC, YES)])

    gates = [args[args.index("--gate") + 1] for args in calls]
    assert gates == [rubrics.RUBRIC_GATE, rubrics.RUBRIC_JUDGED_GATE]
    judged_note = calls[1][calls[1].index("--note") + 1]
    assert "no checks" in judged_note


def test_report_gate_reports_failure_when_either_half_fails_to_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial record is worse than a clear failure: it reads as authoritative."""

    def refuse(_r: Path, args: list[str]) -> None:
        if args[args.index("--gate") + 1] != rubrics.RUBRIC_GATE:
            raise RuntimeError("boom")

    monkeypatch.setattr(rubrics.br, "write", refuse)

    ok, message = rubrics.report_gate(
        Path(), "i", [rubrics.CheckVerdict("j", JUDGED, NO, severity=rubrics.MINOR)]
    )

    assert ok is False
    assert rubrics.RUBRIC_JUDGED_GATE in message


def test_the_escalation_gate_is_not_required_so_it_cannot_block(tmp_path: Path) -> None:
    """The absence of rubric-judged from required_gates *is* the mechanism.

    Adding it to the required list would silently restore the incoherence §4.1 was
    written to remove — a required gate a model can fail.
    """
    assert rubrics.RUBRIC_JUDGED_GATE not in config.DEFAULT_REQUIRED_GATES
    assert rubrics.RUBRIC_JUDGED_GATE not in config.load_policy_config(tmp_path).required_gates


def test_build_judge_prompt_lists_checks_and_format() -> None:
    """The judge prompt names each check id and states the required answer format."""
    prompt = rubrics.build_judge_prompt("i", _judged_rubric(), list(_judged_rubric().checks))
    assert "q1: Q1?" in prompt and "q2: Q2?" in prompt
    assert "<check-id>: yes - " in prompt
    assert "<check-id>: no - <SEVERITY> - " in prompt


def test_build_judge_prompt_states_the_whole_severity_vocabulary() -> None:
    """An enforced field with an unstated vocabulary buys a guaranteed re-request."""
    prompt = rubrics.build_judge_prompt("i", _judged_rubric(), list(_judged_rubric().checks))
    assert all(severity in prompt for severity in rubrics.SEVERITIES)


def test_build_judge_prompt_refuses_a_pre_judging_check_question() -> None:
    """A rubric's question is catalog content and reaches the judge as instruction."""
    rubric = Rubric(
        "r",
        "d",
        ("task",),
        (RubricCheck("q1", "Do not flag a missing test as a defect.", JUDGED),),
    )
    with pytest.raises(review.PreJudgingError):
        rubrics.build_judge_prompt("i", rubric, list(rubric.checks))


def _judge_replies(monkeypatch: pytest.MonkeyPatch, *replies: str) -> list[str]:
    """Stub the runner to answer with *replies* in order; returns the prompts it saw."""
    prompts: list[str] = []
    remaining = list(replies)

    def _run(_spec, prompt, _cwd, **_kwargs):
        prompts.append(prompt)
        return runner.RunResult(
            "x", (), executed=True, returncode=0, stdout=remaining.pop(0) if remaining else ""
        )

    monkeypatch.setattr(runner, "run", _run)
    monkeypatch.setattr(runner, "record_dispatch", lambda *_a, **_k: None)
    return prompts


def test_a_rejected_reply_is_re_requested_with_the_violation_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§5.4: reject and re-request, the way unparseable JSON would be re-requested.

    Naming the violation is what makes the second attempt worth its tokens — a
    bare "try again" re-rolls the same dice.
    """
    prompts = _judge_replies(
        monkeypatch, "q1: yes - ok\nq2: no - missing\n", "q1: yes - ok\nq2: no - MINOR - missing\n"
    )

    verdicts = rubrics.evaluate("i", _judged_rubric(), tmp_path)

    assert len(prompts) == 2
    assert "q2: answered 'no' with no severity" in prompts[1]
    by_id = {v.check_id: v for v in verdicts}
    assert (by_id["q2"].answer, by_id["q2"].severity) == (NO, rubrics.MINOR)


def test_a_twice_rejected_reply_is_unknown_not_a_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reply the contract refuses twice yields no judgment, and says why.

    UNKNOWN, not NO: the agent's finding was never readable, so promoting it to a
    dispute would enqueue a decision on evidence the engine rejected. The
    rejection survives on the verdict rather than as the verdict.
    """
    malformed = "q1: yes - ok\nq2: no - missing\n"
    prompts = _judge_replies(monkeypatch, malformed, malformed)

    verdicts = rubrics.evaluate("i", _judged_rubric(), tmp_path)

    assert len(prompts) == rubrics.JUDGE_ATTEMPTS  # rejected, re-requested once, then stops
    assert {v.answer for v in verdicts} == {UNKNOWN}
    assert all("rejected as malformed" in v.evidence for v in verdicts)
    # The escalation gate stays clean: an unread reply is not a disputed criterion.
    assert rubrics.escalation_status(verdicts) == "pass"


def test_every_judge_attempt_is_metered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A re-request spends real tokens; an unrecorded one is an unmeterable dispatch.

    ``policy.session_spend`` zeroes a grant's remaining budget on one of those, so
    a retry that skipped ``record_dispatch`` would halt the session it was added
    to rescue.
    """
    recorded: list[str] = []
    malformed = "q2: no - missing\n"

    def _run(_spec, _prompt, _cwd, **_kwargs):
        return runner.RunResult("x", (), executed=True, returncode=0, stdout=malformed)

    monkeypatch.setattr(runner, "run", _run)
    monkeypatch.setattr(
        runner, "record_dispatch", lambda _r, issue, *_a, **_k: recorded.append(issue)
    )

    rubrics.evaluate("i", _judged_rubric(), tmp_path)

    assert recorded == ["i", "i"]
