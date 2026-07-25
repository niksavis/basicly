"""Tests for the behavioral-rubric framework (basicly-0122).

Covers the source model + loader + work-type selection (basicly-0122.1) and the
evaluation + advisory-gate layer (basicly-0122.2): deterministic checks via the
verify runner, judged checks via the agent-agnostic runner, and gate status.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from basicly import rubrics, runner
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


def test_evaluate_judged_parses_runner_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Judged checks dispatch one runner call and parse its structured answers."""
    monkeypatch.setattr(
        runner,
        "run",
        lambda *_a, **_k: runner.RunResult(
            "x", (), executed=True, returncode=0, stdout="q1: yes - ok\nq2: no - missing\n"
        ),
    )
    verdicts = rubrics.evaluate("i", _judged_rubric(), tmp_path)
    assert {v.check_id: v.answer for v in verdicts} == {"q1": YES, "q2": NO}


def test_evaluate_judged_is_bounded_and_metered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The judge obeys runner_timeout and writes a run-record (basicly-kjc5.31).

    Unmetered, a judged dispatch spends tokens that never reach the session's D3
    ceiling; unbounded, a hung judge hangs the whole pass.
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
    # capture_usage would flip some adapters' stdout to JSON, which parse_judged
    # cannot read — the record falls back to the flagged estimate instead.
    assert seen["capture_usage"] is False
    # The recorded inputs identify the dispatch (D9): the phase it ran in and the
    # exact prompt, which the record keeps only as a digest.
    assert len(recorded) == 1
    issue, phase, prompt = recorded[0]
    assert (issue, phase) == ("i", "validate")
    assert prompt and "q1" in prompt


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


def test_gate_status_is_deterministic_first() -> None:
    """Only a deterministic 'no' fails the gate; a judged 'no' stays advisory."""
    det_no = [rubrics.CheckVerdict("d", DETERMINISTIC, NO)]
    judged_no = [rubrics.CheckVerdict("j", JUDGED, NO)]
    assert rubrics.gate_status(det_no) == "fail"
    assert rubrics.gate_status(judged_no) == "pass"
    assert rubrics.gate_status([rubrics.CheckVerdict("d", DETERMINISTIC, YES)]) == "pass"


def test_build_judge_prompt_lists_checks_and_format() -> None:
    """The judge prompt names each check id and states the required answer format."""
    prompt = rubrics.build_judge_prompt("i", _judged_rubric(), list(_judged_rubric().checks))
    assert "q1: Q1?" in prompt and "q2: Q2?" in prompt
    assert "yes|no" in prompt
