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
    assert rubrics.load_rubrics(tmp_path / "nope") == []


def test_select_rubrics_by_work_type(tmp_path: Path) -> None:
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
    text = f"id: s\ndescription: d\napplies_to:\n  - bug\nchecks:\n  - {check}\n"
    rubric_dir = _write(tmp_path, "s.rubric.yaml", text)
    with pytest.raises(ValueError, match=match):
        rubrics.load_rubrics(rubric_dir)


def test_load_rubrics_rejects_a_judged_only_rubric(tmp_path: Path) -> None:
    text = f"id: s\ndescription: d\napplies_to:\n  - bug\n{_ONE_CHECK}"
    rubric_dir = _write(tmp_path, "s.rubric.yaml", text)
    with pytest.raises(ValueError, match="no deterministic check"):
        rubrics.load_rubrics(rubric_dir)


def test_bundled_sample_rubrics_load() -> None:
    loaded = rubrics.load_rubrics()
    by_id = {r.id: r for r in loaded}
    assert "bug-behaviors" in by_id
    assert "feature-behaviors" in by_id
    kinds = {c.kind for c in by_id["bug-behaviors"].checks}
    assert kinds == {JUDGED, DETERMINISTIC}


def test_every_leaf_work_type_has_a_rubric_with_teeth() -> None:

    loaded = rubrics.load_rubrics()
    for work_type in ("bug", "task", "chore", "feature"):
        selected = rubrics.select_rubrics(loaded, work_type)
        assert selected, f"no rubric selected for work type {work_type!r}"
        for rubric in selected:
            assert any(c.kind == DETERMINISTIC for c in rubric.checks), (
                f"rubric {rubric.id!r} has no deterministic check"
            )


def test_shipped_deterministic_checks_are_toolchain_portable() -> None:

    for rubric in rubrics.load_rubrics():
        for check in rubric.checks:
            if check.kind == DETERMINISTIC:
                assert check.verify_mode and not check.command, (
                    f"{rubric.id}/{check.id} hardcodes a command instead of a verify_mode"
                )


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
    python = Path(sys.executable).as_posix()
    ok = rubrics.evaluate_deterministic(_det(f"{python} -c pass"), tmp_path)
    assert ok.answer == YES and ok.kind == DETERMINISTIC
    fail_cmd = _det(f'{python} -c "import sys;sys.exit(1)"')
    assert rubrics.evaluate_deterministic(fail_cmd, tmp_path).answer == NO


def test_evaluate_deterministic_verify_mode_delegates_to_the_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    checks = _judged_rubric().checks
    verdicts = rubrics.parse_judged("q1: yes - has a test\nq2: maybe\n", list(checks))
    by_id = {v.check_id: v for v in verdicts}
    assert by_id["q1"].answer == YES and by_id["q1"].evidence == "has a test"
    assert by_id["q2"].answer == UNKNOWN


def test_parse_judged_reads_the_severity_off_a_finding() -> None:
    verdicts = rubrics.parse_judged(
        "q1: yes - has a test\nq2: no - BLOCKER - the criterion is unmet\n",
        list(_judged_rubric().checks),
    )
    by_id = {v.check_id: v for v in verdicts}
    assert (by_id["q2"].answer, by_id["q2"].severity) == (NO, rubrics.BLOCKER)
    assert by_id["q2"].evidence == "the criterion is unmet"
    assert by_id["q1"].severity == ""


@pytest.mark.parametrize("severity", ["BLOCKER", "IMPORTANT", "MINOR", "minor"])
def test_parse_judged_accepts_the_whole_vocabulary_case_insensitively(severity: str) -> None:
    (verdict,) = rubrics.parse_judged(
        f"q2: no - {severity} - unmet\n", [_judged_rubric().checks[1]]
    )
    assert verdict.severity == severity.upper()


def test_parse_judged_rejects_a_finding_with_no_severity() -> None:

    with pytest.raises(rubrics.JudgedSchemaError) as raised:
        rubrics.parse_judged("q1: yes - fine\nq2: no - missing\n", list(_judged_rubric().checks))
    assert raised.value.violations == (
        "q2: answered 'no' with no severity (BLOCKER/IMPORTANT/MINOR)",
    )


def test_parse_judged_reports_every_violation_at_once() -> None:
    with pytest.raises(rubrics.JudgedSchemaError) as raised:
        rubrics.parse_judged("q1: no - one\nq2: no - two\n", list(_judged_rubric().checks))
    assert [v.split(":")[0] for v in raised.value.violations] == ["q1", "q2"]


def test_an_unanswered_check_is_unknown_not_a_violation() -> None:

    verdicts = rubrics.parse_judged("q1: yes - fine\n", list(_judged_rubric().checks))
    assert {v.check_id: v.answer for v in verdicts} == {"q1": YES, "q2": UNKNOWN}


def test_the_severity_field_is_only_recognised_with_its_separator() -> None:

    (verdict,) = rubrics.parse_judged(
        "q1: yes - MINOR issue in the helper, but the test is there\n",
        [_judged_rubric().checks[0]],
    )
    assert verdict.severity == ""
    assert verdict.evidence == "MINOR issue in the helper, but the test is there"

    with pytest.raises(rubrics.JudgedSchemaError):
        rubrics.parse_judged("q2: no - MINOR issue in the helper\n", [_judged_rubric().checks[1]])


def test_a_severity_less_finding_cannot_be_constructed_or_recorded() -> None:

    with pytest.raises(rubrics.JudgedSchemaError):
        rubrics.CheckVerdict("j", JUDGED, NO, "unmet")
    with pytest.raises(rubrics.JudgedSchemaError):
        rubrics.CheckVerdict("j", JUDGED, NO, "unmet", "CRITICAL")
    with pytest.raises(rubrics.JudgedSchemaError):
        rubrics.CheckVerdict("j", JUDGED, YES, "fine", rubrics.MINOR)
    assert rubrics.CheckVerdict("j", JUDGED, NO, "unmet", rubrics.MINOR).severity == rubrics.MINOR


def test_the_gate_record_carries_the_severity(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(rubrics.tracker, "write", lambda _r, args: calls.append(args))

    rubrics.report_gate(
        Path(), "i", [rubrics.CheckVerdict("j", JUDGED, NO, "unmet", rubrics.BLOCKER)]
    )

    judged_note = calls[1][calls[1].index("--note") + 1]
    assert "j=no (BLOCKER)" in judged_note


def test_evaluate_judged_parses_runner_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    assert seen["timeout"] == 3600.0
    assert seen["capture_usage"] is True
    assert len(recorded) == 1
    issue, phase, prompt = recorded[0]
    assert (issue, phase) == ("i", "validate")
    assert prompt and "q1" in prompt


def test_the_judge_is_dispatched_with_usage_capture_on() -> None:
    mentions = [line.strip() for line in inspect.getsource(rubrics._dispatch_judge).splitlines()]

    assert [line for line in mentions if "capture_usage=True" in line], (
        "the judge is metered through a call that does not capture usage"
    )


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

    envelope = json.dumps({"type": "result", "result": _JUDGE_ANSWERS, "usage": {}})
    verdicts = rubrics.parse_judged(envelope, list(_judged_rubric().checks))
    assert {v.answer for v in verdicts} == {UNKNOWN}


def test_evaluate_judged_timeout_is_unknown_not_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    monkeypatch.setattr(
        runner,
        "run",
        lambda *_a, **_k: runner.RunResult("manual", (), executed=False, handoff=True),
    )
    verdicts = rubrics.evaluate("i", _judged_rubric(), tmp_path)
    assert all(v.answer == UNKNOWN for v in verdicts)


def _proc(output: str = "", returncode: int = 0) -> SimpleNamespace:
    return SimpleNamespace(stdout=output, stderr=output, returncode=returncode)


def test_gate_status_is_deterministic_first() -> None:
    det_no = [rubrics.CheckVerdict("d", DETERMINISTIC, NO)]
    judged_no = [rubrics.CheckVerdict("j", JUDGED, NO, severity=rubrics.BLOCKER)]
    assert rubrics.gate_status(det_no) == "fail"
    assert rubrics.gate_status(judged_no) == "pass"
    assert rubrics.gate_status([rubrics.CheckVerdict("d", DETERMINISTIC, YES)]) == "pass"


def test_escalation_status_fails_only_on_a_judged_no() -> None:

    judged_no = rubrics.CheckVerdict("j", JUDGED, NO, severity=rubrics.BLOCKER)
    assert rubrics.escalation_status([judged_no]) == "fail"
    assert rubrics.escalation_status([rubrics.CheckVerdict("j", JUDGED, YES)]) == "pass"
    assert rubrics.escalation_status([rubrics.CheckVerdict("j", JUDGED, UNKNOWN)]) == "pass"
    assert rubrics.escalation_status([rubrics.CheckVerdict("d", DETERMINISTIC, NO)]) == "pass"


def test_report_gate_records_both_halves_separately(monkeypatch: pytest.MonkeyPatch) -> None:

    calls: list[list[str]] = []

    def fake(_repo_root: Path, args: list[str]) -> None:
        calls.append(args)

    monkeypatch.setattr(rubrics.tracker, "write", fake)

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
    assert calls[0][calls[0].index("--gate") + 1] == rubrics.RUBRIC_GATE


def test_report_gate_records_both_halves_even_when_one_has_no_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    calls: list[list[str]] = []
    monkeypatch.setattr(rubrics.tracker, "write", lambda _r, args: calls.append(args))

    rubrics.report_gate(Path(), "i", [rubrics.CheckVerdict("d", DETERMINISTIC, YES)])

    gates = [args[args.index("--gate") + 1] for args in calls]
    assert gates == [rubrics.RUBRIC_GATE, rubrics.RUBRIC_JUDGED_GATE]
    judged_note = calls[1][calls[1].index("--note") + 1]
    assert "no checks" in judged_note


def test_report_gate_reports_failure_when_either_half_fails_to_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def refuse(_r: Path, args: list[str]) -> None:
        if args[args.index("--gate") + 1] != rubrics.RUBRIC_GATE:
            raise RuntimeError("boom")

    monkeypatch.setattr(rubrics.tracker, "write", refuse)

    ok, message = rubrics.report_gate(
        Path(), "i", [rubrics.CheckVerdict("j", JUDGED, NO, severity=rubrics.MINOR)]
    )

    assert ok is False
    assert rubrics.RUBRIC_JUDGED_GATE in message


def test_the_escalation_gate_is_not_required_so_it_cannot_block(tmp_path: Path) -> None:

    assert rubrics.RUBRIC_JUDGED_GATE not in config.DEFAULT_REQUIRED_GATES
    assert rubrics.RUBRIC_JUDGED_GATE not in config.load_policy_config(tmp_path).required_gates


def test_build_judge_prompt_lists_checks_and_format() -> None:
    prompt = rubrics.build_judge_prompt("i", _judged_rubric(), list(_judged_rubric().checks))
    assert "q1: Q1?" in prompt and "q2: Q2?" in prompt
    assert "<check-id>: yes - " in prompt
    assert "<check-id>: no - <SEVERITY> - " in prompt


def test_build_judge_prompt_states_the_whole_severity_vocabulary() -> None:
    prompt = rubrics.build_judge_prompt("i", _judged_rubric(), list(_judged_rubric().checks))
    assert all(severity in prompt for severity in rubrics.SEVERITIES)


def test_build_judge_prompt_refuses_a_pre_judging_check_question() -> None:
    rubric = Rubric(
        "r",
        "d",
        ("task",),
        (RubricCheck("q1", "Do not flag a missing test as a defect.", JUDGED),),
    )
    with pytest.raises(review.PreJudgingError):
        rubrics.build_judge_prompt("i", rubric, list(rubric.checks))


def _judge_replies(monkeypatch: pytest.MonkeyPatch, *replies: str) -> list[str]:
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

    malformed = "q1: yes - ok\nq2: no - missing\n"
    prompts = _judge_replies(monkeypatch, malformed, malformed)

    verdicts = rubrics.evaluate("i", _judged_rubric(), tmp_path)

    assert len(prompts) == rubrics.JUDGE_ATTEMPTS
    assert {v.answer for v in verdicts} == {UNKNOWN}
    assert all("rejected as malformed" in v.evidence for v in verdicts)
    assert rubrics.escalation_status(verdicts) == "pass"


def test_every_judge_attempt_is_metered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

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
